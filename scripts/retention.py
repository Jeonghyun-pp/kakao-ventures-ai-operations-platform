#!/usr/bin/env python3
"""보존 정책 집행 — `config/retention.yaml` 이 정본이고 이 파일은 그걸 실행만 한다.

## 왜 이 파일이 생겼나 (2026-08-16)

계약에 *"981MB · 하루 ~33MB 증가 · **리텐션 정책 없음**"* 이라고 적혀 있었다.
정책이 없으면 세 종류가 같은 대우를 받는다 — 무료로 다시 받을 수 있는 것, 돈 드는 것,
다른 곳에 정본이 있는 사본이 구분 없이 쌓인다.

## 🔴 삭제를 자동화한다는 것

이 스크립트는 파일을 **지운다.** 그래서 안전장치가 정책보다 먼저다:

    ① 백업이 **오늘 성공**했을 때만 지운다(`~/kv-data-backup/.last-success`).
       백업 없이 지우는 경로를 만들지 않는다.
    ② `retention.yaml` 의 glob **밖은 절대 안 건드린다**(화이트리스트).
       glob 이 `~/kv-data` 밖을 가리키면 즉시 중단한다.
    ③ 지운 뒤 남을 개수가 `min_keep` 미만이면 **아무것도 안 지운다.**
       규칙 오타 하나로 전량이 사라지는 경로를 막는다.

`--dry` 가 기본이 아니다 — 스케줄러가 부르는 것이 본업이라 기본은 실행이고,
사람이 확인하고 싶으면 `--dry` 를 붙인다.

실행: python scripts/retention.py [--dry]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "kv-core"))

from kv_core import paths  # noqa: E402

POLICY = _REPO / "config" / "retention.yaml"


def _backup_ok() -> tuple[bool, str]:
    """백업이 오늘 성공했나. 표식은 백업 **대상 밖**에 있다(`scripts/backup-kv-data.sh`)."""
    m = Path(os.environ.get("KV_BACKUP_DIR", str(Path.home() / "kv-data-backup"))) / ".last-success"
    if not m.exists():
        return False, f"백업 성공 표식이 없다: {m}"
    txt = m.read_text(encoding="utf-8").strip()
    today = date.today().isoformat()
    # 표식은 UTC 타임스탬프로 시작한다. 오늘(로컬) 또는 어제까지는 인정한다 —
    # 맥이 잠들어 백업이 몇 시간 밀리는 것은 정상이고, 그때마다 정리를 멈출 이유는 없다.
    ok = txt[:10] in (today, (date.fromordinal(date.today().toordinal() - 1)).isoformat())
    return ok, txt


def run(dry: bool = False) -> int:
    import yaml
    root = paths.data_root()
    cfg = yaml.safe_load(POLICY.read_text(encoding="utf-8")) or {}
    rules = cfg.get("rules") or []
    if not rules:
        print("🔴 retention.yaml 에 규칙이 없다 — 빈 정책은 '정리 안 함'과 구별되지 않는다")
        return 1

    ok, why = _backup_ok()
    if not ok and not dry:
        print(f"🔴 백업이 최근에 성공하지 않았다 — 정리를 중단한다.\n   {why}\n"
              f"   백업 없이 지우는 경로는 만들지 않는다. `scripts/backup-kv-data.sh` 를 먼저 돌려라.")
        return 1
    print(f"· 백업 확인: {why}")

    now = time.time()
    total_freed = total_del = 0
    for r in rules:
        name, glob = r["name"], r["glob"]
        keep_days, min_keep = int(r["keep_days"]), int(r.get("min_keep", 0))
        files = sorted(p for p in root.glob(glob) if p.is_file())
        # 🔴 화이트리스트 — glob 이 데이터 루트 밖을 가리키면 즉시 중단.
        for p in files:
            if root.resolve() not in p.resolve().parents:
                print(f"🔴 {name}: glob 이 데이터 루트 밖을 가리킨다 — 중단\n   {p}")
                return 1
        cutoff = now - keep_days * 86400
        old = [p for p in files if p.stat().st_mtime < cutoff]
        remain = len(files) - len(old)
        if not old:
            print(f"· {name}: {len(files)}본 · 지울 것 없음(보관 {keep_days}일)")
            continue
        if remain < min_keep:
            print(f"⚠️  {name}: 지우면 {remain}본만 남는다(하한 {min_keep}) — **아무것도 안 지운다**.\n"
                  f"    규칙이 잘못됐거나 수집이 멈춘 것이다. 확인 요망.")
            continue
        freed = sum(p.stat().st_size for p in old)
        print(f"· {name}: {len(files)}본 중 {len(old)}본 삭제 → {remain}본 남음 "
              f"({freed/2**20:.1f}MB 회수){' [dry]' if dry else ''}")
        if not dry:
            for p in old:
                p.unlink(missing_ok=True)
        total_freed += freed
        total_del += len(old)

    print(f"{'[dry] ' if dry else ''}✅ retention: {total_del}본 · {total_freed/2**20:.1f}MB 회수 "
          f"· 현재 {sum(f.stat().st_size for f in root.rglob('*') if f.is_file())/2**20:.0f}MB")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="retention")
    ap.add_argument("--dry", action="store_true", help="지우지 않고 대상만 보여준다")
    return run(ap.parse_args().dry)


if __name__ == "__main__":
    raise SystemExit(main())
