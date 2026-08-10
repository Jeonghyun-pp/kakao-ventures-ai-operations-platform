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

# 개별 차트의 진공은 정상이지만 **전체의 진공은 고장**이다. 절대 행 수로 임계를 잡으면
# 스토어 규모·차트 수에 따라 흔들리므로, **비어있지 않은 차트의 비율**로 본다(규모 무관).
# 실측(2026-08-10): 84차트 중 83개가 비어있지 않고 총 15,456행 — 여유가 크다.
_MIN_NONEMPTY_RATIO = 0.5


def _board_files_today() -> tuple[int, int, list[str]]:
    """오늘자 보드 스냅샷 판독 → (온전한 파일 수, 총 행 수, 정당하게 빈 차트 목록).

    ## 진공(정당한 0)과 결손(고장난 0)을 구분한다  [W9 재설계 Batch E · R5]

    [중간점검 T4] 존재만 세면 0바이트·절단 파일 84개도 통과한다(실증) — 그래서 "온전한가"를
    봐야 한다. 그런데 예전 판정은 **비어있지 않은 배열만** 온전하다고 셌고, 그 결과:

        appstore_board_7006_top-paid (= 카지노 유료 차트) 가 8/4 부터 매일 `[]`
        → 유효 83/84 → "부분 결손 · 해당 차트는 영구 손실 가능" 알림이 **매일** 발동

    한국 앱스토어에 유료 카지노 게임 차트가 비어 있는 것은 **애플의 정답**이다. 결손이
    아니라 진공이다. 빈 배열은 잘 형성된 응답이고 0바이트·절단은 그렇지 않다 —
    **파싱 성공 여부**가 둘을 정확히 가른다.

    "전부 빈 배열이면 통과하지 않느냐"는 반론은 **총 행 수**(집계 하한)가 막는다.
    기준일이 로컬(KST)인 이유: board.py 가 그 기준으로 파일명을 쓴다. 스케줄러의 period 는
    UTC 라 00~09시 KST 에 하루 어긋나므로, UTC 로 세면 매일 아침 오탐이 난다.
    """
    import json
    snap = paths.data("net-sourcing", "snapshots")
    n = rows = 0
    empty: list[str] = []
    for p in snap.glob(f"appstore_board_*_{date.today().isoformat()}.json"):
        try:
            if p.stat().st_size == 0:
                continue                              # 0바이트 = 쓰다 만 것 (T4)
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                continue                              # 배열이 아니면 계약 위반
            n += 1                                    # 잘 형성된 응답 — 비어 있어도 온전하다
            rows += len(data)
            if not data:
                empty.append(p.name.replace("appstore_board_", "").replace(".json", ""))
        except (json.JSONDecodeError, OSError):
            continue                                  # 절단·파손 = 무효 (임계가 잡는다)
    return n, rows, sorted(empty)


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
    n, rows, empty = _board_files_today()
    if n < 80:
        print(f"🔴 {tag}appstore-snapshot: 오늘자 온전한 파일 {n}/84 (<80) — 실패 처리(다음 wake 재시도)")
        return False
    nonempty = n - len(empty)
    if rows == 0 or (n and nonempty < n * _MIN_NONEMPTY_RATIO):
        # 파일은 다 있는데 내용이 (거의) 전부 비었다 = 수집 자체가 고장난 것.
        # 규칙 05(진공 통과 금지)는 **개별**이 아니라 **집계** 수준에서 지킨다.
        print(f"🔴 {tag}appstore-snapshot: 온전한 파일 {n}/84 이나 비어있지 않은 차트 "
              f"{nonempty}개(총 {rows}행) — 전량 진공은 결손으로 본다. 실패 처리")
        return False
    if n < 84:
        msg = (f"⚠️ {tag}appstore-snapshot: 오늘자 온전한 파일 {n}/84 — "
               f"{84 - n}개가 파손/누락(해당 차트는 영구 손실 가능)")
        print(msg)
        # [중간점검 A5 + W9 Batch C] 영구 결손은 stdout 침묵으로 두지 않는다.
        # **사실만 기록**하고 발송은 dispatch 가 정한다 — 예전엔 여기서 직접 push 했고,
        # 슬랙이 그 순간 5xx 면 claim 이 반납되며 흔적 없이 사라졌다(B-H3). 그런데 이 잡은
        # return True 라 워터마크가 전진해 **재시도 경로 자체가 없었다**.
        from . import alerts
        alerts.observe("partial_output", f"partial-{task.name}", msg,
                       task=task.name, valid=n, expected=84, rows=rows)
    else:
        # 빈 차트는 **정상 사실**로 로그에만 남긴다(알림 아님) — 카지노 유료 차트처럼
        # 한국 스토어에 원래 없는 것이 매일 경보를 만들던 자리다.
        tail = f" · 빈 차트 {len(empty)}개({', '.join(empty[:3])}{'…' if len(empty) > 3 else ''})" if empty else ""
        print(f"✅ {tag}appstore-snapshot: 온전한 파일 {n}/84 · 총 {rows}행{tail}")
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

def run_meeting_mail(task: Task, period: date) -> bool:
    print(f"▶ meeting-mail ({period}) — jobs/meeting_mail_job.py")
    try:
        rc = subprocess.run([str(_PY), "jobs/meeting_mail_job.py", "--period", period.isoformat()],
                            cwd=_PROJ, timeout=1800, check=False).returncode
    except subprocess.TimeoutExpired:
        print("🔴 meeting-mail: 30분 timeout — 실패 처리")
        return False
    return rc == 0


WIRED: dict[str, Runner] = {
    "appstore-snapshot": run_appstore_snapshot,
    "judge": run_judge,
    "meeting-mail": run_meeting_mail,
}


def dispatch(task: Task, period: date) -> bool:
    # [중간점검 A11] 방어: 스케줄러의 이름 필터를 우회해 미배선 task 가 들어오면 —
    # KeyError 가 "실행 실패"로 조용히 흡수돼 시도 없이 last_attempt 만 전진한다. loud 처리.
    fn = WIRED.get(task.name)
    if fn is None:
        print(f"🔴 dispatch 버그: 미배선 task '{task.name}' 가 필터를 우회해 들어옴 — 실행 없음")
        return False
    return fn(task, period)
