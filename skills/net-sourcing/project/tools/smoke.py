#!/usr/bin/env python3
"""스모크 테스트 — 미검증 경로를 **최소 물량**으로 1회씩만 찔러본다. 총 ~$0.60.

## 왜 풀 실행($9.6) 대신 이걸 먼저 하나
확인해야 할 미지수는 6개인데, 그걸 알아내는 데 21개 키워드 전량과 60명 팔로잉이 필요하지 않다.
각 경로가 **살아 있는지 / 우리 파서에 걸리는지**만 보면 되고, 그건 계정 1개·앵커 2개면 충분하다.
스모크가 스왑 여부를 결정하고, 그게 풀 실행을 $18.8이냐 $9.6이냐로 가른다.

## 이 스크립트가 **하지 않는** 것 (중요)
- 노션에 아무것도 쓰지 않는다.
- 커서·seen-store·워치리스트·앵커풀 **어떤 상태 파일도 건드리지 않는다.**
  (스모크가 커서를 전진시키면 그 사람들 글을 다음 실행에 안 산다)
- 백필 커서(threads_last_success)를 전진시키지 않는다.

## 하는 것
각 액터를 최소 물량으로 부르고, **원본 출력을 data/fixtures/live_*.json 에 저장**한 뒤,
우리 파서에 먹여서 필드가 채워지는지 본다. 실패하면 **실제 키 이름을 찍는다** →
그 다음부터는 **돈 한 푼 안 쓰고** 저장된 원본으로 파서를 고치고 재현할 수 있다.

사용:
  ./.venv/bin/python tools/smoke.py            # 전체 (~$0.60)
  ./.venv/bin/python tools/smoke.py --dry      # 무엇을 얼마에 돌릴지만 출력 (무과금)
  ./.venv/bin/python tools/smoke.py --only meta_kr
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))
FIX = PROJ / "data" / "fixtures"

BURBN = "burbn~threads-search-scraper"
META = "futurizerush~meta-threads-scraper"
IGVIEW = "igview-owner~threads-user-following-scraper"
REPLIES = "futurizerush~threads-replies-scraper"
LI = "harvestapi~linkedin-profile-search"


def _save(name: str, items: list) -> Path:
    FIX.mkdir(parents=True, exist_ok=True)
    p = FIX / f"live_{name}.json"
    p.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def _cover(cands, fields: dict) -> tuple[bool, list[str]]:
    """파싱 결과의 필드 충족률. (전부 통과?, 실패 메시지들)"""
    n = len(cands)
    bad = []
    for label, fn in fields.items():
        miss = sum(1 for c in cands if not fn(c))
        if miss > n * 0.2:
            bad.append(f"{label} 누락 {miss}/{n}")
    return (not bad), bad


# ─────────────────────────── 프로브들 ───────────────────────────
def probe_burbn_from(a, apify, ch) -> dict:
    """burbn `from` 파라미터 — 워치리스트·팔로잉 글 층의 전제. **한 번도 실행된 적 없다.**"""
    from adapters.threads import _user_posts_payload
    user = "zenonplane"
    items = apify.run_actor(BURBN, _user_posts_payload(BURBN, [user], 3, ""))
    _save("burbn_from", items)
    if not items:
        return {"ok": False, "note": f"0건 — burbn의 `from`이 안 먹거나 @{user}가 최근 글이 없다. "
                                     f"워치리스트·팔로잉 글 층이 통째로 무력해진다."}
    cands = [a._post_to_candidate(p, sourcing_method="watchlist", search_mode="KEYWORD",
                                  search_type="RECENT", query="", is_reply=False,
                                  layer="watchlist") for p in items]
    same = sum(1 for c in cands if c.founder.lower() == user)
    ok, bad = _cover(cands, {"작성자": lambda c: c.founder, "본문": lambda c: c.raw_text,
                             "URL": lambda c: c.url})
    if same < len(cands):
        bad.append(f"🔴 `from`이 무시됐다 — @{user} 글만 와야 하는데 {len(cands)-same}건이 남의 글")
        ok = False
    return {"ok": ok, "note": f"{len(items)}건 · @{user} 글 {same}/{len(cands)}" +
                              ("" if ok else " · " + " / ".join(bad)),
            "keys": sorted(items[0].keys())[:14]}


def probe_igview(a, apify, ch) -> dict:
    """igview 팔로잉 액터 — **한국 계정**에서 실제로 팔로잉을 주는가."""
    from adapters.threads import _is_profile_row, _handle_of
    seed = (a._threads_keywords().get("following_seed_accounts") or ["zenonplane"])[0]
    items = apify.run_actor(IGVIEW, {"username": seed, "max_following": 10})
    _save("igview", items)
    profs = [i for i in items if _is_profile_row(i)]
    if not profs:
        return {"ok": False, "note": f"@{seed} 팔로잉 0명 — 액터가 한국 계정에 안 먹거나 비공개. "
                                     f"팔로잉그래프 층이 죽는다.",
                "keys": sorted(items[0].keys())[:14] if items else []}
    handles = [_handle_of(p) for p in profs]
    n_ok = sum(1 for h in handles if h)
    return {"ok": n_ok == len(profs),
            "note": f"@{seed} → 팔로잉 {len(profs)}명 · 핸들 추출 {n_ok}/{len(profs)} "
                    f"· 예: {', '.join('@'+h for h in handles[:3] if h)}",
            "keys": sorted(profs[0].keys())[:14]}


def probe_replies(a, apify, ch) -> dict:
    """리플라이 액터 — **한국 앵커**의 댓글을 실제로 주는가."""
    anchors = a._load_anchors()[:2]
    if not anchors:
        return {"ok": False, "note": "앵커 풀이 비어 있다 — keyword 층이 먼저 앵커를 수확해야 한다"}
    items = apify.run_actor(REPLIES, {"post_urls": [{"url": u} for u in anchors],
                                      "max_replies": 10, "include_nested_replies": False})
    _save("replies", items)
    if not items:
        return {"ok": False, "note": f"앵커 {len(anchors)}개에서 댓글 0건 — 액터가 죽었거나 payload 형식 오류"}
    reps = [p for p in items if p.get("item_type") != "original_post"]
    cands = [a._post_to_candidate(p, sourcing_method="reply_crawl", search_mode="KEYWORD",
                                  search_type="RECENT", query="", is_reply=True,
                                  layer="replies") for p in reps]
    ok, bad = _cover(cands, {"작성자": lambda c: c.founder, "본문": lambda c: c.raw_text,
                             "URL": lambda c: c.url})
    who = sorted({c.founder for c in cands if c.founder})
    return {"ok": ok, "note": f"앵커 {len(anchors)}개 → 댓글 {len(reps)}건 · 고유 작성자 {len(who)}명"
                              + ("" if ok else " · " + " / ".join(bad)),
            "keys": sorted(items[0].keys())[:14]}


def probe_meta_kr(a, apify, ch) -> dict:
    """meta-threads가 **한국어 검색**을 지원하는가 — 액터 스왑의 전제. 실패하면 burbn 유지."""
    from adapters.threads import _search_payload
    q = "만들었습니다"
    items = apify.run_actor(META, _search_payload(META, q, 20, ""))
    _save("meta_kr", items)
    posts = [p for p in items if p.get("text_content") or p.get("post_url")]
    if not posts:
        return {"ok": False, "note": f"'{q}' 검색 0건 — **한국어를 지원하지 않는다.** "
                                     f"스왑 불가 → burbn 유지 + config 축소(월 $150)"}
    cands = [a._post_to_candidate(p, sourcing_method="keyword_search", search_mode="KEYWORD",
                                  search_type="RECENT", query=q, is_reply=False,
                                  layer="keyword") for p in posts]
    ok, bad = _cover(cands, {"작성자": lambda c: c.founder, "본문": lambda c: c.raw_text,
                             "URL": lambda c: c.url, "게시일": lambda c: c.released_at})
    kr = sum(1 for c in cands if any('가' <= ch_ <= '힣' for ch_ in (c.raw_text or "")))
    bio = sum(1 for c in cands if c.signals.get("author_headline"))
    return {"ok": ok, "note": f"'{q}' → {len(posts)}건 · 한글 본문 {kr}/{len(cands)} · bio 있음 {bio}"
                              + ("" if ok else " · " + " / ".join(bad)),
            "keys": sorted(items[0].keys())[:14]}


def probe_li_stealth(a, apify, ch) -> dict:
    """LinkedIn `searchQuery: "Stealth Startup"` 축 — 요구사항의 핵심인데 한 번도 안 돌았다."""
    items = apify.run_actor(LI, {
        "searchQuery": "Stealth Startup", "locations": ["South Korea"],
        "profileScraperMode": "Full", "startPage": 1, "takePages": 1, "maxItems": 25})
    _save("li_stealth", items)
    if not items:
        return {"ok": False, "note": "0명 — searchQuery 축이 안 먹는다. Stealth 발굴 경로가 죽는다"}
    def txt(p):
        return json.dumps(p, ensure_ascii=False).lower()
    hit = [p for p in items if "stealth" in txt(p)]
    names = [(p.get("firstName", "") + " " + p.get("lastName", "")).strip() for p in items[:3]]
    return {"ok": bool(hit),
            "note": f"{len(items)}명 · 그중 stealth 표기 {len(hit)}명 · 예: {', '.join(n for n in names if n)}"
                    + ("" if hit else "  🔴 stealth가 하나도 없다 — 축이 헛돌고 있다"),
            "keys": sorted(items[0].keys())[:14]}


PROBES = {
    "burbn_from":  (probe_burbn_from,  0.18, "burbn `from` (워치리스트·팔로잉 글의 전제)"),
    "igview":      (probe_igview,      0.06, "igview 팔로잉 (한국 계정에 먹히나)"),
    "replies":     (probe_replies,     0.06, "리플라이 (한국 앵커 댓글)"),
    "meta_kr":     (probe_meta_kr,     0.07, "meta-threads 한국어 (스왑의 전제)"),
    "li_stealth":  (probe_li_stealth,  0.20, "LinkedIn Stealth 축"),
}


def main() -> int:
    dry = "--dry" in sys.argv
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]

    names = [only] if only else list(PROBES)
    if only and only not in PROBES:
        print(f"🔴 모르는 프로브: {only} (가능: {list(PROBES)})")
        return 1

    total = sum(PROBES[n][1] for n in names)
    print("스모크 테스트 — 미검증 경로를 최소 물량으로 1회씩\n")
    for n in names:
        print(f"  {n:12} ~${PROBES[n][1]:.2f}  {PROBES[n][2]}")
    print(f"  {'합계':12} ~${total:.2f}\n")
    if dry:
        print("[--dry] 실제로 돌리려면 --dry 를 빼라.")
        return 0

    # 🔴 [2026-08-16] 인라인 `.env` 파서 → `kv_core.secrets`.
    #    `main.py:28-36` 이 *"자격증명 정본은 `$KV_CONFIG/.env` 하나뿐. 스킬별 `.env` 를
    #    다시 만들지 마라"* 라고 선언하고 옮겼는데 도구들은 옛 파서를 그대로 들고 있었고,
    #    `project/.env` 는 **존재하지 않아** 실행 즉시 `FileNotFoundError` 로 죽었다.
    from kv_core import secrets
    secrets.load()

    from adapters import apify, threads
    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    kw = yaml.safe_load((PROJ / "keywords.yaml").read_text(encoding="utf-8"))
    apify.configure(cfg)

    # 프로브는 소량이므로 런당 상한을 **더 조인다** — 액터가 폭주해도 몇 센트에서 멈춘다.
    for slug in (BURBN, META, IGVIEW, REPLIES, LI):
        apify._OPTS.setdefault(slug, {})
        apify._OPTS[slug] = {**apify._OPTS[slug], "max_charge_usd": 0.35}
    apify._BUDGET = max(1.5, total * 2)          # 스모크 전용 예산 (풀 실행 예산과 별개)

    a = threads.ThreadsAdapter(cfg, kw)
    ch = cfg["channels"]["threads"]
    before = apify.usage_usd()

    results: dict[str, dict] = {}
    for n in names:
        fn = PROBES[n][0]
        print(f"▶ {n} — {PROBES[n][2]}")
        try:
            r = fn(a, apify, ch)
        except Exception as e:
            r = {"ok": False, "note": f"예외: {e}"}
        results[n] = r
        mark = "✅" if r["ok"] else "🔴"
        print(f"  {mark} {r['note']}")
        if r.get("keys"):
            print(f"     실제 출력 키: {r['keys']}")
        print()

    after = apify.usage_usd()
    if before is not None and after is not None:
        print(f"💰 실제 과금 ${after - before:.3f} (추정 ${apify.spent_usd():.3f})\n")

    print("─" * 60)
    passed = [n for n in names if results[n]["ok"]]
    failed = [n for n in names if not results[n]["ok"]]
    print(f"통과 {len(passed)}/{len(names)}")
    if failed:
        print(f"🔴 실패: {', '.join(failed)}")
        print("\n   → 실패한 액터의 **원본 출력이 data/fixtures/live_*.json 에 저장돼 있다.**")
        print("     위 '실제 출력 키'를 보고 파서(_post_to_candidate / _handle_of)를 고친 뒤,")
        print("     저장된 파일로 오프라인 재현하라 — **추가 비용 $0**이다.")

    print("\n다음 단계:")
    if results.get("meta_kr", {}).get("ok"):
        print("  ✅ meta-threads 한국어 OK → config.channels.threads.actors 의 keyword/user_posts 를")
        print("     'futurizerush~meta-threads-scraper' 로 바꿔라 → 풀 실행 $18.8 → **$9.6**")
    elif "meta_kr" in names:
        print("  🔴 meta-threads 한국어 실패 → burbn 유지. config.yaml 상단 주석의 '②' 축소안 적용.")
    print("  그다음: ./.venv/bin/python tools/estimate_cost.py  → 예산 확인 → main.py run --channel all")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
