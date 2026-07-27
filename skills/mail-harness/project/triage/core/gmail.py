"""Gmail 어댑터 — 읽기 · 아카이브 · 초안 생성.

**이 모듈에는 발송(messages.send) 경로가 없다.** 의도적이다.
회사 이름으로 나가는 메일은 사람이 Gmail에서 확인하고 보낸다(가이드라인: "VC는 레퓨테이션
비즈니스"). 자동 발송을 붙이려면 여기에 send를 추가해야 하는데, 그때 이 주석을 지우고
config의 autonomy.send_enabled를 함께 켤 것.

인증·MIME 구조는 bplan-automation/project/main.py에서 검증된 것을 승계한다.
특히 _to_gmail_html: Gmail 초안 에디터가 긴 text/plain 라인을 중간에서 wrap하며 문단 빈 줄을
접는 문제가 있어, 수동 작성 시의 <div> 블록 구조를 흉내낸 text/html 파트를 함께 넣는다.
"""
from __future__ import annotations

import base64
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as _html_escape
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from kv_core import secrets

PROJ = Path(__file__).resolve().parent.parent
# 자격증명은 스킬별 복사본이 아니라 $KV_CONFIG 단일 참조다.
# 통합 전에는 token.json 이 4벌이었는데 refresh_token·client_id 가 전부 동일했다 —
# 물리적 복사본일 뿐이었고, 그래서 재인증도 4번 해야 했다. 이제 한 곳만 갱신된다.
TOKEN_FILE = secrets.credential_path("google/token.json")
CRED_FILE = secrets.credential_path("google/gmail_credentials.json")

# modify = 읽기/쓰기(라벨·초안) - 영구삭제 제외. 초안만 만들 것이므로 이걸로 충분하다.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def service():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CRED_FILE.exists():
                raise FileNotFoundError(f"gmail_credentials.json 없음: {CRED_FILE}")
            flow = InstalledAppFlow.from_client_secrets_file(str(CRED_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def labels(svc) -> dict:
    res = svc.users().labels().list(userId="me").execute()
    return {l["name"]: l["id"] for l in res.get("labels", [])}


# ── 읽기 ────────────────────────────────────────────────────────────────

def _decode(part) -> str:
    data = part.get("body", {}).get("data")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _body_text(payload) -> str:
    """MIME 트리를 걸어 본문 텍스트를 얻는다. text/plain 우선, 없으면 html 태그 제거."""
    plain, html = [], []

    def walk(part):
        mt = part.get("mimeType", "")
        if mt == "text/plain":
            plain.append(_decode(part))
        elif mt == "text/html":
            html.append(_decode(part))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload or {})
    if any(plain):
        return "\n".join(plain)
    text = re.sub(r"<br\s*/?>", "\n", "\n".join(html), flags=re.I)
    text = re.sub(r"</(p|div|tr|li)>", "\n", text, flags=re.I)
    return re.sub(r"<[^>]+>", " ", text)


def _addrs(value: str) -> list[str]:
    """헤더 문자열에서 이메일 주소만 뽑는다."""
    return re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", value or "")


def inbox_messages(svc, lookback_days: int, max_results: int) -> list[dict]:
    """인박스 메시지를 정규화해서 반환. 스레드가 아니라 메시지 단위로 본다.

    발신이 우리 자신인 메시지(SENT)는 제외한다 — 비플랜 접수 메일은 hello@가 hello@에게
    보내는 구조라 SENT+INBOX를 동시에 달고 있으므로, 여기서 거르지 않고 rules.py의
    BPLAN_INTAKE 규칙이 명시적으로 잡아 보호한다.
    """
    q = f"in:inbox newer_than:{lookback_days}d"
    res = svc.users().messages().list(userId="me", q=q, maxResults=max_results).execute()
    out = []
    for ref in res.get("messages", []):
        m = svc.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        payload = m.get("payload", {})
        h = {x["name"].lower(): x["value"] for x in payload.get("headers", [])}
        to_all = _addrs(h.get("to", "")) + _addrs(h.get("cc", "")) + _addrs(h.get("bcc", ""))
        out.append({
            "id": m["id"],
            "thread_id": m.get("threadId"),
            "message_id_header": h.get("message-id"),
            "subject": h.get("subject", ""),
            "sender": (_addrs(h.get("from", "")) or [""])[0].lower(),
            "sender_raw": h.get("from", ""),
            "to": to_all,
            "recipient_count": len(to_all),
            "date": h.get("date", ""),
            "list_unsubscribe": bool(h.get("list-unsubscribe")),
            "labels": m.get("labelIds", []),
            "body": _body_text(payload),
            "snippet": m.get("snippet", ""),
        })
    return out


# ── 쓰기: 아카이브 ───────────────────────────────────────────────────────

def archive(svc, msg_id: str):
    """INBOX 라벨만 제거. 삭제가 아니라 아카이빙이므로 되돌릴 수 있다."""
    svc.users().messages().modify(
        userId="me", id=msg_id, body={"removeLabelIds": ["INBOX"]}
    ).execute()


# ── 쓰기: 초안 ──────────────────────────────────────────────────────────

def _to_gmail_html(text: str) -> str:
    lines = text.split("\n")
    return "".join(
        "<div><br></div>" if l == "" else f"<div>{_html_escape(l)}</div>" for l in lines
    )


def _quote(msg: dict) -> str:
    """포워드 본문에 붙일 원본 인용 블록. Gmail 수동 포워드와 같은 형식."""
    return (
        "\n\n---------- Forwarded message ---------\n"
        f"From: {msg.get('sender_raw') or msg.get('sender')}\n"
        f"Date: {msg.get('date')}\n"
        f"Subject: {msg.get('subject')}\n"
        f"To: {', '.join(msg.get('to', []))}\n\n"
        f"{msg.get('body', '')}"
    )


def draft_reply(svc, msg: dict, body: str, to: str) -> str:
    """원본 스레드에 답장 초안. to를 명시적으로 받는다 — 절대 헤더에서 추측하지 않는다."""
    if not to:
        raise ValueError("draft_reply: 수신자가 비어 있다")
    subject = msg["subject"]
    mime = MIMEMultipart("alternative")
    mime["To"] = to
    mime["Subject"] = subject if subject.startswith("Re: ") else f"Re: {subject}"
    if msg.get("message_id_header"):
        mime["In-Reply-To"] = msg["message_id_header"]
        mime["References"] = msg["message_id_header"]
    mime.attach(MIMEText(body, "plain", "utf-8"))
    mime.attach(MIMEText(_to_gmail_html(body), "html", "utf-8"))
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    d = svc.users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": msg["thread_id"]}}
    ).execute()
    return d["id"]


def draft_forward(svc, msg: dict, body: str, to: str) -> str:
    """포워드 초안 — 본문(body) + 원본 인용. 수신자는 호출부가 결정한다.

    Webflow 메일이 정확히 이 경로를 써야 한다. reply를 쓰면 수신자가
    no-reply@webflow.com으로 잡혀 반송된다(2026-07-10 실제 발생).
    """
    if not to:
        raise ValueError("draft_forward: 수신자가 비어 있다")
    subject = msg["subject"]
    full = body + _quote(msg)
    mime = MIMEMultipart("alternative")
    mime["To"] = to
    mime["Subject"] = subject if subject.startswith("Fwd: ") else f"Fwd: {subject}"
    mime.attach(MIMEText(full, "plain", "utf-8"))
    mime.attach(MIMEText(_to_gmail_html(full), "html", "utf-8"))
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    d = svc.users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": msg["thread_id"]}}
    ).execute()
    return d["id"]
