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


class _P:
    returncode = 0


workers.subprocess.run = lambda *a, **kw: _P()
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
n_v, rows_v, empty_v = workers._board_files_today()
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
n_t4, _, _ = workers._board_files_today()
check("④ T4 보호 유지: 0바이트는 온전한 것으로 세지 않는다", n_t4 == 74, f"온전 {n_t4}(74 기대)")

print()
if FAIL:
    print(f"❌ 실패: {FAIL}")
    raise SystemExit(1)
print("✅ 디스패치 합성 전부 통과")
