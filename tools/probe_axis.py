"""축 모수 프로브 — **사기 전에 얼마짜리인지 안다.**

액터는 모든 아이템에 `_meta.pagination.totalElements` 를 실어 준다. 그래서 `maxItems=1` 로
1명만 사면(페이지값 $0.10 + 아이템 $0.004) 그 축의 **총원과 전량 비용이 확정**된다.

🔴 이게 필요한 이유: 2026-08-19 에 학교 23곳짜리 축을 그냥 돌렸다가 모수가 10,038 인 걸
   **산 뒤에** 알았다. 천장이 2,500 이라 "전량"이 애초에 불가능한 축이었다.
   티어를 내려갈 때마다 이 프로브를 먼저 돌려라 — 회당 $0.10 이고, 틀리면 $80 이다.

    ~/kv/.venv/bin/python tools/probe_axis.py s-stealth kaist-founder
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kv_transport import apify          # noqa: E402
from skills.linkedin import axes        # noqa: E402

CEILING_PAGES = 100                     # 액터 상한 (startPage 100 × 25명)


def probe(axis: str) -> None:
    payload = dict(axes.payload(axis, 1), maxItems=1)
    f = apify.run_actor(axes.SLUG, payload, skill="linkedin", est_usd=axes.USD_PER_PAGE)
    if not f.is_measured:
        print(f"  🔴 {axis}: {f.reason}")
        return
    items = f.require()
    if not items:
        print(f"  🔴 {axis}: 0건 — 쿼리가 아무도 안 맞혔거나 액터가 깨졌다")
        return
    m = (items[0].get("_meta") or {}).get("pagination") or {}
    total, pages = m.get("totalElements"), m.get("totalPages") or 0
    full = min(pages, CEILING_PAGES)
    print(f"  {axis:14} 모수 {total:>7,} · 총페이지 {pages:>3}"
          f" · 전량 {full}p = ${full * axes.USD_PER_PAGE:.2f}"
          + ("  🔴 천장에 걸린다 — 쪼개야 '전부'가 된다" if pages >= CEILING_PAGES else ""))


if __name__ == "__main__":
    names = sys.argv[1:] or [a for a in axes.AXES if not axes.AXES[a].get("parked")]
    print(f"▶ 프로브 {len(names)}개 · 예상 ${len(names) * 0.104:.2f}")
    for a in names:
        probe(a)
    print(f"· 오늘 지출 ${apify.spent_today():.2f}")
