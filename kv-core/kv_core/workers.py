"""[W6] task → 로컬 러너 디스패치. 스케줄러 `--run` 이 부른다.

## 성공 계약 (execution contract)
러너는 `(task, period) -> bool` 이고 **True = 그 주기의 일이 실제로 완주됐다**는 뜻이다.
run_due 는 True 일 때만 last_success 를 전진시킨다 — queued·부분실패를 True 로 돌려주면
워터마크가 실행 없이 전진해 결손이 조용히 굳는다(at-least-once 붕괴). 의심스러우면 False.

## 미배선 task
WIRED 에 없는 task 는 스케줄러가 run_due **앞에서** loud 스킵한다(워터마크 불변) —
"안 한 것·못 한 것·잊은 것"을 로그로 구분한다(관측의 최소 단위).

## 왜 subprocess 인가
board.py 는 cwd/sys.path 에 취약하다(8일 침묵 장애 전력) — plist·GH 워크플로가 이미 검증한
호출 패턴(cwd=project, 상대경로 실행)을 그대로 쓴다. 스킬 의존성이 kv_core 프로세스로
새지 않는 격리 효과도 있다.
"""
from __future__ import annotations

import subprocess
from datetime import date
from typing import Callable

from . import paths
from .registry import Task

_PROJ = paths.repo_root() / "skills" / "net-sourcing" / "project"
_PY = paths.repo_root() / ".venv" / "bin" / "python"


# ─────────────────────────── appstore-snapshot ───────────────────────────

def _board_files_today() -> int:
    """오늘자 **유효한** 보드 스냅샷 수 — 기준일은 board.py 와 동일한 로컬(KST) date.today().

    [중간점검 T4] 존재만 세면 0바이트/절단 파일 84개도 통과한다(실증됨) — 검증은
    "몇 개 있나"가 아니라 "몇 개가 **온전한가**": 비어있지 않은 JSON 배열만 센다(규칙 05·07).
    스케줄러의 period 는 UTC 라 00~09시 KST 에 하루 어긋난다 — UTC 로 세면 오탐.
    """
    import json
    snap = paths.data("net-sourcing", "snapshots")
    n = 0
    for p in snap.glob(f"appstore_board_*_{date.today().isoformat()}.json"):
        try:
            if p.stat().st_size == 0:
                continue
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                n += 1
        except (json.JSONDecodeError, OSError):
            continue                                  # 파손 = 무효 (카운트 제외 → 임계가 잡는다)
    return n


def run_appstore_snapshot(task: Task, period: date) -> bool:
    tag = "[shadow] " if task.shadow else ""
    print(f"▶ {tag}appstore-snapshot ({period}) — board.py --snapshot-all")
    try:
        subprocess.run([str(_PY), "tools/board.py", "--snapshot-all"],
                       cwd=_PROJ, timeout=1800, check=False)
    except subprocess.TimeoutExpired:
        print(f"🔴 {tag}appstore-snapshot: 30분 timeout — 실패 처리")
        return False

    # board.py 는 84차트 중 0개를 저장해도 exit 0 이다 — rc 를 믿지 않고 **산출물을 센다**
    # (규칙 05 상태 관찰 · 규칙 07 세어서 판정). 정상 84 · 하한 80(로컬은 primary 후보라
    # GH 의 관용치 40 보다 빡빡하게 — 재시도 비용 0: 같은 날 파일 멱등 덮어쓰기).
    n = _board_files_today()
    if n < 80:
        print(f"🔴 {tag}appstore-snapshot: 오늘자 유효 파일 {n}/84 (<80) — 실패 처리(다음 wake 재시도)")
        return False
    if n < 84:
        msg = f"⚠️ {tag}appstore-snapshot: 오늘자 유효 파일 {n}/84 — 부분 결손(해당 차트는 영구 손실 가능)"
        print(msg)
        from . import notify
        notify.slack(msg)      # [중간점검 A5] 영구 결손은 stdout 침묵으로 두지 않는다(best-effort)
    else:
        print(f"✅ {tag}appstore-snapshot: 오늘자 유효 파일 {n}/84")
    return True


# ─────────────────────────── judge ───────────────────────────

def run_judge(task: Task, period: date) -> bool:
    print(f"▶ judge ({period}) — jobs/judge_job.py")
    try:
        rc = subprocess.run([str(_PY), "jobs/judge_job.py", "--period", period.isoformat()],
                            cwd=_PROJ, timeout=10800, check=False).returncode
    except subprocess.TimeoutExpired:
        print("🔴 judge: 3시간 timeout — 실패 처리")
        return False
    # judge_job 은 completed 만 exit 0 — 룰 미기입(fail-loud)·검증 실패는 rc 1 로 온다.
    return rc == 0


# ─────────────────────────── 디스패치 ───────────────────────────

Runner = Callable[[Task, date], bool]

WIRED: dict[str, Runner] = {
    "appstore-snapshot": run_appstore_snapshot,
    "judge": run_judge,
}


def dispatch(task: Task, period: date) -> bool:
    # [중간점검 A11] 방어: 스케줄러의 이름 필터를 우회해 미배선 task 가 들어오면 —
    # KeyError 가 "실행 실패"로 조용히 흡수돼 시도 없이 last_attempt 만 전진한다. loud 처리.
    fn = WIRED.get(task.name)
    if fn is None:
        print(f"🔴 dispatch 버그: 미배선 task '{task.name}' 가 필터를 우회해 들어옴 — 실행 없음")
        return False
    return fn(task, period)
