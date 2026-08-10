"""[중간점검 D13 · W9 재설계 Batch B] 관측 평면 합성 검증.

교훈(패턴 재발 스코어카드): "read-only" 같은 계약은 주석이 아니라 **기계 검사**로만 지켜진다.
여기의 AST 검사가 관측 모듈에 쓰기 코드가 들어오는 순간 깨진다 — 그게 의도다.

[W9 재설계] 판정이 `health` 로 옮겨가면서 이 파일도 둘로 나뉜다.
  · **판정** 단언 → `health.*` 를 부른다 (신선도·해결여부·TZ 정규화)
  · **렌더링** 단언 → `orrery.render_*` 에 `SystemState` 를 넣는다
이 분리 자체가 회귀 잠금이다 — orrery 에 판정이 다시 생기면 여기서 부를 것이 없어진다.

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_orrery.py  (외부 부작용 0·슬랙 0)
"""
from __future__ import annotations

import os
os.environ.setdefault("KV_NO_SEND", "1")   # [W9] 실발송 킬 스위치 — 스텁 누락 시의 최후 방어

import ast
import json
import sys
from datetime import date, timedelta
from pathlib import Path

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import health, orrery, paths  # noqa: E402
from kv_core.health import FailureState, SystemState, TaskState  # noqa: E402
from kv_core.registry import Task  # noqa: E402

FAIL: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


def ts(name="t", **kw) -> TaskState:
    base = dict(name=name, skill="s", cadence="daily", runner="local", shadow=False,
                status="ok", late_days=0, limit_days=1, last_success=None,
                last_attempt=None, first_seen=None, note="")
    base.update(kw)
    return TaskState(**base)


def state(**kw) -> SystemState:
    base = dict(now=date(2026, 8, 5), tasks=[], failures=[],
                approvals={"undecided": 0, "approved_pending_exec": 0,
                           "today": {"approved": 0, "rejected": 0, "executed": 0},
                           "total": 0, "undecided_rows": []},
                dlq=[], alerts_today=[],
                costs={"claude": {"calls": 0, "warn": None, "block": None},
                       "apify": {"available": False}},
                log_last_touch=None)
    base.update(kw)
    return SystemState(**base)


print("=== 관측 평면 합성 검증 ===\n")

# ── ① read-only 계약 AST 검증 (orrery + health 둘 다) ──────────────────────
WRITERS = ("mkdir", "write_text", "write_bytes", "unlink", "atomic_write",
           "append_event", "rmdir", "touch", "rename", "replace")
for mod in (orrery, health):
    src = Path(mod.__file__).read_text(encoding="utf-8")
    violations: list[str] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                for arg in node.args[1:2]:
                    if isinstance(arg, ast.Constant) and any(m in str(arg.value) for m in ("w", "a", "+")):
                        violations.append(f"open 쓰기모드 @L{node.lineno}")
            if isinstance(node.func, ast.Attribute) and node.func.attr in WRITERS:
                violations.append(f".{node.func.attr} @L{node.lineno}")
    check(f"① read-only AST({mod.__name__.split('.')[-1]}): 쓰기 호출 0건",
          not violations, f"위반={violations}")

# ── ② 빈 KV_DATA 에서 mkdir 부작용 0 (D1 회귀) ─────────────────────────────
root = paths.data_root()
before = {str(p) for p in root.rglob("*")} if root.exists() else set()
orrery.gather(with_network=False)
after = {str(p) for p in root.rglob("*")} if root.exists() else set()
check("② gather 가 파일시스템을 안 바꿈(mkdir 0)", before == after,
      f"신규={sorted(after - before)[:3]}")

# ── ③ 파손 워터마크 내성 (D2 회귀) ─────────────────────────────────────────
from kv_core import job, scheduler  # noqa: E402
t = Task(name="syn-corrupt", skill="_orrery-syn", cadence="daily",
         catch_up="coalesce", watermark="last_success")
job.atomic_write(scheduler._wm_path(t), json.dumps({"last_success": "corrupt-value"}))
s3 = health.compute(now=date(2026, 8, 5), tasks=[t]).tasks[0]
check("③ 파손 워터마크 → ⚠️ 강등(크래시 0)",
      s3.icon == "⚠️" and "파손" in s3.note and s3.alertable is False, f"{s3.status} {s3.note}")

# ── ④ 신선도 표 (D9: daily 유예 0, weekly 유예 1) ──────────────────────────
def fr(cad, days_ago):
    tt = Task(name="syn-f", skill="_orrery-fr", cadence=cad, catch_up="coalesce",
              watermark="last_success")
    ls = (date(2026, 8, 10) - timedelta(days=days_ago)).isoformat()
    job.atomic_write(scheduler._wm_path(tt),
                     json.dumps({"last_success": ls, "last_attempt": ls, "first_attempt": ls}))
    return health.compute(now=date(2026, 8, 10), tasks=[tt]).tasks[0].icon


cases = [("daily", 1, "✅"), ("daily", 2, "🔴"),          # daily: 주기 초과 즉시 🔴
         ("weekly", 7, "✅"), ("weekly", 8, "⚠️"), ("weekly", 9, "🔴")]
ok4 = all(fr(c, n) == want for c, n, want in cases)
check("④ 신선도 임계(daily 유예0·weekly 유예1)", ok4,
      f"{[(c, n, fr(c, n)) for c, n, _ in cases]}")

# ── ⑤ 헬스 ts TZ 정규화 (D4) ───────────────────────────────────────────────
d_naive = health._ts_to_utc_date("2026-08-05T01:30:00")        # naive=KST 01:30 → UTC 08-04
d_aware = health._ts_to_utc_date("2026-08-05T01:30:00+00:00")  # aware UTC → 그대로
check("⑤ naive ts=KST 간주 UTC 변환", d_naive == date(2026, 8, 4) and d_aware == date(2026, 8, 5),
      f"naive→{d_naive}, aware→{d_aware}")

# ── ⑥ DLQ 파손 센티널 (D5) ─────────────────────────────────────────────────
s6 = state(dlq=[{"skill": "s", "file": "a.jsonl", "count": 3, "bytes": 10},
                {"skill": "s", "file": "b.jsonl", "count": -1, "bytes": -1}])
txt = orrery.render_text(s6)
sl = orrery.render_slack(s6)
check("⑥ 파손 DLQ: 합산 오염 0 + 양쪽 렌더 표기",
      "3건 · 파손 파일 1개" in txt and "파손" in sl and "미설정" in txt)

# ── ⑦ 실패 잡 파손 정렬 (D8) ───────────────────────────────────────────────
s7 = state(failures=[FailureState("s", "j-broken", "", False, True),
                     FailureState("s", "j-recent", "2026-08-05T01:00", False, False)])
sl3 = orrery.render_slack(s7)
check("⑦ 슬랙 '최근' 자리에 파손 아닌 잡", "j-recent" in sl3)

# ── ⑧ [W9] 해결된 실패 강등 (alert fatigue 방지) ────────────────────────────
print()
SK = "_orrery-w9"
# (a) 실패 후 같은 base 의 -r2 가 성공 → 해결됨
job.create(SK, {"t": 1}, "tk-2026-08-06")
job.transition(SK, "tk-2026-08-06", "queued"); job.transition(SK, "tk-2026-08-06", "running")
job.transition(SK, "tk-2026-08-06", "failed")
job.create(SK, {"t": 1}, "tk-2026-08-06-r2")
job.transition(SK, "tk-2026-08-06-r2", "queued"); job.transition(SK, "tk-2026-08-06-r2", "running")
job.transition(SK, "tk-2026-08-06-r2", "validating"); job.transition(SK, "tk-2026-08-06-r2", "completed")
# (b) 재시도 없이 계속 실패 → 미해결
job.create(SK, {"t": 1}, "solo-2026-08-06")
job.transition(SK, "solo-2026-08-06", "queued"); job.transition(SK, "solo-2026-08-06", "running")
job.transition(SK, "solo-2026-08-06", "failed")

fails = health._failures(date(2026, 8, 10), [])
by = {f.job: f.resolved for f in fails}
check("⑧ -r2 성공 → 원 실패 '해결됨' 강등", by.get("tk-2026-08-06") is True, f"{by}")
check("⑧ 재시도 없는 실패 → 미해결 유지", by.get("solo-2026-08-06") is False)

s8 = state(failures=[FailureState(SK, "tk-2026-08-06", "2026-08-06T00:00", True, False),
                     FailureState(SK, "solo-2026-08-06", "2026-08-06T01:00", False, False)])
txt8, sl8 = orrery.render_text(s8), orrery.render_slack(s8)
check("⑧ 텍스트 렌더: 미해결 1 · 해결됨 1 표기",
      "미해결 1건 · 해결됨 1건" in txt8 and "solo-2026-08-06" in txt8
      and "tk-2026-08-06 " not in txt8)
check("⑧ 슬랙 렌더: 미해결만 카운트",
      "미해결 실패 잡 1건" in sl8 and "solo" in sl8, sl8.split("\n")[1] if "\n" in sl8 else sl8)

# ── ⑧b [W9 재설계 Batch A] **규모 경계** — cap 을 실제로 넘겨본다 (검증 사각 ③) ──
# 왜: 기존 ⑧ 은 실패를 2건만 만들어 cap(10)을 **한 번도 건드리지 않았다**. 그래서 C-H1
# (해결된 최신 실패가 상위 10칸을 채워 미해결이 잘림)을 원리적으로 못 봤다.
# 실환경에서는 meeting-mail 이 실패→r2 성공 패턴을 하루 2건씩 만들어 5일이면 도달한다.
print()
SKC = "_orrery-cap"
for i in range(12):                       # 해결된 실패 12건 — 전부 미해결보다 최신
    jid = f"noise-2026-08-0{6 if i < 4 else 7}-{i:02d}"
    job.create(SKC, {"t": 1}, jid)
    job.transition(SKC, jid, "queued"); job.transition(SKC, jid, "running")
    job.transition(SKC, jid, "failed")
    job.create(SKC, {"t": 1}, jid + "-r2")
    job.transition(SKC, jid + "-r2", "queued"); job.transition(SKC, jid + "-r2", "running")
    job.transition(SKC, jid + "-r2", "validating"); job.transition(SKC, jid + "-r2", "completed")
for jid in ("real-2026-08-04", "real-2026-08-05"):        # 진짜 미해결 2건
    job.create(SKC, {"t": 1}, jid)
    job.transition(SKC, jid, "queued"); job.transition(SKC, jid, "running")
    job.transition(SKC, jid, "failed")

fc = health._failures(date(2026, 8, 10), [])
unresolved_c = [f.job for f in fc if f.skill == SKC and not f.resolved]
check("⑧b cap 을 넘겨도 미해결이 잘리지 않는다 (해결 12 + 미해결 2)",
      set(unresolved_c) == {"real-2026-08-04", "real-2026-08-05"},
      f"미해결={sorted(unresolved_c)}")
sl_c = orrery.render_slack(state(failures=fc))
check("⑧b 슬랙이 미해결 2건을 침묵시키지 않는다", "미해결 실패 잡" in sl_c)

# ── ⑨ [W9] freshness 선언 우선 (판정은 health, 선언은 cadence) ──────────────
from kv_core import cadence as _cad  # noqa: E402
t_decl = Task(name="f-decl", skill="_orrery-d", cadence="daily", catch_up="coalesce",
              watermark="last_success", freshness_days=5)
t_auto = Task(name="f-auto", skill="_orrery-d", cadence="daily", catch_up="coalesce",
              watermark="last_success")
check("⑨ freshness_days 선언 우선", _cad.freshness_limit(t_decl) == 5)
check("⑨ 미지정 시 주기 기반(daily=유예0)", _cad.freshness_limit(t_auto) == 1)


def decl_icon(days_ago: int) -> str:
    ls = (date(2026, 8, 10) - timedelta(days=days_ago)).isoformat()
    job.atomic_write(scheduler._wm_path(t_decl),
                     json.dumps({"last_success": ls, "last_attempt": ls, "first_attempt": ls}))
    return health.compute(now=date(2026, 8, 10), tasks=[t_decl]).tasks[0].icon


check("⑨ 선언 5일: 4일 경과는 🔴 아님", decl_icon(4) != "🔴")
check("⑨ 선언 5일: 6일 경과는 🔴", decl_icon(6) == "🔴")

# ── ⑩ [W9 재설계] 승인 통계가 표면에서 사라지지 않는다 (A-M6) ────────────────
# 예전 렌더러는 `pending()` 만 봤고 `_is_closed` 가 거부·실행완료를 거기서 뺐다 →
# `❌거부` 는 **어떤 입력에도 0인 죽은 카운터**였다.
print()
s10 = state(approvals={"undecided": 1, "approved_pending_exec": 2,
                       "today": {"approved": 3, "rejected": 4, "executed": 5},
                       "total": 12, "undecided_rows": [{"key": "k1", "kind": "mail", "task": "t"}]})
txt10, sl10 = orrery.render_text(s10), orrery.render_slack(s10)
check("⑩ 텍스트: 오늘 거부·실행완료가 표면에 남는다",
      "❌4" in txt10 and "📤5" in txt10 and "누적 12" in txt10,
      [l for l in txt10.split("\n") if "승인 큐" in l])
check("⑩ 슬랙: 오늘 활동 요약이 실린다", "❌4" in sl10 and "📤5" in sl10, sl10)

print()
if FAIL:
    print(f"❌ 실패: {FAIL}")
    raise SystemExit(1)
print("✅ orrery 합성 전부 통과")
