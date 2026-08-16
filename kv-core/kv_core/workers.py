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
import sys
from datetime import date
from pathlib import Path
from typing import Callable

from . import paths
from .registry import Task

_PROJ = paths.repo_root() / "skills" / "net-sourcing" / "project"
_MAIL_PROJ = paths.repo_root() / "skills" / "mail-harness" / "project"
# 🔴 [2026-08-16] `.venv` 가 **없을 수 있다.** CI 는 `setup-python` + `pip install -e` 로
#    돌아 리포에 `.venv` 가 생기지 않는다. 예전에 `_synthetic_loud_failure` 가 같은 함정에
#    빠져 푸시를 막았고(08-16 새벽), 결정 ⑫ 로 판정기를 subprocess 로 부르면서 **같은 실수를
#    반복했다.** 없으면 지금 돌고 있는 인터프리터를 쓴다 — 그게 이미 kv_core 를 import 한
#    파이썬이므로 스킬도 같은 환경에서 돈다.
_VENV_PY = paths.repo_root() / ".venv" / "bin" / "python"
_PY = _VENV_PY if _VENV_PY.exists() else Path(sys.executable)


def _last_json(out: str) -> dict | None:
    """스킬 판정기의 stdout **마지막 JSON 줄**을 읽는다.

    사람이 읽을 문구가 앞줄에 섞여 나오므로 마지막 줄만 본다. 못 읽으면 `None` —
    호출부가 **성공으로 넘기지 않도록** 명시적으로 구분한다.
    """
    import json
    for ln in reversed((out or "").splitlines()):
        ln = ln.strip()
        if ln.startswith("{"):
            try:
                return json.loads(ln)
            except json.JSONDecodeError:
                return None
    return None


# ─────────────────────────── appstore-snapshot ───────────────────────────

def run_appstore_snapshot(task: Task, period: date) -> bool:
    """[2026-08-16 · 결정 ⑫] 판정을 **스킬로 내보냈다.**

    예전엔 이 파일에 `_board_files_today()` 45줄 + 임계 3개(84·80·0.5)가 들어 있었다.
    254줄 중 85줄(33%)이 앱스토어 전용 지식이었다 — "차트는 84개다", "카지노 유료
    차트가 비어 있는 건 정상이다", 스냅샷 파일명 패턴.

    그런데 `kv-core` 는 [2]통로 + [3]컨트롤 플레인이고 그 지식은 **[4] 스킬의 것**이다.
    섞여 있으면 애플이 차트를 84 → 90 으로 늘릴 때 **코어를 고쳐야** 하고, 재빌드 계획의
    *"[3]은 유지, [4]는 재빌드"* 가 참이 아니게 된다.

    🔴 **판정을 `board.py` 안이 아니라 별도 파일(`tools/board_health.py`)로 옮겼다.**
       원래 이 로직이 코어에 있던 이유가 *"board.py 는 84차트 중 0개를 저장해도 exit 0
       이다 — rc 를 믿지 않고 산출물을 센다"* 였다(2026-07-19 사고: DNS 실패로 전멸했는데
       `✅ 0건` + 종료코드 0). `board.py` 안으로 넣으면 **자기가 자기를 채점**하게 되어
       그 방어가 사라진다. 층은 [4]로 돌리되 **채점자는 여전히 다른 프로그램**이다.

    이 함수가 아는 것은 이제 다른 러너들과 같다 — **무엇을 부르고, rc 를 어떻게 읽나**.
    숫자를 다시 해석하지 않는다(그게 지식 누수의 시작이다). 문구는 스킬이 완성해 준다.
    """
    tag = "[shadow] " if task.shadow else ""
    print(f"▶ {tag}appstore-snapshot ({period}) — board.py --snapshot-all")
    try:
        subprocess.run([str(_PY), "tools/board.py", "--snapshot-all"],
                       cwd=_PROJ, timeout=1800, check=False)
    except subprocess.TimeoutExpired:
        print(f"🔴 {tag}appstore-snapshot: 30분 timeout — 실패 처리")
        return False

    # 산출물 판정 — **별도 프로그램**이 센다(위 독스트링 참조).
    try:
        r = subprocess.run([str(_PY), "tools/board_health.py", "--check"],
                           cwd=_PROJ, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        print(f"🔴 {tag}appstore-snapshot: 산출물 판정 timeout — 실패 처리")
        return False

    verdict = _last_json(r.stdout)
    if verdict is None:
        # 판정기가 말을 못 하면 **성공으로 넘기지 않는다.** 조용한 통과가 이 잡의 사고 형태다.
        print(f"🔴 {tag}appstore-snapshot: 산출물 판정 결과를 못 읽었다 "
              f"(rc={r.returncode}) — 실패 처리\n{(r.stderr or r.stdout)[-300:]}")
        return False

    print(f"{tag}{verdict['msg']}")
    if not verdict.get("ok"):
        return False
    if verdict.get("partial"):
        # 영구 결손은 stdout 침묵으로 두지 않는다. **사실만 기록**하고 발송은 dispatch 가
        # 정한다 — 예전엔 여기서 직접 push 했고, 슬랙이 그 순간 5xx 면 claim 이 반납되며
        # 흔적 없이 사라졌다(B-H3). 그런데 이 잡은 return True 라 재시도 경로가 없었다.
        from . import alerts
        alerts.observe("partial_output", f"partial-{task.name}", verdict["msg"],
                       task=task.name, valid=verdict.get("valid"),
                       expected=verdict.get("expected"), rows=verdict.get("rows"))
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


# ─────────────────────────── bplan-score ───────────────────────────

def run_bplan_score(task: Task, period: date) -> bool:
    """[W11] 비플랜 1차 스크리닝. **`점수` 만 쓴다 — `To do` 는 안 건드린다.**

    그래서 판정이 틀려도 드랍 메일 경로가 안 열린다(사람이 To do 를 옮겨야 열린다).
    타임아웃 30분: 미판정 행이 하루 수십 건 규모라 배치 몇 개면 끝난다.
    """
    print(f"▶ bplan-score ({period}) — jobs/bplan_score_job.py")
    try:
        rc = subprocess.run([str(_PY), "jobs/bplan_score_job.py"],
                            cwd=_MAIL_PROJ, timeout=1800, check=False).returncode
    except subprocess.TimeoutExpired:
        print("🔴 bplan-score: 30분 timeout — 실패 처리")
        return False
    return rc == 0


# ─────────────────────────── mail-triage ───────────────────────────

def run_mail_triage(task: Task, period: date) -> bool:
    """[W11] hello@ 인바운드 **분류만** — 읽기 전용. 계획 파일만 남긴다.

    🔴 **`run` 이 아니라 `triage` 다.** 2026-08-13 정정.
    어제(08-12) 배선할 때 `run`(= triage + apply)을 걸었는데, 그건
    `skills/mail-harness/SKILL.md:22-26` 의 HARD-GATE #1 위반이다:

        "컷오버가 완료되기 전에는 apply·sync·drop 이 실행되는 어떤 형태
         (크론·수동 run)도 시작하지 않는다. **예외 없음.**
         컷오버 전 허용되는 것은 `--dry-run` 과 `triage`(읽기 전용) 뿐이다."

    `apply` 는 `gmail.archive()` 로 **실 메일함을 바꾼다**(triage/main.py:163).
    인박스·구글시트 A1 커서·노션 Bplan DB 가 원본 스킬과 물리적으로 공유되므로,
    컷오버 전 병행 실행은 중복 초안·행 건너뜀을 만든다 — 그게 이 게이트의 이유다.

    다행히 **한 번도 안 돌았다**: 배선(21:57:50)이 그날 마지막 스케줄러 발화(21:54:05)보다
    3분 45초 늦었다. 우연이 막아준 것이지 설계가 막은 게 아니다.
    → 그래서 `_synthetic_outbound.py` 에 게이트 검사를 추가한다(선언이 아니라 코드로).

    지금 이 잡이 하는 일: 인박스를 읽고 분류해 **계획 파일만** 쓴다. 아카이브·초안 0.
    컷오버(W5)가 끝나면 `run` 으로 승격한다 — 그때는 게이트가 해제된 뒤다.
    """
    print(f"▶ mail-triage ({period}) — triage/main.py triage (읽기 전용 · HARD-GATE 준수)")
    try:
        rc = subprocess.run([str(_PY), "triage/main.py", "triage"],
                            cwd=_MAIL_PROJ, timeout=1800, check=False).returncode
    except subprocess.TimeoutExpired:
        print("🔴 mail-triage: 30분 timeout — 실패 처리")
        return False
    return rc == 0


def run_backup(task: Task, period: date) -> bool:
    """[2026-08-16] `~/kv-data` 스냅샷 백업.

    ## 왜 스케줄러 task 인가 (별도 plist 가 아니라)

    백업은 **조용히 멈추는 것이 가장 위험한 종류의 잡**이다. 멈춰도 아무 증상이 없고,
    필요해지는 순간에야 없다는 걸 안다. 그래서 실행보다 **부재 감지**가 더 중요하다.

    별도 plist 로 두면 실행은 되지만 워터마크가 없어 `health` 가 "며칠째 안 돌았다"를
    못 본다(`launchd-direct` task 는 `health_channel` 선언이 필요한데, 백업은 수집
    채널이 아니라 그 자리에 넣을 것이 없다). 스케줄러 task 로 두면 `last_success`
    워터마크와 `cadence: daily` 선언만으로 **기존 부재 감지가 그대로 걸린다** —
    바로 이 리포가 *"선언은 있는데 발동하지 않는 가드"* 를 반복해 만든 자리를,
    이번엔 선언이 곧 감시가 되게 한다.

    ## 이 잡이 지켜야 하는 것

    🔴 **백업이 데이터를 지우는 경로를 만들지 않는다.** 스크립트가 원본 부재·파일 수
    급감을 먼저 검사하고 중단한다(빈 원본을 성실히 반영하면 그게 오늘 스냅샷이 되고,
    보관 기간이 지나면 멀쩡한 스냅샷이 전부 밀려난다). 여기서는 그 스크립트를 부르고
    **종료코드를 그대로 진위로 쓴다** — 삼키면 워터마크가 헛되이 전진한다.
    """
    script = paths.repo_root() / "scripts" / "backup-kv-data.sh"
    print(f"▶ backup ({period}) — {script.name}")
    try:
        rc = subprocess.run(["/bin/bash", str(script)], timeout=3600, check=False).returncode
    except subprocess.TimeoutExpired:
        print("🔴 backup: 60분 timeout — 실패 처리")
        return False
    if rc != 0:
        print(f"🔴 backup 실패 rc={rc} — **워터마크를 전진시키지 않는다.** "
              f"다음 발화가 다시 시도한다.")
    return rc == 0


def run_retention(task: Task, period: date) -> bool:
    """[2026-08-16] 보존 정책 집행 — `config/retention.yaml` 이 정본.

    백업과 같은 이유로 스케줄러 task 다: **안 돌아도 증상이 없는 잡**이라 실행보다
    부재 감지가 중요하다. 3개월 뒤 디스크가 5GB 가 됐을 때 알게 되면 늦다.

    🔴 이 잡은 파일을 **지운다.** 안전장치는 스크립트 안에 있고(백업 성공 확인 ·
       glob 화이트리스트 · `min_keep` 하한), 여기서는 rc 를 그대로 진위로 쓴다.
       삼키면 "정리했다"고 워터마크만 전진한다.
    """
    script = paths.repo_root() / "scripts" / "retention.py"
    print(f"▶ retention ({period}) — {script.name}")
    try:
        rc = subprocess.run([str(_PY), str(script)], timeout=1800, check=False).returncode
    except subprocess.TimeoutExpired:
        print("🔴 retention: 30분 timeout — 실패 처리")
        return False
    if rc != 0:
        print("🔴 retention 실패 — **워터마크를 전진시키지 않는다.** 다음 발화가 재시도한다.")
    return rc == 0


WIRED: dict[str, Runner] = {
    "backup": run_backup,
    "retention": run_retention,
    "bplan-score": run_bplan_score,
    "mail-triage": run_mail_triage,
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
