"""디스콰이엇 어댑터 — 제품 목록 list-diff.

disquiet.io는 Rails 서버렌더 앱. 목록 페이지 HTML의 제품 카드(<a href="/products/{slug}">이름</a>
+ 뒤따르는 태그라인 텍스트)를 stdlib로 파싱한다. Chrome User-Agent 헤더 필수(없으면
Cloudflare 403 — 과거 "403 난공불락"의 실제 원인).

스냅샷 대비 신규 slug만 signals.new_launch=true. 무자격·쿠키리스. 정중한 rate-limit.

세부: references/disquiet.md
"""
from __future__ import annotations

import re

import requests

from . import _util
from .base import Adapter, Candidate

LIST_URL = "https://disquiet.io/products?page={page}"
BASE = "https://disquiet.io"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
SNAP_PREFIX = "disquiet"
SNAP_KEY = "products"

_ANCHOR = re.compile(r'href="/products/([A-Za-z0-9_-]+)"\s*>(.*?)</a>', re.S)


class DisquietAdapter(Adapter):
    source = "디스콰이엇"

    def collect(self) -> list[Candidate]:
        ch = self.cfg["channels"].get("disquiet", {})
        max_pages = ch.get("max_pages", 5)
        lookback = ch.get("new_entry_lookback_days", 7)

        cards = self._parse_pages(max_pages)             # slug -> {name, tagline}
        prev = _util.load_prev_snapshot(SNAP_PREFIX, SNAP_KEY, lookback)
        _util.save_snapshot(SNAP_PREFIX, SNAP_KEY, list(cards.keys()))

        out: list[Candidate] = []
        for slug, info in cards.items():
            out.append(Candidate(
                source=self.source,
                name=info["name"],
                url=f"{BASE}/products/{slug}",
                one_liner=info["tagline"],
                raw_text=(info["name"] + "\n" + info["tagline"]).strip(),
                signals={
                    "slug": slug,
                    "new_launch": bool(prev) and slug not in prev,
                },
            ))
        return out

    def _parse_pages(self, max_pages: int) -> dict[str, dict]:
        cards: dict[str, dict] = {}
        for page in range(1, max_pages + 1):
            html = self._fetch(page)
            self._parse_cards(html, cards)
        return cards

    def _fetch(self, page: int) -> str:
        r = requests.get(LIST_URL.format(page=page),
                         headers={"User-Agent": UA}, timeout=30)
        r.raise_for_status()
        return r.text

    @staticmethod
    def _parse_cards(html: str, cards: dict[str, dict]) -> None:
        """제품 앵커 = 이름, 앵커 뒤~다음 앵커/아이콘 전까지 텍스트 = 태그라인.

        같은 slug가 로고/텍스트 앵커로 중복 등장 → 비어있는 필드만 채워 merge.
        """
        matches = list(_ANCHOR.finditer(html))
        for i, m in enumerate(matches):
            slug = m.group(1)
            name = _util.strip_html(m.group(2))
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else start + 500
            window = re.split(r"<svg", html[start:end], maxsplit=1)[0]
            tagline = _util.strip_html(window)

            entry = cards.setdefault(slug, {"name": "", "tagline": ""})
            if name and not entry["name"]:
                entry["name"] = name
            if tagline and not entry["tagline"]:
                entry["tagline"] = tagline
