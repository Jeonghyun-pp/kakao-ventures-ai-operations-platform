#!/usr/bin/env python3
"""`cut_*.json` 압축 — 275MB → 3MB. **원본은 지우지 않고 격리한다.**

## 왜 (2026-08-16 실측)

    원본  21파일 · 275.4MB · 165,719행
    압축   1파일 ·   3.12MB · 고유 키 15,750개   (98.9% 절감)

매일 **전량 스냅샷**이라 연속 이틀이 65% 중복이다(8,697건 중 5,654건 동일).

## 🔴 무엇을 남기고 무엇을 버리나 — 기준은 하나다

**이 파일에만 있는 정보만 남긴다.** 실측으로 확정했다:

    cut_*.json 14필드 = raw/appstore_*.json 13필드 + `_cut_reason` 1개
    컷 키 8,697/8,697 (100%) 이 같은 날 raw 에 존재
    공통 13필드 값 일치 24,000 / 불일치 0 (앞 2,000행)

즉 이 파일이 만들어낸 정보는 **`_cut_reason` 하나**고, 나머지 13필드는 raw 의 글자 그대로
사본이다. 275MB 중 고유 정보는 사유 문자열뿐이었다.

    남긴다 ①  `_cut_reason`              이 파일에만 있다
    남긴다 ②  k · name · source · adapter  압축본을 **혼자 읽을 수 있게** 하는 최소 색인
    남긴다 ③  first · last · n            접으면서 새로 생기는 정보(며칠부터 며칠까지 몇 번)
    버린다    raw_text · signals · one_liner · founder · homepage · linkedin_url · released_at

②가 원칙의 예외인 이유: 이것들도 raw 의 사본이지만, appstore raw 를 **14일 뒤 지우기로**
했으므로(`config/retention.yaml`) 색인이 없으면 그 뒤엔 압축본에 키만 남아 **사람이
무엇인지 알 수 없다.** 조인할 상대가 사라진다. 이름 한 줄이 그걸 막는다.

⚠️ 잃는 것 하나: 컷 룰을 고쳐 **과거 전수에 재적용**하는 것은 판정 재료(raw_text·signals)가
   있어야 하므로, 압축 뒤에는 **retention 이 보관하는 14일 범위에서만** 가능하다.
   이 둘은 같은 결정이다.

## 소비자 확인

`cut_*.json` 을 읽는 곳은 `_synthetic_vacuum` V3 하나이고, `_cut_reason` **카운트만** 쓴다.
그래서 일별 사유 집계를 별도로 보존한다 — 그러면 V3 는 압축 후에도 그대로 돈다.

실행: python scripts/compact_cuts.py [--apply]   (기본은 dry)
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import sys
from datetime import date
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "kv-core"))

from kv_core import paths  # noqa: E402

OUT_NAME = "cut_compact.json"


def _key(r: dict) -> str:
    return str(r.get("ext_id") or r.get("app_id") or r.get("url") or r.get("name") or "")


def compact(files: list[Path], prev: dict | None = None) -> dict:
    """cut 파일들 → 압축본. `prev` 가 있으면 그 위에 병합한다(누적)."""
    recs: dict[str, dict] = {r["k"]: dict(r) for r in (prev or {}).get("records", [])}
    tally: dict[str, dict] = dict((prev or {}).get("daily_reason_tally", {}))
    # V3(`_synthetic_vacuum`)는 사유뿐 아니라 **그날 어떤 소스가 돌았는지**도 본다
    # (`CUT_RULES` 를 소스별로 대조하기 때문). 이것도 보존해야 압축 후에도 검사가 성립한다.
    dsrc: dict[str, list] = dict((prev or {}).get("daily_sources", {}))
    srcs: list[str] = list((prev or {}).get("source_files", []))
    rows = int((prev or {}).get("compacted_rows", 0))

    for f in sorted(files):
        day = f.stem.replace("cut_", "")
        data = json.loads(f.read_text(encoding="utf-8"))
        rows += len(data)
        tally[day] = dict(collections.Counter(
            str(r.get("_cut_reason", "")).split("(")[0].strip() for r in data))
        dsrc[day] = sorted({str(r.get("source", "")) for r in data if r.get("source")})
        for r in data:
            k = _key(r)
            if not k:
                continue
            e = recs.get(k)
            if e is None:
                recs[k] = {"k": k, "name": r.get("name", ""), "source": r.get("source", ""),
                           "adapter": r.get("adapter", ""),
                           "reason": str(r.get("_cut_reason", "")),
                           "first": day, "last": day, "n": 1}
            else:
                e["n"] += 1
                if day > e["last"]:
                    e["last"] = day
                    if r.get("_cut_reason"):
                        e["reason"] = str(r["_cut_reason"])   # 최신 사유를 남긴다
                if day < e["first"]:
                    e["first"] = day
        if f.name not in srcs:
            srcs.append(f.name)
    return {"records": sorted(recs.values(), key=lambda r: r["k"]),
            "daily_reason_tally": dict(sorted(tally.items())),
            "daily_sources": dict(sorted(dsrc.items())),
            "source_files": sorted(srcs), "compacted_rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(prog="compact_cuts")
    ap.add_argument("--apply", action="store_true", help="실제로 쓰고 원본을 격리한다")
    a = ap.parse_args()

    d = paths.data("net-sourcing")
    files = sorted(d.glob("cut_*.json"))
    if not files:
        print("· cut_*.json 없음 — 할 일 없음")
        return 0
    before = sum(f.stat().st_size for f in files)

    out_path = d / OUT_NAME
    prev = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else None
    result = compact(files, prev)
    blob = json.dumps(result, ensure_ascii=False)

    print(f"  원본     {len(files)}파일 · {before/2**20:.1f}MB · {result['compacted_rows']:,}행")
    print(f"  압축 후   1파일 · {len(blob.encode())/2**20:.2f}MB · 고유 키 {len(result['records']):,}개")
    print(f"  절감     {(before-len(blob.encode()))/2**20:.1f}MB "
          f"({1-len(blob.encode())/before:.1%})")
    if not a.apply:
        print("\n[dry] --apply 를 붙이면 압축본을 쓰고 원본을 _quarantine 으로 옮긴다.")
        return 0

    out_path.write_text(blob, encoding="utf-8")
    # 🔴 지우지 않고 **격리**한다. 되돌릴 수 있어야 한다 — 확인한 뒤 사람이 지운다.
    q = paths.data_root() / "_quarantine" / f"{date.today().isoformat()}-cut-compaction"
    q.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.move(str(f), str(q / f.name))
    (q / "README.md").write_text(
        f"# cut_*.json 압축 격리 ({date.today().isoformat()})\n\n"
        f"`scripts/compact_cuts.py --apply` 가 옮겼다. 압축본: `net-sourcing/{OUT_NAME}`\n\n"
        f"    원본 {len(files)}파일 · {before/2**20:.1f}MB · {result['compacted_rows']:,}행\n"
        f"    압축  1파일 · {len(blob.encode())/2**20:.2f}MB · 고유 키 {len(result['records']):,}개\n\n"
        f"버린 필드는 전부 `raw/appstore_*.json` 의 사본이다(실측: 키 100% 일치 · 공통 13필드 "
        f"값 불일치 0). 이 파일에만 있던 정보는 `_cut_reason` 하나이고 압축본이 보존한다.\n"
        f"일별 사유 집계도 `daily_reason_tally` 로 보존해 `_synthetic_vacuum` V3 가 그대로 돈다.\n\n"
        f"확인했으면 지워도 된다.\n", encoding="utf-8")
    print(f"\n✅ {out_path.name} 기록 · 원본 {len(files)}본 → {q}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
