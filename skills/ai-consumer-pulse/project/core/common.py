"""공통 유틸 — 설정/레지스트리 로딩, 주차 키, HTTP, HTML→텍스트, 아이템 저장.

설계 원칙 (signal-system-design.md):
- 수집은 무LLM·결정적. 원본은 raw/<주차>/<source_id>/ 에 불변 저장.
- seen 키는 소스가 아니라 **콘텐츠 식별자**(정규화 URL/영상ID/트윗ID) 기준 —
  같은 글이 두 경로(예: kv rss + thought-provoking scrape, a16z 이중경로)로 와도 1회만 저장.
"""
from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
import yaml

PROJ = Path(__file__).resolve().parents[1]
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# 일부 사이트는 브라우저 UA를 AI 크롤러로 보고 타르핏(마르코프 생성 가짜 본문)을 반환한다.
# pivot-to-ai.com이 실증 사례 — 200 OK에 정상 XML이라 오류로도 안 잡힌다.
# sources.yaml 에서 `ua: minimal` 로 소스별 우회.
UA_MINIMAL = "curl/8.7.1"
UA_PRESETS = {"default": UA, "minimal": UA_MINIMAL}

_cfg = None
_sources = None


def config() -> dict:
    global _cfg
    if _cfg is None:
        _cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    return _cfg


def data_root() -> Path:
    """실데이터 루트. config에 값이 있으면 그것, 없으면 $KV_DATA/ai-consumer-pulse.

    config를 우선하는 이유는 이전 설치가 깨지지 않게 하기 위해서다. 새 설치에서는
    비워두는 것이 정본이다 — 절대경로가 config에 박히면 머신 이전이 불가능해진다.
    """
    from kv_core import paths
    v = (config().get("paths") or {}).get("data_root")
    return Path(v).expanduser() if v else paths.data("ai-consumer-pulse")


def sources(adapter: str | None = None) -> list[dict]:
    """sources.yaml 로드 (+defaults 병합). adapter로 필터 가능."""
    global _sources
    if _sources is None:
        doc = yaml.safe_load((data_root() / "sources.yaml").read_text(encoding="utf-8"))
        defaults = doc.get("defaults", {})
        _sources = [{**defaults, **s} for s in doc["sources"]]
    return [s for s in _sources if adapter is None or s.get("adapter") == adapter]


def org_groups() -> dict:
    doc = yaml.safe_load((data_root() / "sources.yaml").read_text(encoding="utf-8"))
    return doc.get("org_groups", {})


def week_key(dt: datetime | None = None) -> str:
    """ISO 주차 키 — 예: 2026-W29."""
    d = (dt or datetime.now(timezone.utc)).isocalendar()
    return f"{d.year}-W{d.week:02d}"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch(url: str, timeout: int = 30, as_bytes: bool = False, ua: str | None = None):
    """UA 헤더 포함 GET. (status_code, text|bytes) 반환 — 예외도 status로 수렴.

    ua: sources.yaml의 프리셋 키("minimal") 또는 UA 문자열 자체. None이면 기본 Chrome UA.
    """
    agent = UA_PRESETS.get(ua, ua) if ua else UA
    try:
        r = requests.get(url, headers={"User-Agent": agent}, timeout=timeout)
        if not as_bytes:
            # 🔴 [2026-07-20] 한글·일본어 소스가 통째로 깨지던 버그.
            #   requests는 Content-Type에 charset이 없으면 RFC 2616을 따라 text/*를 ISO-8859-1로
            #   가정한다. 지피터스 본문이 "ë©í°ìì´ì í¸"처럼 저장돼 Pass 1이 판독 불가였다.
            #   서버가 charset을 명시하지 않았을 때만 실제 바이트로 추정한다(명시된 경우는 존중).
            declared = "charset=" in (r.headers.get("Content-Type") or "").lower()
            if not declared:
                r.encoding = r.apparent_encoding or "utf-8"
        return r.status_code, (r.content if as_bytes else r.text)
    except Exception as e:  # noqa: BLE001 — 어댑터가 소스 하나 때문에 죽으면 안 됨
        return 0, f"(fetch error: {e})"


def polite_delay():
    time.sleep(float(config()["caps"].get("request_delay_secs", 2)))


_TAG = re.compile(r"<(script|style|nav|header|footer|svg)[^>]*>.*?</\1>", re.S | re.I)
_ALLTAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\n{3,}")


def html_to_text(raw: str) -> str:
    """단순 태그 제거 — LLM 입력용. 완벽한 파싱보다 견고함 우선."""
    t = _TAG.sub(" ", raw)
    t = re.sub(r"<br\s*/?>|</p>|</div>|</h[1-6]>|</li>", "\n", t, flags=re.I)
    t = _ALLTAG.sub(" ", t)
    t = html.unescape(t)
    t = re.sub(r"[ \t]+", " ", t)
    return _WS.sub("\n\n", t).strip()


def norm_url(u: str) -> str:
    """seen 키용 URL 정규화 — 쿼리/트레일링 슬래시/프로토콜 차이 제거."""
    u = u.split("?")[0].split("#")[0].rstrip("/")
    return re.sub(r"^https?://(www\.)?", "", u).lower()


def save_item(src: dict, key: str, title: str, url: str, published: str,
              content: str, extra: dict | None = None) -> Path:
    """아이템을 raw/<주차>/<source_id>/에 저장. 파일명은 key 해시 기반."""
    wk = week_key()
    d = data_root() / "raw" / wk / src["id"]
    d.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", key.lower())[:80].strip("-") or "item"
    p = d / f"{slug}.json"
    p.write_text(json.dumps({
        "key": key, "source_id": src["id"], "org": src.get("org"),
        "tier": src.get("tier", 1), "track": src.get("track", "full"),
        "adapter": src.get("adapter"), "title": title, "url": url,
        "published": published, "fetched_at": now_iso(),
        "content": content, **(extra or {}),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    return p
