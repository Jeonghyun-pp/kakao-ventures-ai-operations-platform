#!/usr/bin/env python3
"""링크드인 채널 DB의 **같은 사람 두 행**을 찾아 옛 행을 아카이브한다. 무과금.

## 왜 생겼나 (2026-07-27 발견)
dedup 키가 **LinkedIn URL 정확매칭**인데, Short 모드는 `publicIdentifier`를 안 줘서
`/in/{urn}`으로 폴백하고 Full 모드는 vanity URL(`/in/haonpark`)을 준다.
→ **같은 사람이 다른 문자열**이 되어 dedup을 통과했다. 실측 345행 중 **84행이 중복**(실제 261명).

같은 사람임을 확증한 방법(84쌍 전수·불일치 0): URN은 `AC?AA` + 멤버ID(9자) + 세션꼬리 구조라
**3번째 글자만 다르고 앞 12자가 같다** — `ACoAABq8pMkB` vs `ACwAABq8pMkB`.

## 무엇을 지우나 — **사람 손이 안 닿은 쪽만**
실측한 두 행의 성격이 정반대다:
| | 착지일 | URL | 내용 |
|---|---|---|---|
| 지울 행 | 2026-07-11 | 난독화 URN | Tenure 0/84 · Raw Text 중앙 23자 · **타깃·재검토·빌더·Tier 전부 0** |
| 남길 행 | 2026-07-13 | vanity | Tenure 83/84 · Raw Text 115자 · **타깃 ✅ 8명** |

🔴 그래도 **실행 시점에 다시 확인한다.** 사람이 그새 체크를 켰을 수 있다 —
   지울 후보에 타깃/재검토/빌더/Tier/AI Reason 중 **하나라도 있으면 그 쌍은 건너뛴다.**
   (조용한 삭제 없음: 건너뛴 쌍은 사유와 함께 출력한다)

## 되돌리기
노션 **아카이브**(휴지통)라 복구 가능하다. 지운 목록은 `data/dedupe_linkedin_{날짜}.json`에 남는다.

사용:
  ./.venv/bin/python tools/dedupe_linkedin_rows.py            # dry-run (기본)
  ./.venv/bin/python tools/dedupe_linkedin_rows.py --apply
"""
from __future__ import annotations

import collections
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

_URN = re.compile(r"AC[A-Za-z0-9]AA[A-Za-z0-9_\-]{20,}")


def _tail(u: str) -> str:
    return (u or "").rstrip("/").split("/")[-1]


def main() -> int:
    apply = "--apply" in sys.argv
    # 🔴 [2026-08-16] 인라인 `.env` 파서 → `kv_core.secrets`.
    #    `main.py:28-36` 이 *"자격증명 정본은 `$KV_CONFIG/.env` 하나뿐. 스킬별 `.env` 를
    #    다시 만들지 마라"* 라고 선언하고 옮겼는데 도구들은 옛 파서를 그대로 들고 있었고,
    #    `project/.env` 는 **존재하지 않아** 실행 즉시 `FileNotFoundError` 로 죽었다.
    from kv_core import secrets
    secrets.load()
    import yaml
    import core.notion as notion

    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    token = os.environ["NOTION_TOKEN"]
    db = cfg["notion"]["channel_db_ids"]["링크드인"]

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
            pp = pg["properties"]
            rows.append({
                "id": pg["id"],
                "name": notion._plain(pp.get("서비스명")),
                "url": (pp.get("LinkedIn URL") or {}).get("url") or "",
                "tenure": (pp.get("Tenure Months") or {}).get("number"),
                "tgt": bool((pp.get("타깃") or {}).get("checkbox")),
                "rechk": bool((pp.get("재검토") or {}).get("checkbox")),
                "builder": bool((pp.get("빌더") or {}).get("checkbox")),
                "tier": ((pp.get("Tier") or {}).get("select") or {}).get("name"),
                "reason": notion._plain(pp.get("AI Reason")),
                "raw_len": len(notion._plain(pp.get("Raw Text")) or ""),
            })
        if not d.get("has_more"):
            break
        cur = d.get("next_cursor")

    by = collections.defaultdict(list)
    for r_ in rows:
        by[r_["name"]].append(r_)

    kill, skipped = [], []
    for name, v in by.items():
        if len(v) != 2:
            continue
        urn = [x for x in v if _URN.fullmatch(_tail(x["url"]))]
        van = [x for x in v if not _URN.fullmatch(_tail(x["url"]))]
        if len(urn) != 1 or len(van) != 1:
            continue                                  # 둘 다 vanity/둘 다 URN → 사람 판단 필요
        old, keep = urn[0], van[0]
        touched = [t for t, on in (("타깃", old["tgt"]), ("재검토", old["rechk"]),
                                   ("빌더", old["builder"]), ("Tier", bool(old["tier"])),
                                   ("AI Reason", bool(old["reason"]))) if on]
        if touched:
            skipped.append((name, touched))
            continue
        if old["raw_len"] > keep["raw_len"]:          # 지울 쪽이 정보가 더 많으면 멈춘다
            skipped.append((name, [f"지울 쪽 Raw Text가 더 김({old['raw_len']}>{keep['raw_len']})"]))
            continue
        kill.append((name, old, keep))

    print(f"· 링크드인 {len(rows)}행 · 이름 2행 {sum(1 for v in by.values() if len(v)==2)}명")
    print(f"· 아카이브 대상 {len(kill)}행 (전부 난독화 URN·사람 손 안 닿음)")
    print(f"· 🔴 건너뛴 쌍 {len(skipped)}  (사람이 값을 넣어둔 행이 섞여 있다)")
    for n, why in skipped[:10]:
        print(f"    - {n[:24]:26s} {why}")
    for n, old, keep in kill[:5]:
        print(f"    예) {n[:22]:24s} 지움 [{_tail(old['url'])[:22]}… Tenure={old['tenure']}]"
              f" / 남김 [{_tail(keep['url'])[:26]} Tenure={keep['tenure']}]")

    if not apply:
        print(f"\n[dry-run] --apply 를 붙이면 {len(kill)}행을 **아카이브**한다(휴지통 → 복구 가능).")
        return 0

    snap = PROJ / "data" / f"dedupe_linkedin_{date.today().isoformat()}.json"
    snap.write_text(json.dumps([{"name": n, "archived_page": o["id"], "archived_url": o["url"],
                                 "kept_page": k["id"], "kept_url": k["url"]}
                                for n, o, k in kill], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n· 아카이브 목록: {snap.name}")
    ok = fail = 0
    for n, old, _ in kill:
        r = notion._write("PATCH", f"{notion.API}/pages/{old['id']}", token, {"archived": True})
        if r.status_code == 200:
            ok += 1
        else:
            fail += 1
            print(f"  🔴 {n[:20]} {r.status_code}: {r.text[:150]}")
    print(f"\n{'✅' if not fail else '🔴'} 아카이브 {ok}행 · 실패 {fail}행")
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
