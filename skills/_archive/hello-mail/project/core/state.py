"""처리 이력 — 같은 메일에 초안을 두 번 만들지 않기 위해.

bplan-automation의 알려진 결함(비원자적 쓰기 → 재실행 시 중복 생성)을 여기서 되풀이하지 않는다.
액션을 **실행한 직후 즉시** 기록하고, 매 실행은 기록된 id를 건너뛴다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STORE = Path(__file__).resolve().parent.parent / "data" / "processed.json"


def load() -> dict:
    if not STORE.exists():
        return {}
    try:
        return json.loads(STORE.read_text())
    except Exception:
        return {}


def mark(msg_id: str, category: str, action: str, detail: str = "") -> None:
    data = load()
    data[msg_id] = {
        "category": category,
        "action": action,
        "detail": detail,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def seen(msg_id: str) -> bool:
    return msg_id in load()
