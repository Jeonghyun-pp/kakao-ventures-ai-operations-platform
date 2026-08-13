#!/usr/bin/env python3
"""무과금 리허설 — 유료 실행이 **정확히 무엇을 만들지**를 돈 안 쓰고 본다.

## 어떻게 무과금인가
스모크에서 **진짜 액터가 뱉은 출력**을 data/fixtures/live_*.json 에 저장해뒀다.
Apify 호출만 그걸로 대체하고, 나머지(파싱 → 날짜컷 → dedup → L0 → 인물접기 → 노션 페이로드)는
**전부 진짜 코드**를 탄다. 앱스토어는 애플 무료 API라 **진짜로** 수집한다.

## 무엇을 잡는가
1. 파서가 실제 출력에서 필드를 뽑는가 (작성자·본문·URL·게시일)
2. dedup / L0 / 인물접기가 실데이터에서 도는가
3. **노션 페이로드가 라이브 스키마와 맞는가** — select/multi_select 값이 옵션에 없으면
   노션이 **조용히 버리고 200을 준다**. 이 스킬의 1번 실패 패턴이다. 쓰기 전에 대조한다.
4. 착지 시 신규/갱신이 어떻게 갈리는가

## 무엇을 못 잡는가 (정직하게)
- 실제 수율(키워드 21개가 몇 건을 줄지) — 유료 실행만 안다
- 규모에서만 나는 문제(타임아웃 등)

사용: ./.venv/bin/python tools/rehearse.py [--land]
      --land 를 붙이면 대표 행 몇 개를 **진짜 노션에 써보고 확인 후 아카이브**한다.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
FIX = PROJ / "data" / "fixtures"


def _env():
    for line in (PROJ / ".env").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _fx(name: str) -> list:
    p = FIX / f"live_{name}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def _snapshot_state() -> dict[Path, bytes | None]:
    """상태 파일 스냅샷. **리허설은 상태를 바꾸면 안 된다** —
    첫 리허설이 링크드인 축 커서를 전진시켜 두 번째 리허설이 '주기 미도래'로 건너뛰었다.
    그대로 뒀으면 **유료 실행이 축 12개를 통째로 건너뛸 뻔했다.**"""
    d = PROJ / "data"
    snap: dict[Path, bytes | None] = {}
    for p in list(d.glob("*.json")) + list(d.glob("*.pending")):
        if p.name.startswith("cut_"):
            continue
        snap[p] = p.read_bytes()
    return snap


def _restore_state(snap: dict[Path, bytes | None]) -> None:
    d = PROJ / "data"
    for p in list(d.glob("*.json")) + list(d.glob("*.pending")):
        if p.name.startswith("cut_"):
            continue
        if p not in snap:
            p.unlink()                       # 리허설이 새로 만든 것 → 제거
    for p, b in snap.items():
        if b is not None:
            p.write_bytes(b)
    print("· 상태 파일 원복 (커서·seen-store·워치리스트·앵커풀 무변경)")


def main() -> int:
    _env()
    do_land = "--land" in sys.argv
    snap = _snapshot_state()
    try:
        return _run(do_land)
    finally:
        _restore_state(snap)


def _run(do_land: bool) -> int:
    from adapters import apify, threads
    from core import fold, judge, dedup, notion

    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    kws = yaml.safe_load((PROJ / "keywords.yaml").read_text(encoding="utf-8"))
    apify.configure(cfg)

    KEY = _fx("igview-owner_threads-search-scraper")   # 키워드 층 (실제 25건)
    IG = _fx("igview")                                  # 팔로잉 확장 (실제 10명)
    USER = _fx("meta_user")                             # 유저별 글 (실제 14건)
    REP = _fx("replies")                                # 댓글 (실제 19건)
    LI = _fx("li_stealth")                              # 링크드인 (실제 25명)

    # 픽스처는 며칠 전 글이라 날짜 컷(after=2일)에 걸린다 → 오늘 날짜로 당긴다.
    # (날짜 컷 로직 자체는 이미 검증됨. 여기선 그 뒤 단계를 보는 게 목적)
    from datetime import date
    today = date.today().isoformat()

    def refresh(items, dkey):
        out = []
        for i, it in enumerate(items):
            o = dict(it)
            for k in ("takenAtISO", "created_at", "timestamp"):
                if k in o:
                    o[k] = f"{today}T09:{i % 60:02d}:00.000Z"
            out.append(o)
        return out

    calls = {"n": 0}

    def fake(slug, payload, timeout=180):
        calls["n"] += 1
        if "user-following" in slug:
            return IG
        if "replies" in slug:
            return refresh(REP, "created_at")
        if payload.get("mode") == "user":
            return refresh(USER, "created_at")
        if "linkedin" in slug:
            return LI
        return refresh(KEY, "takenAtISO")          # 키워드 검색

    apify.run_actor = threads.apify.run_actor = fake
    try:
        from adapters import linkedin as li_mod
        li_mod.apify.run_actor = fake
    except Exception:
        pass

    print("═" * 66)
    print("무과금 리허설 — Apify는 실제 저장 출력으로 대체 · 앱스토어는 진짜 수집")
    print("═" * 66)

    # ── 1. 스레드 (4개 층, 실제 액터 출력)
    print("\n▶ 스레드 (실제 액터 출력)")
    tc = threads.ThreadsAdapter(cfg, kws).collect()
    from collections import Counter
    print(f"  → {len(tc)}건 · 층별 {dict(Counter(c.signals['sourcing_method'] for c in tc))}")

    # ── 2. 앱스토어 (진짜 · 무료)
    print("\n▶ 앱스토어 (진짜 수집 — 애플 무료 API)")
    from adapters.appstore import AppStoreAdapter
    ac = AppStoreAdapter(cfg, kws).collect()
    print(f"  → {len(ac)}건")

    # ── 3. 링크드인 (실제 프로필 25명)
    print("\n▶ 링크드인 (실제 Stealth 축 출력 25명)")
    from adapters.linkedin import LinkedInAdapter
    try:
        lc = LinkedInAdapter(cfg, kws).collect()
    except Exception as e:
        print(f"  ⚠️  {e}")
        lc = []
    print(f"  → {len(lc)}건")

    allc = [c.to_dict() for c in (tc + ac + lc)]

    # ── 4. dedup → L0 → 인물접기
    print("\n▶ 파이프라인")
    n0 = len(allc)
    fresh_ = dedup.filter_new(cfg, allc)
    kept, cut = judge.hardcut(cfg, fresh_)
    people = fold.by_author(kept)
    print(f"  {n0} → dedup {len(fresh_)} → L0 {len(kept)} → 접기 {len(people)}")
    if cut:
        print(f"  L0 컷 사유: {dict(Counter(c['_cut_reason'].split('(')[0] for c in cut))}")

    # ── 5. 🔴 노션 페이로드 검증 (쓰지 않고)
    print("\n▶ 노션 페이로드 검증 (라이브 스키마 대조 — 조용한 드롭 탐지)")
    token = notion._token()
    schema = notion._fetch_schema(cfg, token)
    seen = notion.existing_landing_keys(cfg)

    dropped: dict[str, set] = {}
    missing_props: set = set()
    create = update = blocks = 0
    for c in people:
        props = notion._build_landing_properties(cfg, c, schema, f"{today}T09:00:00")
        s = c.get("signals") or {}
        # 코드가 쓰려는 값 vs 노션이 받아주는 값
        for name, val in (("Sourcing Method", s.get("sourcing_method")),
                          ("Sourcing Layers", s.get("sourcing_layers")),
                          ("Matched Keyword", s.get("matched_keyword")),
                          ("Korea Signal", s.get("korea_signal")),
                          ("Channel", notion.CHANNEL_LABEL.get(c.get("source", ""))),
                          ("주차", notion._iso_week(f"{today}T09:00:00"))):
            if not val:
                continue
            if name not in schema:
                missing_props.add(name)
                continue
            allowed = {o["name"] for o in (schema[name].get(schema[name]["type"]) or {}).get("options", [])}
            for v in (val if isinstance(val, list) else [val]):
                if allowed and v not in allowed:
                    dropped.setdefault(name, set()).add(v)
        for n in ("Post Count", "Followers", "Author Headline", "Posted At"):
            if n in schema and s.get(n.lower().replace(" ", "_")) is None and n == "Post Count":
                pass
        key = notion._landing_key(c)
        posts = (s.get("posts") or []) if notion._person_key(c) else []
        if key and key in seen:
            update += 1
        else:
            create += 1
        blocks += len(posts)

    ok = True
    if missing_props:
        ok = False
        print(f"  🔴 노션에 없는 property: {sorted(missing_props)} → 값이 통째로 버려진다")
    if dropped:
        ok = False
        for name, vals in dropped.items():
            print(f"  🔴 '{name}' 옵션 없음 → 조용히 버려질 값: {sorted(vals)}")
            print(f"      → tools/notion_migrate.py --apply 로 옵션을 먼저 만들어라")
    if ok:
        print("  ✅ 모든 select/multi_select 값이 노션 옵션에 존재 — 조용한 드롭 없음")

    print(f"\n▶ 착지 예상: 신규 {create}행 · 갱신 {update}행 · 본문 글 {blocks}편")

    # 채널별 칸 충족률 — "노션에서 사람이 볼 게 있는가"
    print("\n▶ 노션에서 사람이 볼 칸 (채널별 충족률)")
    for src, label in (("스레드", "Threads"), ("링크드인", "LinkedIn"), ("앱스토어", "App Store")):
        rows = [c for c in people if c.get("source") == src]
        if not rows:
            continue
        n = len(rows)
        def pct(fn):
            return f"{sum(1 for c in rows if fn(c))}/{n}"
        s_ = lambda c: c.get("signals") or {}
        print(f"  {label} {n}행")
        if src == "스레드":
            print(f"    작성자 {pct(lambda c: s_(c).get('author_username'))} · "
                  f"한줄 {pct(lambda c: c.get('one_liner'))} · "
                  f"Post Count {pct(lambda c: s_(c).get('post_count') is not None)} · "
                  f"bio {pct(lambda c: s_(c).get('author_headline'))} · "
                  f"팔로워 {pct(lambda c: s_(c).get('followers'))}")
        elif src == "링크드인":
            print(f"    이름 {pct(lambda c: c.get('name'))} · "
                  f"URL {pct(lambda c: c.get('linkedin_url'))} · "
                  f"헤드라인 {pct(lambda c: s_(c).get('author_headline') or s_(c).get('headline'))} · "
                  f"재직개월 {pct(lambda c: s_(c).get('tenure_months') is not None)} · "
                  f"한국신호 {pct(lambda c: s_(c).get('korea_signal'))}")
        else:
            print(f"    앱명 {pct(lambda c: c.get('name'))} · "
                  f"개발사 {pct(lambda c: c.get('founder'))} · "
                  f"출시일 {pct(lambda c: c.get('released_at'))} · "
                  f"평점 {pct(lambda c: s_(c).get('rating'))}")

    if not do_land:
        print("\n(--land 를 붙이면 대표 행 3개를 진짜 노션에 써보고 확인 후 아카이브한다)")
        return 0 if ok else 1

    # ── 6. 대표 행 실착지 → 확인 → 아카이브
    print("\n▶ 실착지 테스트 (채널별 1행씩, 확인 후 아카이브)")
    # [W12 · B-F1] 노션 전송 규율의 정본은 kv_core 다(백오프·페이싱·재시도).
    from kv_core import notion as _kv
    picks = []
    for src in ("스레드", "링크드인", "앱스토어"):
        r = [c for c in people if c.get("source") == src]
        if r:
            picks.append(max(r, key=lambda c: (c.get("signals") or {}).get("post_count") or 0))
    if not picks:
        print("  대상 없음")
        return 0
    okn, failed = notion.land(cfg, picks, f"{today}T09:00:00")
    print(f"  land → 성공 {okn} · 실패 {failed}")
    seen2 = notion.existing_landing_keys(cfg)
    H = {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28"}
    for c in picks:
        pid = seen2.get(notion._landing_key(c))
        if not pid:
            print(f"  🔴 {c['name']}: 착지 안 됨")
            continue
        pr = _kv.read(f"{_kv.API}/pages/{pid}", token).json()["properties"]
        filled = [k for k, v in pr.items()
                  if v.get(v["type"]) not in (None, "", [], {})]
        body = notion.existing_post_urls(token, pid)
        print(f"  ✅ [{c['source']}] {c['name']}: 채워진 칸 {len(filled)}개 · 본문 글 {len(body or [])}편")
        notion._write("PATCH", f"{notion.API}/pages/{pid}", token, {"archived": True})
    print("  · 테스트 행 전부 아카이브")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
