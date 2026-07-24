"""네이버(블로그+카페) 어댑터 — Naver Open API, keyword-search.

무료 자가발급 키(developers.naver.com). 무료 25,000req/day. OR/boolean 불가라 키워드당 1콜.
노이즈 많음 → recall 높고 precision은 judge가. <b> 태그·HTML 엔티티 strip 필요.

세부: references/naver.md
"""
from __future__ import annotations

import os
from datetime import date

import requests

from . import _util
from .base import Adapter, Candidate

ENDPOINT = "https://openapi.naver.com/v1/search/{kind}.json"


class NaverAdapter(Adapter):
    source = "네이버"

    def collect(self) -> list[Candidate]:
        cid = os.environ.get("NAVER_CLIENT_ID")
        secret = os.environ.get("NAVER_CLIENT_SECRET")
        if not (cid and secret):
            raise RuntimeError("NAVER_CLIENT_ID/SECRET 없음 — .env 확인 (references/naver.md)")

        ch = self.cfg["channels"].get("naver", {})
        kinds = ch.get("endpoints", ["blog", "cafearticle"])
        display = ch.get("display", 100)
        keywords = self.keywords.get("naver", [])
        headers = {"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": secret}

        out: list[Candidate] = []
        for kw in keywords:
            for kind in kinds:
                items = self._search(kind, kw, display, headers)
                for it in items:
                    out.append(self._to_candidate(kind, kw, it))
        return out

    def _search(self, kind: str, query: str, display: int, headers: dict) -> list[dict]:
        r = requests.get(ENDPOINT.format(kind=kind), headers=headers,
                         params={"query": query, "display": display, "sort": "date"},
                         timeout=30)
        r.raise_for_status()
        return r.json().get("items", [])

    def _to_candidate(self, kind: str, query: str, it: dict) -> Candidate:
        title = _util.strip_html(it.get("title", ""))
        desc = _util.strip_html(it.get("description", ""))
        # blog엔 postdate(YYYYMMDD), cafe엔 없음 → 실행일 폴백(sort=date라 최신순)
        postdate = it.get("postdate", "")
        released = f"{postdate[:4]}-{postdate[4:6]}-{postdate[6:8]}" if len(postdate) == 8 \
            else date.today().isoformat()
        return Candidate(
            source=f"네이버{'블로그' if kind == 'blog' else '카페'}",
            name=title,
            url=it.get("link", ""),
            one_liner=desc,
            founder=it.get("bloggername", "") or it.get("cafename", ""),
            released_at=released,
            raw_text=(title + "\n" + desc).strip(),
            signals={"query": query, "kind": kind},
        )
