"""Product Hunt 어댑터 — 공식 GraphQL v2, feed-parse.

무료 developer token(자가발급, 무기한). free-text 검색 없음 → 날짜창 전량 pull 후
한국 필터는 judge/enrich(글로벌 캐치용, 영어 위주). rate 6250 complexity/15min.

세부: references/producthunt.md
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import requests

from .base import Adapter, Candidate

GRAPHQL = "https://api.producthunt.com/v2/api/graphql"
QUERY = """
query($postedAfter: DateTime, $first: Int, $after: String) {
  posts(postedAfter: $postedAfter, order: NEWEST, first: $first, after: $after) {
    edges { node {
      name tagline description url votesCount featuredAt createdAt
      makers { name username }
      topics { edges { node { name } } }
    } }
    pageInfo { hasNextPage endCursor }
  }
}
"""


class ProductHuntAdapter(Adapter):
    source = "프로덕트헌트"

    def collect(self) -> list[Candidate]:
        token = os.environ.get("PRODUCTHUNT_TOKEN")
        if not token:
            raise RuntimeError("PRODUCTHUNT_TOKEN 없음 — .env 확인 (references/producthunt.md)")

        ch = self.cfg["channels"].get("producthunt", {})
        lookback = ch.get("lookback_days", 7)
        first = ch.get("first", 50)
        posted_after = (datetime.now(timezone.utc) - timedelta(days=lookback)).isoformat()
        headers = {"Authorization": f"Bearer {token}"}

        out: list[Candidate] = []
        after = None
        while True:
            variables = {"postedAfter": posted_after, "first": first, "after": after}
            r = requests.post(GRAPHQL, headers=headers,
                             json={"query": QUERY, "variables": variables}, timeout=60)
            r.raise_for_status()
            posts = (r.json().get("data", {}) or {}).get("posts", {})
            for edge in posts.get("edges", []):
                out.append(self._to_candidate(edge.get("node", {})))
            page = posts.get("pageInfo", {})
            if not page.get("hasNextPage"):
                break
            after = page.get("endCursor")
        return out

    def _to_candidate(self, node: dict) -> Candidate:
        makers = node.get("makers") or []
        topics = [e.get("node", {}).get("name") for e in
                  (node.get("topics") or {}).get("edges", [])]
        return Candidate(
            source=self.source,
            name=node.get("name", ""),
            url=node.get("url", ""),
            one_liner=node.get("tagline", ""),
            founder=makers[0].get("name", "") if makers else "",
            released_at=node.get("featuredAt") or node.get("createdAt", ""),
            raw_text=(node.get("tagline", "") + "\n" + (node.get("description") or "")).strip(),
            signals={
                "votes": node.get("votesCount"),
                "topics": [t for t in topics if t],
                "makers": [m.get("username") for m in makers],
            },
        )
