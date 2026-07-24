#!/usr/bin/env python3
"""실행 **전에** 비용을 계산한다. 무과금. `collect` 전에 항상 이걸 먼저 본다.

왜 필요한가: 지금까지 "얼마 나올지"를 아무도 모른 채 돌렸고, 그래서 07-10에 $5.94가 터졌다.
budget_usd 가드는 **이미 쓴 돈**을 보고 막으므로, 한도를 넘기면 남은 채널이 통째로 차단된다
(스레드가 예산을 다 쓰면 링크드인이 0건이 된다 — 그건 가드가 아니라 사고다).
→ 실행 전에 총액을 뽑아 **config를 고칠 기회**를 준다.

🔴 **채널을 받는다.** 안 받으면 `--channel appstore`(= $0, 애플 무료 API)로 돌려도 스레드+링크드인
   전체를 추정한다. 그러면 (a) 로그엔 "$8.14 추정"이 찍히는데 실지출은 $0이라 사람이 헷갈리고,
   (b) 나중에 키워드를 늘려 추정이 preflight_max를 넘기는 순간 **$0짜리 앱스토어 크론이
   아무 상관 없는 이유로 조용히 죽는다.** 캡 검사도 같은 이유로 채널 범위로 좁힌다.

사용: ./.venv/bin/python tools/estimate_cost.py [--channel all|appstore|threads|linkedin]
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))


BIO_ACTOR = "apify~threads-profile-api-scraper"    # tools/backfill_bio.py 와 같은 값이어야 한다


def _batch(slug: str) -> bool:
    return "meta-threads-scraper" in slug


def _batch_runs(slug: str, n_users: int) -> int:
    """배치 액터의 런 수. meta는 usernames maxItems=20이라 20명씩 쪼갠다
    (adapters/threads.py::MAX_BATCH_USERS와 같은 값이어야 한다 — 다르면 천장이 틀린다)."""
    if not _batch(slug):
        return n_users                       # 비배치 = 유저당 런 1회
    return max(1, -(-n_users // 20))         # ceil(n/20)


def _channel_arg(argv: list[str]) -> str:
    for i, a in enumerate(argv):
        if a == "--channel" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--channel="):
            return a.split("=", 1)[1]
    return "all"


def main() -> int:
    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    kws = yaml.safe_load((PROJ / "keywords.yaml").read_text(encoding="utf-8"))
    ap = cfg["apify"]
    acts = ap["actors"]

    # 채널 확장은 **pipeline._expand가 정본**이다. 여기서 별칭 규칙을 다시 적으면 드리프트가 난다
    # ("linkedin" 하나가 2개 어댑터로 퍼지는 규칙이 두 군데에 있으면 언젠가 갈라진다).
    from core.pipeline import _expand
    channel = _channel_arg(sys.argv)
    chans = set(_expand(channel))
    want_threads = "threads" in chans
    want_li = any(c.startswith("linkedin") for c in chans)
    if channel != "all":
        print(f"· 채널: {channel} → {sorted(chans)}\n")

    # 🔴 코드가 **부를 수 있는** 액터는 전부 config.apify.actors에 캡이 있어야 한다.
    #   빠진 액터는 (a) 추정에 안 잡히고 (b) max_charge_usd 없이 돈다 = 이중으로 보이지 않는다.
    #   실제로 harvestapi~linkedin-profile-scraper 가 그랬다(post_search 안에서만 불려서 안 걸렸다).
    #   ⚠️ **이번 실행이 부를 수 있는** 액터만 본다 — 앱스토어 단독 실행($0)이 링크드인 config
    #      문제로 죽으면 안 된다. 그게 이 채널 인자를 만든 이유다.
    from adapters.linkedin import POST_ACTOR, PROFILE_ACTOR, SCRAPER_ACTOR
    reachable: set[str] = set()
    if want_li:
        reachable |= {PROFILE_ACTOR, POST_ACTOR, SCRAPER_ACTOR}
    if want_threads:
        reachable |= set((cfg["channels"]["threads"].get("actors") or {}).values())
        reachable.add(BIO_ACTOR)              # land 후 backfill_bio.py 가 실제로 부른다
    naked = [s for s in sorted(reachable)
             if not (acts.get(s) or {}).get("max_charge_usd")]
    if naked:
        print("🔴 캡 없는 액터 — config.apify.actors에 max_charge_usd 를 박아라:")
        for s in naked:
            print(f"   · {s}")
        print("   (지금 안 불리더라도, 엔진을 켜는 순간 **추정에도 안 잡히고 캡도 없이** 돈다.)")
        return 1

    def cost(slug: str, runs: int, items: int) -> float:
        o = acts.get(slug, {})
        gb = max(1, int(o.get("memory_mb", 1024)) // 1024)
        return runs * float(o.get("start_usd", 0)) * gb + items * float(o.get("price_per_item", 0))

    t = cfg["channels"]["threads"]
    tk = kws["threads"]
    # (층 이름, 런, 아이템, 비용, **슬러그**) — 슬러그가 있어야 액터 캡과 대조할 수 있다.
    rows: list[tuple[str, int, int, float, str]] = []

    if t.get("enabled") and want_threads:
        layers = t["layers"]
        A = t["actors"]
        cap = int(t["max_posts"])

        # ★ [2026-07-20] **주기 미도래 층은 이번 실행에서 안 돈다 → 추정에서도 빼야 한다.**
        #   전엔 cadence를 아예 안 읽어서 추정이 **항상 최악주**를 보고했다. 그 결과
        #   (a) 절감이 영영 안 보이고("레버가 안 먹는다"고 오진), (b) 기준선이 과대보고되고,
        #   (c) 프리플라이트 게이트가 실제보다 빡빡하게 걸린다.
        #   🔴 판정 규칙은 **어댑터가 정본**이다. 여기서 다시 적으면 갈라진다(`_expand`와 같은 이유).
        from adapters.threads import ThreadsAdapter
        _tstate = ThreadsAdapter._load_layer_state()
        layers = [ly for ly in layers if ThreadsAdapter._layer_due(ly, t, _tstate)]

        if "keyword" in layers:
            n = len(tk["keyword"])
            # burbn 실측: 캡 40에 41~52건이 온다(초과 반환). 1.15배로 잡는다.
            items = int(n * cap * 1.15)
            rows.append(("스레드 keyword", n, items, cost(A["keyword"], n, items), A["keyword"]))

        if "watchlist" in layers:
            accts = min(int(t.get("watchlist_max_accounts", 12)),
                        len(tk.get("watch_accounts") or []))
            per = int(t["watchlist_posts_per_account"])
            # 워치리스트는 **user_posts 액터**를 쓴다(어댑터와 동일). keyword 액터가 아니다 —
            # burbn의 `from`은 searchQuery가 필수라 "그 사람의 모든 글"을 못 준다(실측 400).
            wl = A.get("user_posts") or A["keyword"]
            runs = _batch_runs(wl, accts)
            rows.append(("스레드 watchlist", runs, accts * per, cost(wl, runs, accts * per), wl))

        if "following_graph" in layers:
            # 🔴 시드는 이제 {username, runs} 가중치 스펙이다 — adapters/threads.py::_seed_runs 와
            #   **같은 규칙**이어야 한다. 다르면 천장 계산이 틀리고 가드가 장식이 된다.
            from adapters.threads import _seed_runs
            seed_runs = (_seed_runs(tk.get("following_seed_accounts"))
                         + _seed_runs(tk.get("following_seed_trials")))
            seeds = len(seed_runs)
            per_acct = int(t["following_max_per_account"])
            rows.append(("스레드 following(확장)", seeds, seeds * per_acct,
                         cost(A["following"], seeds, seeds * per_acct), A["following"]))

            # bio enrich — land 후 backfill_bio.py가 산다. **FG 인물은 글이 없어 bio가 유일한 신호다.**
            n_bio = int(t.get("following_max_expanded", 60))
            rows.append(("스레드 following(bio)", 1, n_bio,
                         cost(BIO_ACTOR, 1, n_bio), BIO_ACTOR))

            # 글 구매는 폐지됐다(following_posts_budget: 0). 0이면 행 자체를 안 만든다.
            buy = int(t.get("following_posts_budget", 0))
            if buy > 0:
                ppu = int(t["following_posts_per_user"])
                runs = _batch_runs(A["user_posts"], buy)
                rows.append(("스레드 following(글)", runs, buy * ppu,
                             cost(A["user_posts"], runs, buy * ppu), A["user_posts"]))

        # [팔로업 ② 가시화] main.py recheck — **수동 전용이라 run 추정 합계엔 안 넣는다**
        # (넣으면 매 run 프리플라이트가 $0.5씩 부풀어 거짓 게이트가 된다). 캡은 BIO_ACTOR의
        # max_charge_usd + apify.budget_usd 가 이미 강제한다. 여기선 천장만 알려준다.
        rk_cap = int((cfg.get("recheck") or {}).get("max_people", 100))
        print(f"· (수동 경로) recheck: 상한 {rk_cap}명 × $0.005 = ${rk_cap * 0.005:.2f}/회 "
              f"— run 추정에 미포함(수동 전용, 액터 캡 적용됨)")

        if "replies" in layers:
            anchors = min(int(t["replies_max_anchors"]), 20)
            per = min(int(t["replies_max_per_anchor"]), 50)
            rows.append(("스레드 replies", 1, anchors * per,
                         cost(A["replies"], 1, anchors * per), A["replies"]))

    li = cfg["channels"]["linkedin"]
    if li.get("enabled") and want_li and "profile_search" in li["engines"]:
        # 🔴 페이지 수는 `batch_pages`(=한 런에 요청할 페이지, 항상 1)가 **아니라**
        #    축마다 붙은 `pages` 필드다. 이걸 헷갈려 12런/$2.40로 추정했는데
        #    실제는 62런/$12.4였다(pages: 0인 축은 **소진까지 = 최대 100페이지 = $20**).
        #    batch_pages=1이라 **페이지 1개 = 런 1회**다.
        MAX_PAGE = 100                       # adapters/linkedin.py:42
        default_pages = int(li["profile_search"].get("pages", 0))
        runs = 0
        # ★ [2026-07-20] 스레드 층과 같은 이유로 **주기 미도래 축을 뺀다.**
        #   `_axis_due`는 cadence뿐 아니라 rewind/exhausted/refresh까지 본다 — 그래서 규칙을
        #   베끼지 않고 **어댑터 것을 그대로 부른다**(committed_only=True, 판단은 확정본으로).
        from adapters.linkedin import LinkedInAdapter
        _listate = LinkedInAdapter._axis_state(committed_only=True)
        _due = [s for s in kws["linkedin"]["profile_search"]
                if LinkedInAdapter._axis_due(s, _listate)]
        if len(_due) < len(kws["linkedin"]["profile_search"]):
            _skip = [s.get("label", "?") for s in kws["linkedin"]["profile_search"]
                     if s not in _due]
            print(f"· 링크드인: 주기 미도래 축 {len(_skip)}개 제외 — {', '.join(_skip)}")
        for spec in _due:
            p = int(spec.get("pages", default_pages))
            runs += p if p else MAX_PAGE     # pages:0 = 결과 소진까지 → 최악 100페이지
        full = str(li["profile_search"].get("scraper_mode", "Short")).lower().startswith("full")
        per_page = 0.10 + (25 * 0.004 if full else 0.0)     # search-page + (Full이면 프로필 과금)
        # 슬러그는 config가 아니라 **어댑터 상수**가 정본이다. config에 또 적으면 드리프트가 난다.
        from adapters.linkedin import PROFILE_ACTOR
        rows.append(("링크드인 profile_search", runs, runs * 25, runs * per_page, PROFILE_ACTOR))
    if li.get("enabled") and want_li and "post_search" in li["engines"]:
        # 🔴 [2026-07-20] 전엔 여기가 **`48.0` 상수**였다. 그건 추정이 아니라 자리표시자라서,
        #   엔진을 켜는 순간 **실지출이 추정에 안 잡힌다**(버그로그 패턴 ③의 비용판).
        #   → config에서 실제로 계산한다. 이 숫자가 커야 preflight 게이트가 제대로 막는다.
        ps = li.get("post_search") or {}
        disc, mon = ps.get("discovery") or {}, ps.get("monitoring") or {}
        items = 0
        if disc.get("enabled"):
            nq = len(kws["linkedin"].get("post_search") or [])
            items += nq * int(disc.get("max_posts", 0)) * int(disc.get("pages", 1))
        if mon.get("enabled"):
            items += int(mon.get("batch", 0)) * int(mon.get("max_posts", 0))
        # post-search는 **start가 $0이고 아이템 100%** — profile-search(런당 $0.10 고정비)와
        # 비용 구조가 정반대다. 여기선 `max_posts`가 유일한 노브다(stage6 3원칙 적용 대상 아님).
        rows.append(("🔴 링크드인 post_search", 1, items, cost(POST_ACTOR, 1, items), POST_ACTOR))

    print(f"{'층':26} {'런':>5} {'아이템':>8} {'추정':>9} {'캡/런':>7} {'천장':>8}")
    print("-" * 70)
    total = ceiling = 0.0
    runs_total = 0
    starved: list[tuple[str, float, float]] = []      # 캡이 추정보다 낮은 층 = 액터가 잘라버린다
    for name, runs, items, c, slug in rows:
        cap_run = float((acts.get(slug) or {}).get("max_charge_usd") or 0)
        roof = runs * cap_run if cap_run else float("inf")
        per_run = c / runs if runs else 0.0
        flag = ""
        if cap_run and runs and cap_run < per_run:
            flag = " 🔴"
            starved.append((name, per_run, cap_run))
        print(f"{name:26} {runs:5} {items:8} ${c:8.2f} "
              f"{('$%.2f' % cap_run) if cap_run else '  없음':>7} "
              f"{('$%.2f' % roof) if cap_run else '    ∞':>8}{flag}")
        total += c
        ceiling += roof
        runs_total += runs
    print("-" * 70)
    print(f"{'합계':26} {runs_total:5} {'':8} ${total:8.2f} {'':>7} ${ceiling:7.2f}")
    if "appstore" in chans:
        print(f"\n앱스토어: $0 (애플 무료 엔드포인트 — Apify를 거치지 않는다)")

    # 🔴 [2026-07-13] '천장' = Σ(런 × 액터 max_charge_usd). 이건 **우리 추정이 아니라
    #    Apify가 액터를 강제로 멈추는 값**이다. 전역 차단선(budget_usd)은 이 천장보다 **위**에
    #    있어야 한다 — 그래야 정상 실행에서 절대 안 터지고, '액터 캡이 조용히 무시됐을 때'만
    #    잡는 백스톱이 된다.
    #    ⚠️ 두 가드는 터질 때의 결과가 다르다:
    #       · 액터 캡이 걸리면 → 그 런만 아이템을 덜 준다(우아한 열화, 나머지 층은 진행)
    #       · 전역 차단선이 걸리면 → 뒤쪽 채널이 **통째로 0건**(= 돈은 다 쓰고 결과는 반쪽)
    #    그래서 안전장치는 **아래(액터별)**에 두고, 전역은 **터지면 안 되는 높이**에 둔다.
    if starved:
        print("\n🔴 액터 캡이 추정보다 **낮은** 층이 있다 — 이 층은 Apify가 중간에 잘라버린다:")
        for name, per_run, cap_run in starved:
            print(f"   · {name}: 런당 추정 ${per_run:.2f} > 캡 ${cap_run:.2f} "
                  f"(약 {(1 - cap_run / per_run) * 100:.0f}% 손실)")
        print("   → config.apify.actors[...].max_charge_usd 를 런당 추정의 1.3배로 올려라.")
        return 1

    # 🔴 게이트는 preflight_max_usd다 — budget_usd가 **아니다.**
    #   budget_usd는 실행 **중** 폭주 차단선이라 추정보다 한참 위에 있다. 그걸 게이트로 쓰면
    #   "추정이 차단선보다 낮으니 OK"가 되어, 정작 config가 부풀어도 못 막는다.
    #   반대로 둘을 같은 값으로 두면 추정에 딱 붙은 차단선이 되어 **정상 변동에도 중간에 터진다**
    #   (= 돈은 다 쓰고 뒤쪽 채널은 0건). 두 숫자는 역할이 다르다.
    gate = float(ap.get("preflight_max_usd") or 0)
    trip = float(ap.get("budget_usd") or 0)
    if not gate:                                  # 구 config 호환 — 없으면 차단선을 게이트로도 쓴다
        gate = trip

    if gate and total > gate:
        print(f"\n🔴 추정 ${total:.2f} > preflight_max_usd ${gate:.2f} — **한 푼도 쓰지 않고 중단한다.**")
        print("   (여기서 막는 건 공짜다. 그냥 돌렸다가 실행 중 차단선에 걸리면 이미 쓴 돈은 못 돌려받고")
        print("    뒤쪽 채널만 0건이 된다.)")
        print("   → config를 먼저 고쳐라. 가장 큰 레버:")
        starts = sum(r[1] for r in rows if "스레드" in r[0])
        print(f"      · 스레드 액터 런이 {starts}회다. 런 시작 수수료는 **메모리 GB당** 붙는다"
              f" — 액터 스왑이 아이템 수 줄이기보다 효과가 크다.")
        print("      · keywords.yaml의 LinkedIn 축별 `pages`를 줄여라 (pages: 0 = 최대 100페이지다).")
        print("      · 의도된 지출이면 config.apify.preflight_max_usd 를 올려라 — 조용히 넘기지 않는다.")
        return 1

    if gate:
        print(f"\n✅ 추정 ${total:.2f} ≤ preflight_max_usd ${gate:.2f} — 시작한다.")

    if trip:
        if ceiling == float("inf"):
            print(f"\n🔴 액터 캡(max_charge_usd)이 없는 층이 있다 — 천장이 무한대다.")
            print(f"   그러면 전역 차단선 ${trip:.2f}가 **유일한 방벽**이고, 걸리는 순간 뒤쪽 채널이 0건이 된다.")
            print(f"   → 모든 액터에 max_charge_usd 를 박아라. 안전장치는 아래(액터별)에 둔다.")
            return 1
        if ceiling > trip:
            print(f"\n🔴 천장 ${ceiling:.2f} > 폭주 차단선 ${trip:.2f} — **정상 실행에서도 터질 수 있다.**")
            print(f"   전역 차단선이 걸리면 뒤쪽 채널(링크드인)이 통째로 0건이다 — 그건 가드가 아니라 사고다.")
            print(f"   → 액터별 max_charge_usd 를 조여 천장을 내리거나, budget_usd 를 ${ceiling:.2f} 위로 올려라.")
            return 1
        print(f"   천장 ${ceiling:.2f} < 폭주 차단선 ${trip:.2f} — 전역 차단선은 **백스톱**이다.")
        print(f"   (액터 캡이 정상 작동하면 여기 절대 안 걸린다. 걸린다면 maxTotalChargeUsd가 무시됐다는 뜻이다.)")

    # ★ [2026-07-20] 기계가 읽는 줄. `core/pipeline.py::_preflight_cost`가 이 값을 파싱해
    #   **월 예산 가드**(monthly_budget_usd)에 쓴다 — "실제누적 + 이번런추정 > 상한"을 재려면
    #   이번 런 추정치가 숫자로 필요하다. 사람이 읽는 표를 정규식으로 긁는 것보다 안전하다.
    #   🔴 형식을 바꾸면 파서도 같이 바꿔라(둘은 한 몸이다).
    print(f"#EST_TOTAL {total:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
