"""HuggingFace 데이터셋 어댑터 — 릴리스 폴더 신규 감지 (무인증·무료).

🟢 [2026-07-21] Anthropic Economic Index 자동화용. manual→자동 승격.
   랜딩(anthropic.com/economic-index)은 JS 렌더라 'Loading data'만 나와 수집 불가였는데,
   데이터는 HF 데이터셋에 릴리스 폴더로 쌓인다:
     GET https://huggingface.co/api/datasets/<repo>/tree/main?expand=true
       → type=="directory" 이고 이름이 release_YYYY_MM_DD 인 폴더 집합
   신규 폴더가 생기면(집합 diff) 그 릴리스의 문서를 raw로 가져온다:
     https://huggingface.co/datasets/<repo>/raw/main/<release_dir>/data_documentation.md

⚠️ 트리거는 폴더명 집합 diff로만 한다. 상단 sha/lastModified는 README 편집에도 flip하고,
   폴더 lastCommit.date는 back-date되므로 커밋날짜 정렬 금지(실측 2026-07-21).
   릴리스가 분기~반기라 대부분 실행은 no-change(=정상, 침묵 아님).

sources.yaml 스펙:
    adapter: hfdataset
    repo:      "Anthropic/EconomicIndex"
    release_re: "^release_\\d{4}_\\d{2}_\\d{2}$"   (기본값 동일)
    doc_file:  "data_documentation.md"            (릴리스 폴더 내 문서 파일명)
"""
from __future__ import annotations

import json
import re

from core.common import fetch, save_item, sources
from core.state import load_seen, mark_seen, record_health

API = "https://huggingface.co/api/datasets/{repo}/tree/main?expand=true"
RAW = "https://huggingface.co/datasets/{repo}/raw/main/{path}"


def collect() -> dict:
    seen = load_seen()
    summary = {}
    for src in sources("hfdataset"):
        repo = src["repo"]
        rel_re = re.compile(src.get("release_re", r"^release_\d{4}_\d{2}_\d{2}$"))
        doc_file = src.get("doc_file", "data_documentation.md")

        status, body = fetch(API.format(repo=repo))
        if status != 200:
            record_health(src["id"], 0, status, "HF tree API 오류")
            summary[src["id"]] = 0
            continue
        try:
            tree = json.loads(body)
        except json.JSONDecodeError:
            record_health(src["id"], 0, status, "HF tree JSON 파싱 실패")
            summary[src["id"]] = 0
            continue

        releases = sorted(
            e["path"] for e in tree
            if e.get("type") == "directory" and rel_re.match(e.get("path", ""))
        )
        new = 0
        for rel in releases:
            key = f"hf:{repo}/{rel}"
            if key in seen:
                continue
            # 릴리스 문서 본문 시도(없으면 목록 사실만이라도 기록)
            dst, dbody = fetch(RAW.format(repo=repo, path=f"{rel}/{doc_file}"))
            text = dbody if dst == 200 and dbody and not dbody.startswith("(fetch error") else \
                f"{repo} 신규 릴리스: {rel} (문서 파일 없음/비공개 — HF에서 직접 확인)"
            url = f"https://huggingface.co/datasets/{repo}/tree/main/{rel}"
            save_item(src, key, f"{repo} — {rel}", url, rel, text[:8000],
                      {"kind": "hf-release", "repo": repo, "release": rel})
            mark_seen(key)
            seen.add(key)
            new += 1

        record_health(src["id"], new, status)
        summary[src["id"]] = new
    return summary


if __name__ == "__main__":
    s = collect()
    print(f"[hfdataset] {sum(s.values())} new / {len(s)} sources: "
          + ", ".join(f"{k}={v}" for k, v in s.items()))
