"""오디오 팟캐스트 어댑터 — Training Data / Minus One.

- Training Data: RSS 요약(300단어+챕터)이 1차 콘텐츠. 에피소드 링크가 sequoiacap.com이면
  서버렌더링 전문 트랜스크립트(실측 확인)를 이어붙인다. Whisper 불필요.
- Minus One: RSS 설명 저장. 유튜브 크로스포스트 자막 매칭은 v1.1 TODO —
  폴백 Whisper는 config.whisper.enabled=false로 기본 비활성 ($0.33/편).
"""
from __future__ import annotations

import re

from adapters.rss import _parse_items
from core.common import fetch, html_to_text, norm_url, polite_delay, save_item, sources, config
from core.state import load_seen, mark_seen, record_health


def _site_transcript(link: str) -> str:
    """sequoiacap.com 에피소드 페이지에서 전문 트랜스크립트 추출 시도."""
    if "sequoiacap.com" not in link:
        return ""
    status, body = fetch(link)
    if status != 200:
        return ""
    text = html_to_text(body)
    # 트랜스크립트 페이지는 매우 길다 — 8k자 미만이면 요약 페이지로 판정
    return text if len(text) > 8000 else ""


def collect() -> dict:
    seen = load_seen()
    cap = int(config()["caps"]["max_new_per_source"])
    summary = {}
    for src in sources("podcast"):
        status, body = fetch(src["url"])
        if status != 200:
            record_health(src["id"], 0, status, "피드 HTTP 오류")
            summary[src["id"]] = 0
            continue
        new = 0
        for it in _parse_items(body):
            if new >= cap:
                break
            key = norm_url(it["link"] or it["title"])
            if not key or key in seen:
                continue
            content = html_to_text(it["content"])
            transcript = _site_transcript(it["link"])
            extra = {"kind": "podcast", "quote_verbatim": bool(transcript)}
            if transcript:
                content = content + "\n\n[FULL TRANSCRIPT]\n" + transcript
                extra["transcript"] = "site"
            save_item(src, key, it["title"], it["link"], it["published"], content, extra)
            mark_seen(key)
            seen.add(key)
            new += 1
            polite_delay()
        record_health(src["id"], new, status)
        summary[src["id"]] = new
    return summary


if __name__ == "__main__":
    s = collect()
    print(f"[podcast] {sum(s.values())} new: " + ", ".join(f"{k}={v}" for k, v in s.items()))
