"""X(트위터) 어댑터 — Apify apidojo/tweet-scraper(V2), keyword-search.

기존 APIFY_TOKEN 재사용. 비용 $0.40/1k(쿼리당 최소 과금) → 키워드는 한 배열로 배치.
공식 X API v2는 무료티어 없음 → 회피. dedup은 이름 기반(app_id 미사용, cross-run 대조 일치).

세부: references/x.md
"""
from __future__ import annotations

from . import apify
from .base import Adapter, Candidate

ACTOR = "apidojo~tweet-scraper"


class XAdapter(Adapter):
    source = "X"

    def collect(self) -> list[Candidate]:
        ch = self.cfg["channels"].get("x", {})
        keywords = self.keywords.get("x", [])
        if not keywords:
            return []
        payload = {
            "searchTerms": keywords,             # 한 배열로 배치(과금 최소화)
            "sort": "Latest",
            "tweetLanguage": ch.get("lang", "ko"),
            "maxItems": ch.get("max_items", 300),
        }
        items = apify.run_actor(ACTOR, payload)
        return [self._to_candidate(p) for p in items]

    def _to_candidate(self, p: dict) -> Candidate:
        author = p.get("author") or {}
        handle = author.get("userName") or author.get("username") or ""
        name = author.get("name") or handle
        return Candidate(
            source=self.source,
            name=name,
            url=p.get("url") or p.get("twitterUrl", ""),
            founder=f"@{handle}" if handle else name,
            released_at=p.get("createdAt") or p.get("created_at", ""),
            raw_text=p.get("text") or p.get("fullText", ""),
            signals={
                "tweet_id": p.get("id") or p.get("id_str"),
                "likes": p.get("likeCount") or p.get("likes"),
                "retweets": p.get("retweetCount") or p.get("retweets"),
                "followers": author.get("followers"),
                "verified": author.get("isVerified") or author.get("verified"),
            },
        )
