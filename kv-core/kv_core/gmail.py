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


def sent_messages(*, days: int = 30, query: str = "", svc=None,
                  max_messages: int = 2000) -> tuple[list[dict], bool]:
    """보낸 메일 목록(헤더 + 본문) — **읽기 전용.** 반환: (메시지들, 잘렸나).

    [W10 무수정률 측정] 승격 게이트가 "심사역이 초안을 고치지 않고 그대로 보냈나"를 재려면
    실제로 나간 메일을 읽어야 한다. `gmail.modify` 스코프가 이미 읽기를 포함한다.

    [적대리뷰 MED-3] 예전엔 `maxResults=200` 한 번만 부르고 페이지네이션이 없었다.
    21일치가 200건을 넘는 건 hello@ 에서 흔한데, 잘린 건은 측정에서 `unmatched` 로 빠져
    **n 이 줄고 게이트가 안 열린다** — 그리고 리포트는 그걸 "아직 안 보냈나 보다"로
    **틀리게 설명했다**. 이제 끝까지 읽고, 상한에 걸리면 **잘렸다는 사실을 반환**한다.
    측정 쪽은 잘린 결과로 자격을 판정하면 안 된다.

    ⚠️ 읽기만 한다. AST 무발송 검사가 이 모듈에 `.send()` 0건임을 매 커밋 확인한다.
    """
    svc = svc or service()
    q = f"in:sent newer_than:{int(days)}d" + (f" {query}" if query else "")
    ids: list[str] = []
    token = None
    truncated = False
    while True:
        try:
            page = svc.users().messages().list(
                userId="me", q=q, maxResults=500,
                **({"pageToken": token} if token else {})).execute()
        except Exception as e:                    # noqa: BLE001
            print(f"⚠️  gmail.sent_messages 목록 실패: {type(e).__name__}: {e}")
            return [], True                       # 못 읽었으면 '잘림'으로 본다(측정 무효)
        ids += [m["id"] for m in (page.get("messages") or [])]
        token = page.get("nextPageToken")
        if not token:
            break
        if len(ids) >= max_messages:
            truncated = True
            break

    out: list[dict] = []
    for mid in ids[:max_messages]:
        try:
            full = svc.users().messages().get(userId="me", id=mid, format="full").execute()
        except Exception:                         # noqa: BLE001  한 건 실패가 측정을 죽이지 않는다
            continue
        payload = full.get("payload", {}) or {}
        hdr = {h["name"].lower(): h["value"] for h in (payload.get("headers") or [])}
        body, ok = _plain_body(payload)
        out.append({"id": mid, "to": hdr.get("to", ""), "subject": hdr.get("subject", ""),
                    "date": hdr.get("date", ""), "body": body, "body_ok": ok})
    return out, truncated


_TAG = __import__("re").compile(r"<[^>]+>")
_BR = __import__("re").compile(r"<br\s*/?>|</p>|</div>", __import__("re").I)


def _charset(part: dict) -> str:
    for h in part.get("headers") or []:
        if h.get("name", "").lower() == "content-type":
            m = __import__("re").search(r'charset="?([\w-]+)"?', h.get("value", ""), __import__("re").I)
            if m:
                return m.group(1)
    return "utf-8"


def _decode_part(part: dict) -> str | None:
    """파트 본문 디코드. 실패는 **None** — 조용한 빈 문자열은 측정을 오염시킨다.

    [적대리뷰 HIGH-3/MED-2] 예전엔 실패 시 `""` 를 돌려줬다. 빈 문자열 둘의 유사도는
    difflib 에서 **1.0** 이라 "무수정"이 공짜로 적립됐다. 그리고 charset 을 안 읽어
    euc-kr 한국어가 통째로 깨져 '거짓 수정'이 됐다.
    """
    import base64 as _b64
    data = (part.get("body") or {}).get("data")
    if not data:
        return None
    try:
        raw = _b64.urlsafe_b64decode(data + "==")   # 패딩 관대
    except Exception:                             # noqa: BLE001
        return None
    for enc in (_charset(part), "utf-8", "cp949"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return None


def _plain_body(payload: dict) -> tuple[str, bool]:
    """MIME 트리 → (본문, 신뢰 가능한가).

    [적대리뷰 MED-1] 예전 폴백은 mimeType 검사 없이 `data` 만 있으면 본문으로 삼았다 —
    HTML 은 물론 **인라인 이미지의 바이너리**까지 본문이 됐다(프로브로 재현됨).
    이제 ①트리 전체에서 text/plain 을 먼저 찾고 ②없으면 text/html 을 태그 제거해 쓰고
    ③그 외에는 **본문 없음**으로 보고한다(빈 문자열을 조용히 흘리지 않는다).
    """
    def walk(node, want):
        if (node.get("mimeType") or "").startswith(want):
            got = _decode_part(node)
            if got:
                return got
        for part in node.get("parts") or []:
            got = walk(part, want)
            if got:
                return got
        return None

    plain = walk(payload, "text/plain")
    if plain:
        return plain, True
    html = walk(payload, "text/html")
    if html:
        import html as _h
        t = _BR.sub("\n", html)
        t = _TAG.sub("", t)
        return _h.unescape(t), True
    return "", False                              # 본문을 못 얻었다 — 측정에서 제외해야 한다


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
