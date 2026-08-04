"""[W6] claude 일일 호출 게이트 — 합성 검증. claude 실호출 0 (subprocess monkeypatch).

증명:
  ① 카운터 append — run_session 1회 = jsonl 1줄
  ② 경고선(warn) 도달 시 경고, 실행은 계속
  ③ 차단선(block) 초과 시 SessionError + spawn 없음 + 카운트 미증가
  ④ timeout 도 카운트된다 (쿼터를 태웠으므로)

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_claude_gate.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 경로 필수 (실카운터 보호)"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import gate, session  # noqa: E402

FAIL: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


class _FakeProc:
    returncode = 0
    stdout = "OK"
    stderr = ""


_spawn_count = 0


def _fake_run(*a, **kw):
    global _spawn_count
    _spawn_count += 1
    return _FakeProc()


def _fake_run_timeout(*a, **kw):
    global _spawn_count
    _spawn_count += 1
    raise subprocess.TimeoutExpired(cmd="claude", timeout=1)


print("=== claude 호출 게이트 합성 검증 ===\n")

# 임계를 테스트용으로 작게 오버라이드 (선언 원본은 안 건드림 — config 캐시 주입)
gate._cfg = {"claude": {"daily_calls_warn": 3, "daily_calls_block": 5}}

subprocess_run_orig = subprocess.run
session.subprocess.run = _fake_run

# ① 1회 = 1줄
n0 = gate.calls_today("claude")
session.run_session("t1")
check("① 1콜 = 카운터 1줄", gate.calls_today("claude") == n0 + 1,
      f"{n0}→{gate.calls_today('claude')}")

# ② 경고선: 3콜째부터 경고 (실행은 계속)
session.run_session("t2")
session.run_session("t3")            # 이 시점 누적 3 = warn
v = gate.check_calls("claude", quiet=True)
check("② 경고선 도달 시 warning", any("경고선" in w for w in v.warnings), f"warnings={v.warnings}")
check("② 경고여도 실행 허용", v.allowed)

# ③ 차단선: 5 초과 시 SessionError, spawn 없음
session.run_session("t4")
session.run_session("t5")            # 누적 5 = block (5+1 > 5 → 다음 호출 차단)
spawn_before = _spawn_count
blocked = False
try:
    session.run_session("t6")
except session.SessionError as e:
    blocked = "게이트 차단" in str(e)
check("③ 차단선 초과 → SessionError", blocked)
check("③ 차단 시 spawn 0 (카운트 미증가)",
      _spawn_count == spawn_before and gate.calls_today("claude") == 5,
      f"spawn={_spawn_count - spawn_before}, calls={gate.calls_today('claude')}")

# ④ timeout 도 카운트 — 카운터 리셋 위해 임계 올림
gate._cfg = {"claude": {"daily_calls_warn": 30, "daily_calls_block": 50}}
session.subprocess.run = _fake_run_timeout
n_before = gate.calls_today("claude")
try:
    session.run_session("t7", timeout=1)
except session.SessionError:
    pass
check("④ timeout 도 카운트(쿼터 소모)", gate.calls_today("claude") == n_before + 1)

# 정리
session.subprocess.run = subprocess_run_orig
gate._cfg = None

print()
if FAIL:
    print(f"❌ 실패: {FAIL}")
    raise SystemExit(1)
print("✅ claude 게이트 합성 전부 통과")
