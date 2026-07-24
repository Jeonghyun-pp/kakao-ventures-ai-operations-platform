"""이메일 전용 뉴스레터 수집 — hello-mail OAuth 크리덴셜 재사용, **읽기만** (발송 경로 없음).

대상: a16z Consumer Substack(공개 아카이브/RSS 없음 확정 — 이메일이 유일 경로).
sources.yaml의 gmail 어댑터 엔트리마다 query(Gmail 검색 문법)를 실행해 본문을 저장.
라벨링/아카이빙 등 mutation은 하지 않는다 — hello-mail 영역과 충돌 방지.
"""
from __future__ import annotations

import base64

from core.common import config, save_item, sources
from core.state import load_seen, mark_seen, record_health


def _svc():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(config()["gmail"]["token_path"])
    return build("gmail", "v1", credentials=creds)


def _body_text(payload: dict) -> str:
    """multipart에서 text/plain 우선 추출."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
    for part in payload.get("parts", []) or []:
        t = _body_text(part)
        if t:
            return t
    return ""


def collect() -> dict:
    lookback = int(config()["gmail"].get("lookback_days", 8))
    seen = load_seen()
    summary = {}
    try:
        svc = _svc()
    except Exception as e:  # noqa: BLE001
        for src in sources("gmail"):
            record_health(src["id"], 0, 0, f"Gmail 크리덴셜 실패: {e}")
            summary[src["id"]] = 0
        return summary
    for src in sources("gmail"):
        q = f"({src['query']}) newer_than:{lookback}d"
        try:
            res = svc.users().messages().list(userId="me", q=q, maxResults=20).execute()
            msgs = res.get("messages", [])
        except Exception as e:  # noqa: BLE001
            record_health(src["id"], 0, 0, f"쿼리 실패: {e}")
            summary[src["id"]] = 0
            continue
        new = 0
        for m in msgs:
            key = f"gm:{m['id']}"
            if key in seen:
                continue
            full = svc.users().messages().get(userId="me", id=m["id"], format="full").execute()
            headers = {h["name"].lower(): h["value"] for h in full["payload"].get("headers", [])}
            text = _body_text(full["payload"]) or full.get("snippet", "")
            save_item(src, key, headers.get("subject", "(no subject)"), "",
                      headers.get("date", ""), text, {"kind": "newsletter-email",
                                                      "from": headers.get("from", "")})
            mark_seen(key)
            seen.add(key)
            new += 1
        record_health(src["id"], new, 200)
        summary[src["id"]] = new
    return summary


if __name__ == "__main__":
    s = collect()
    print(f"[gmail] {sum(s.values())} new: " + ", ".join(f"{k}={v}" for k, v in s.items()))
