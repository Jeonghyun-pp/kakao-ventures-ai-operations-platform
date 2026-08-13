#!/usr/bin/env python3
"""노션 팔로잉그래프 행의 `Seed`를 `Match Reason` 문장에서 소급 복원한다. 멱등. $0.

왜: 시드 귀속이 예전엔 **`Match Reason` 텍스트 안에만** 있었다
("팔로잉그래프: @sunday_builders → @xxx (1홉)"). 구조화 필드가 아니라서 재려면 정규식으로
문장을 파싱해야 했고, **그래서 아무도 안 쟀다** — 가장 값나가는 노브(허브 계정)가 측정 불가였다.
(어댑터는 2026-07-14에 고쳤다. 이 도구는 **이미 착지한 과거 행**만 메운다.)

🔴 과거 행에는 **이미 제거된 시드**(zenonplane·820_nam)가 들어 있다. 그 옵션이 노션에 없으면
   `_known_options_only`가 **조용히 버린다** → 이 도구가 **옵션부터 추가**한다.
   (지우고 싶어도 기록은 남겨야 한다. "왜 뺐나"의 근거가 그 행들이다.)

사용: ./.venv/bin/python tools/backfill_seed.py [--apply]
"""
from __future__ import annotations

import os
import re
import sys
from collections import Counter
from pathlib import Path

import requests

# [W12 · B-F1] 노션 전송 규율의 정본은 kv_core 다(백오프·페이싱·재시도).
from kv_core import notion as _kv

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

API = "https://api.notion.com/v1"
# "팔로잉그래프: @a → @b" · "팔로잉그래프: @a + @b → @c" (v2 다중 시드) 둘 다 잡는다
SEED_RE = re.compile(r"팔로잉그래프:\s*(.+?)\s*→")
AT_RE = re.compile(r"@([\w.\-]+)")


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"}


def _plain(p: dict | None) -> str:
    if not p:
        return ""
    return "".join(x["plain_text"] for x in p.get("rich_text", []))


def main() -> int:
    apply = "--apply" in sys.argv
    for line in (PROJ / ".env").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    import yaml
    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    db, token = cfg["notion"]["db_id"], os.environ["NOTION_TOKEN"]

    schema = _kv.read(f"{API}/databases/{db}", token).json()["properties"]
    if "Seed" not in schema:
        print("🔴 `Seed`(multi_select)가 없다 — tools/notion_migrate.py --apply 를 먼저 돌려라")
        return 1

    rows, cursor = [], None
    while True:
        body: dict = {"page_size": 100,
                      "filter": {"property": "Channel", "select": {"equals": "Threads"}}}
        if cursor:
            body["start_cursor"] = cursor
        j = _kv.write("POST", f"{API}/databases/{db}/query", token, body).json()
        rows += j["results"]
        if not j.get("has_more"):
            break
        cursor = j["next_cursor"]

    todo: list[tuple[str, list[str]]] = []
    found: Counter = Counter()
    fg = skipped = 0
    for r in rows:
        p = r["properties"]
        layers = {o["name"] for o in (p.get("Sourcing Layers") or {}).get("multi_select", [])}
        if "following_graph" not in layers:
            continue
        fg += 1
        if (p.get("Seed") or {}).get("multi_select"):
            skipped += 1                       # 멱등
            continue
        m = SEED_RE.search(_plain(p.get("Match Reason")))
        if not m:
            continue
        seeds = AT_RE.findall(m.group(1))
        if seeds:
            todo.append((r["id"], seeds))
            found.update(seeds)

    print(f"· 팔로잉그래프 행 {fg} · 이미 Seed 있음 {skipped} · 복원 대상 {len(todo)}")
    for s, n in found.most_common():
        print(f"    @{s:22} {n:>3}행")

    have = {o["name"] for o in schema["Seed"]["multi_select"]["options"]}
    missing = sorted(set(found) - have)
    if missing:
        print(f"\n  ~ Seed 옵션에 없는 시드 {missing} — **먼저 추가하지 않으면 조용히 버려진다**")
    if not todo:
        print("\n✅ 바꿀 것 없음 (멱등)")
        return 0
    if not apply:
        print("\n[dry-run] --apply 를 붙이면 실제로 채운다.")
        return 0

    if missing:
        r = _kv.write("PATCH", f"{API}/databases/{db}", token, {"properties": {
            "Seed": {"multi_select": {"options": [{"name": n} for n in sorted(have | set(found))]}}}},
            timeout=30)
        print(f"  옵션 추가 PATCH {r.status_code}")
        if r.status_code != 200:
            print(f"  🔴 {r.text[:200]}")
            return 1

    ok = fail = 0
    for page_id, seeds in todo:
        r = _kv.write("PATCH", f"{API}/pages/{page_id}", token, {
            "properties": {"Seed": {"multi_select": [{"name": s} for s in seeds]}}}, timeout=30)
        if r.status_code == 200:
            ok += 1
        else:
            fail += 1
            print(f"  🔴 {r.status_code} {r.text[:100]}")

    # 검증 — 200을 믿지 않는다. 다시 세어본다. (_known_options_only가 조용히 버렸을 수 있다)
    # 🔴 노션 쿼리 인덱스는 **즉시 일관되지 않다.** PATCH 직후에 세면 몇 건이 덜 잡히고,
    #   그러면 이 도구가 **거짓 🔴**를 낸다. 거짓 경보는 진짜 경보를 죽인다 → 재시도한다.
    import time
    want = skipped + ok
    for attempt in range(5):
        n, cursor = 0, None
        while True:
            body = {"page_size": 100,
                    "filter": {"property": "Seed", "multi_select": {"is_not_empty": True}}}
            if cursor:
                body["start_cursor"] = cursor
            j = _kv.write("POST", f"{API}/databases/{db}/query", token, body).json()
            n += len(j["results"])
            if not j.get("has_more"):
                break
            cursor = j["next_cursor"]
        if n >= want:
            break
        if attempt < 4:
            print(f"  · 검증 {n}/{want} — 노션 인덱스 반영 대기 중 (재시도 {attempt + 1}/4)")
            time.sleep(5)
    good = n >= want and not fail
    print(f"\n{'✅' if good else '🔴'} Seed 채워진 행 {n} (기대 {want}) · 실패 {fail}")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
