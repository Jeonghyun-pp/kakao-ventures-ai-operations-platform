#!/usr/bin/env python3
"""`주차`가 빈 행을 `Captured At`에서 역산해 채운다. 무과금·멱등.

## 왜 필요한가 (2026-07-28)
`주차`는 착지 때 `core/notion._iso_week(run_id)`가 **이미 계산한다.** 그런데 **앱스토어 DB에
칸이 없어서** 스키마 교집합(`if name not in schema: continue`)에서 조용히 버려졌다 —
1,711행 전부 빈칸이었다. 칸을 만들었으니(migrate ⑪) **앞으로는 자동으로 찬다.**
이 스크립트는 **과거분만** 소급한다.

🔴 **옵션이 먼저다.** 노션 select는 옵션에 없는 값을 조용히 버리고 PATCH는 200을 준다
   (CRITICAL #8). 과거 주차(W28~W30)는 `_weeks_through`의 기본 시작이 '오늘'이라
   옵션에 없었다 — migrate에 `start=오늘-120일`을 넣어 만들어 두고 여기로 온다.
   그래서 이 스크립트는 **쓰기 전에 옵션 존재를 확인하고, 없으면 그 행을 건너뛰며 소리를 낸다.**

사용:
  ./.venv/bin/python tools/backfill_week.py <채널>            # dry-run
  ./.venv/bin/python tools/backfill_week.py <채널> --apply
  채널: 앱스토어 | 스레드 | 링크드인
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    channel = args[0] if args else "앱스토어"

    from kv_core import secrets
    secrets.load()
    import requests
    import yaml
    from core import notion

    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    token = os.environ["NOTION_TOKEN"]
    db = str(cfg["notion"]["channel_db_ids"][channel]).split("://", 1)[-1]

    schema = requests.get(f"{notion.API}/databases/{db}",
                          headers=notion._headers(token), timeout=30).json()["properties"]
    wk = schema.get("주차")
    if not wk or wk["type"] != "select":
        print(f"🔴 {channel} DB에 `주차` select 칸이 없다 — notion_migrate 먼저 돌려라")
        return 1
    opts = {o["name"] for o in wk["select"]["options"]}
    print(f"· {channel} · `주차` 옵션 {len(opts)}개")

    rows, cur = [], None
    while True:
        body: dict = {"page_size": 100}
        if cur:
            body["start_cursor"] = cur
        r = notion._write("POST", f"{notion.API}/databases/{db}/query", token, body)
        if r.status_code != 200:
            print(f"🔴 쿼리 실패 {r.status_code}: {r.text[:200]}")
            return 1
        d = r.json()
        for pg in d.get("results", []):
            p = pg["properties"]
            have = ((p.get("주차") or {}).get("select") or {}).get("name")
            cap = ((p.get("Captured At") or {}).get("date") or {}).get("start")
            rows.append((pg["id"], have, cap))
        if not d.get("has_more"):
            break
        cur = d.get("next_cursor")

    plan, no_date, no_opt = [], 0, set()
    for pid, have, cap in rows:
        if have or not cap:
            no_date += 0 if have else 1
            continue
        y, w, _ = date.fromisoformat(cap[:10]).isocalendar()
        label = f"{y}-W{w:02d}"
        if label not in opts:                 # 🔴 조용히 버려질 값 — 쓰지 않고 소리를 낸다
            no_opt.add(label)
            continue
        plan.append((pid, label))

    from collections import Counter
    c = Counter(l for _, l in plan)
    print(f"· 전체 {len(rows)}행 · 이미 있음 {sum(1 for _,h,_ in rows if h)} · "
          f"Captured At 없음 {no_date} · **채울 행 {len(plan)}**")
    for k, v in sorted(c.items()):
        print(f"     {k}  {v:>5}행")
    if no_opt:
        print(f"🔴 옵션에 없어 건너뛴 주차: {sorted(no_opt)} — migrate로 옵션부터 만들어라")

    if not apply:
        print("\n[dry-run] --apply 를 붙이면 실제로 쓴다.")
        return 0

    ok = fail = 0
    for pid, label in plan:
        r = notion._write("PATCH", f"{notion.API}/pages/{pid}", token,
                          {"properties": {"주차": {"select": {"name": label}}}})
        if r.status_code == 200:
            ok += 1
        else:
            fail += 1
            if fail <= 3:
                print(f"  🔴 {pid[:8]} {r.status_code}: {r.text[:140]}")
    print(f"\n{'✅' if not fail else '🔴'} {ok}행 기록 · {fail}행 실패")
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
