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

# ④ task 구조적 실패 — **신선도 한계 초과** → 발동 / 한계 안 → 미발동
#
# [W9 재설계 Batch B] 임계의 의미가 바뀌었다(의도된 변경).
#   구: `last_attempt - last_success` **격차** ≥ 주기×3     ← alerts 만의 규칙
#   신: `now - last_success` > `freshness_limit`            ← orrery 와 **같은 규칙**
# 예전엔 orrery 가 "2일 밀림 = 🔴"이라 그리는 동안 alerts 는 격차 2 < 3 이라 침묵했다.
# 같은 질문에 두 답이 나오던 지점이고(C-H4 계열), 통합의 대가로 알림이 하루 빨라진다.
# 지배적 실패 모드가 **가짜 침묵**이었으므로 이 방향이 맞다(유예는 dispatch 정책의 몫).
from kv_core import scheduler
from kv_core.registry import Task
t = Task(name="syn-alert-task", skill="_alerts-syn", cadence="daily",
         catch_up="coalesce", watermark="last_success")
scheduler.write_watermark(t, {"last_attempt": today.isoformat(),
                              "last_success": (today - timedelta(days=4)).isoformat(),
                              "first_attempt": (today - timedelta(days=9)).isoformat()})
import kv_core.registry as _reg
_orig_load = _reg.load
_reg.load = lambda *a, **k: [t]
alerts_reg = alerts
got4 = alerts_reg.scan()
check("④ 4일 밀림(한계 1일 초과) → task-fail 발동", "task-fail-syn-alert-task" in got4, f"{got4}")
scheduler.write_watermark(t, {"last_attempt": today.isoformat(),
                              "last_success": (today - timedelta(days=1)).isoformat(),
                              "first_attempt": (today - timedelta(days=9)).isoformat()})
for m in (paths.data_root() / "_alerts" / "sent").glob("task-fail-*"):
    m.unlink()
check("④ 1일 경과(한계 안) → 미발동", "task-fail-syn-alert-task" not in alerts_reg.scan())
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
# [W9 Phase0] **억제를 잠시 끈다** — push 는 미발송을 "억제(의도)"와 "실패(사고)"로 나누는데,
# KV_NO_SEND 가 켜진 채로는 전부 억제로 분류된다(정상 동작). 이 케이스가 보려는 건 실패다.
# 이 블록은 notify.slack 을 스텁으로 잡고 있으므로 실발송 위험은 0이다.
_no_send_saved = os.environ.pop("KV_NO_SEND", None)
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

# [W9 Phase0] **킬 스위치가 이 파일의 검증을 무력화하지 않는가** — 이 파일이 실제로 겪은 함정.
# 억제 판정을 push 맨 앞에 뒀더니, notify.slack 을 스텁으로 갈아끼운 이 테스트까지 전부
# 억제돼 ①~④ 가 한꺼번에 죽었다. 스텁이 이미 실발송을 막고 있는데 스위치가 한 번 더
# 막은 것 — 그건 방어가 아니라 **검증 능력의 파괴**다. 억제 판정은 발송 경계에만 둔다.
# (억제 의미론 자체는 _review_regressions·_p0_probes 가 실 경로로 검증한다)
os.environ["KV_NO_SEND"] = "1"
notify.slack = lambda text, **kw: (_sent.append(text), True)[1]
r4 = alerts.push("stub-wins-test", "스텁이 걸렸으면 알림 로직이 검증돼야 한다")
check("킬 스위치가 스텁 건 테스트의 알림 경로를 막지 않음", r4 == "sent", f"{r4}")
if _no_send_saved is None:
    os.environ.pop("KV_NO_SEND", None)            # [A-M9 교훈] 환경은 반드시 원복

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

# 구 스키마(first_attempt 없음) — **단언을 뒤집었다.**
#
# [W9 리뷰 C-H5] 이 자리에는 "폴백으로 미발동(오탐 0)"이 정답으로 박혀 있었다. 그런데 그
# "폴백"은 `first_attempt or last_attempt` 였고, `last_attempt` 는 매일 갱신되므로 경과일이
# **영원히 0**이 된다 — 즉 이 단언은 오탐을 막은 게 아니라 **영구 침묵을 CI 가 보증**하고
# 있었다. 실제로 judge 는 4일간 매일 실패하면서 알림이 0이었고, 사람이 손으로 워터마크에
# 값을 넣고서야 울렸다. "테스트가 선언이 아니라 기억을 단언한" 사례다.
#
# 지금 health 는 ①잡 디렉토리에서 최초 시도일을 복원하고 ②그것마저 없으면 **모른다는
# 사실 자체를 알린다**. 침묵보다 한 번의 확인 요청이 싸다.
t_old = _T(name="syn-oldwm", skill="_alerts-syn", cadence="daily",
           catch_up="coalesce", watermark="last_success")
scheduler.write_watermark(t_old, {"last_attempt": today.isoformat()})   # first_attempt 없음
_reg.load = lambda *a, **k: [t_old]
for m in (paths.data_root() / "_alerts" / "sent").glob("never-*"):
    m.unlink()
check("구 스키마(first_attempt 없음)는 침묵하지 않고 알린다",
      any(i.startswith("never-succeeded") for i in alerts.scan()))

# 그리고 잡 흔적이 있으면 손으로 넣지 않아도 최초 시도일이 복원된다(자기치유)
from kv_core import health as _hz, job as _job
_job.create("_alerts-syn", {"x": 1}, "syn-oldwm-2026-07-20")
st_heal = _hz.compute(tasks=[t_old])
check("잡 흔적에서 first_attempt 자기치유",
      st_heal.tasks[0].first_seen == "2026-07-20" and st_heal.tasks[0].first_seen_known,
      f"first_seen={st_heal.tasks[0].first_seen}")
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
