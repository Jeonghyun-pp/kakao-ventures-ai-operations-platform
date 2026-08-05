"""[중간점검 D13] orrery 합성 검증 — read-only 계약을 기계로 지킨다.

교훈(패턴 재발 스코어카드): "read-only" 같은 계약은 주석이 아니라 **기계 검사**로만 지켜진다.
여기의 AST 검사가 orrery 에 쓰기 코드가 들어오는 순간 깨진다 — 그게 의도다.

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_orrery.py  (외부 부작용 0·슬랙 0)
"""
from __future__ import annotations

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

print()
if FAIL:
    print(f"❌ 실패: {FAIL}")
    raise SystemExit(1)
print("✅ orrery 합성 전부 통과")
