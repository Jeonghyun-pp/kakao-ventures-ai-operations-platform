"""과거 plan 기록의 msg id들을 Gmail에서 재수집해 리플레이 코퍼스를 만든다 (읽기 전용).
inbox_messages()와 동일한 정규화를 거쳐, 두 시스템에 '완전히 같은 입력'을 보장한다."""
import json
import sys
from pathlib import Path

TRIAGE = Path.home() / ".claude/skills/mail-harness/project/triage"
sys.path.insert(0, str(TRIAGE))
from core import gmail  # noqa: E402

H_DATA = Path.home() / ".claude/skills/hello-mail/project/data"
OUT = Path(sys.argv[1])

history = {}   # id → (category, action) 당시 기록
for f in sorted(H_DATA.glob("plan_*.json")):
    for p in json.loads(f.read_text()):
        history[p["id"]] = {"category": p["category"], "action": p["action"]}

svc = gmail.service()
corpus, gone = [], []
for mid in history:
    try:
        m = svc.users().messages().get(userId="me", id=mid, format="full").execute()
    except Exception:
        gone.append(mid)
        continue
    payload = m.get("payload", {})
    h = {x["name"].lower(): x["value"] for x in payload.get("headers", [])}
    to_all = (gmail._addrs(h.get("to", "")) + gmail._addrs(h.get("cc", ""))
              + gmail._addrs(h.get("bcc", "")))
    corpus.append({
        "id": m["id"], "thread_id": m.get("threadId"),
        "message_id_header": h.get("message-id"),
        "subject": h.get("subject", ""),
        "sender": (gmail._addrs(h.get("from", "")) or [""])[0].lower(),
        "sender_raw": h.get("from", ""), "to": to_all,
        "recipient_count": len(to_all), "date": h.get("date", ""),
        "list_unsubscribe": bool(h.get("list-unsubscribe")),
        "labels": m.get("labelIds", []),
        "body": gmail._body_text(payload), "snippet": m.get("snippet", ""),
        "_history": history[m["id"]],
    })

OUT.write_text(json.dumps(corpus, ensure_ascii=False))
print(f"✅ 코퍼스 {len(corpus)}건 수집 (삭제됨 {len(gone)}건 제외) → {OUT}")
