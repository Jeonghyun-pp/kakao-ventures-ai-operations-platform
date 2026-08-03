"""Gmail 초안 생성 — kv-core 공용. **발송 경로 없음(구조적 금지).**

승인 브릿지가 '발송할 메일'을 Gmail 초안으로 만들 때 쓴다. `drafts().create` 만 부르고
`messages().send` 는 이 모듈에 **존재하지 않는다** — 발송은 사람이 Gmail 에서 직접(버튼).
OAuth 스코프도 `gmail.modify`(발송 불가)라 API 레벨에서도 send 가 막혀 있다.
(triage/core/gmail.py 의 초안 로직을 kv-core 로 승격 — 승인 브릿지가 스킬에 의존하지 않게.)
"""
from __future__ import annotations

import base64
from email.mime.text import MIMEText

from . import secrets

_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]   # send 없음


def _assert_no_send() -> None:
    """발송 경로가 불리면 즉시 멈춘다 — 선언이 아니라 구조로 막는다."""
    raise RuntimeError("kv_core.gmail 은 발송하지 않는다 — 초안까지만. 발송은 사람이 Gmail 에서.")


def service():
    """Gmail 서비스 — `$KV_CONFIG/gmail/token.json`(gmail.modify) 로 빌드."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    tok = secrets.credential("gmail/token.json")
    creds = Credentials.from_authorized_user_file(str(tok), _SCOPES)
    return build("gmail", "v1", credentials=creds)


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
