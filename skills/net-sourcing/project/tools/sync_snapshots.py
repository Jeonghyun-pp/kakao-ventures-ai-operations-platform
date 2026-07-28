#!/usr/bin/env python3
"""`data` 브랜치의 앱스토어 스냅샷을 $KV_DATA 로 당겨온다. 무과금.

## 왜 필요한가

계층 A(GitHub Actions)가 매일 스냅샷을 찍어 **`data` 브랜치에 커밋**한다.
로컬 보드는 `$KV_DATA/net-sourcing/snapshots/` 를 읽는다. 둘을 잇는 게 이 스크립트다.

    맥북을 3일 닫아둠
      → 로컬 launchd 는 3일치를 못 찍음
      → GitHub 은 계속 찍어 data 브랜치에 쌓아둠
      → 맥북을 열고 이걸 돌리면 3일치가 채워진다

이게 없으면 계층 A를 만들어도 **로컬 보드는 여전히 구멍이 뚫린 채**다.

## append-only — 기존 파일은 덮지 않는다

스냅샷은 날짜별 불변 파일이다. 같은 이름이면 내용도 같아야 한다.
혹시 다르더라도 **로컬을 정본으로 둔다**(로컬이 실제 그 시각에 찍은 것이므로).

사용:
  ./.venv/bin/python tools/sync_snapshots.py            # 당겨오기
  ./.venv/bin/python tools/sync_snapshots.py --dry-run  # 무엇이 채워질지만
"""
from __future__ import annotations

import subprocess
import sys
import tarfile
from io import BytesIO
from pathlib import Path

from kv_core import paths

BRANCH = "data"
SUBDIR = "snapshots"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


def main() -> int:
    dry = "--dry-run" in sys.argv
    repo = paths.repo_root()
    dest = paths.data("net-sourcing", SUBDIR)

    r = _git("fetch", "origin", f"{BRANCH}:refs/remotes/origin/{BRANCH}", cwd=repo)
    if r.returncode != 0:
        # 브랜치가 아직 없으면 정상 종료 — 계층 A를 아직 안 붙였다는 뜻이다.
        if "couldn't find remote ref" in (r.stderr or ""):
            print(f"· origin/{BRANCH} 없음 — 계층 A가 아직 스냅샷을 올리지 않았다. 할 일 없음.")
            return 0
        print(f"🔴 fetch 실패: {r.stderr.strip()[:200]}")
        return 1

    # git archive 로 꺼낸다 — 인덱스와 작업트리를 건드리지 않는다.
    r = subprocess.run(["git", "archive", f"origin/{BRANCH}", SUBDIR],
                       cwd=repo, capture_output=True)
    if r.returncode != 0:
        print(f"🔴 archive 실패: {r.stderr.decode()[:200]}")
        return 1

    have = {p.name for p in dest.glob("*.json")}
    added = skipped = 0
    with tarfile.open(fileobj=BytesIO(r.stdout)) as tf:
        for m in tf.getmembers():
            if not m.isfile() or not m.name.endswith(".json"):
                continue
            name = Path(m.name).name
            if name in have:
                skipped += 1
                continue                       # 로컬이 정본 — 덮지 않는다
            added += 1
            if dry:
                continue
            src = tf.extractfile(m)
            if src:
                (dest / name).write_bytes(src.read())

    print(f"{'[dry-run] ' if dry else ''}· 신규 {added}개 · 이미 있음 {skipped}개 → {dest}")
    _report_staleness(dest)
    return 0


def _report_staleness(dest: Path) -> None:
    """며칠 밀렸는지 표면화한다.

    🔴 간헐 가용 환경에서 가장 위험한 것은 실패가 아니라 **실행되지 않은 것**이다.
       실패는 로그에라도 남지만 안 돈 것은 아무 흔적이 없다.
       실제로 2026-07-20~27 8일이 그렇게 비었고, 아무도 몰랐다.
       계층 A(Actions)의 실패 알림을 외부에 둘 수 없으므로(열쇠 반출 금지)
       **로컬에서 밀림을 재는 것이 유일한 감지 경로**다. W7에서 정식 지표로 승격한다.
    """
    from datetime import date
    days = sorted({p.name.rsplit("_", 1)[-1].removesuffix(".json") for p in dest.glob("*.json")})
    if not days:
        print("🔴 스냅샷이 하나도 없다 — Δ 계산 불가")
        return
    try:
        last = date.fromisoformat(days[-1])
    except ValueError:
        return
    gap = (date.today() - last).days
    print(f"· 보유 {len(days)}일 · 최신 {days[-1]}", end="")
    if gap <= 1:
        print("  ✅ 최신")
    elif gap <= 3:
        print(f"  ⚠️  {gap}일 밀림")
    else:
        print(f"\n🔴 {gap}일 밀렸다 — 그 기간의 Δ는 **영구 손실**이다(애플이 과거 순위를 안 준다).\n"
              f"   계층 A(GitHub Actions)가 도는지, 로컬 launchd 잡이 사는지 확인하라.")


if __name__ == "__main__":
    raise SystemExit(main())
