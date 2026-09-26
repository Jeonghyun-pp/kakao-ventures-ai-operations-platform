#!/usr/bin/env python3
"""kr_origin 분류기를 고정 정답 라벨로 재측정한다.

    python tools/kr_origin_eval.py            # 요약 + 유실 목록
    python tools/kr_origin_eval.py --verbose  # 전 건 판정 근거

통과 기준은 하나: **유실(한국인데 foreign 판정) 0건.**
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from skills.appstore import kr_origin  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="kr_origin 분류기 정확도 측정")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    data = json.loads(kr_origin.FIXTURE.read_text(encoding="utf-8"))
    rows = data["rows"]

    lost, leaked, cells = [], [], {}
    for r in rows:
        verdict, why = kr_origin.classify(
            r["seller_name"], r["seller_url"], r["app_name"], r["description"])
        cells[(r["label"], verdict)] = cells.get((r["label"], verdict), 0) + 1
        if r["label"] == "kr" and verdict == "foreign":
            lost.append((r, why))
        if r["label"] == "foreign" and verdict == "kr":
            leaked.append((r, why))
        if args.verbose:
            print(f'{r["label"]:7} → {verdict:7} | {r["app_name"][:30]:32} | {why}')

    n_kr = sum(1 for r in rows if r["label"] == "kr")
    n_fo = len(rows) - n_kr
    dropped_fo = cells.get(("foreign", "foreign"), 0)

    print(f"\n픽스처 {len(rows)}건 (정답: 한국 {n_kr} / 외국 {n_fo})")
    print(f"{'정답':<8}{'kr':>6}{'foreign':>9}{'unknown':>9}")
    for label in ("kr", "foreign"):
        print(f"{label:<8}" + "".join(
            f"{cells.get((label, v), 0):>{6 if v == 'kr' else 9}}"
            for v in ("kr", "foreign", "unknown")))

    print(f"\n· 외국 제거율 : {dropped_fo}/{n_fo} = {dropped_fo / n_fo:.0%}")
    print(f"· 한국 유실   : {len(lost)}/{n_kr}  ← 0이어야 한다")
    print(f"· 외국이 kr로 : {len(leaked)}건")

    if lost:
        print("\n🔴 유실 (한국인데 버려짐):")
        for r, why in lost:
            print(f'   {r["app_name"][:34]:36} | {r["seller_name"]:32} | {why}')

    if lost:
        print("\n❌ FAIL — 한국 앱이 버려진다. 규칙을 되돌리거나 예외를 추가해라.")
        return 1
    print("\n✅ PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
