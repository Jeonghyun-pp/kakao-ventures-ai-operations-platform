"""Gmail 초안 생성 — kv-core 공용. **발송 경로 없음(구조적 금지).**

승인 브릿지가 '발송할 메일'을 Gmail 초안으로 만들 때 쓴다. `drafts().create` 만 부르고
`messages().send` 는 이 모듈에 **존재하지 않는다** — 발송은 사람이 Gmail 에서 직접(버튼).

⚠️ 발송을 막는 것은 **"코드에 send 호출이 없다"는 사실 하나**다. 스코프(`gmail.modify`)는
구글 문서상 `messages.send` 를 **허용**하므로 API 레벨 차단이 아니다(W0-W5 리뷰 H6 에서 정정
— 이전 주석은 이중 방어라고 잘못 주장했다). 그래서 "send 호출 0건"을 사람 눈이 아니라
**AST 검사**(tests/_synthetic_slice.py ④)가 상시 확인한다. 이 모듈에 send 헬퍼를 추가하면
그 테스트가 깨진다 — 그게 의도다(발송 승격은 W10 승인 브릿지 경유로만).
(triage/core/gmail.py 의 초안 로직을 kv-core 로 승격 — 승인 브릿지가 스킬에 의존하지 않게.)
"""
from __future__ import annotations

import base64
from email.mime.text import MIMEText

from . import secrets

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]   # 주의: 이 스코프는 send 를 막지 않는다(위 참조)


def service():
    """Gmail 서비스 — `$KV_CONFIG/google/token.json`(gmail.modify) 로 빌드."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    tok = secrets.credential("google/token.json")
    creds = Credentials.from_authorized_user_file(str(tok), _SCOPES)
    return build("gmail", "v1", credentials=creds)


def get_draft(draft_id: str, *, svc=None) -> dict | None:
    """초안 1건 조회 — [W8] read-back 검증용("만들었다 ≠ 반영됐다"). 읽기 전용."""
    svc = svc or service()
    try:
        return svc.users().drafts().get(userId="me", id=draft_id).execute()
    except Exception as e:                        # noqa: BLE001
        print(f"⚠️  gmail.get_draft 실패({draft_id}): {type(e).__name__}")
        return None


def sent_messages(*, days: int = 30, query: str = "", svc=None) -> list[dict]:
    """보낸 메일 목록(헤더 + 본문) — **읽기 전용.** [W10 무수정률 측정]

    W10 승격 게이트가 "심사역이 초안을 고치지 않고 그대로 보냈나"를 재려면 실제로 나간
    메일을 읽어야 한다. `gmail.modify` 스코프가 이미 읽기를 포함하므로 권한 추가는 없다.

    ⚠️ 이 함수는 **읽기만 한다.** AST 무발송 검사가 이 모듈에 `.send()` 가 0건임을 매 커밋
    확인하고, 이 함수도 그 검사 아래 있다.
    """
    svc = svc or service()
    q = f"in:sent newer_than:{int(days)}d" + (f" {query}" if query else "")
    out: list[dict] = []
    try:
        page = svc.users().messages().list(userId="me", q=q, maxResults=200).execute()
    except Exception as e:                        # noqa: BLE001
        print(f"⚠️  gmail.sent_messages 목록 실패: {type(e).__name__}: {e}")
        return out
    for m in page.get("messages", []) or []:
        try:
            full = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
        except Exception:                         # noqa: BLE001  한 건 실패가 측정을 죽이지 않는다
            continue
        hdr = {h["name"].lower(): h["value"]
               for h in (full.get("payload", {}).get("headers") or [])}
        out.append({"id": m["id"], "to": hdr.get("to", ""), "subject": hdr.get("subject", ""),
                    "date": hdr.get("date", ""), "body": _plain_body(full.get("payload", {}))})
    return out


def _plain_body(payload: dict) -> str:
    """MIME 트리에서 text/plain 을 꺼낸다(없으면 첫 텍스트 파트)."""
    import base64 as _b64

    def _decode(d):
        try:
            return _b64.urlsafe_b64decode(d.encode()).decode("utf-8", "replace")
        except Exception:                         # noqa: BLE001
            return ""

    if payload.get("mimeType") == "text/plain":
        return _decode((payload.get("body") or {}).get("data") or "")
    for part in payload.get("parts") or []:
        got = _plain_body(part)
        if got:
            return got
    return _decode((payload.get("body") or {}).get("data") or "")


def create_draft(to, subject: str, body_text: str, *, svc=None) -> str:
    """초안 1개 생성 → draft id. **발송 안 함**(drafts().create 만)."""
    svc = svc or service()
    msg = MIMEText(body_text)
    msg["to"] = to if isinstance(to, str) else ", ".join(to)
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    draft = svc.users().drafts().create(
        userId="me", body={"message": {"raw": raw}}).execute()
    return draft["id"]
