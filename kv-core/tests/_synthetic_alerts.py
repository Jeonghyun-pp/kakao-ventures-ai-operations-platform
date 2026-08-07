"""[W8 배치4] 실패 푸시 합성 검증 — notify 스텁(실발송 0)·스크래치 KV_DATA.

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_alerts.py
"""
from __future__ import annotations

import os
os.environ.setdefault("KV_NO_SEND", "1")   # [W9] 실발송 킬 스위치 — 스텁 누락 시의 최후 방어

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import alerts, gate, notify, paths  # noqa: E402

_sent: list[str] = []
notify.slack = lambda text, **kw: (_sent.append(text), True)[1]
alerts.notify = notify
gate.notify = notify

FAIL: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


def _usage_write(day, entries):
    """[W9] ts 를 함께 기록 — alerts 가 '오늘 발생한 실패인지'를 ts 로 판정한다(B-F7)."""
    d = paths.data_root() / "_usage" / "claude"
    d.mkdir(parents=True, exist_ok=True)
    rows = [dict(e, ts=e.get("ts", f"{day.isoformat()}T00:00:00+00:00")) for e in entries]
    (d / f"{day.isoformat()}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


today = datetime.now(timezone.utc).date()
yday = today - timedelta(days=1)

print("=== 실패 푸시 합성 검증 ===\n")

# ① 연속 3 실패 → 발동 · dedup · ok 섞이면 미발동
_usage_write(today, [{"ok": True}, {"ok": False, "err": "timeout"},
                     {"ok": False, "err": "timeout"}, {"ok": False, "err": "rc"}])
got = alerts.scan()
check("① 연속 3 실패 → auth-suspect 발동", "auth-suspect-claude" in got, f"{got}")
got2 = alerts.scan()
check("① 재스캔 dedup(하루 1회)", "auth-suspect-claude" not in got2)
_usage_write(today, [{"ok": False}, {"ok": True}, {"ok": False}])
# dedup 마커 제거 후 재검(미발동이어야)
for m in (paths.data_root() / "_alerts" / "sent").glob("auth-suspect-*"):
    m.unlink()
check("① ok 섞이면 미발동", "auth-suspect-claude" not in alerts.scan())

# ② UTC 경계: 어제 꼬리 2 + 오늘 1 연속 실패 → 검출
_usage_write(yday, [{"ok": True}, {"ok": False, "err": "timeout"}, {"ok": False, "err": "timeout"}])
_usage_write(today, [{"ok": False, "err": "timeout"}])   # 오늘 발생분 포함 → 발동해야 함
for m in (paths.data_root() / "_alerts" / "sent").glob("auth-suspect-*"):
    m.unlink()
check("② UTC 자정 경계(어제2+오늘1) 검출", "auth-suspect-claude" in alerts.scan())

# ③ 게이트 차단 → 이벤트 1줄 + 같은 날 2번째 차단 발송 0
gate._cfg = {"claude": {"daily_calls_warn": 1, "daily_calls_block": 1}}
_usage_write(today, [{"ok": True}, {"ok": True}])          # calls_today=2 > block=1
v = gate.check_calls("claude", quiet=True)
n_before = len(_sent)
gate.notify_blocked(v)
gate.notify_blocked(v)                                     # 같은 날 2번째
ev = (paths.data_root() / "_alerts" / "events.jsonl").read_text(encoding="utf-8")
n_events = sum(1 for ln in ev.splitlines() if '"gate_block"' in ln)
check("③ 차단 이벤트 장부 기록", n_events == 2, f"events={n_events}")
check("③ 발송은 하루 1회(dedup)", len(_sent) == n_before + 1, f"sent+{len(_sent)-n_before}")
gate._cfg = None

# ④ task 구조적 실패: attempt 전진·success 4일 뒤짐(daily×3 초과) → 발동 / 2일 → 미발동
from kv_core import scheduler
from kv_core.registry import Task
t = Task(name="syn-alert-task", skill="_alerts-syn", cadence="daily",
         catch_up="coalesce", watermark="last_success")
scheduler.write_watermark(t, {"last_attempt": today.isoformat(),
                              "last_success": (today - timedelta(days=4)).isoformat()})
import kv_core.registry as _reg
_orig_load = _reg.load
_reg.load = lambda *a, **k: [t]
alerts_reg = alerts
got4 = alerts_reg.scan()
check("④ 격차 4일(≥3) → task-fail 발동", "task-fail-syn-alert-task" in got4, f"{got4}")
scheduler.write_watermark(t, {"last_attempt": today.isoformat(),
                              "last_success": (today - timedelta(days=2)).isoformat()})
for m in (paths.data_root() / "_alerts" / "sent").glob("task-fail-*"):
    m.unlink()
check("④ 격차 2일(<3) → 미발동", "task-fail-syn-alert-task" not in alerts_reg.scan())
_reg.load = _orig_load

# ⑤ push 원자성: 마커 선점 시 notify 0
d = paths.data_root() / "_alerts" / "sent"
marker = d / f"pre-claimed-{today.isoformat()}"
marker.touch()
n_before = len(_sent)
ok5 = alerts.push("pre-claimed", "should not send")
check("⑤ 마커 선점 → 발송 0·deduped", ok5 == "deduped" and len(_sent) == n_before)

# ── [W9 리뷰 회귀] B-F1·F2·F3·F7·F8 ─────────────────────────────────────────
print()
print("[W9 리뷰 회귀]")

# B-F2: 발송 실패 시 claim 반납 → 복구 후 재푸시 가능 (예전엔 마커가 막아 영구 유실)
_fail_mode = {"on": True}
notify.slack = lambda text, **kw: (False if _fail_mode["on"]
                                   else (_sent.append(text), True)[1])
r1 = alerts.push("recover-test", "1차(실패)")
check("B-F2 발송 실패 → 'failed' 반환", r1 == "failed", f"{r1}")
_fail_mode["on"] = False
r2 = alerts.push("recover-test", "2차(복구 후)")
check("B-F2 복구 후 재푸시 성공(claim 반납됨)", r2 == "sent", f"{r2}")
r3 = alerts.push("recover-test", "3차")
check("B-F2 성공 후에는 dedup", r3 == "deduped", f"{r3}")

# B-F1: 인증 만료(rc0 + 빈 출력)가 ok:false 로 기록되는가 — session 실경로 스텁
import subprocess as _sp
from kv_core import session as _sess


class _EmptyProc:
    returncode = 0
    stdout = "   "          # 빈 출력 = 인증 만료 증상
    stderr = ""


_sess.subprocess.run = lambda *a, **k: _EmptyProc()
gate._cfg = {"claude": {"daily_calls_warn": 99, "daily_calls_block": 99}}
n_before = gate.calls_today("claude")
try:
    _sess.run_session("t")
except _sess.SessionError:
    pass
tail = alerts._usage_tail(1)
check("B-F1 빈 출력(인증 만료 증상) → ok:false 기록",
      tail and tail[-1].get("ok") is False and tail[-1].get("err") == "empty_output", f"{tail}")

# B-F3: never-succeeded — 시도는 있고 성공 이력이 없는 task 도 결국 알린다
from kv_core.registry import Task as _T
t_never = _T(name="syn-never", skill="_alerts-syn", cadence="daily",
             catch_up="coalesce", watermark="last_success")
scheduler.write_watermark(t_never, {"last_attempt": today.isoformat(),
                                    "first_attempt": (today - timedelta(days=10)).isoformat()})
_reg.load = lambda *a, **k: [t_never]
got_nv = alerts.scan()
check("B-F3 never-succeeded 도 알린다(10일 경과)",
      any(i.startswith("never-succeeded") for i in got_nv), f"{got_nv}")
scheduler.write_watermark(t_never, {"last_attempt": today.isoformat(),
                                    "first_attempt": today.isoformat()})
for m in (paths.data_root() / "_alerts" / "sent").glob("never-*"):
    m.unlink()
check("B-F3 유예 안에선 미발동(배선 당일)",
      not any(i.startswith("never-succeeded") for i in alerts.scan()))
_reg.load = _orig_load

# [점검 후속] first_attempt 를 **스케줄러가 실제로 기록**하는가 — 이게 없으면 never-succeeded
# 판정이 last_attempt 로 폴백해 경과일이 영원히 0 이 된다(실환경에서 알림이 침묵했다).
t_fa = _T(name="syn-firstattempt", skill="_alerts-syn", cadence="daily",
          catch_up="coalesce", watermark="last_success")
scheduler.run_due([t_fa], lambda task, p: False, now=today)          # 1일차 실패
wm_fa = scheduler.read_watermark(t_fa)
check("점검 first_attempt 최초 기록", wm_fa.get("first_attempt") == today.isoformat(), f"{wm_fa}")
scheduler.run_due([t_fa], lambda task, p: False, now=today + timedelta(days=1))
wm_fa2 = scheduler.read_watermark(t_fa)
check("점검 first_attempt 는 이후 보존(덮어쓰지 않음)",
      wm_fa2.get("first_attempt") == today.isoformat()
      and wm_fa2.get("last_attempt") == (today + timedelta(days=1)).isoformat(), f"{wm_fa2}")

# 구 스키마(first_attempt 없음) 폴백이 **오작동하지 않는지** — 폴백은 안전하게 보수적이어야
t_old = _T(name="syn-oldwm", skill="_alerts-syn", cadence="daily",
           catch_up="coalesce", watermark="last_success")
scheduler.write_watermark(t_old, {"last_attempt": today.isoformat()})   # first_attempt 없음
_reg.load = lambda *a, **k: [t_old]
for m in (paths.data_root() / "_alerts" / "sent").glob("never-*"):
    m.unlink()
check("점검 구 스키마는 폴백으로 미발동(오탐 0)",
      not any(i.startswith("never-succeeded") for i in alerts.scan()))
_reg.load = _orig_load

# B-F8: identity 위생 — 경로 문자가 있어도 raise 하지 않는다
try:
    rid = alerts.push("net-sourcing/threads:x", "identity 위생 테스트")
    check("B-F8 경로 문자 identity 도 안전(raise 0)", rid in ("sent", "deduped", "failed"), rid)
except Exception as e:  # noqa: BLE001
    check("B-F8 경로 문자 identity 도 안전(raise 0)", False, repr(e))

print()
if FAIL:
    print(f"❌ 실패: {FAIL}")
    raise SystemExit(1)
print("✅ 실패 푸시 전부 통과")
