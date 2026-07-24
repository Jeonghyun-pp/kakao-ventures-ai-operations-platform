#!/usr/bin/env python3
"""data/research/labels.json 의 빌더를 노션 `빌더` checkbox 로 들여온다. 멱등. $0 (노션 API만).

왜 필요한가:
  빌더 라벨 60명이 **로컬 JSON에만** 있어서 노션에서 필터할 수가 없었다. 그래서 사람이
  "누구를 라벨링할지" 고를 방법이 없다. 이 도구가 그 60명에 `빌더 ✅`를 찍으면
  노션 뷰 필터 하나(`빌더 = 체크됨`)로 60행이 뜨고, 사람은 거기에 `타깃`을 켜면 된다.

두 칸의 방향이 반대다 — 헷갈리지 마라:
  `빌더`  = **기계가 쓰고 사람이 읽는다** (이 도구가 채운다. 손으로 켜지 마라 — 다음 실행에 덮인다)
  `타깃`  = **사람이 쓰고 기계가 읽는다** (사람만 켠다. 이 도구는 절대 안 건드린다)

이걸로 얻는 것: 빌더→타깃 전환율 · 층/시드/키워드별 **타깃** 산출(지금 튜닝은 전부 *빌더* 수로
했다 — 목적함수가 틀렸을 수 있다) · judge L1 `[A6]`의 유일한 정답지.

사용: ./.venv/bin/python tools/mark_builders.py [--apply]
      --apply 없으면 dry-run.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

API = "https://api.notion.com/v1"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"}


def _plain(prop: dict | None) -> str:
    if not prop:
        return ""
    return "".join(x["plain_text"] for x in prop.get("rich_text", []))


def _threads_rows(db_id: str, token: str) -> list[dict]:
    rows, cursor = [], None
    while True:
        body: dict = {"page_size": 100,
                      "filter": {"property": "Channel", "select": {"equals": "Threads"}}}
        if cursor:
            body["start_cursor"] = cursor
        r = requests.post(f"{API}/databases/{db_id}/query", headers=_headers(token),
                          json=body, timeout=30)
        r.raise_for_status()
        j = r.json()
        rows += j["results"]
        if not j.get("has_more"):
            return rows
        cursor = j["next_cursor"]


def main() -> int:
    apply = "--apply" in sys.argv

    for line in (PROJ / ".env").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    import yaml
    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    db_id, token = cfg["notion"]["db_id"], os.environ["NOTION_TOKEN"]

    # 🔴 스키마 확인 먼저 — `빌더`가 없으면 PATCH는 **200을 주고 값은 버려진다.**
    r = requests.get(f"{API}/databases/{db_id}", headers=_headers(token), timeout=30)
    r.raise_for_status()
    if "빌더" not in r.json()["properties"]:
        print("🔴 노션에 `빌더`(checkbox) property가 없다 — tools/notion_migrate.py --apply 를 먼저 돌려라")
        return 1

    labels = json.loads((PROJ / "data/research/labels.json").read_text(encoding="utf-8"))
    builders = {h.lstrip("@").strip() for h in labels["builders"]}
    labeled = {d["handle"].lstrip("@").strip() for d in labels["all"]}

    rows = _threads_rows(db_id, token)
    print(f"· 스레드 행 {len(rows)} · 라벨된 사람 {len(labeled)} · 그중 빌더 {len(builders)}\n")

    to_check, to_uncheck, already = [], [], 0
    seen: set[str] = set()
    for row in rows:
        props = row["properties"]
        user = _plain(props.get("Author Username")).lstrip("@").strip()
        now = bool((props.get("빌더") or {}).get("checkbox"))
        want = user in builders
        if want:
            seen.add(user)
        if want and not now:
            to_check.append((row["id"], user))
        elif want and now:
            already += 1
        elif not want and now:
            # 라벨이 바뀌었거나 손으로 켠 행. 이 칸의 진실은 labels.json 이다.
            to_uncheck.append((row["id"], user))

    missing = sorted(builders - seen)
    print(f"  + 체크할 행      {len(to_check)}")
    print(f"  = 이미 체크됨    {already}")
    print(f"  - 해제할 행      {len(to_uncheck)}  (labels.json에 없는데 켜져 있음)")
    if missing:
        print(f"  🔴 노션에서 못 찾은 빌더 {len(missing)}명: {missing}")
        print("     (Author Username 이 비었거나 핸들이 바뀌었다 — 그만큼 뷰에서 빠진다)")

    if not (to_check or to_uncheck):
        print("\n✅ 바꿀 것 없음 (멱등)")
        return 0
    if not apply:
        print(f"\n[dry-run] --apply 를 붙이면 실제로 반영한다.")
        return 0

    fail = 0
    for page_id, user in [(p, u) for p, u in to_check] + [(p, u) for p, u in to_uncheck]:
        val = any(page_id == p for p, _ in to_check)
        r = requests.patch(f"{API}/pages/{page_id}", headers=_headers(token),
                           json={"properties": {"빌더": {"checkbox": val}}}, timeout=30)
        if r.status_code != 200:
            print(f"  🔴 {user}: {r.status_code} {r.text[:120]}")
            fail += 1

    # 검증 — 200을 믿지 않는다. 다시 세어본다.
    after = _threads_rows(db_id, token)
    checked = sum(1 for x in after if (x["properties"].get("빌더") or {}).get("checkbox"))
    want_n = len(builders) - len(missing)
    ok = checked == want_n and fail == 0
    print(f"\n{'✅' if ok else '🔴'} 노션 `빌더 ✅` = {checked}행 (기대 {want_n}) · 실패 {fail}")
    if ok:
        print("\n다음: 노션에서 필터 `빌더 = 체크됨` 뷰를 만들고, 각 행의 `타깃` 을 켜라.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
