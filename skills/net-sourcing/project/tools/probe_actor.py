#!/usr/bin/env python3
"""액터 스왑 프로브 — burbn(검증됨) vs meta-threads(미검증)를 **같은 키워드**로 돌려 대조한다.

## 왜 이게 결제 후 첫 행동인가
burbn은 minMemoryMbytes=4096이라 메모리를 못 낮춘다 → Apify가 actor-start를 **GB당** 물리므로
**런당 $0.16이 고정**이다. 스레드 4개 층은 런이 52회 나오고, 시작 수수료만 $8.32다.
데이터 값이 아니라 **런을 여는 값**이 비용의 대부분이다.

meta-threads는 같은 4GB인데 start 단가가 1/8($0.02/런)이고, `keywords`/`usernames`를 **배열**로
받아 워치리스트·팔로잉 글을 **런 1회**로 끝낸다. 전 층 1회 실행이 $18.8 → $7.7로 떨어진다.

**단 출력 스키마가 미검증이다.** 눈감고 스왑하면 스레드 채널이 전면 0건이 된다.
이 스크립트가 그걸 ~$0.4에 판정한다.

사용: ./.venv/bin/python tools/probe_actor.py [키워드]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

BURBN = "burbn~threads-search-scraper"
META = "futurizerush~meta-threads-scraper"


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else "만들었습니다"

    # 🔴 [2026-08-16] 인라인 `.env` 파서 → `kv_core.secrets`.
    #    `main.py:28-36` 이 *"자격증명 정본은 `$KV_CONFIG/.env` 하나뿐. 스킬별 `.env` 를
    #    다시 만들지 마라"* 라고 선언하고 옮겼는데 도구들은 옛 파서를 그대로 들고 있었고,
    #    `project/.env` 는 **존재하지 않아** 실행 즉시 `FileNotFoundError` 로 죽었다.
    from kv_core import secrets
    secrets.load()

    from adapters import apify, threads
    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    apify.configure(cfg)

    before = apify.usage_usd()
    print(f"· 계정 누적(전): ${before:.4f}\n" if before is not None else "")

    results: dict[str, list[dict]] = {}
    for slug in (BURBN, META):
        payload = threads._search_payload(slug, query, 20, "")
        print(f"▶ {slug}\n  payload={payload}")
        try:
            items = apify.run_actor(slug, payload)
        except Exception as e:
            print(f"  🔴 실패: {e}\n")
            results[slug] = []
            continue
        results[slug] = items
        print(f"  → {len(items)}건")
        if items:
            print(f"  실제 키: {sorted(items[0].keys())}")
        print()

    after = apify.usage_usd()
    if before is not None and after is not None:
        print(f"💰 이 프로브 실제 과금: ${after - before:.4f} (추정 ${apify.spent_usd():.4f})\n")

    # ── 판정: meta 출력이 _post_to_candidate 폴백에 걸리는가
    meta_items = results.get(META) or []
    if not meta_items:
        print("🔴 meta-threads가 0건 — 스왑 불가. burbn 유지하고 config를 좁혀라.")
        return 1

    a = threads.ThreadsAdapter(cfg, yaml.safe_load((PROJ / "keywords.yaml").read_text(encoding="utf-8")))
    parsed = [a._post_to_candidate(p, sourcing_method="keyword_search", search_mode="KEYWORD",
                                   search_type="RECENT", query=query, is_reply=False,
                                   layer="keyword") for p in meta_items]
    miss = {
        "작성자(founder)": sum(1 for c in parsed if not c.founder),
        "본문(raw_text)": sum(1 for c in parsed if not c.raw_text),
        "URL": sum(1 for c in parsed if not c.url),
        "게시일(released_at)": sum(1 for c in parsed if not c.released_at),
    }
    n = len(parsed)
    print(f"=== meta-threads 출력 → Candidate 파싱 ({n}건) ===")
    bad = False
    for field, m in miss.items():
        rate = m / n
        mark = "🔴" if rate > 0.1 else "✅"
        if rate > 0.1:
            bad = True
        print(f"  {mark} {field:20} 누락 {m}/{n}")
    if bad:
        print(f"\n🔴 폴백이 못 흡수하는 필드가 있다. 실제 키를 보고 _post_to_candidate를 고쳐라:")
        print(f"   {sorted(meta_items[0].keys())}")
        print("   고치기 전엔 **스왑하지 마라** — 스레드가 전면 0건이 된다.")
        return 1

    burbn_n = len(results.get(BURBN) or [])
    print(f"\n✅ meta-threads 출력이 폴백에 전부 잡힌다. 수집량 burbn {burbn_n}건 vs meta {n}건.")
    print("\n스왑하려면 config.yaml channels.threads.actors 를 이렇게:")
    print(f'    keyword:    "{META}"')
    print(f'    user_posts: "{META}"')
    print("  → 워치리스트·팔로잉 글이 **런 1회**로 끝나고, 전 층 1회 실행이 $18.8 → ~$7.7")
    print("  → tools/estimate_cost.py 로 재확인 후 collect.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
