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
os.environ.setdefault("KV_NO_SEND", "1")   # [W9] 실발송 킬 스위치 — 스텁 누락 시의 최후 방어

import os
import subprocess
import sys
from pathlib import Path

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 경로 필수 (실카운터 보호)"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import gate, notify, session  # noqa: E402

# [중간점검 B1] notify 를 스텁으로 캡처 — 이 스텁이 없으면 차단 시나리오가 **실 슬랙 웹훅**으로
# 가짜 "차단" 경보를 발송한다(실측 사고). 테스트는 외부 부작용 0 이어야 한다.
_slack_captured: list[str] = []
notify.slack = lambda text, **kw: (_slack_captured.append(text), True)[1]
gate.notify = notify   # gate 가 참조하는 모듈 객체도 동일 스텁을 보게

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

# ⑤ [B3] block: 0 = 전면 동결 (falsy 함정 회귀)
gate._cfg = {"claude": {"daily_calls_warn": 0, "daily_calls_block": 0}}
v0 = gate.check_calls("claude", quiet=True)
check("⑤ block=0 → 전면 동결(허용 아님)", v0.allowed is False, f"allowed={v0.allowed}")

# ⑥ [B2] 차단선 미설정 verdict 의 summary 가 크래시 없이
gate._cfg = {"claude": {}}
v_none = gate.check_calls("claude", quiet=True)
try:
    s = v_none.summary()
    check("⑥ ceiling None summary 무크래시", "미설정" in s, s[:50])
except TypeError as e:
    check("⑥ ceiling None summary 무크래시", False, repr(e))

# ⑦ [B1 · W9 Batch C] 차단·경고가 실 슬랙이 아니라 **사실 장부**를 거쳐 스텁에 닿는지.
# gate 는 이제 notify 를 직접 부르지 않는다(정책이 흩어지던 지점) — observe 후 dispatch.
from kv_core import alerts as _al
_al.dispatch()
check("⑦ notify 전부 스텁 캡처(실발송 0)", len(_slack_captured) >= 1,
      f"captured={len(_slack_captured)}건")

# ⑧ [E6] 두 파일에 걸친 불변식 기계검증.
#
# 🔴 [적대리뷰 H3 · 2026-08-11] 예전 식(`max_batches_per_run < daily_calls_warn`)은
#    judge 를 ×1 로 셌다. 그런데 judge_job 도 meeting_mail_job 과 **같은 attempt-suffix
#    루프**(-r2·-r3)를 갖는다 — 잡이 실패하면 런 상한이 처음부터 다시 열려 하루 최악
#    3 × 20 = 60콜이다. config.yaml 은 meeting_mail 에만 "최악치 = cap × attempts" 보정을
#    적어두고 judge 엔 안 했고, 이 검증도 그 낙관을 그대로 복사했다.
#    **검증이 모델을 낙관적으로 잡으면, 통과는 안전의 증거가 아니라 사각의 증거다.**
#
# 이제 judge 는 gate 의 일별 장부를 읽어 (차단선 − 예약) 에서 스스로 멈춘다. 그래서
# 검증할 불변식은 둘이다: **예약이 meeting_mail 최악치를 실제로 덮는가**, 그리고
# **judge 천장 + meeting_mail 최악치가 차단선을 안 넘는가**.
import yaml
_repo = Path(__file__).resolve().parents[2]
res = yaml.safe_load((_repo / "config" / "resources.yaml").read_text(encoding="utf-8"))
ncfg = yaml.safe_load((_repo / "skills/net-sourcing/project/config.yaml").read_text(encoding="utf-8"))
_mm = ncfg["meeting_mail"]
mm_worst = int(_mm["max_drafts_per_run"]) * int(_mm["max_attempts_per_day"])
reserve = int(ncfg["judge"]["reserve_calls_for_others"])
block = int(res["claude"]["daily_calls_block"])
ceiling = block - reserve
check("⑧ 불변식: 예약이 meeting_mail 최악치를 덮는다", reserve >= mm_worst,
      f"예약 {reserve} >= {mm_worst}(={_mm['max_drafts_per_run']}×{_mm['max_attempts_per_day']})")
check("⑧ 불변식: judge 천장 + meeting_mail 최악 <= 차단선", ceiling + mm_worst <= block,
      f"{ceiling} + {mm_worst} <= {block}")
check("⑧ 불변식: judge 천장이 양수(예약이 차단선을 먹지 않았다)", ceiling > 0, f"천장 {ceiling}")

# 정리
session.subprocess.run = subprocess_run_orig
gate._cfg = None

print()
if FAIL:
    print(f"❌ 실패: {FAIL}")
    raise SystemExit(1)
print("✅ claude 게이트 합성 전부 통과")
