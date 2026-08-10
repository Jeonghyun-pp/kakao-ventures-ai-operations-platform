"""[중간점검 D13] orrery 합성 검증 — read-only 계약을 기계로 지킨다.

교훈(패턴 재발 스코어카드): "read-only" 같은 계약은 주석이 아니라 **기계 검사**로만 지켜진다.
여기의 AST 검사가 orrery 에 쓰기 코드가 들어오는 순간 깨진다 — 그게 의도다.

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_orrery.py  (외부 부작용 0·슬랙 0)
"""
from __future__ import annotations

import os
os.environ.setdefault("KV_NO_SEND", "1")   # [W9] 실발송 킬 스위치 — 스텁 누락 시의 최후 방어

import ast
import json
import os
import sys
from datetime import date, timedelta, timezone, datetime
from pathlib import Path

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import orrery, paths  # noqa: E402
from kv_core.registry import Task  # noqa: E402

FAIL: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


print("=== orrery 합성 검증 ===\n")

# ── ① read-only 계약 AST 검증 ──────────────────────────────────────────────
src = Path(orrery.__file__).read_text(encoding="utf-8")
tree = ast.parse(src)
violations: list[str] = []
for node in ast.walk(tree):
    if isinstance(node, ast.Call):
        # open(..., "w"/"a") — 쓰기 모드 오픈
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            for arg in node.args[1:2]:
                if isinstance(arg, ast.Constant) and any(m in str(arg.value) for m in ("w", "a", "+")):
                    violations.append(f"open 쓰기모드 @L{node.lineno}")
        # .mkdir / .write_text / .write_bytes / .unlink / atomic_write / append_event
        if isinstance(node.func, ast.Attribute) and node.func.attr in (
                "mkdir", "write_text", "write_bytes", "unlink", "atomic_write",
                "append_event", "rmdir", "touch", "rename", "replace"):
            violations.append(f".{node.func.attr} @L{node.lineno}")
check("① read-only AST: 쓰기 호출 0건", not violations, f"위반={violations}")

# ── ② 빈 KV_DATA 에서 mkdir 부작용 0 (D1 회귀) ─────────────────────────────
root = paths.data_root()
before = {str(p) for p in root.rglob("*")} if root.exists() else set()
d = orrery.gather(with_network=False)
after = {str(p) for p in root.rglob("*")} if root.exists() else set()
check("② gather 가 파일시스템을 안 바꿈(mkdir 0)", before == after,
      f"신규={sorted(after - before)[:3]}")

# ── ③ 파손 워터마크 내성 (D2 회귀) ─────────────────────────────────────────
from kv_core import scheduler, job
t = Task(name="syn-corrupt", skill="_orrery-syn", cadence="daily",
         catch_up="coalesce", watermark="last_success")
job.atomic_write(scheduler._wm_path(t), json.dumps({"last_success": "corrupt-value"}))
row = orrery._freshness(t, "corrupt-value", None, date(2026, 8, 5))
check("③ 파손 워터마크 → ⚠️ 강등(크래시 0)", row["status"] == "⚠️" and "파손" in row["note"],
      f"{row}")

# ── ④ 신선도 표 (D9: daily 유예 0, weekly 유예 1) ──────────────────────────
def fr(cad, days_ago):
    tt = Task(name="syn-f", skill="s", cadence=cad, catch_up="coalesce",
              watermark="last_success")
    ls = (date(2026, 8, 10) - timedelta(days=days_ago)).isoformat()
    return orrery._freshness(tt, ls, None, date(2026, 8, 10))["status"]

cases = [("daily", 1, "✅"), ("daily", 2, "🔴"),          # daily: 주기 초과 즉시 🔴
         ("weekly", 7, "✅"), ("weekly", 8, "⚠️"), ("weekly", 9, "🔴")]
ok4 = all(fr(c, n) == want for c, n, want in cases)
check("④ 신선도 임계(daily 유예0·weekly 유예1)", ok4,
      f"{[(c, n, fr(c, n)) for c, n, _ in cases]}")

# ── ⑤ 헬스 ts TZ 정규화 (D4) ───────────────────────────────────────────────
d_naive = orrery._ts_to_utc_date("2026-08-05T01:30:00")        # naive=KST 01:30 → UTC 08-04
d_aware = orrery._ts_to_utc_date("2026-08-05T01:30:00+00:00")  # aware UTC → 그대로
check("⑤ naive ts=KST 간주 UTC 변환", d_naive == date(2026, 8, 4) and d_aware == date(2026, 8, 5),
      f"naive→{d_naive}, aware→{d_aware}")

# ── ⑥ DLQ 파손 센티널 (D5) ─────────────────────────────────────────────────
d2 = {"now": "2026-08-05", "log_last_touch": None,
      "tasks": [], "failures": [],
      "dlq": [{"skill": "s", "file": "a.jsonl", "count": 3},
              {"skill": "s", "file": "b.jsonl", "count": -1}],
      "approvals": [], "cost": {"claude": {"calls": 0, "warn": None, "block": None},
                                "apify": {"available": False}}}
txt = orrery.render_text(d2)
sl = orrery.render_slack(d2)
check("⑥ 파손 DLQ: 합산 오염 0 + 양쪽 렌더 표기",
      "3건 · 파손 파일 1개" in txt and "파손" in sl and "미설정" in txt,
      "")

# ── ⑦ 실패 잡 파손 정렬 (D8) ───────────────────────────────────────────────
d3 = dict(d2, failures=[{"skill": "s", "job": "j-broken", "ts": "", "broken": True},
                        {"skill": "s", "job": "j-recent", "ts": "2026-08-05T01:00", "broken": False}])
sl3 = orrery.render_slack(d3)
check("⑦ 슬랙 '최근' 자리에 파손 아닌 잡", "j-recent" in sl3 and "j-broken" not in sl3.split("\n")[1] if len(sl3.split("\n")) > 1 else True, sl3)

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

fails = orrery._recent_failures()
by = {f["job"]: f.get("resolved") for f in fails}
check("⑧ -r2 성공 → 원 실패 '해결됨' 강등", by.get("tk-2026-08-06") is True, f"{by}")
check("⑧ 재시도 없는 실패 → 미해결 유지", by.get("solo-2026-08-06") is False)

d8 = dict(d2, failures=[{"skill": SK, "job": "tk-2026-08-06", "ts": "2026-08-06T00:00",
                         "broken": False, "resolved": True},
                        {"skill": SK, "job": "solo-2026-08-06", "ts": "2026-08-06T01:00",
                         "broken": False, "resolved": False}])
txt8, sl8 = orrery.render_text(d8), orrery.render_slack(d8)
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
# 해결된 실패 12건 — 전부 **미해결보다 최신**이라 정렬 상위를 점유한다
for i in range(12):
    jid = f"noise-2026-08-0{6 if i < 4 else 7}-{i:02d}"
    job.create(SKC, {"t": 1}, jid)
    job.transition(SKC, jid, "queued"); job.transition(SKC, jid, "running")
    job.transition(SKC, jid, "failed")
    job.create(SKC, {"t": 1}, jid + "-r2")
    job.transition(SKC, jid + "-r2", "queued"); job.transition(SKC, jid + "-r2", "running")
    job.transition(SKC, jid + "-r2", "validating"); job.transition(SKC, jid + "-r2", "completed")
# 진짜 미해결 2건
for jid in ("real-2026-08-04", "real-2026-08-05"):
    job.create(SKC, {"t": 1}, jid)
    job.transition(SKC, jid, "queued"); job.transition(SKC, jid, "running")
    job.transition(SKC, jid, "failed")

fc = orrery._recent_failures()
unresolved_c = [f["job"] for f in fc if f["skill"] == SKC and not f.get("resolved")]
check("⑧b cap 을 넘겨도 미해결이 잘리지 않는다 (해결 12 + 미해결 2)",
      set(unresolved_c) == {"real-2026-08-04", "real-2026-08-05"},
      f"미해결={sorted(unresolved_c)}")
sl_c = orrery.render_slack(dict(d2, failures=fc))
check("⑧b 슬랙이 미해결 2건을 침묵시키지 않는다", "미해결 실패 잡" in sl_c, sl_c.split("\n")[1] if "\n" in sl_c else sl_c)

# ── ⑨ [W9] freshness 선언 우선 (orrery·alerts 공용 헬퍼) ────────────────────
from kv_core import cadence as _cad
t_decl = Task(name="f-decl", skill="s", cadence="daily", catch_up="coalesce",
              watermark="last_success", freshness_days=5)
t_auto = Task(name="f-auto", skill="s", cadence="daily", catch_up="coalesce",
              watermark="last_success")
check("⑨ freshness_days 선언 우선", _cad.freshness_limit(t_decl) == 5)
check("⑨ 미지정 시 주기 기반(daily=유예0)", _cad.freshness_limit(t_auto) == 1)
ls5 = (date(2026, 8, 10) - timedelta(days=4)).isoformat()
check("⑨ 선언 5일: 4일 경과는 🔴 아님",
      orrery._freshness(t_decl, ls5, None, date(2026, 8, 10))["status"] != "🔴")
ls6 = (date(2026, 8, 10) - timedelta(days=6)).isoformat()
check("⑨ 선언 5일: 6일 경과는 🔴",
      orrery._freshness(t_decl, ls6, None, date(2026, 8, 10))["status"] == "🔴")

print()
if FAIL:
    print(f"❌ 실패: {FAIL}")
    raise SystemExit(1)
print("✅ orrery 합성 전부 통과")
