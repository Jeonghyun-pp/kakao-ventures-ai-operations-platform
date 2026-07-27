#!/usr/bin/env python3
"""SKILL.md CRITICAL #12 기계 검증 — `current*` 필드가 "현재"를 몇 % 놓치는가. 무과금.

## 무엇을 재나
액터가 주는 `currentPosition[]`(또는 `currentPositions[]`)과, `experience[]` 중
`endDate == "Present"` 인 항목을 비교한다. 후자에만 있는 포지션이 **누락**이다.

## 왜 재나 (2026-07-27 실측)
`currentPosition`이 진행 중 경력을 통째로 빼먹는다 — 271명 중 **117명(43%)**.
그 결과 극초기(<12개월) 판정이 **17명** 뒤집혔다(탈락 14·편입 3). 대표 피해:
  Example Person F — currentPosition=[행정안전부 자문(5개월)] 하나뿐 → tenure 4개월로 착지 →
  "극초기 창업자"로 분류. 실제는 AIM Intelligence 공동창업 **2년 3개월**차.

## 언제 돌리나
**새 액터·새 스크래퍼 모드를 붙였을 때, 착지 전에.** 판정 기준은 SKILL.md #12의 표와 같다:
  · 불일치율 10% 이상 → ❌ rc=1. 파서를 고치기 전에 착지하지 마라
  · 3~10%            → ⚠️ rc=0. 고치고 가되 영향 행 수를 남겨라
  · 3% 미만          → ✅ rc=0

사용:
  ./.venv/bin/python tools/check_present_coverage.py                  # data/raw/_actor/ 전체
  ./.venv/bin/python tools/check_present_coverage.py <파일.json> ...  # 특정 원본만
"""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
ERROR_PCT, WARN_PCT = 10.0, 3.0


def _key(e: dict) -> tuple:
    return (str(e.get("companyName") or ""), str(e.get("position") or e.get("title") or ""))


def _is_present(e: dict) -> bool:
    ed = e.get("endDate")
    if isinstance(ed, dict):
        return str(ed.get("text") or "").strip().lower() == "present"
    return not ed and bool(e.get("startDate"))


def main() -> int:
    files = sys.argv[1:] or sorted(glob.glob(str(PROJ / "data/raw/_actor/*.json")))
    profiles: dict[str, dict] = {}
    for f in files:
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        items = d if isinstance(d, list) else d.get("items", d)
        if not isinstance(items, list):
            continue
        for p in items:
            # experience가 없는 응답(Short 모드·다른 채널)은 애초에 이 검사 대상이 아니다.
            if isinstance(p, dict) and isinstance(p.get("experience"), list):
                profiles.setdefault(p.get("publicIdentifier") or p.get("id") or id(p), p)

    if not profiles:
        print("· 검사 대상 없음 (experience를 주는 응답이 저장돼 있지 않다). "
              "Full 모드 원본이 data/raw/_actor/ 에 있어야 한다.")
        return 0

    # 🔴 재는 대상은 **우리 파서**다. 액터의 누락률(raw)은 고쳐도 영원히 그대로라
    #    그걸 판정 기준으로 삼으면 이 체커는 **항상 실패한다**(= 아무도 안 보게 된다).
    #    판정 = `_positions()`가 Present를 다 흡수했는가. 파서가 옳으면 0%가 나온다.
    sys.path.insert(0, str(PROJ))
    from adapters.linkedin import _positions      # noqa: E402

    raw_missing, parser_missing, worst = 0, 0, []
    for pid, p in profiles.items():
        cur = {_key(e) for k in ("currentPosition", "currentPositions", "positions")
               for e in (p.get(k) or []) if isinstance(e, dict)}
        pres = [e for e in p["experience"] if isinstance(e, dict) and _is_present(e)]
        if [e for e in pres if _key(e) not in cur]:
            raw_missing += 1                       # 참고치 — 액터가 빼먹는 비율
        parsed = {_key(e) for e in _positions(p)}
        miss = [e for e in pres if _key(e) not in parsed]
        if miss:
            parser_missing += 1                    # ★ 판정치 — 우리가 놓치는 비율
            worst.append((len(miss), pid, [f"{e.get('position')} @ {e.get('companyName')}"
                                           for e in miss[:2]]))

    n = len(profiles)
    pct = 100.0 * parser_missing / n
    print(f"· 표본 {n}명")
    print(f"· [참고] 액터의 currentPosition이 Present를 누락: {raw_missing}명 "
          f"({100.0*raw_missing/n:.1f}%) — 이건 액터의 성질이라 안 변한다")
    print(f"· [판정] **우리 파서(_positions)가 놓친 사람**: {parser_missing}명 ({pct:.1f}%)")
    for cnt, pid, ex in sorted(worst, reverse=True)[:5]:
        print(f"    - {str(pid)[:26]:28s} 누락 {cnt}건: {ex}")

    if pct >= ERROR_PCT:
        print(f"\n❌ 불일치율 {pct:.1f}% ≥ {ERROR_PCT}% — **파서를 고치기 전에 착지하지 마라.**")
        print("   현재 포지션은 `currentPosition[] ∪ experience[] 중 endDate=Present`로 구한다.")
        print("   해석은 `adapters/linkedin.py::_positions()` **한 곳에서만** 한다. (SKILL.md #12)")
        return 1
    if pct >= WARN_PCT:
        print(f"\n⚠️  불일치율 {pct:.1f}% — 고치고 가되 영향 행 수를 로그에 남겨라. (SKILL.md #12)")
        return 0
    print(f"\n✅ 불일치율 {pct:.1f}% < {WARN_PCT}% — 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
