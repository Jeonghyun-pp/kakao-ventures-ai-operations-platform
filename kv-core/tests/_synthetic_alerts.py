"""[W8 배치4] 실패 푸시 합성 검증 — notify 스텁(실발송 0)·스크래치 KV_DATA.

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_alerts.py
"""
from __future__ import annotations

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
    d = paths.data_root() / "_usage" / "claude"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day.isoformat()}.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


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
_usage_write(today, [{"ok": False, "err": "timeout"}])
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
check("⑤ 마커 선점 → 발송 0·False", ok5 is False and len(_sent) == n_before)

print()
if FAIL:
    print(f"❌ 실패: {FAIL}")
    raise SystemExit(1)
print("✅ 실패 푸시 전부 통과")
