#!/usr/bin/env python3
"""이미 착지한 링크드인 행에 `프로필 점수`·`점수 근거`를 **저장된 액터 원본으로** 채운다. 무과금.

## 왜 필요한가 (2026-08-15)
점수는 어댑터가 수집 시점에 계산한다 — 즉 **앞으로 들어올 행에만** 붙는다. 그런데 슬랙 큐를
점수 내림차순으로 채우는 순간, 기존 행은 점수가 빈칸이라 **전부 맨 아래로 가라앉는다.**
이미 노션에 있는 사람이 사실은 4점인데 안 보이는 상태가 된다.

원본을 `data/raw/_actor/`에 전부 보관해왔으므로 이 교정은 **$0이고 재현 가능**하다.

## 무엇을 쓰나 (2칸만)
  · 프로필 점수   — 0~4 (core/profile_score.score)
  · 점수 근거     — "스텔스 · 설카포연고 · 학부2016"

🔴 **팔로업 필드는 절대 안 건드린다** (`Last Seen`·`Last Checked`·`재검토`) — SKILL.md CRITICAL #11.
   체크박스는 falsy도 직렬화돼서, 같이 실으면 **사람이 꺼둔 재검토를 코드가 되살린다.**
🔴 **`Tier`(Catch/Trend/Wait)도 안 건드린다.** 축이 다르고, `recheck_linkedin`이 우선군
   조건으로 읽는 칸이다.

## 0점을 빈칸으로 두지 않는다
0점과 빈칸은 뜻이 다르다 — 0점은 "신호가 하나도 없음(판정함)", 빈칸은 "아직 안 쟀음"이다.
정렬에서 둘을 못 가르면 백필이 어디까지 됐는지 알 수 없다.

## 되돌리기
`data/backfill_score_{날짜}.json`에 **바꾸기 전 값**을 남긴다. 원본이 그대로라 재계산도 무과금.

사용:
  ~/kv-automation/.venv/bin/python tools/backfill_score.py            # dry-run (기본)
  ~/kv-automation/.venv/bin/python tools/backfill_score.py --apply
"""
from __future__ import annotations

import glob
import json
import os
import sys
from datetime import date
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))


def _load_env() -> None:
    for line in (PROJ / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _load_profiles() -> dict[str, dict]:
    """저장된 액터 원본. 키를 셋 다 건다(id·publicIdentifier·URL 꼬리).

    Short 모드로 착지한 행의 URL은 난독화 URN이라 publicIdentifier로는 안 걸리고 id로만 걸린다.
    (backfill_tenure.py 와 같은 이유·같은 방식 — 한쪽만 고치지 마라.)
    """
    prof: dict[str, dict] = {}
    for f in glob.glob(str(PROJ / "data/raw/_actor/harvestapi-linkedin-profile-*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:                                  # noqa: BLE001
            continue
        items = d if isinstance(d, list) else d.get("items", d)
        if not isinstance(items, list):
            continue
        for p in items:
            if not isinstance(p, dict):
                continue
            for k in (p.get("id"), p.get("publicIdentifier"),
                      (p.get("linkedinUrl") or "").rstrip("/").split("/")[-1]):
                if k:
                    prof.setdefault(str(k), p)
    return prof


def main() -> int:
    apply = "--apply" in sys.argv
    _load_env()
    import yaml

    import core.notion as notion
    from core import profile_score as ps

    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    token = os.environ["NOTION_TOKEN"]
    ncfg = cfg["notion"]
    # 링크드인 행은 두 군데에 있다 — 2026-07-15 채널 DB 분리 **이전** 행은 기본 DB에 남아 있고
    # (이관하지 않기로 한 사용자 결정), 이후 행만 채널 DB로 간다. 한쪽만 백필하면 절반이 빈다.
    dbs = [d for d in (ncfg.get("channel_db_ids", {}).get("링크드인"), ncfg.get("db_id")) if d]

    prof = _load_profiles()
    print(f"· 저장된 액터 원본: {len(prof)}키")

    rows = []
    for db in dict.fromkeys(dbs):
        cur = None
        while True:
            body: dict = {"page_size": 100}
            if cur:
                body["start_cursor"] = cur
            r = notion._write("POST", f"{notion.API}/databases/{db}/query", token, body)
            if r.status_code != 200:
                print(f"🔴 DB {db[:8]}… 쿼리 실패 {r.status_code}: {r.text[:200]}")
                return 1
            d = r.json()
            for pg in d.get("results", []):
                pp = pg["properties"]
                url = (pp.get("LinkedIn URL") or {}).get("url") or ""
                if not url:
                    continue                               # 링크드인 행이 아니다(기본 DB는 혼재)
                rows.append({
                    "id": pg["id"],
                    "name": notion._plain(pp.get("서비스명")),
                    "url": url,
                    "score": (pp.get("프로필 점수") or {}).get("number"),
                    "why": notion._plain(pp.get("점수 근거")),
                })
            if not d.get("has_more"):
                break
            cur = d.get("next_cursor")

    plan, unmatched = [], 0
    for row in rows:
        p = prof.get((row["url"] or "").rstrip("/").split("/")[-1])
        if not p:
            unmatched += 1
            continue                                       # 원본 없으면 **건드리지 않는다**
        new_s, new_why = ps.score(p)
        why_txt = " · ".join(new_why)
        props: dict = {}
        if row["score"] is None or int(row["score"]) != int(new_s):
            props["프로필 점수"] = {"number": new_s}
        if (row["why"] or "") != why_txt:
            props["점수 근거"] = {"rich_text": [{"text": {"content": why_txt[:1900]}}]}
        if props:
            plan.append((row, props, new_s))

    from collections import Counter
    dist = Counter(s for _, _, s in plan)
    band = " · ".join(f"{s}점 {dist[s]}" for s in range(ps.MAX_SCORE, -1, -1) if dist[s])
    print(f"· 노션 {len(rows)}행 · 원본 매칭 {len(rows) - unmatched} · "
          f"대조 불가 {unmatched}(건드리지 않음)")
    print(f"· 바꿀 행 {len(plan)} — {band or '(없음)'}")

    if not apply:
        print("\n(dry-run) 실제로 쓰려면 --apply")
        for row, props, s in plan[:15]:
            print(f"   {s}점  {row['name'][:24]:24s} {props.get('점수 근거', {}).get('rich_text', [{}])[0].get('text', {}).get('content', '')}")
        return 0

    # 되돌리기용 — **바꾸기 전** 값을 먼저 남긴다(쓰기 전에 저장해야 중간 실패에도 남는다).
    snap = PROJ / f"data/backfill_score_{date.today().isoformat()}.json"
    snap.write_text(json.dumps(
        [{"id": r["id"], "name": r["name"], "score": r["score"], "why": r["why"]}
         for r, _, _ in plan], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"· 이전 값 스냅샷: {snap.name}")

    ok = fail = 0
    for row, props, _ in plan:
        r = notion._write("PATCH", f"{notion.API}/pages/{row['id']}", token, {"properties": props})
        if r.status_code == 200:
            ok += 1
        else:
            fail += 1
            print(f"   🔴 {row['name'][:20]}: {r.status_code} {r.text[:120]}")
    print(f"✅ 적용 {ok}행 · 실패 {fail}행")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
