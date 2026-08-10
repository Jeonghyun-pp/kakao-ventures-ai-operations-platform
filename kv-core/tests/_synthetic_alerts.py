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


def _reset_alert(prefix: str) -> None:
    """알림 상태 초기화 — **두 층**을 지운다.

    [W9 재설계 Batch C] dedup 은 이제 두 겹이다: ①마커(하루 1회) ②정책(백오프 간격).
    마커만 지우고 재발동을 기대하면 정책이 막는다 — 그건 결함이 아니라 설계다.
    """
    for d, pat in ((paths.data_root() / "_alerts" / "sent", f"{prefix}*"),
                   (paths.data_root() / "_alerts" / "policy", f"{prefix}*.json")):
        if d.exists():
            for m in d.glob(pat):
                m.unlink()


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
_reset_alert("auth-suspect")
check("① ok 섞이면 미발동", "auth-suspect-claude" not in alerts.scan())

# ② UTC 경계: 어제 꼬리 2 + 오늘 1 연속 실패 → 검출
_usage_write(yday, [{"ok": True}, {"ok": False, "err": "timeout"}, {"ok": False, "err": "timeout"}])
_usage_write(today, [{"ok": False, "err": "timeout"}])   # 오늘 발생분 포함 → 발동해야 함
_reset_alert("auth-suspect")
check("② UTC 자정 경계(어제2+오늘1) 검출", "auth-suspect-claude" in alerts.scan())

# ③ 게이트 차단 → 이벤트 1줄 + 같은 날 2번째 차단 발송 0
gate._cfg = {"claude": {"daily_calls_warn": 1, "daily_calls_block": 1}}
_usage_write(today, [{"ok": True}, {"ok": True}])          # calls_today=2 > block=1
v = gate.check_calls("claude", quiet=True)
n_before = len(_sent)
gate.notify_blocked(v)
gate.notify_blocked(v)                                     # 같은 날 2번째
# [자기리뷰 F4] 이벤트는 일자별 파일로 나뉜다 — 판독은 alerts.read_events 로.
n_events = sum(1 for e in alerts.read_events() if e.get("kind") == "gate_block")
check("③ 차단 이벤트 장부 기록", n_events == 2, f"events={n_events}")
# [Batch C] gate 는 **발송하지 않는다** — 사실만 남긴다. 알릴지는 dispatch 가 정한다.
# 그래서 두 번 차단돼도 장부는 2줄, 발송은 1회다(정책이 한 곳에 있으므로 특례가 없다).
alerts.dispatch()
alerts.dispatch()
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
_reset_alert("task-fail")
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
# [Batch B/C] 사용 로그 판독은 health 로 옮겼다 — 지키는 성질은 같다(빈 출력을 성공으로
# 기록하지 않는가). 이게 무너지면 인증 만료가 ok:true 로 남아 가짜 침묵이 된다(B-F1).
from kv_core import health as _hz
_auth = _hz._claude_auth(today)
check("B-F1 빈 출력(인증 만료 증상) → ok:false 기록",
      _auth["consecutive_failures"] >= 1 and _auth["failure_today"], f"{_auth}")

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
_reset_alert("never-")
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
_reset_alert("never-")
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

# ══════════════════════════════════════════════════════════════════════
# [W9 재설계 Batch C] **백오프 30일 시뮬** — B-M1 의 직접 실증 (검증 사각 ⑤)
#
# 리뷰 B-M1: "never-succeeded 에 백오프·상한·에스컬레이션이 전무 — 매일 같은 문장을 영원히.
#            first_attempt=8/4 고정, 30일 시뮬 → **27일 연속 발동 · 마커 27개**.
#            첫: '3일간 성공 0' → 끝: '29일간 성공 0'. 일수만 바뀐다."
# 실환경에서도 never-succeeded-judge 가 8/7~8/10 **4일 연속** 나갔다.
#
# 여기서 지키는 성질은 두 가지고, 둘 다 필요하다:
#   ① 30일 동안 발송이 **크게 줄어든다**            (alert fatigue 방지)
#   ② 그렇다고 **끊기지는 않는다**                  (가짜 침묵 방지 — 더 큰 실패 모드)
print()
print("[Batch C] 백오프 30일 시뮬")
from datetime import date as _date
_reg.load = lambda *a, **k: [t_never]
scheduler.write_watermark(t_never, {"last_attempt": today.isoformat(),
                                    "first_attempt": (today - timedelta(days=10)).isoformat()})
_reset_alert("never-")
_sim_sent = []
notify.slack = lambda text, **kw: (_sim_sent.append(text), True)[1]
_days_fired = []
for i in range(30):
    day = today + timedelta(days=i)
    scheduler.write_watermark(t_never, {"last_attempt": day.isoformat(),
                                        "first_attempt": (today - timedelta(days=10)).isoformat()})
    if alerts.dispatch(today=day):
        _days_fired.append(i)
_reg.load = _orig_load
check("백오프: 30일 중 발송이 10회 이하로 줄어든다(구: 30회)",
      len(_days_fired) <= 10, f"발송 {len(_days_fired)}회 · 발동일={_days_fired}")
check("백오프: 그래도 끊기지 않는다(마지막 7일 안에 최소 1회)",
      any(d >= 23 for d in _days_fired), f"발동일={_days_fired}")
check("백오프: 간격이 점점 벌어진다",
      len(_days_fired) > 2 and (_days_fired[-1] - _days_fired[-2]) > (_days_fired[1] - _days_fired[0]),
      f"초기간격={_days_fired[1]-_days_fired[0]} 후기간격={_days_fired[-1]-_days_fired[-2]}")
# 지키는 성질: 반복분에 **얼마나 오래 반복했는지**가 실려 배경 소음화를 막는다.
# 문구는 "며칠째 반복" 기준 하나만 쓴다 — 본문(health note)이 이미 "문제가 며칠째"를
# 말하므로, 한 메시지에 서로 다른 기준의 숫자 두 개가 나오면 사람이 어느 쪽을 믿을지 헷갈린다.
check("에스컬레이션: 반복분에 반복 횟수·기간이 실린다",
      any("같은 알림" in x and "일간 반복" in x for x in _sim_sent),
      f"{[x[:44] for x in _sim_sent[-2:]]}")

# ══════════════════════════════════════════════════════════════════════
# [자기 적대 리뷰 회귀] 재설계가 만든 결함 5건 — 전부 프로브로 실증된 것들이다
print()
print("[자기리뷰] 알림 발송 정책 5건")
from datetime import date as _d
from kv_core.health import SystemState as _SS, TaskState as _TS

_D = _d(2026, 8, 10)


def _fresh(sub: str):
    """이 블록 전용 스크래치 — 앞 시나리오의 마커·정책이 섞이지 않게."""
    import shutil
    for name in ("sent", "policy"):
        d = paths.data_root() / "_alerts" / name
        if d.exists():
            shutil.rmtree(d)


# F1: 관측과 dispatch 사이에 UTC 자정이 끼면 사실이 영구 유실됐다.
# 실측: 승인 틱 390회 중 1시간 초과 공백 21건, 그중 UTC 자정을 넘긴 것이 6일간 3건(최대 19.5h).
# 재관측이 없는 사실(고아 초안·게이트 경고 교차·틱 오류)은 그러면 아무도 다시 못 본다.
_fresh("f1")
alerts.append_event("orphan_draft", identity="orphan-midnight", text="🔴 고아 초안",
                    ts="2026-08-09T23:50:00+00:00")
_after = [i for i, _ in alerts._from_events(_D)]
check("F1 UTC 자정을 넘겨도 사실이 살아남는다", "orphan-midnight" in _after, str(_after))

# F2-a: alerts.yaml 값 오타 하나로 dispatch 가 통째로 죽어 그 틱 전 알림이 미발송됐다.
# 알림은 안전망이므로 설정 사고에 죽지 않고 기본값으로 계속 돌아야 한다 — 대신 조용히는 아니다.
_fresh("f2a")
_saved_cfg = alerts._cfg
alerts._cfg = {"backoff": {"escalate_after_sends": "4회", "rare_every_days": "7일"}}
alerts._cfg_warned.clear()
_r = alerts.dispatch(state=None, today=_D)
check("F2a 설정 값 오타에도 dispatch 가 살아남는다", isinstance(_r, list))
check("F2a 설정이 깨졌다는 사실 자체를 알린다",
      any("config-broken" in x for x in _r), str(_r))
alerts._cfg = _saved_cfg

# F2-b: `_try` 가 순회 중인 후보 리스트에 append 해서, _should_send 가 전역 실패하면
# 후보가 무한 증식했다. 그 프로세스는 heavy_slot("approvals")을 쥔 채 살아 있어 stale
# 회수 대상이 아니므로, 이후 모든 틱이 SlotBusy 로 물러나 **승인 리컨실러가 영구 정지**한다.
_fresh("f2b")
import signal as _sig
_orig_ss = alerts._should_send
alerts._should_send = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("주입"))
_sig.signal(_sig.SIGALRM, lambda *a: (_ for _ in ()).throw(TimeoutError("무한 루프")))
_sig.alarm(10)
try:
    alerts.dispatch(state=None, today=_D)
    check("F2b 발송 경로 전역 실패에도 dispatch 가 끝난다(무한 증식 없음)", True)
except TimeoutError:
    check("F2b 발송 경로 전역 실패에도 dispatch 가 끝난다(무한 증식 없음)", False, "10초 초과")
finally:
    _sig.alarm(0)
    alerts._should_send = _orig_ss

# F3: `_forget` 이 "이틀 조용하면 해결"을 last_sent 로 판정해 위상에 의존했다.
# 간헐 장애(맥 슬립으로 하루 걸러 도는 이 환경의 정상)에는 정책이 매번 지워져
# 간격이 2일에서 멈추고 count 가 리셋돼 에스컬레이션이 한 번도 안 나갔다(30일 15회).
_fresh("f3")


def _st_problem():
    t = _TS(name="judge", skill="s", cadence="daily", runner="local", shadow=False,
            status="never_bad", late_days=9, limit_days=1, last_success=None,
            last_attempt="x", first_seen="2026-08-01", note="배선 후 9일간 성공 0")
    return _SS(now=_D, tasks=[t])


# 이 블록만의 캡처 — 앞 시나리오가 notify.slack 을 갈아끼운 채 두면 여기서 발화한 문구가
# 엉뚱한 리스트로 가서 "미도달"로 보인다(검증 사각 ④가 이 파일 안에서 재현됐다).
_f3_sent: list = []
_saved_slack = notify.slack
notify.slack = lambda text, **kw: (_f3_sent.append(text), True)[1]
try:
    _fired = [i for i in range(30)
              if alerts.dispatch(_st_problem() if i % 3 != 2 else _SS(now=_D, tasks=[]),
                                 today=_D + timedelta(days=i))]
finally:
    notify.slack = _saved_slack                  # 반드시 원복
check("F3 간헐 장애에서도 백오프가 쌓인다(30일 10회 이하)", len(_fired) <= 10,
      f"발동 {len(_fired)}회 · {_fired}")
check("F3 에스컬레이션 문구까지 도달한다",
      any("같은 알림" in x for x in _f3_sent),
      f"{[x[:36] for x in _f3_sent[-2:]]}")

# F5: 발송 이력이 미래를 가리키면 영원히 백오프에 걸려 아무 로그 없이 침묵했다.
# `dispatch(today=)` 는 공개 인자라 시뮬 1회·시계 어긋남·백업 복원이면 도달한다.
# **정책 파일에 last_sent 를 저장하던 시절의 문제**이고, 지금은 마커가 정본이라
# "미래 마커는 세지 않는다"로 같은 성질을 지킨다(저장된 사본이 없으니 어긋날 것도 없다).
_fresh("f5")
_sent_dir = paths.data_root() / "_alerts" / "sent"
_sent_dir.mkdir(parents=True, exist_ok=True)
(_sent_dir / "future-test-2026-09-19").touch()          # 미래 발송 마커
_ok, _st = alerts._should_send("future-test", _D)
check("F5 미래 발송 이력은 무시하고 지금 보낸다(영구 침묵 방지)",
      _ok is True, f"{_ok} · {_st}")
check("F5 정책 파일은 유도 불가능한 것만 저장한다(last_seen 하나)",
      set(_st) == {"last_seen"}, f"{sorted(_st)}")

# [자기감사] 발송 이력은 **마커에서 유도**한다 — 저장된 사본과 어긋날 수 없다.
# 실데이터에서 실제로 어긋나 있었다(마커 4건인데 정책 count=0 → 에스컬레이션 영구 미발동).
_fresh("f6")
_sent_dir.mkdir(parents=True, exist_ok=True)     # _fresh 가 지웠으므로 다시 만든다
for d in ("2026-08-07", "2026-08-08", "2026-08-10"):
    (_sent_dir / f"derive-test-{d}").touch()
check("유도: 발송일 목록이 마커와 일치",
      alerts.sent_days("derive-test") == ["2026-08-07", "2026-08-08", "2026-08-10"],
      str(alerts.sent_days("derive-test")))
_n, _age = alerts._history("derive-test", _D)
check("유도: 횟수·경과일이 마커에서 계산된다", (_n, _age) == (3, 3), f"n={_n} age={_age}")

print()
if FAIL:
    print(f"❌ 실패: {FAIL}")
    raise SystemExit(1)
print("✅ 실패 푸시 전부 통과")
