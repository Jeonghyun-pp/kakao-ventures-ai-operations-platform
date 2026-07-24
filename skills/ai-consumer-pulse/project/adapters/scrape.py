"""Scrape 어댑터 — RSS 없는 6곳. 전부 정적 HTML 실측(JS 렌더링 불필요).

경로 2종:
- pattern: 리스팅 페이지에서 기사 href 정규식 추출 → 신규 URL만 본문 fetch
- sitemap: sitemap.xml의 <loc>+<lastmod>로 신규 감지 (RSS급 폴링 — bitkraft/sensortower)
a16z는 **이중경로**(pattern + sitemap) — 두 경로 결과가 어긋나면 audit이 경고할 수 있게
diff를 health note에 남긴다 (한쪽 파손을 침묵 없이 감지).
"""
from __future__ import annotations

import re

from core.common import fetch, html_to_text, norm_url, polite_delay, save_item, sources, config
from core.state import load_seen, mark_seen, record_health


def _links_from_pattern(src: dict, body: str) -> list[str]:
    pat = src.get("pattern")
    if not pat:
        return []
    prefix = src.get("link_prefix", "")
    out = []
    for m in re.finditer(pat, body):
        link = m.group(1)
        if prefix and not link.startswith("http"):
            link = prefix + link.lstrip("/") if prefix.endswith("/") else prefix + link
        out.append(link)
    return list(dict.fromkeys(out))                    # 순서 보존 dedupe


def _links_from_sitemap(src: dict) -> list[str]:
    status, body = fetch(src["sitemap"], ua=src.get("ua"))
    if status != 200:
        return []
    locs = re.findall(r"<loc>([^<]+)</loc>", body)
    filt = src.get("sitemap_filter")
    if filt:
        locs = [u for u in locs if filt in u]
    return locs[:60]                                    # sitemap은 전체 이력 — 앞쪽만


def _fetch_article(url: str, ua: str | None = None) -> tuple[int, str, str]:
    """(status, title, text)"""
    status, body = fetch(url, ua=ua)
    if status != 200:
        return status, "", ""
    m = re.search(r"<title[^>]*>([^<]+)</title>", body, re.I)
    title = (m.group(1).strip() if m else url)
    return status, title, html_to_text(body)


def collect() -> dict:
    seen = load_seen()
    cap = int(config()["caps"]["max_new_per_source"])
    summary = {}
    for src in sources("scrape"):
        # 🔴 [2026-07-20] 소스 하나의 예외가 어댑터 전체를 죽이던 버그를 여기서 막는다.
        #   실제 사고: sitemap만 있고 url이 없는 소스(gpters-nocode 등)에서 KeyError가 나
        #   scrape 17개가 통째로 0건이 됐다. 한 소스의 문제는 그 소스만 0건이어야 한다.
        try:
            summary[src["id"]] = _collect_one(src, seen, cap)
        except Exception as e:  # noqa: BLE001
            record_health(src["id"], 0, 0, f"어댑터 예외: {type(e).__name__}: {e}"[:160])
            summary[src["id"]] = 0
        polite_delay()
    return summary


def _collect_one(src: dict, seen: set, cap: int) -> int:
    links, note = [], ""
    status = 200
    # url은 없을 수 있다(sitemap 전용 소스) — 있을 때만 패턴 경로를 탄다.
    base = src.get("url") or ""
    if src.get("pattern") and base:
        status, body = fetch(base, ua=src.get("ua"))
        if status == 200:
            links = _links_from_pattern(src, body)
            if not links:
                note = "패턴 매치 0건 — 사이트 개편 의심 (링크 0개 = 실패 이벤트)"
    if src.get("sitemap"):
        sm = _links_from_sitemap(src)
        if src.get("pattern") and links:                # a16z 이중경로 diff
            only_sm = set(map(norm_url, sm)) - set(map(norm_url, links))
            if len(only_sm) > 10:
                note = f"이중경로 diff: sitemap에만 {len(only_sm)}건 — href 패턴 파손 의심"
        links = list(dict.fromkeys(links + sm))
    base_key = norm_url(base) if base else None
    new = 0
    for link in links:
        if new >= cap:
            break
        key = norm_url(link)
        if not key or key in seen or (base_key and key == base_key):
            continue
        st, title, text = _fetch_article(link, ua=src.get("ua"))
        if st != 200 or len(text) < 200:                # 목차/태그 페이지 오탐 컷
            continue
        save_item(src, key, title, link, "", text)
        mark_seen(key)
        seen.add(key)
        new += 1
        polite_delay()
    record_health(src["id"], new, status, note)
    return new


if __name__ == "__main__":
    s = collect()
    print(f"[scrape] {sum(s.values())} new / {len(s)} sources: "
          + ", ".join(f"{k}={v}" for k, v in s.items() if v))
