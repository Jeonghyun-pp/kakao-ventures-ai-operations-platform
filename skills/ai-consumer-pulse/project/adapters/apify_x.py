"""X 수집 — Apify Tweet Scraper V2 (apidojo~tweet-scraper), 주 1회 배치.

✅ VERIFIED(2026-07-17 스모크): 스키마 확정 — author.userName/text/createdAt/url/isReply 전부 일치.
   단, payload의 `start` 날짜 필터를 액터가 무시함(요청 07-09, 응답에 07-03 포함 실측)
   → run()에서 createdAt 클라이언트 필터 필수 (제거 금지).
   createdAt 포맷: "Fri Jul 03 13:53:34 +0000 2026" (트위터 레거시).
⚠️ 이 어댑터는 collect.sh(무료 트랙)에 넣지 않는다 — 스킬 Phase B에서
   `--estimate`로 예상 비용을 먼저 보여주고 사용자 y/n 후 실행하는 것이 Non-Negotiable.

사용:
  .venv/bin/python -m adapters.apify_x --estimate     # 비용 추정만 (무과금)
  .venv/bin/python -m adapters.apify_x --run          # 실제 실행 (과금)
  .venv/bin/python -m adapters.apify_x --run --handles omooretweets,venturetwins  # 부분 실행
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from adapters import apify_client
from core.common import config, save_item, sources
from core.state import load_seen, mark_seen, record_health

ACTOR = "apidojo~tweet-scraper"
PROJ = Path(__file__).resolve().parents[1]


def _load_env() -> None:
    """자격증명 정본은 $KV_CONFIG/.env 하나뿐이다 (스킬별 .env 를 다시 만들지 마라)."""
    from kv_core import secrets
    secrets.load()


def _src() -> dict:
    return sources("apify_x")[0]


def estimate(handles: list[str]) -> float:
    o = config()["apify"]["actors"].get(ACTOR, {})
    per_handle = 25                                     # 주간 평균 추정 트윗 수
    est = len(handles) * per_handle * float(o.get("price_per_item", 0.0004))
    print(f"[apify_x] {len(handles)} handles × ~{per_handle} tweets × "
          f"${o.get('price_per_item', 0.0004)}/tweet ≈ ${est:.2f}"
          f" (하드캡 maxTotalChargeUsd=${o.get('max_charge_usd', '?')})")
    return est


def run(handles: list[str] | None = None) -> int:
    _load_env()
    src = _src()
    handle_org = src["handles"]
    handles = handles or list(handle_org)
    apify_client.configure(config())

    warn = apify_client.preflight()
    if warn:
        print(f"⚠️  {warn}")
    before = apify_client.usage_usd()

    since = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%d")
    # Tweet Scraper V2 입력 (문서 기준 — UNVERIFIED): 핸들 배열 + 기간 + 최신순
    payload = {
        "twitterHandles": handles,
        "start": since,
        "sort": "Latest",
        "maxItems": len(handles) * 40,
        "includeSearchTerms": False,
    }
    items = apify_client.run_actor(ACTOR, payload, timeout=300)

    cutoff = datetime.now(timezone.utc) - timedelta(days=8)
    seen = load_seen()
    new = 0
    skipped_old = 0
    for t in items:
        # 액터가 start 필터를 무시함(스모크 실측) — createdAt으로 직접 컷
        try:
            created = datetime.strptime(t.get("createdAt", ""), "%a %b %d %H:%M:%S %z %Y")
            if created < cutoff:
                skipped_old += 1
                continue
        except ValueError:
            pass                                        # 파싱 불가 시 보수적으로 통과
        tid = str(t.get("id") or t.get("id_str") or t.get("url", "").split("/")[-1])
        key = f"x:{tid}"
        if not tid or key in seen:
            continue
        author = (t.get("author") or {}).get("userName") or t.get("username") or "?"
        org = handle_org.get(author, handle_org.get(author.lower(), "unknown"))
        text = t.get("text") or t.get("fullText") or ""
        save_item({**src, "id": f"x-{author}", "org": org}, key,
                  f"@{author}: {text[:60]}", t.get("url", ""),
                  t.get("createdAt", ""), text,
                  {"kind": "tweet", "author": author,
                   "is_reply": bool(t.get("isReply")), "is_retweet": bool(t.get("isRetweet")),
                   "quote_verbatim": True})
        mark_seen(key)
        seen.add(key)
        new += 1

    after = apify_client.usage_usd()
    if before is not None and after is not None:
        print(f"[apify_x] 실과금 delta: ${after - before:.2f} (추정 ${apify_client.spent_usd():.2f})")
    record_health("x-accounts", new, 200 if items else 0,
                  "" if items else "액터 0건 — 입력 스키마/차단 확인 (UNVERIFIED 상태)")
    print(f"[apify_x] {new} new tweets from {len(handles)} handles"
          + (f" (8일 초과 {skipped_old}건 컷)" if skipped_old else ""))
    return new


if __name__ == "__main__":
    args = sys.argv[1:]
    hs = None
    if "--handles" in args:
        hs = args[args.index("--handles") + 1].split(",")
    if "--run" in args:
        run(hs)
    else:
        estimate(hs or list(_src()["handles"]))
        print("(무과금 추정 모드 — 실행은 --run)")
