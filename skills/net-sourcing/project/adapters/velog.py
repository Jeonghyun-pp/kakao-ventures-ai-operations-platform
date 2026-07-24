"""velog 어댑터 — 비공식 공개 GraphQL(v3.velog.io), keyword/tag.

무자격. 태그 피드(고신호: 사이드프로젝트/창업 등) + 키워드 검색 혼합.
searchPosts는 관련도순이라 released_at 최신순 recency 필터 필수(오래된 글 제거).
저빈도 호출(ToS 문서 없음).

세부: references/velog.md
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from .base import Adapter, Candidate

GRAPHQL = "https://v3.velog.io/graphql"

_POST_FIELDS = "id title short_description url_slug released_at likes user { username }"
_TAG_QUERY = (
    "query($input: GetPostsInput!) { posts(input: $input) { " + _POST_FIELDS + " } }"
)
_SEARCH_QUERY = (
    "query($input: GetSearchPostsInput!) { searchPosts(input: $input) { posts { "
    + _POST_FIELDS + " } } }"
)


class VelogAdapter(Adapter):
    source = "velog"

    def collect(self) -> list[Candidate]:
        ch = self.cfg["channels"].get("velog", {})
        tags = ch.get("tags", ["사이드프로젝트", "창업", "개발"])
        recent_days = ch.get("recent_days", 30)
        limit = ch.get("limit", 50)
        keywords = self.keywords.get("velog", [])
        cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)

        out: list[Candidate] = []
        # 태그 피드 — 이미 최신순
        for tag in tags:
            data = self._gql(_TAG_QUERY, {"input": {"tag": tag, "limit": limit}})
            for p in data.get("posts", []) or []:
                out.append(self._to_candidate(p, signal={"tag": tag}))
        # 키워드 검색 — 관련도순 → recency 필터
        for kw in keywords:
            data = self._gql(_SEARCH_QUERY,
                             {"input": {"keyword": kw, "limit": limit, "offset": 0}})
            posts = (data.get("searchPosts") or {}).get("posts", []) or []
            for p in posts:
                if self._is_recent(p.get("released_at"), cutoff):
                    out.append(self._to_candidate(p, signal={"query": kw}))
        return out

    def _gql(self, query: str, variables: dict) -> dict:
        """비공식 엔드포인트라 일시적 5xx가 잦음 → 개별 쿼리 실패는 삼키고 빈 dict."""
        try:
            r = requests.post(GRAPHQL, json={"query": query, "variables": variables},
                              headers={"Origin": "https://velog.io"}, timeout=30)
            r.raise_for_status()
            return r.json().get("data", {}) or {}
        except requests.RequestException as e:
            print(f"  · velog 쿼리 스킵({e.__class__.__name__})")
            return {}

    @staticmethod
    def _is_recent(released_at: str | None, cutoff: datetime) -> bool:
        if not released_at:
            return False
        try:
            dt = datetime.fromisoformat(released_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        return dt >= cutoff

    def _to_candidate(self, p: dict, signal: dict) -> Candidate:
        user = (p.get("user") or {}).get("username", "")
        slug = p.get("url_slug", "")
        signals = {"likes": p.get("likes")}
        signals.update(signal)
        return Candidate(
            source=self.source,
            name=p.get("title", ""),
            url=f"https://velog.io/@{user}/{slug}" if user else "",
            founder=user,
            released_at=p.get("released_at", ""),
            one_liner=p.get("short_description", "") or "",
            raw_text=(p.get("title", "") + "\n" + (p.get("short_description") or "")).strip(),
            signals=signals,
        )
