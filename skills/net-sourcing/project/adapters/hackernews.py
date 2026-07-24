"""Hacker News(Show HN) 어댑터 — Algolia 검색 API, feed-parse.

무인증·무료. lookback 창의 Show HN 글을 전량 pull. 한국 필터는 judge/enrich에 위임
(글로벌 캐치용). 키워드 불필요 — Show HN 태그 자체가 "출시 직후" 신호.

세부: references/hackernews.md
"""
from __future__ import annotations

import time

import requests

from . import _util
from .base import Adapter, Candidate

SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
ITEM = "https://news.ycombinator.com/item?id={id}"


class HackerNewsAdapter(Adapter):
    source = "해커뉴스"

    def collect(self) -> list[Candidate]:
        ch = self.cfg["channels"].get("hackernews", {})
        lookback = ch.get("lookback_days", 7)
        since = int(time.time()) - lookback * 86400

        out: list[Candidate] = []
        page = 0
        while True:
            params = {
                "tags": "show_hn",
                "numericFilters": f"created_at_i>{since}",
                "hitsPerPage": 100,
                "page": page,
            }
            r = requests.get(SEARCH, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            hits = data.get("hits", [])
            for h in hits:
                out.append(self._to_candidate(h))
            page += 1
            if page >= data.get("nbPages", 0) or not hits:
                break
        return out

    def _to_candidate(self, h: dict) -> Candidate:
        obj_id = h.get("objectID", "")
        title = h.get("title", "")
        return Candidate(
            source=self.source,
            name=title,
            url=h.get("url") or ITEM.format(id=obj_id),
            founder=h.get("author", ""),
            released_at=h.get("created_at", ""),
            raw_text=(title + "\n" + _util.strip_html(h.get("story_text") or "")).strip(),
            signals={
                "hn_id": obj_id,
                "points": h.get("points"),
                "num_comments": h.get("num_comments"),
            },
        )
