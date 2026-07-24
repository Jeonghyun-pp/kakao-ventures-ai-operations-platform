"""seen/health 상태 — 멱등성과 침묵-실패 감지의 원천.

- seen.jsonl: 아이템 키(정규화 URL/영상ID/트윗ID) 원장. **즉시 append** —
  hello-mail의 즉시-기록 패턴(비원자적 쓰기로 인한 중복 처리 버그 회피).
- health.json: 소스별 {last_run, last_ok, last_count, http_status, consecutive_zero}.
  "추출 0건 = 실패 이벤트" — nitter stale 캐시 같은 침묵 실패를 이벤트로 승격하는 생명줄.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.common import data_root, now_iso


def _state_dir() -> Path:
    d = data_root() / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_seen() -> set[str]:
    p = _state_dir() / "seen.jsonl"
    if not p.exists():
        return set()
    return {json.loads(l)["k"] for l in p.read_text(encoding="utf-8").splitlines() if l.strip()}


def mark_seen(key: str) -> None:
    """한 건 처리할 때마다 즉시 기록 — 중간에 죽어도 중복 수집 없음."""
    with (_state_dir() / "seen.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"k": key, "d": now_iso()}, ensure_ascii=False) + "\n")


def load_health() -> dict:
    p = _state_dir() / "health.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def record_health(source_id: str, count: int, http_status: int, note: str = "") -> None:
    """수집 결과를 health에 기록. 0건이면 consecutive_zero 증가 (침묵 실패 카운터)."""
    h = load_health()
    e = h.get(source_id, {"consecutive_zero": 0})
    e["last_run"] = now_iso()
    e["last_count"] = count
    e["http_status"] = http_status
    if count > 0:
        e["last_ok"] = now_iso()
        e["consecutive_zero"] = 0
    else:
        e["consecutive_zero"] = e.get("consecutive_zero", 0) + 1
    if note:
        e["note"] = note
    h[source_id] = e
    (_state_dir() / "health.json").write_text(
        json.dumps(h, ensure_ascii=False, indent=1), encoding="utf-8")
    if count == 0 and http_status == 200:
        print(f"⚠️  HEALTH: {source_id} — HTTP 200인데 신규 0건 "
              f"(연속 {e['consecutive_zero']}회). 패턴 파손 또는 실제 침묵 — audit에서 cadence 대조")
