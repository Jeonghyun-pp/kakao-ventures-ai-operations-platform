#!/usr/bin/env python3
"""로컬 스위트 러너 — **테스트가 프로덕션 데이터를 건드리면 실패시킨다.**

## 왜 이 파일이 생겼나 (2026-08-16)

`_synthetic_apify_gate.py` 가 넉 달간 `~/kv-data/net-sourcing/raw/` 에 2바이트 파일을
써 왔다. 그중 `threads_<D>.json` 은 **실채널 이름**이라, "raw 최신본을 읽는" 하류가
테스트 산물을 진짜 수집 결과로 소비할 수 있는 상태였다.

원인은 한 줄이다:

    kv_core.paths ──(KV_DATA 존중)──> 스크래치        ← 대부분의 스킬
    net-sourcing  ──PROJ/"data" ─심볼릭 링크→ ~/kv-data/net-sourcing
                                                      ← **KV_DATA 를 우회한다**

그리고 그 심볼릭 링크는 `.gitignore` 라 **CI 러너에는 존재하지 않는다.** 거기서는
같은 코드가 임시 디렉토리를 만들 뿐이라 초록으로 끝난다. 즉 이 결함은 **원리적으로
CI 가 못 보는 종류**다 — 로컬에만 있는 위험은 로컬 러너가 봐야 한다.

개별 테스트를 고치는 것으로는 부족하다. 같은 함정이 다음에 또 놓인다(심볼릭 링크는
계속 있을 것이고, 새 테스트가 net-sourcing 모듈을 import 할 것이다). 그래서 여기서는
**테스트를 고치는 대신 부류를 막는다**: 각 테스트 전후로 `~/kv-data` 를 스냅샷하고,
바뀐 게 있으면 어떤 테스트가 무엇을 건드렸는지 이름을 대고 실패시킨다.

## 쓰는 법

    .venv/bin/python kv-core/tests/run_suite.py            # 전량
    .venv/bin/python kv-core/tests/run_suite.py apify      # 이름에 apify 가 든 것만
    .venv/bin/python kv-core/tests/run_suite.py --no-guard # 스냅샷 생략(빠름, 권장 안 함)

`_synthetic_slice.py` 는 **실 claude 를 부른다** — 기본 제외다(`--with-slice` 로 포함).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "kv-core" / "tests"
PROD = Path.home() / "kv-data"

# 실 claude 호출 → 쿼터를 태운다. CI 도 같은 이유로 제외한다.
_COSTLY = {"_synthetic_slice"}


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    """{상대경로: (크기, mtime_ns)}. 내용은 안 읽는다 — 949MB 를 해싱하면 러너가 쓸모없어진다.

    크기+mtime 조합은 "테스트가 파일을 만들었나/덮었나"를 잡기에 충분하다. 같은 크기로
    같은 나노초에 덮어쓰는 경우는 이 목적에선 존재하지 않는다.
    """
    out: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return out
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                st = p.lstat()
            except OSError:
                continue
            out[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


def diff(before: dict, after: dict) -> tuple[list[str], list[str], list[str]]:
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    changed = sorted(k for k in (set(before) & set(after)) if before[k] != after[k])
    return added, removed, changed


def main() -> int:
    args = [a for a in sys.argv[1:]]
    guard = "--no-guard" not in args
    with_slice = "--with-slice" in args
    pats = [a for a in args if not a.startswith("--")]

    names = sorted(p.stem for p in TESTS.glob("_*.py") if p.stem != "__init__")
    if not with_slice:
        names = [n for n in names if n not in _COSTLY]
    if pats:
        names = [n for n in names if any(p in n for p in pats)]
    if not names:
        print("🔴 실행할 테스트가 없다 — 패턴을 확인하라"); return 1

    scratch = Path(os.environ.get("KV_SUITE_SCRATCH", "/tmp/kv-suite"))
    # 🔴 스크래치를 **매 실행 비운다.** 안 비우면 2회차부터 결과가 달라진다 —
    #    누적형 상태(쿼터 장부 `_usage/claude/<날짜>.jsonl`, 커서, 알림 중복 마커 …)가
    #    남아 다음 회차의 전제를 깨기 때문이다. 실측: 같은 커밋에서 1회차 25/25 통과,
    #    2회차 14/25 통과였고, `_synthetic_claude_gate` 는 *"일일 호출 차단선 초과:
    #    오늘 6 + 1 > 5"* 로 죽었다 — 코드가 아니라 **지난 회차가 태운 쿼터** 때문이다.
    #    재현되지 않는 초록/빨강은 신호가 아니라 소음이다.
    #    ⚠️ rmtree 가 있으므로 겨눌 수 없는 곳을 **먼저** 막는다. `assert` 를 안 쓰는 건
    #       `python -O` 가 assert 를 통째로 제거하기 때문이다(가드가 사라진 줄도 모른다).
    _s = scratch.resolve()
    if _s == PROD.resolve() or _s in PROD.resolve().parents or _s == Path.home() or _s == Path("/"):
        raise SystemExit(f"🔴 스크래치로 쓸 수 없는 경로다: {_s}\n"
                         f"   이 러너는 스크래치를 rmtree 한다. KV_SUITE_SCRATCH 를 바꿔라.")
    if scratch.exists():
        import shutil
        shutil.rmtree(scratch)
    print(f"테스트 {len(names)}본 · 스크래치 {scratch}"
          f" · 프로덕션 가드 {'ON' if guard else '🔴 OFF'}")
    if guard and not PROD.exists():
        print(f"  ⚠️  {PROD} 가 없다 — 가드가 잴 대상이 없다(이 머신은 운영 머신이 아님)")
        guard = False

    base = snapshot(PROD) if guard else {}
    if guard:
        print(f"  · 프로덕션 스냅샷 {len(base):,}파일\n")

    passed, failed, leaked = [], [], []
    for n in names:
        env = {**os.environ, "KV_NO_SEND": "1", "KV_DATA": str(scratch / n)}
        t0 = time.time()
        r = subprocess.run([sys.executable, str(TESTS / f"{n}.py"), "--synthetic"],
                           capture_output=True, text=True, env=env)
        dt = time.time() - t0
        ok = r.returncode == 0
        (passed if ok else failed).append(n)
        mark = "✅" if ok else "❌"
        note = ""

        if guard:
            now = snapshot(PROD)
            added, removed, changed = diff(base, now)
            if added or removed or changed:
                leaked.append((n, added, removed, changed))
                mark = "🔴"
                note = f"  ← 프로덕션 {len(added)}추가/{len(removed)}삭제/{len(changed)}변경"
                base = now          # 다음 테스트에 같은 누출을 중복 계상하지 않는다
        print(f"  {mark} {n:34} {dt:5.1f}s{note}")
        if not ok:
            for ln in (r.stdout or "").splitlines()[-12:]:
                print(f"        {ln}")
            for ln in (r.stderr or "").splitlines()[-6:]:
                print(f"      ! {ln}")

    print(f"\n통과 {len(passed)} · 실패 {len(failed)}"
          + (f" · 🔴 프로덕션 누출 {len(leaked)}" if guard else ""))

    if leaked:
        print("\n🔴 프로덕션 데이터를 건드린 테스트 — 스크래치로 못 가둔 경로가 있다는 뜻이다.")
        print("   (net-sourcing 은 `project/data` 심볼릭 링크로 KV_DATA 를 우회한다 —")
        print("    그 모듈을 쓰는 테스트는 경로 상수를 직접 스크래치로 돌려놔야 한다)")
        for n, a, rm, c in leaked:
            print(f"\n   · {n}")
            for label, items in (("추가", a), ("삭제", rm), ("변경", c)):
                for f in items[:8]:
                    print(f"       {label} {f}")
                if len(items) > 8:
                    print(f"       {label} … 외 {len(items)-8}개")

    if failed or leaked:
        return 1
    print("🎉 전부 통과 · 프로덕션 무변경")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
