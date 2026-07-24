"""LinkedIn 수집 — Apify harvestapi~linkedin-profile-posts (net-sourcing에서 사용 중인 계열).

✅ VERIFIED(2026-07-17 스모크): 개인 프로필 3곳 각 10건 정상. 실측 확정 사항:
   - URL 필드는 `linkedinUrl` (`url` 아님), 날짜는 `postedAt.date`(dict)
   - 응답은 프로필의 활동 피드 — **리포스트 포함**. author.publicIdentifier가 슬러그와
     다르면 리포스트 → is_repost + actual_author 기록 (org 에코 방어에 필수, 제거 금지)
   - company URL(southparkcommons)은 이 액터가 미지원(0건) — 회사 페이지는 별도 액터 필요.
     SPC는 블로그 RSS로 이미 커버되므로 v1은 개인 프로필만.
   - 8일 컷은 클라이언트에서 (maxPosts=10이 날짜 무관 최신 10건을 가져옴)
직접 수집은 HTTP 999로 불가(실측) — Apify가 유일 경로.
사용법은 apify_x와 동일: --estimate(무과금) / --run(과금, 승인 후).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from adapters import apify_client
from adapters.apify_x import _load_env
from core.common import config, save_item, sources
from core.state import load_seen, mark_seen, record_health

ACTOR = "harvestapi~linkedin-profile-posts"


def _src() -> dict:
    return sources("apify_linkedin")[0]


def estimate() -> float:
    o = config()["apify"]["actors"].get(ACTOR, {})
    targets = _src()["targets"]
    est = len(targets) * 5 * float(o.get("price_per_item", 0.002))
    print(f"[apify_linkedin] {len(targets)} targets × ~5 posts × "
          f"${o.get('price_per_item', 0.002)}/post ≈ ${est:.2f}")
    return est


def run() -> int:
    _load_env()
    src = _src()
    apify_client.configure(config())
    seen = load_seen()
    new = 0
    cutoff_ms = (datetime.now(timezone.utc) - timedelta(days=8)).timestamp() * 1000
    for slug, org in src["targets"].items():
        if slug == "southparkcommons":                  # 회사 페이지 미지원 (스모크 실측 0건)
            continue
        payload = {"profileUrls": [f"https://www.linkedin.com/in/{slug}/"], "maxPosts": 10}
        try:
            items = apify_client.run_actor(ACTOR, payload, timeout=180)
        except RuntimeError as e:
            print(f"⚠️  {slug}: {e}")
            continue
        for p in items:
            posted = p.get("postedAt") or {}
            if posted.get("timestamp") and posted["timestamp"] < cutoff_ms:
                continue                                # 8일 컷 (액터는 날짜 무관 최신 N건)
            pid = str(p.get("id") or p.get("entityId") or "")
            key = f"li:{pid}"
            if not pid or key in seen:
                continue
            text = p.get("content") or ""
            author = (p.get("author") or {}).get("publicIdentifier") or ""
            is_repost = bool(author) and author.lower() != slug.lower()
            save_item({**src, "id": f"li-{slug}", "org": org}, key,
                      f"{slug}: {text[:60]}", p.get("linkedinUrl", ""),
                      posted.get("date", ""), text,
                      {"kind": "linkedin-post", "quote_verbatim": True,
                       "actual_author": author, "is_repost": is_repost})
            mark_seen(key)
            seen.add(key)
            new += 1
    record_health("linkedin-profiles", new, 200,
                  "" if new else "0건 — 액터 스키마 확인 (UNVERIFIED 상태)")
    print(f"[apify_linkedin] {new} new posts")
    return new


if __name__ == "__main__":
    if "--run" in sys.argv[1:]:
        run()
    else:
        estimate()
        print("(무과금 추정 모드 — 실행은 --run)")
