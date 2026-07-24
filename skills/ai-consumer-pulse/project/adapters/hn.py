"""Hacker News 어댑터 — Algolia HN API (무료·무인증).

🔴 [2026-07-20] hnrss.org 대체. 실측으로 확인된 hnrss 고장:
     - hnrss.org/launches → HTTP 200인데 480바이트 빈 채널(item 0개). 3회 재시도 모두 동일.
       health의 consecutive_zero가 4까지 올라가 있었다 — 전형적인 '조용한 실패'다.
     - hnrss.org/show?q=AI&points=50 → 502. 이전 실행에서도 200/502가 혼재했다.
   같은 시각 Algolia는 정상이었다(Launch HN 1,188건, 최신 2026-07-16).
   hnrss는 커뮤니티 운영 미러라 가용성 보장이 없다 — 1차 소스인 Algolia를 직접 쓴다.

sources.yaml 스펙:
    adapter: hn
    query:       Algolia 검색어 (예: "Launch HN")
    hn_tags:     Algolia tags 파라미터 (기본 "story")
    min_points:  이 점수 미만은 버림 (0이면 필터 없음)
    hits:        1회 요청 건수 (기본 30)

Algolia는 search_by_date로 최신순을 준다 — 주 1회 실행에 30건이면 충분히 겹친다.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode

from core.common import config, fetch, polite_delay, save_item, sources
from core.state import load_seen, mark_seen, record_health

API = "https://hn.algolia.com/api/v1/search_by_date"


def collect() -> dict:
    seen = load_seen()
    cap = int(config()["caps"]["max_new_per_source"])
    summary = {}

    for src in sources("hn"):
        params = {
            "query": src.get("query", ""),
            "tags": src.get("hn_tags", "story"),
            "hitsPerPage": int(src.get("hits", 30)),
        }
        status, body = fetch(f"{API}?{urlencode(params)}")
        if status != 200:
            record_health(src["id"], 0, status, "Algolia HTTP 오류")
            summary[src["id"]] = 0
            continue
        try:
            hits = json.loads(body).get("hits", [])
        except json.JSONDecodeError:
            record_health(src["id"], 0, status, "Algolia JSON 파싱 실패")
            summary[src["id"]] = 0
            continue

        minp = int(src.get("min_points", 0))
        new = 0
        for h in hits:
            if new >= cap:
                break
            pts = h.get("points") or 0
            if pts < minp:
                continue
            oid = h.get("objectID")
            key = f"hn:{oid}"
            if not oid or key in seen:
                continue
            title = h.get("title") or h.get("story_title") or "?"
            # 외부 링크가 있으면 그쪽, 없으면(Ask/Launch 본문형) HN 스레드
            link = h.get("url") or f"https://news.ycombinator.com/item?id={oid}"
            text = (f"{title}\n"
                    f"points: {pts} | comments: {h.get('num_comments') or 0} | by {h.get('author')}\n"
                    f"{(h.get('story_text') or '')[:2000]}")
            save_item(src, key, title, link, h.get("created_at", ""), text,
                      {"kind": "hn", "points": pts,
                       "num_comments": h.get("num_comments") or 0,
                       "hn_url": f"https://news.ycombinator.com/item?id={oid}"})
            mark_seen(key)
            seen.add(key)
            new += 1

        record_health(src["id"], new, status)
        summary[src["id"]] = new
        polite_delay()

    return summary


if __name__ == "__main__":
    s = collect()
    print(f"[hn] {sum(s.values())} new / {len(s)} sources: "
          + ", ".join(f"{k}={v}" for k, v in s.items()))
