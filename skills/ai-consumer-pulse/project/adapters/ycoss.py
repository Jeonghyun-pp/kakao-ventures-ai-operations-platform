"""yc-oss 어댑터 — YC 배치별 회사 JSON (Algolia 인덱스 매일 미러, 무료·무인증).

consumer AI 신생기업 최조기 시그널. 트랙 1(포폴 분류 파이프라인)과도 공유 가능한 데이터.
배치 이름은 sources.yaml의 batches 리스트 — 신규 배치가 뜨면 거기 추가.
API 경로는 https://github.com/yc-oss/api 문서 기준: /api/batches/<slug>.json
"""
from __future__ import annotations

import json
import re

from core.common import fetch, save_item, sources
from core.state import load_seen, mark_seen, record_health


def _batch_slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def collect() -> dict:
    summary = {}
    seen = load_seen()
    for src in sources("ycoss"):
        base = src["url"].rstrip("/")
        total_new, status_any = 0, 0
        for batch in src.get("batches", []):
            url = f"{base}/batches/{_batch_slug(batch)}.json"
            status, body = fetch(url)
            status_any = status
            if status != 200:
                continue                      # 아직 안 뜬 배치는 404 — 정상
            try:
                companies = json.loads(body)
            except json.JSONDecodeError:
                continue
            for c in companies:
                key = f"yc:{c.get('slug') or c.get('id') or c.get('name')}"
                if key in seen:
                    continue
                text = (f"{c.get('name', '')} — {c.get('one_liner', '')}\n"
                        f"batch: {c.get('batch', batch)} | tags: {', '.join(c.get('tags', []))}\n"
                        f"{c.get('long_description', '')[:800]}")
                save_item(src, key, c.get("name", "?"), c.get("website") or c.get("url", ""),
                          batch, text, {"kind": "yc-company", "batch": batch,
                                        "tags": c.get("tags", [])})
                mark_seen(key)
                seen.add(key)
                total_new += 1
        record_health(src["id"], total_new, status_any or 0,
                      "" if total_new else "신규 배치 미공개 또는 API 경로 변경 확인 필요")
        summary[src["id"]] = total_new
    return summary


if __name__ == "__main__":
    s = collect()
    print(f"[ycoss] {sum(s.values())} new companies")
