"""[W6] 워커 디스패치 합성 검증 — 실행·스킵·워터마크 규율. 외부 부작용 0.

증명:
  ① 배선 task: 러너 실행 + last_success 전진
  ② 미배선 task: due 여도 run_due 에 안 들어감 → 워터마크 파일 자체가 없음
  ③ shadow task: due 에 포함(검증용 병행 실행)
  ④ appstore 러너 파일 카운트 판정: 84→성공 / 82→성공+경고 / 40→실패

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_dispatch.py
"""
from __future__ import annotations

import os
os.environ.setdefault("KV_NO_SEND", "1")   # [W9] 실발송 킬 스위치 — 스텁 누락 시의 최후 방어

import os
import sys
from datetime import date
from pathlib import Path

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import paths, scheduler, workers  # noqa: E402
from kv_core.registry import Task  # noqa: E402

FAIL: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


NOW = date(2026, 8, 4)

print("=== 디스패치 합성 검증 ===\n")

# ①+② 배선 vs 미배선
wired_t = Task(name="syn-wired", skill="_dispatch-syn", cadence="daily",
               catch_up="coalesce", watermark="last_success")
unwired_t = Task(name="syn-unwired", skill="_dispatch-syn", cadence="daily",
                 catch_up="coalesce", watermark="last_success")

calls: list[str] = []
report = scheduler.run_due([wired_t], lambda t, p: (calls.append(t.name), True)[1], now=NOW)
wm = scheduler.read_watermark(wired_t)
check("① 배선 task 실행 + last_success 전진",
      calls == ["syn-wired"] and wm.get("last_success") == NOW.isoformat(),
      f"calls={calls}, wm={wm}")

# 미배선: scheduler.main 의 필터를 재현 — run_due 에 안 넣는 것 자체가 규율
dues = scheduler.due_tasks([unwired_t], now=NOW)
check("② 미배선 task 는 due 로 감지되나(스킵 로그 대상)", len(dues) == 1)
check("② 미배선 워터마크 파일 없음(불변)",
      not scheduler._wm_path(unwired_t).exists())

# ③ shadow 가 due 에 포함
shadow_t = Task(name="syn-shadow", skill="_dispatch-syn", cadence="daily",
                catch_up="coalesce", watermark="last_success",
                runner="github-actions", shadow=True)
plain_ext = Task(name="syn-ext", skill="_dispatch-syn", cadence="daily",
                 catch_up="coalesce", watermark="last_success", runner="github-actions")
dues2 = scheduler.due_tasks([shadow_t, plain_ext], now=NOW)
names = [d.task.name for d in dues2]
check("③ shadow=due 포함 · 일반 외부러너=제외", names == ["syn-shadow"], f"dues={names}")

# ④ appstore 러너 파일 카운트 판정 (subprocess 는 no-op 으로 대체)
print()
import subprocess

# 계약 불변식 대응 — docs/contracts/11-state-schemas.md PART C.
# 이 파일이 실제로 재는 것만 적는다. 이름만 걸어두면 커버리지 숫자가 거짓말이 된다.
#   I5  배선 task 실행 후 last_success 전진 · 미배선은 워터마크 미생성
#   I22 정당하게 빈 차트 1개는 결손이 아니다 — 측정된 0 과 미상을 가른다
CONTRACT_INVARIANTS = ['I5', 'I22']


class _P:
    returncode = 0
    stdout = ""
    stderr = ""


# 🔴 [2026-08-16 · 결정 ⑫] 수집기(`board.py`)만 스텁하고 **판정기는 진짜로 돌린다.**
#    판정이 `kv_core` 에서 `skills/net-sourcing/project/tools/board_health.py` 로
#    옮겨갔으므로, 여기서 그것도 스텁하면 이 검사는 아무것도 안 재게 된다
#    (스텁이 스텁을 부르는 초록). 판정기는 실제 subprocess 로 태운다 —
#    `KV_DATA` 가 상속되므로 아래에서 만드는 스크래치 스냅샷을 그대로 읽는다.
_real_run = workers.subprocess.run


def _run_stub(cmd, *a, **kw):
    if any("board_health" in str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else [cmd])):
        return _real_run(cmd, *a, **kw)
    return _P()


workers.subprocess.run = _run_stub

# 판정기를 모듈로도 쓴다(수치 단언용). 같은 프로세스라 SNAP_DIR 이 스크래치를 가리킨다.
sys.path.insert(0, str(paths.repo_root() / "skills" / "net-sourcing" / "project"))
from tools import board_health as _bh  # noqa: E402
snap = paths.data("net-sourcing", "snapshots")
app_t = Task(name="appstore-snapshot", skill="net-sourcing", cadence="daily",
             catch_up="coalesce", watermark="last_success",
             runner="github-actions", shadow=True)


def _fill(n):
    for f in snap.glob("appstore_board_*.json"):
        f.unlink()
    for i in range(n):
        # [T4 반영] 검증이 "존재"가 아니라 "온전함"을 재므로 유효(비어있지 않은) 배열이어야 함
        (snap / f"appstore_board_{i}_top-free_{date.today().isoformat()}.json").write_text('["app1","app2"]')


_fill(84)
check("④ 84파일 → 성공", workers.run_appstore_snapshot(app_t, NOW) is True)
_fill(82)
check("④ 82파일 → 성공(경고)", workers.run_appstore_snapshot(app_t, NOW) is True)
_fill(40)
check("④ 40파일 → 실패", workers.run_appstore_snapshot(app_t, NOW) is False)

# [W9 재설계 Batch E · R5] **진공과 결손을 가른다**
# 실환경 증상: 7006(카지노) 유료 차트가 8/4 부터 매일 `[]` — 한국 스토어에 원래 없는
# 차트라 그게 애플의 정답인데, 구 판정은 "비어있지 않은 배열만 유효"라 83/84 로 세어
# **매일 부분결손 경보**를 쐈다. 빈 배열은 잘 형성된 응답이고 0바이트·절단은 아니다.
for f in snap.glob("appstore_board_*.json"):
    f.unlink()
for i in range(84):
    body = "[]" if i == 7 else '["app1","app2"]'      # 1개만 정당하게 빈 차트
    (snap / f"appstore_board_{i}_top-free_{date.today().isoformat()}.json").write_text(body)
check("④ 진공: 정당하게 빈 차트 1개는 결손이 아니다(경보 없이 성공)",
      workers.run_appstore_snapshot(app_t, NOW) is True)
n_v, rows_v, empty_v = _bh.scan()
check("④ 진공: 빈 차트를 온전한 것으로 세되 목록으로 남긴다",
      n_v == 84 and len(empty_v) == 1, f"온전 {n_v} · 빈 {empty_v}")

# 반대 방향 — **전량 진공은 결손이다**(규칙 05 를 집계 수준에서 지킨다)
for f in snap.glob("appstore_board_*.json"):
    f.unlink()
for i in range(84):
    (snap / f"appstore_board_{i}_top-free_{date.today().isoformat()}.json").write_text("[]")
check("④ 전량 진공은 실패 처리(수집 고장)",
      workers.run_appstore_snapshot(app_t, NOW) is False)

# 0바이트·절단은 여전히 무효 — T4 보호가 약해지지 않았는지 확인
for f in snap.glob("appstore_board_*.json"):
    f.unlink()
for i in range(84):
    body = "" if i < 10 else '["app1","app2"]'
    (snap / f"appstore_board_{i}_top-free_{date.today().isoformat()}.json").write_text(body)
n_t4, _, _ = _bh.scan()
check("④ T4 보호 유지: 0바이트는 온전한 것으로 세지 않는다", n_t4 == 74, f"온전 {n_t4}(74 기대)")

# 🔴 [결정 ⑫] 층 분리 자체를 잠근다 — 코어가 앱스토어 판정 지식을 다시 갖지 않는다.
_wsrc = (paths.repo_root() / "kv-core" / "kv_core" / "workers.py").read_text(encoding="utf-8")


def _executable_only(src: str) -> str:
    """주석 **과 독스트링**을 뺀 실행 코드만. 둘 다 빼야 한다 —
    처음엔 주석만 걸렀더니 "왜 옮겼나"를 설명하는 독스트링이 걸려 거짓 실패가 났다.
    지식이 **문서로 남는 것**은 옳고, **코드로 남는 것**만 금지다."""
    import ast as _ast
    tree = _ast.parse(src)
    skip = set()
    for n in _ast.walk(tree):
        if (isinstance(n, _ast.Expr) and isinstance(n.value, _ast.Constant)
                and isinstance(n.value.value, str)):
            skip.update(range(n.lineno, (n.end_lineno or n.lineno) + 1))
    return "\n".join(l.split("#", 1)[0]
                     for i, l in enumerate(src.splitlines(), 1) if i not in skip)


_code = _executable_only(_wsrc)
check("④ 코어에 앱스토어 판정 지식이 없다(임계·파일명 패턴·세는 함수)",
      not any(k in _code for k in ("_board_files_today", "appstore_board_", "MIN_NONEMPTY")),
      "workers.py 에 도메인 지식이 되돌아왔다")
check("④ 판정기가 스킬 쪽에 있다",
      (paths.repo_root() / "skills" / "net-sourcing" / "project" / "tools"
       / "board_health.py").exists())

# 판정기의 **자체 경계 검사**도 여기서 태운다 — `tools/` 는 CI 의 잡 스캔(`jobs/*.py`)
# 밖이라, 걸어두지 않으면 그 6개 검사가 로컬에서만 돌고 CI 에선 영영 안 돈다.
_bhr = _real_run([str(paths.repo_root() / ".venv" / "bin" / "python"),
                  "tools/board_health.py", "--synthetic"],
                 cwd=paths.repo_root() / "skills" / "net-sourcing" / "project",
                 capture_output=True, text=True)
check("④ 판정기 자체 경계 검사 통과(5경계 + T4)", _bhr.returncode == 0,
      (_bhr.stdout or _bhr.stderr)[-300:])

print()
if FAIL:
    print(f"❌ 실패: {FAIL}")
    raise SystemExit(1)
print("✅ 디스패치 합성 전부 통과")
