#!/usr/bin/env python3
"""그물망 소싱 DB 스키마 마이그레이션 — 1회 실행. 멱등(이미 있으면 건너뜀).

왜 코드보다 **먼저** 돌려야 하나:
  core/notion.py 는 착지 시 `if name not in schema: continue` 로 스키마에 없는 property를 버리고,
  `_known_options_only` 는 DB에 없는 select/multi_select **값**을 버린다. 둘 다 조용하다.
  스키마를 안 고치고 코드만 배포하면 Post Count·Sourcing Layers·Tier가 전부 빈칸으로 착지되고,
  로그는 "✅ 700건 착지"라고 찍는다. 이 스킬의 반복 실패 패턴이 정확히 이거다.

하는 일:
  ① Post Count (number)          — 인물 1행 구조에서 "몇 편 썼나". 꾸준한 빌더 vs 한 번 올린 사람.
  ② Sourcing Layers (multi_select) — 여러 층에 동시에 걸린 사람이 강한 신호인데,
     기존 `Sourcing Method`가 select(단일)라 층이 서로를 덮어썼다.
     ⚠️ select→multi_select **타입 변환은 하지 않는다.** 값 보존이 불확실하고 기존 뷰/필터를 깨뜨린다.
        새 property 추가는 무료이고 되돌릴 수 있다. 기존 select는 "대표 층"으로 남긴다.
  ③ Tier 옵션 A|B|C → Catch|Trend|Wait (사용자 확정 2026-07-13)
     극초기 DB가 실제로 쓰는 용어라 나중에 승격할 때 재매핑이 없다. Tier가 채워진 행이 0개라 지금이 공짜.
  ④ 주차 옵션 연장 — `_iso_week`가 만든 값이 옵션에 없으면 조용히 버려진다(W44에서 끊겨 있었다).

  ⑨ 팔로업 필드 (2026-07-17) — Last Seen(date)·Last Checked(date)·재검토(checkbox).
     재조우 표기(core/notion.land)·recheck(tools/recheck.py)가 쓴다. **추가 전용**이라 롤백 시
     안 쓰면 그만(필드 잔존은 무해).

[2026-07-17] **다중 DB**: base(10fb616a)만 보던 것을 base+스레드+링크드인 3개로 확장.
  채널 DB 분리(07-15) 후 마이그레이션이 base만 패치하면 채널 DB 착지분의 신규 옵션/필드가
  조용히 버려진다(_known_options_only). ⚠️ 규칙 두 개:
    · **생성(신규 property)은 base에만** — 채널 DB는 일부러 칸을 줄인 DB다(앱스토어 19칸 등).
      기존 블록의 "없으면 추가" 분기가 채널 DB에 칸을 늘리면 분리 결정을 되돌리는 셈.
      채널 DB에서는 **이미 있는 property의 옵션 동기화만** 한다.
    · 예외 = ⑨ 팔로업 필드: base·스레드·링크드인 3곳 모두 생성한다(기능이 세 DB를 쓴다).
      앱스토어 DB는 제외 — 앱은 원샷(사용자 결정), 팔로업 대상이 아니다.

사용: ./.venv/bin/python tools/notion_migrate.py [--apply]
      --apply 없으면 dry-run (무엇이 바뀔지만 출력).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import requests

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

API = "https://api.notion.com/v1"
TIER_OPTIONS = ["Catch", "Trend", "Wait"]
LAYER_OPTIONS = ["keyword_search", "watchlist", "following_graph", "reply_crawl"]


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"}


def _weeks_through(end: date) -> list[str]:
    """오늘부터 end까지의 ISO 주차 라벨."""
    out, d = [], date.today()
    while d <= end:
        y, w, _ = d.isocalendar()
        label = f"{y}-W{w:02d}"
        if label not in out:
            out.append(label)
        d += timedelta(days=7)
    return out


def _migrate_db(db_id: str, label: str, kws: dict, token: str, apply: bool,
                allow_create: bool, followup: bool) -> int:
    """DB 하나 마이그레이션. allow_create=False면 신규 property 생성 없이 옵션 동기화만
    (⑨ 팔로업 필드는 followup=True면 예외적으로 생성 — 기능이 그 DB를 쓴다)."""
    print(f"\n━━ {label} ({db_id[:8]}…) — 생성 {'허용' if allow_create else '금지(옵션 동기화만)'}")

    r = requests.get(f"{API}/databases/{db_id}", headers=_headers(token), timeout=30)
    if r.status_code != 200:
        print(f"🔴 스키마 조회 실패 {r.status_code}: {r.text[:200]}")
        return 1
    schema = r.json()["properties"]
    print(f"· 현재 property {len(schema)}개")

    patch: dict = {}

    # ① Post Count / Followers
    if "Post Count" in schema:
        print(f"  = Post Count: 이미 있음 ({schema['Post Count']['type']})")
    elif allow_create:
        patch["Post Count"] = {"number": {"format": "number"}}
        print("  + Post Count (number) 추가")

    # Followers — 진성 빌더(팔로워 수백~수천)와 인플루언서/마케터(수십만+)를 가르는 강한 신호.
    # 액터가 공짜로 주는데(meta-threads: followers_count, igview: follower_count) 버리고 있었다.
    if "Followers" in schema:
        print(f"  = Followers: 이미 있음")
    elif allow_create:
        patch["Followers"] = {"number": {"format": "number"}}
        print("  + Followers (number) 추가")

    # ② Sourcing Layers
    if "Sourcing Layers" in schema:
        have = {o["name"] for o in schema["Sourcing Layers"]["multi_select"]["options"]}
        missing = [o for o in LAYER_OPTIONS if o not in have]
        if missing:
            # ⚠️ multi_select.options는 **전체 목록으로 교체**된다. 기존 것을 합쳐 보내야 안 지워진다.
            patch["Sourcing Layers"] = {"multi_select": {
                "options": [{"name": n} for n in sorted(have | set(LAYER_OPTIONS))]}}
            print(f"  ~ Sourcing Layers: 옵션 추가 {missing}")
        else:
            print("  = Sourcing Layers: 이미 있음")
    elif allow_create:
        patch["Sourcing Layers"] = {"multi_select": {
            "options": [{"name": n} for n in LAYER_OPTIONS]}}
        print(f"  + Sourcing Layers (multi_select) 추가 — 옵션 {LAYER_OPTIONS}")

    # ③ Tier 옵션 교체 — 채워진 행이 0개일 때만 안전하다. 확인하고 간다.
    tier = schema.get("Tier")
    if tier and tier["type"] == "select":
        have = [o["name"] for o in tier["select"]["options"]]
        if set(have) == set(TIER_OPTIONS):
            print("  = Tier: 이미 Catch|Trend|Wait")
        else:
            q = requests.post(f"{API}/databases/{db_id}/query", headers=_headers(token),
                              json={"filter": {"property": "Tier", "select": {"is_not_empty": True}},
                                    "page_size": 1}, timeout=30)
            filled = len(q.json().get("results", [])) if q.status_code == 200 else -1
            if filled > 0:
                print(f"  🔴 Tier에 값이 채워진 행이 있다 — 옵션 교체를 건너뛴다(데이터 손실 방지).")
                print(f"     현재 {have} → 원하는 {TIER_OPTIONS}. 수동 판단 필요.")
            elif filled < 0:
                print("  🔴 Tier 사용 여부 조회 실패 — 안전을 위해 교체 건너뜀")
            else:
                # 기존 A|B|C 를 **버리고** 새 3개로. 쓰는 행이 0개라 손실 없음.
                patch["Tier"] = {"select": {"options": [{"name": n} for n in TIER_OPTIONS]}}
                print(f"  ~ Tier: {have} → {TIER_OPTIONS} (채워진 행 0개 확인 — 손실 없음)")

    # ④ Matched Keyword 옵션 ← keywords.yaml 에서 동기화
    #    🔴 keywords.yaml에 단어를 추가하고 노션 옵션을 안 넣으면 그 키워드로 잡힌 사람의
    #       Matched Keyword가 **조용히 빈칸**이 된다(_known_options_only). 발굴 근거가 사라진다.
    #       → 이 스크립트를 "키워드 추가 후 반드시 1회" 돌리는 것으로 규칙화한다.
    want_kw = set((kws.get("threads") or {}).get("keyword") or [])
    mk = schema.get("Matched Keyword")
    if mk and mk["type"] == "multi_select" and want_kw:
        have = {o["name"] for o in mk["multi_select"]["options"]}
        missing = sorted(want_kw - have)
        if missing:
            patch["Matched Keyword"] = {"multi_select": {
                "options": [{"name": n} for n in sorted(have | want_kw)]}}
            print(f"  ~ Matched Keyword: {len(missing)}개 추가 {missing}")
        else:
            print(f"  = Matched Keyword: keywords.yaml의 {len(want_kw)}개 전부 존재")

    # ⑦ Seed (multi_select) — 팔로잉그래프가 **어느 시드에서** 데려왔나.
    #    🔴 예전엔 `Match Reason` **문장 안에만** 있었다("팔로잉그래프: @sunday_builders → @x").
    #      재려면 정규식으로 문장을 파싱해야 해서 **아무도 안 쟀다** — 가장 값나가는 노브(허브 계정)를
    #      측정할 수 없는 상태였다. 실측하니 `sunday_builders` 하나가 **혼자만 타깃 5명**을 데려왔고
    #      (빼면 이 층 타깃이 9→4), `820_nam`은 타깃 2명을 데려오지만 **혼자만 데려온 건 0명**이었다.
    #      ⚠️ 옵션은 keywords.yaml의 시드에서 **동기화**한다. 옵션에 없는 값은 조용히 버려진다.
    seed_names = set()
    for spec in ((kws.get("threads") or {}).get("following_seed_accounts") or []) + \
                ((kws.get("threads") or {}).get("following_seed_trials") or []):
        seed_names.add(spec["username"] if isinstance(spec, dict) else str(spec))
    sd = schema.get("Seed")
    if sd and sd["type"] == "multi_select":
        have = {o["name"] for o in sd["multi_select"]["options"]}
        missing = sorted(seed_names - have)
        if missing:
            patch["Seed"] = {"multi_select": {
                "options": [{"name": n} for n in sorted(have | seed_names)]}}
            print(f"  ~ Seed: 옵션 추가 {missing}")
        else:
            print(f"  = Seed: keywords.yaml의 시드 {len(seed_names)}개 전부 존재")
    elif seed_names and allow_create:
        patch["Seed"] = {"multi_select": {"options": [{"name": n} for n in sorted(seed_names)]}}
        print(f"  + Seed (multi_select) 추가 — 시드 {sorted(seed_names)}")

    # ⑧ Axis (multi_select) — 링크드인에서 **어느 검색축이** 데려왔나.
    #    🔴 `Seed`와 같은 문제였다: `Match Reason` **문장 안에만** 있어서 노션에서 필터를 못 걸었다.
    #      → "새 기준(극초기 필터)으로 발굴된 사람만 보기"가 **불가능**했다.
    #      옵션은 keywords.yaml의 축에서 동기화 + 과거 축 이름도 보존(왜 바꿨는지의 근거가 그 행들이다).
    axis_names = {s["label"] for s in ((kws.get("linkedin") or {}).get("profile_search") or [])
                  if s.get("label")}
    ax = schema.get("Axis")
    if ax and ax["type"] == "multi_select":
        have = {o["name"] for o in ax["multi_select"]["options"]}
        missing = sorted(axis_names - have)
        if missing:
            patch["Axis"] = {"multi_select": {
                "options": [{"name": n} for n in sorted(have | axis_names)]}}
            print(f"  ~ Axis: 옵션 추가 {missing}")
        else:
            print(f"  = Axis: keywords.yaml의 축 {len(axis_names)}개 전부 존재")
    elif axis_names and allow_create:
        patch["Axis"] = {"multi_select": {"options": [{"name": n} for n in sorted(axis_names)]}}
        print(f"  + Axis (multi_select) 추가 — 축 {sorted(axis_names)}")

    # ⑥ 빌더 (checkbox) — 라벨링 정답지를 노션으로 들여온다.
    #    data/research/labels.json 의 빌더 60명이 로컬에만 있어서 **노션에서 필터할 수가 없다.**
    #    사람이 그 60명에 `타깃`(이미 있는 checkbox)을 켜면 빌더→타깃 전환율이 나오고,
    #    그게 judge L1 `[A6]`("문제를 푸는 팀 vs 슬롭")의 유일한 정답지가 된다.
    #    ⚠️ 이 칸은 **기계가 쓰고 사람이 읽는다.** `타깃`은 반대로 **사람이 쓰고 기계가 읽는다.**
    if "빌더" in schema:
        print(f"  = 빌더: 이미 있음 ({schema['빌더']['type']})")
    elif allow_create:
        patch["빌더"] = {"checkbox": {}}
        print("  + 빌더 (checkbox) 추가 — tools/mark_builders.py 가 채운다")

    # ⑤ 주차 옵션 연장
    wk = schema.get("주차")
    if wk and wk["type"] == "select":
        have = {o["name"] for o in wk["select"]["options"]}
        want = _weeks_through(date.today() + timedelta(days=400))
        missing = [w for w in want if w not in have]
        if missing:
            patch["주차"] = {"select": {
                "options": [{"name": n} for n in sorted(have | set(want))]}}
            print(f"  ~ 주차: {len(missing)}개 추가 ({missing[0]} … {missing[-1]}) "
                  f"— 옵션에 없으면 조용히 버려진다")
        else:
            print("  = 주차: 충분함")

    # ⑨ 팔로업 필드 (2026-07-17) — 재조우 표기·recheck가 쓴다.
    #    Last Seen    = 마지막 **새-정보** 착지일 (병합/변화감지 때 코드가 갱신)
    #    Last Checked = recheck/bio백필이 마지막으로 확인한 날 (빈-bio 재구매 구멍 봉합의 핵심)
    #    재검토       = 변화 감지 표식. 🔴 **코드는 켜기만 한다. 끄는 건 사람뿐.**
    #    앱스토어 DB에는 안 만든다(followup=False) — 앱은 원샷(사용자 결정).
    if followup:
        for fname, fspec in (("Last Seen", {"date": {}}),
                             ("Last Checked", {"date": {}}),
                             ("재검토", {"checkbox": {}})):
            if fname in schema:
                print(f"  = {fname}: 이미 있음 ({schema[fname]['type']})")
            else:
                patch[fname] = fspec
                print(f"  + {fname} ({next(iter(fspec))}) 추가 — 팔로업 필드")

    if not patch:
        print("  ✅ 바꿀 것 없음 (멱등)")
        return 0

    if not apply:
        print(f"  [dry-run] --apply 를 붙이면 위 {len(patch)}개를 실제로 반영한다.")
        return 0

    r = requests.patch(f"{API}/databases/{db_id}", headers=_headers(token),
                       json={"properties": patch}, timeout=30)
    if r.status_code != 200:
        print(f"  🔴 마이그레이션 실패 {r.status_code}: {r.text[:400]}")
        return 1

    # 검증 — 재조회해서 실제로 들어갔는지 본다(200을 믿지 않는다)
    r2 = requests.get(f"{API}/databases/{db_id}", headers=_headers(token), timeout=30)
    after = r2.json()["properties"]
    ok = True
    for name in patch:
        if name not in after:
            print(f"  🔴 '{name}' 이 반영되지 않았다")
            ok = False
    if "Tier" in patch:
        got = [o["name"] for o in after["Tier"]["select"]["options"]]
        if set(got) != set(TIER_OPTIONS):
            print(f"  🔴 Tier 옵션이 여전히 {got}")
            ok = False
    print(f"  {'✅' if ok else '🔴'} 마이그레이션 {'완료' if ok else '부분 실패'} — property {len(after)}개")
    return 0 if ok else 1


def main() -> int:
    apply = "--apply" in sys.argv

    env = PROJ / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    import yaml
    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    kws = yaml.safe_load((PROJ / "keywords.yaml").read_text(encoding="utf-8"))
    token = os.environ["NOTION_TOKEN"]

    ncfg = cfg["notion"]
    ch = ncfg.get("channel_db_ids") or {}
    # (db_id, 라벨, 생성허용, 팔로업필드) — 생성은 base에만, 팔로업은 사람 채널 DB까지.
    targets = [(ncfg["db_id"], "base 그물망 소싱 DB", True, True)]
    if ch.get("스레드"):
        targets.append((ch["스레드"], "스레드 채널 DB", False, True))
    if ch.get("링크드인"):
        targets.append((ch["링크드인"], "링크드인 채널 DB", False, True))
    if ch.get("앱스토어"):
        targets.append((ch["앱스토어"], "앱스토어 채널 DB", False, False))  # 원샷 — 팔로업 없음

    rc = 0
    for db_id, label, allow_create, followup in targets:
        rc |= _migrate_db(db_id.replace("collection://", ""), label, kws, token, apply,
                          allow_create=allow_create, followup=followup)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
