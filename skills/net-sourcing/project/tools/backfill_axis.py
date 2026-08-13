#!/usr/bin/env python3
"""노션 링크드인 행의 `Axis`(검색축)를 `Match Reason`에서 소급 복원한다. 멱등. $0.

왜: 축 귀속이 `Match Reason` **문장 안에만** 있어서 노션에서 필터를 못 걸었다
    → **"새 기준(극초기 필터)으로 발굴된 사람만 보기"가 불가능**했다. `Seed`와 같은 문제다.
    (어댑터는 2026-07-14에 배선했다. 이 도구는 **이미 착지한 과거 행**만 메운다.)

🔴 과거 행에는 **이제 없는 옛 축**(t-founder·kr-tie-global…)이 들어 있다. 옵션이 없으면
   `_known_options_only`가 **조용히 버린다** → 이 도구가 옵션부터 추가한다.
   옛 축을 지우지 마라 — **"옛 기준 vs 새 기준"을 노션에서 가르는 유일한 수단**이 그 라벨이다.

사용: ./.venv/bin/python tools/backfill_axis.py [--apply]
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter
from pathlib import Path

import requests

# [W12 · B-F1] 노션 전송 규율의 정본은 kv_core 다.
from kv_core import notion as _kv

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
API = "https://api.notion.com/v1"


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}", "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"}


def _plain(p: dict | None) -> str:
    if not p:
        return ""
    return "".join(x["plain_text"] for x in p.get("rich_text", []))


def _query(db: str, tok: str, flt: dict) -> list[dict]:
    rows, cur = [], None
    while True:
        body: dict = {"page_size": 100, "filter": flt}
        if cur:
            body["start_cursor"] = cur
        j = _kv.write("POST", f"{API}/databases/{db}/query", tok, body).json()
        rows += j["results"]
        if not j.get("has_more"):
            return rows
        cur = j["next_cursor"]


def main() -> int:
    apply = "--apply" in sys.argv
    for line in (PROJ / ".env").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    import yaml
    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    db, tok = cfg["notion"]["db_id"], os.environ["NOTION_TOKEN"]

    schema = _kv.read(f"{API}/databases/{db}", tok).json()["properties"]
    if "Axis" not in schema:
        print("🔴 `Axis`(multi_select)가 없다 — tools/notion_migrate.py --apply 를 먼저 돌려라")
        return 1

    rows = _query(db, tok, {"property": "Channel", "select": {"equals": "LinkedIn"}})
    todo, found, skipped = [], Counter(), 0
    for r in rows:
        p = r["properties"]
        if (p.get("Axis") or {}).get("multi_select"):
            skipped += 1                                   # 멱등
            continue
        mr = _plain(p.get("Match Reason"))
        # 링크드인 profile_search 의 Match Reason = " · ".join(축라벨). 게시글 후보는 비어 있다.
        axes = [x.strip() for x in mr.split("·") if x.strip() and " " not in x.strip()]
        if axes:
            todo.append((r["id"], axes))
            found.update(axes)

    print(f"· 링크드인 {len(rows)}행 · 이미 Axis 있음 {skipped} · 복원 대상 {len(todo)}")
    for a, n in found.most_common():
        print(f"    {a:22} {n:>4}행")

    have = {o["name"] for o in schema["Axis"]["multi_select"]["options"]}
    missing = sorted(set(found) - have)
    if missing:
        print(f"\n  ~ 옵션에 없는 **옛 축** {missing}")
        print("    → 지우지 마라. '옛 기준 vs 새 기준'을 가르는 유일한 라벨이다.")
    if not todo:
        print("\n✅ 바꿀 것 없음 (멱등)")
        return 0
    if not apply:
        print("\n[dry-run] --apply 를 붙이면 실제로 채운다.")
        return 0

    if missing:
        r = _kv.write("PATCH", f"{API}/databases/{db}", tok, {"properties": {
            "Axis": {"multi_select": {"options": [{"name": n} for n in sorted(have | set(found))]}}}},
            timeout=30)
        print(f"  옵션 추가 PATCH {r.status_code}")
        if r.status_code != 200:
            print(f"  🔴 {r.text[:200]}")
            return 1

    ok = fail = 0
    for pid, axes in todo:
        r = _kv.write("PATCH", f"{API}/pages/{pid}", tok, {
            "properties": {"Axis": {"multi_select": [{"name": a} for a in axes]}}}, timeout=30)
        if r.status_code == 200:
            ok += 1
        else:
            fail += 1
            print(f"  🔴 {r.status_code} {r.text[:100]}")

    # 검증 — 200을 믿지 않는다. 노션 쿼리 인덱스는 즉시 일관되지 않으므로 재시도한다.
    want = skipped + ok
    for i in range(5):
        n = len(_query(db, tok, {"and": [{"property": "Channel", "select": {"equals": "LinkedIn"}},
                                         {"property": "Axis", "multi_select": {"is_not_empty": True}}]}))
        if n >= want:
            break
        if i < 4:
            print(f"  · 검증 {n}/{want} — 노션 인덱스 반영 대기 (재시도 {i + 1}/4)")
            time.sleep(5)
    good = n >= want and not fail
    print(f"\n{'✅' if good else '🔴'} Axis 채워진 링크드인 행 {n} (기대 {want}) · 실패 {fail}")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
