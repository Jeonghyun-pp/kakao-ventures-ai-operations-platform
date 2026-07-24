"""RSS/Atom 어댑터 — 검증된 피드 ~20개 폴링.

함정 목록(references/sources-verified.md에도 집약):
- appfigures: /resources/rss/insights 만 작동 (흔한 경로 404)
- sarahtavel: www 필수 (apex 도메인 연결 실패)
- neuron: beehiiv 전용 URL (theneurondaily.com/feed는 404)
- hnrss: 캐시 ~55분 — 주 1회 폴링이면 무관
light 트랙 소스는 제목+요약 500자만 저장 (Pass 0 분리 규칙과 일관).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

from core.common import fetch, html_to_text, norm_url, polite_delay, save_item, sources, config
from core.state import load_seen, mark_seen, record_health

# fetch_body 소스에서 이 길이 미만이면 요약으로 보고 개별 글 본문을 받는다.
# 실측(2026-07-20): 요약형 피드는 100~500자, 전문형은 2,000자 이상이라 1,200이 안전한 경계다.
BODY_MIN = 1200

NS = {"atom": "http://www.w3.org/2005/Atom",
      "content": "http://purl.org/rss/1.0/modules/content/"}


def _parse_items(xml_text: str) -> list[dict]:
    """RSS 2.0 <item> 과 Atom <entry> 를 통일 포맷으로."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        # 흔한 원인: 잘못된 제어문자. 한 번 세척 후 재시도.
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", xml_text)
        try:
            root = ET.fromstring(cleaned.encode("utf-8"))
        except ET.ParseError:
            return []
    out = []
    for it in root.iter("item"):                       # RSS 2.0
        enc = it.find("content:encoded", NS)
        out.append({
            "title": (it.findtext("title") or "").strip(),
            "link": (it.findtext("link") or "").strip(),
            "published": (it.findtext("pubDate") or "").strip(),
            "content": (enc.text if enc is not None and enc.text else it.findtext("description") or ""),
        })
    if not out:                                        # Atom
        for e in root.iter(f"{{{NS['atom']}}}entry"):
            link = ""
            for l in e.findall(f"{{{NS['atom']}}}link"):
                if l.get("rel") in (None, "alternate"):
                    link = l.get("href", "")
            out.append({
                "title": (e.findtext(f"{{{NS['atom']}}}title") or "").strip(),
                "link": link.strip(),
                "published": (e.findtext(f"{{{NS['atom']}}}published")
                              or e.findtext(f"{{{NS['atom']}}}updated") or "").strip(),
                "content": (e.findtext(f"{{{NS['atom']}}}content")
                            or e.findtext(f"{{{NS['atom']}}}summary") or ""),
            })
    return out


def collect() -> dict:
    seen = load_seen()
    cap = int(config()["caps"]["max_new_per_source"])
    summary = {}
    for src in sources("rss"):
        status, body = fetch(src["url"], ua=src.get("ua"))
        if status != 200:
            record_health(src["id"], 0, status, "피드 HTTP 오류")
            summary[src["id"]] = 0
            continue
        items = _parse_items(body)
        new = 0
        for it in items:
            if new >= cap:
                break
            key = norm_url(it["link"] or it["title"])
            if not key or key in seen:
                continue
            text = html_to_text(it["content"])
            if src.get("track") == "light":
                # ⚠️ light가 fetch_body보다 우선한다(elif). 둘을 같이 주면 fetch_body는 무시된다 —
                #    light의 목적이 토큰 절약인데 본문을 받아오면 모순이기 때문.
                #    본문이 필요한 소스는 sources.yaml에서 track: light 를 떼야 한다.
                text = text[:500]                      # light: 제목+도입부만 — 토큰 1/10
            elif src.get("fetch_body") and it["link"] and len(text) < BODY_MIN:
                # 🔴 [2026-07-20] 요약만 주는 피드 보강.
                #   Rest of World·Sixth Tone 등은 1인칭 인터뷰 원문이 가치의 전부인데
                #   RSS에는 리드 2~3문장만 실린다. Pass 1 시험에서 Sixth Tone이 0건을 반환한 원인.
                #   전문이 이미 실린 피드는 이 분기를 타지 않는다(len >= BODY_MIN).
                bst, bbody = fetch(it["link"], ua=src.get("ua"))
                if bst == 200:
                    full = html_to_text(bbody)
                    if len(full) > len(text):
                        text = full
                polite_delay()                          # 개별 글 fetch — 매너 딜레이
            save_item(src, key, it["title"], it["link"], it["published"], text)
            mark_seen(key)
            seen.add(key)
            new += 1
        record_health(src["id"], new, status)
        summary[src["id"]] = new
        polite_delay()
    return summary


if __name__ == "__main__":
    s = collect()
    print(f"[rss] {sum(s.values())} new / {len(s)} sources: "
          + ", ".join(f"{k}={v}" for k, v in s.items() if v))
