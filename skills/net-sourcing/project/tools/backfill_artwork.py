#!/usr/bin/env python3
"""노션 앱스토어 행의 빈 `Artwork`(앱 아이콘)를 채운다. 멱등. $0 (애플 lookup API).

왜 필요한가: 배선은 처음부터 살아 있었는데(appstore.py → core/notion.py:897) **채우는 쪽이
없었다** — `rank_lookup.enrich()`가 `artwork` 키를 아예 안 만들었다. 그래서 1,471행 중 **0행**.
아무도 에러를 못 봤다. (2026-07-14에 `enrich()`를 고쳤으니 **앞으로 들어올 행은 자동으로 찬다.**
이 도구는 **이미 착지한 과거 행**만 메운다.)

앱 보드는 사람이 눈으로 훑는 물건이다. 아이콘 하나가 텍스트 열 개보다 빠르다.

사용: ./.venv/bin/python tools/backfill_artwork.py [--apply]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

# [W12 · B-F1] 노션 전송 규율(백오프·페이싱·재시도)의 정본은 kv_core 다.
from kv_core import notion as _kv

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

API = "https://api.notion.com/v1"
LOOKUP = "https://itunes.apple.com/lookup"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"}


def _plain(prop: dict | None) -> str:
    if not prop:
        return ""
    return "".join(x["plain_text"] for x in prop.get("rich_text", []))


def main() -> int:
    apply = "--apply" in sys.argv
    # 🔴 [2026-08-16] 인라인 `.env` 파서 → `kv_core.secrets`.
    #    `main.py:28-36` 이 *"자격증명 정본은 `$KV_CONFIG/.env` 하나뿐. 스킬별 `.env` 를
    #    다시 만들지 마라"* 라고 선언하고 옮겼는데 도구들은 옛 파서를 그대로 들고 있었고,
    #    `project/.env` 는 **존재하지 않아** 실행 즉시 `FileNotFoundError` 로 죽었다.
    from kv_core import secrets
    secrets.load()

    import yaml
    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    db, token = cfg["notion"]["db_id"], os.environ["NOTION_TOKEN"]

    rows, cursor = [], None
    while True:
        body: dict = {"page_size": 100,
                      "filter": {"property": "Channel", "select": {"equals": "App Store"}}}
        if cursor:
            body["start_cursor"] = cursor
        j = _kv.write("POST", f"{API}/databases/{db}/query", token, body).json()
        rows += j["results"]
        if not j.get("has_more"):
            break
        cursor = j["next_cursor"]

    todo = {}                                  # app_id → page_id
    for r in rows:
        p = r["properties"]
        if (p.get("Artwork") or {}).get("url"):
            continue                           # 멱등 — 이미 찬 행은 건너뛴다
        app_id = _plain(p.get("App ID"))
        if app_id:
            todo[app_id] = r["id"]
    print(f"· 앱스토어 {len(rows)}행 · Artwork 빈 행 {len(todo)}")
    if not todo:
        print("✅ 바꿀 것 없음 (멱등)")
        return 0

    art: dict[str, str] = {}
    ids = list(todo)
    for i in range(0, len(ids), 50):           # lookup은 50개씩 배치
        chunk = ids[i:i + 50]
        try:
            r = requests.get(LOOKUP, params={"id": ",".join(chunk), "country": "kr"}, timeout=30)
            for res in r.json().get("results", []):
                u = res.get("artworkUrl512") or res.get("artworkUrl100")
                if u:
                    art[str(res.get("trackId"))] = u
        except Exception as e:                 # noqa: BLE001
            print(f"  [warn] lookup 배치 실패: {e!r}")
    print(f"· 애플에서 아이콘을 받은 앱 {len(art)}/{len(todo)}")

    if not apply:
        print("\n[dry-run] --apply 를 붙이면 실제로 채운다.")
        return 0

    ok = fail = 0
    for app_id, url in art.items():
        r = _kv.write("PATCH", f"{API}/pages/{todo[app_id]}", token,
                      {"properties": {"Artwork": {"url": url}}})
        if r.status_code == 200:
            ok += 1
        else:
            fail += 1
            print(f"  🔴 {app_id}: {r.status_code} {r.text[:100]}")
    print(f"\n{'✅' if not fail else '🔴'} Artwork {ok}행 채움 · 실패 {fail}")
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
