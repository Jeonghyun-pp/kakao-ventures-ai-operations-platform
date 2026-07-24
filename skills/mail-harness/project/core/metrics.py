"""metrics — 무수정 발송률 측정 (자동 발송 승격의 데이터 근거).

apply가 만든 초안을 sent_watch.jsonl에 등록해두면, 이후 실행에서 Gmail Sent를
읽어 "초안 그대로 나갔는지"를 판정해 metrics.jsonl에 적재한다.

판정: 발송본에 초안 본문이 (공백 정규화 후) 그대로 포함되거나 유사도 ≥ 0.98 → 무수정.
승격 규칙(config): 최근 promote_window_days 동안 rate ≥ promote_threshold, 표본 n ≥ 5.
n<5는 표본 부족으로 승격 불가 — 수치가 좋아도 우연일 수 있다.
"""
from __future__ import annotations

import difflib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
SENT_WATCH = PROJ / "data" / "sent_watch.jsonl"
WATCH_EXPIRE_DAYS = 30   # 이 기간 내 발송 안 되면 '미발송(초안 폐기)'으로 종결


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _gmail_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(
        str(PROJ / "triage" / "token.json"),
        ["https://www.googleapis.com/auth/gmail.modify"])
    if not creds.valid and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


def _body_text(payload) -> str:
    import base64
    plain, html = [], []

    def walk(part):
        data = part.get("body", {}).get("data")
        text = ""
        if data:
            try:
                text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            except Exception:
                text = ""
        mt = part.get("mimeType", "")
        if mt == "text/plain":
            plain.append(text)
        elif mt == "text/html":
            html.append(re.sub(r"<[^>]+>", " ", text))
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload or {})
    return "\n".join(plain) if any(plain) else "\n".join(html)


def watch(entries: list[dict]):
    """apply가 만든 초안을 측정 대기열에 등록. msg_id 기준 중복 제거."""
    seen = set()
    if SENT_WATCH.exists():
        for line in SENT_WATCH.read_text().splitlines():
            if line.strip():
                seen.add(json.loads(line).get("msg_id"))
    SENT_WATCH.parent.mkdir(parents=True, exist_ok=True)
    with SENT_WATCH.open("a") as f:
        for e in entries:
            if e["msg_id"] not in seen:
                f.write(json.dumps({**e, "watched_at": datetime.now().isoformat()},
                                   ensure_ascii=False) + "\n")


def measure(cfg: dict) -> dict:
    """대기열을 훑어 발송 여부·수정 여부를 판정하고 metrics.jsonl에 적재."""
    if not SENT_WATCH.exists():
        return {"measured": 0, "pending": 0}
    rows = [json.loads(l) for l in SENT_WATCH.read_text().splitlines() if l.strip()]
    pending = [r for r in rows if not r.get("done")]
    if not pending:
        return {"measured": 0, "pending": 0}

    svc = _gmail_service()
    log_file = PROJ / cfg["harness"]["metrics"]["log_file"]
    log_file.parent.mkdir(parents=True, exist_ok=True)
    measured = 0

    for r in pending:
        try:
            thread = svc.users().threads().get(
                userId="me", id=r["thread_id"], format="full").execute()
        except Exception:
            continue  # 스레드 조회 실패는 다음 실행에서 재시도
        sent_bodies = []
        for msg in thread.get("messages", []):
            if "SENT" in msg.get("labelIds", []):
                sent_bodies.append(_norm(_body_text(msg.get("payload"))))
        draft = _norm(r.get("draft_body", ""))
        verdict = None
        if sent_bodies and draft:
            best = max(
                (difflib.SequenceMatcher(None, draft,
                                         b[:len(draft) * 2]).ratio() for b in sent_bodies),
                default=0.0)
            contained = any(draft in b for b in sent_bodies)
            verdict = bool(contained or best >= 0.98)
        elif datetime.fromisoformat(r["watched_at"]) < datetime.now() - timedelta(days=WATCH_EXPIRE_DAYS):
            verdict = None  # 만료 — 발송 안 됨(초안 폐기·수동 대응). 표본에서 제외하고 종결.
            r["done"] = "expired"
        if verdict is not None:
            with log_file.open("a") as f:
                f.write(json.dumps({
                    "measured_at": datetime.now().isoformat(),
                    "category": r["category"], "unmodified": verdict,
                }, ensure_ascii=False) + "\n")
            r["done"] = "measured"
            measured += 1

    # 원자적으로 대기열 재기록
    tmp = SENT_WATCH.with_suffix(".tmp")
    tmp.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    tmp.replace(SENT_WATCH)
    return {"measured": measured,
            "pending": sum(1 for r in rows if not r.get("done"))}


def summary(cfg: dict) -> dict:
    """카테고리별 무수정 발송률 (승격 판단용)."""
    mcfg = cfg["harness"]["metrics"]
    log_file = PROJ / mcfg["log_file"]
    window_days = mcfg.get("promote_window_days", 21)
    threshold = mcfg.get("promote_threshold", 0.95)
    if not log_file.exists():
        return {"categories": {}, "window_days": window_days, "threshold": threshold}
    cutoff = datetime.now() - timedelta(days=window_days)
    per: dict[str, list[bool]] = {}
    for line in log_file.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if datetime.fromisoformat(row["measured_at"]) >= cutoff:
            per.setdefault(row["category"], []).append(row["unmodified"])
    return {
        "categories": {c: {"n": len(v), "rate": sum(v) / len(v)} for c, v in per.items()},
        "window_days": window_days, "threshold": threshold,
    }
