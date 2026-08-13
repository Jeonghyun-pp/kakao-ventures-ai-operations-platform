"""스케줄러 — 흩어진 launchd/cron 을 대체하는 **단일 진입점**(결정 G).

## 왜 pull(상태 기반)인가
상시 서버가 없다. cron 처럼 "매일 9시에 깨워 실행"(push)하면 맥북이 자는 동안의 tick 은 **영구
소실**된다. 대신 깨어날 때마다 "마지막 실행 이후 경과"로 각 task 의 실행 여부를 스스로 판정한다
(catch-up). 소실이 없다.

## 세 결정이 여기서 작동
- **A(backfill/coalesce):** backfill = 놓친 주기만큼 반복, coalesce = 1회로 합침.
- **B(last_success/last_attempt):** due 판정의 기준선. last_success 면 실패한 잡은 워터마크가
  안 올라가 다음에 재시도된다. last_attempt 면 시도만 해도 전진(실패는 안 메움).
- 워터마크(last_run)는 `$KV_DATA/<skill>/cadence/<task>.json` 에 UTC 날짜로 둔다(TZ 사고 방지).

## 쓰는 법
    python -m kv_core.scheduler          # dry — 지금 뭐가 due 인지만 출력(실행 안 함)
    python -m kv_core.scheduler --run    # 실제 실행(각 task 의 러너 호출)
"""
from __future__ import annotations

import fcntl
import json
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import cadence, job, paths, registry
from .registry import Task

_BACKFILL_CAP = 30          # 폭주 방지 — 한 번에 최대 30주기까지만 채운다(넘으면 로그로 알림)


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


# ─────────────────────────── 워터마크 ───────────────────────────

def _wm_path(task: Task) -> Path:
    # create=False — 읽기 경로에 mkdir 부작용이 있으면 read-only 관측자(orrery)가 오염된다
    # (중간점검 D1: paths.data 기본 create=True 가 관측 중 스킬 디렉토리를 만들었다).
    # 쓰기는 atomic_write 가 parent.mkdir 를 하므로 문제없다.
    return paths.data(task.skill, "cadence", create=False) / f"{task.name}.json"


def read_watermark(task: Task) -> dict:
    """워터마크를 읽는다. **부재와 손상을 구분한다.**

    🔴 [2026-08-13 W12 · 리뷰 E-F8] 여기가 `except (JSONDecodeError, OSError): return {}`
       한 줄이었다. 부재도 손상도 마운트 실패도 전부 `{}` — "한 번도 안 돌았음"이 된다.

       그 자체도 페일오픈이지만(quarterly task 가 손상 하나로 즉시 due 가 된다),
       **더 나쁜 건 `first_attempt` 가 오늘 날짜로 다시 심긴다는 것이다.**
       정본 4-1 R1 이 낳은 실제 사고가 정확히 그것 — *"`first_attempt` 가 없으면
       `last_attempt` 로 폴백했는데 그 값은 매일 갱신되므로 '배선 후 경과일'이 영원히
       0 이 되어 **알림이 영구 침묵**했다."* 처방으로 아래 `setdefault("first_attempt")`
       를 넣었는데, 판독이 한 번만 실패하면 그 앵커가 오늘로 되감긴다.
       `health._read_watermark` 도 같은 함수를 부르므로 관측까지 똑같이 속는다.

       그래서 세 상태를 가른다:
         · 파일 없음      → `{}`  (정상. 첫 실행이다)
         · 파싱 실패      → 🔴 크게 알리고 `.corrupt-<ts>` 로 **보존** 후 `{}`
                            (원본을 지우지 않는다 — 사람이 복구할 유일한 근거다)
         · OSError        → 🔴 크게 알린다. 마운트·권한 사고면 전 task 가 동시에 샌다
    """
    p = _wm_path(task)
    if not p.exists():
        return {}                                  # 부재 = 첫 실행. 이건 정상이다.
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        bak = p.with_suffix(f".corrupt-{int(_time.time())}")
        try:
            p.rename(bak)                          # 지우지 않는다 — 복구의 유일한 근거
        except OSError:
            bak = None
        print(f"🔴 워터마크 손상 — {task.name}: {e}\n"
              f"   이 task 는 '한 번도 안 돌았음'으로 취급되어 **즉시 due** 가 되고, "
              f"first_attempt 앵커가 오늘로 되감긴다(알림 영구 침묵의 경로다 — R1).\n"
              f"   원본 보존: {bak if bak else '보존 실패'}")
        return {}
    except OSError as e:
        print(f"🔴 워터마크를 못 읽는다 — {task.name}: {e}\n"
              f"   파일은 있는데 읽기가 실패했다. 마운트·권한 사고면 **전 task 가 동시에** "
              f"'한 번도 안 돌았음'이 되어 일제히 due 로 뜬다. ~/kv-data 를 확인하라.")
        return {}


def write_watermark(task: Task, wm: dict) -> None:
    """워터마크를 통째로 덮어쓴다. **경합이 있는 곳에서는 `update_watermark` 를 써라.**"""
    job.atomic_write(_wm_path(task), json.dumps(wm, ensure_ascii=False, indent=2))


def update_watermark(task: Task, **fields) -> dict:
    """읽기-병합-쓰기를 **flock 안에서** 한다. 반환: 병합된 워터마크.

    🔴 [2026-08-13 W12 · 리뷰 E-F6] `run_due` 가 `read_watermark` → 러너 실행(최대 3시간)
       → `write_watermark(wm)` 를 하는데, 그 사이의 다른 쓰기가 **통째로 사라졌다**(lost update).
       실측(진짜 프로세스 2개): `fieldA` 를 쓴 쪽이 증발하고 `fieldB` 만 남았다.

       평소엔 `heavy_slot("scheduler")` 가 막아준다. 그런데 **그 락이 뚫리는 경로가 있었다** —
       6시간 초과 회수 뒤 원 소유자가 회수자의 락을 지우는 결함(E-F1). 그건 이번에 flock 으로
       닫았지만, "상위 락이 있으니 여기는 안전하다"는 가정 자체가 위태롭다.
       정본 4-2 가 이미 그렇게 적었다 — *"정말 락이 필요한 자리(`update()` 의 read-merge-write)
       에서는 `fcntl.flock`."* `approval.update()` 는 그때 그렇게 고쳤는데 워터마크는 안 고쳤다.

    ⚠️ 값 하나를 조건부로 올려야 하면(예: last_success 는 전진만) 호출부가 아니라
       **여기 안에서** 판단해야 한다 — 밖에서 읽고 비교하면 그 사이가 다시 창이 된다.
       그래서 `_ADVANCE_ONLY` 필드는 이 함수가 직접 max 를 취한다.
    """
    p = _wm_path(task)
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(".lock")
    with open(lock, "a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)     # 프로세스가 죽으면 커널이 푼다
        try:
            wm = read_watermark(task)
            for k, v in fields.items():
                if k in _ADVANCE_ONLY and wm.get(k):
                    wm[k] = max(str(wm[k]), str(v))  # 뒤로 가지 않는다
                elif k in _KEEP_FIRST:
                    wm.setdefault(k, v)
                else:
                    wm[k] = v
            job.atomic_write(p, json.dumps(wm, ensure_ascii=False, indent=2))
            return wm
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


# 전진만 하는 값 — 되감기면 이미 한 일을 다시 하거나(중복) 알림 시계가 리셋된다.
_ADVANCE_ONLY = {"last_success", "last_attempt"}
# 최초 1회만 기록하고 보존 — R1 의 "배선 후 경과일" 앵커다.
_KEEP_FIRST = {"first_attempt"}


def _last_run(task: Task, wm: dict) -> str | None:
    """due 판정 기준선 — 워터마크 정책(last_success|last_attempt)이 고르는 타임스탬프."""
    return wm.get(task.watermark)


# ─────────────────────────── due 판정 + catch-up ───────────────────────────

def _missed_periods(task: Task, last_run: str | None, now: date) -> list[date]:
    """backfill 대상 날짜들. coalesce 는 항상 [now] 1개."""
    if task.catch_up == "coalesce":
        return [now]
    gap = cadence.gap_days(task.cadence, where=task.name)
    if not gap or not last_run:
        return [now]
    try:
        last = date.fromisoformat(str(last_run)[:10])
    except (ValueError, TypeError):
        return [now]
    periods: list[date] = []
    d = last + timedelta(days=gap)
    while d <= now and len(periods) < _BACKFILL_CAP:
        periods.append(d)
        d += timedelta(days=gap)
    if len(periods) >= _BACKFILL_CAP:
        print(f"⚠️  {task.name}: 밀린 주기가 {_BACKFILL_CAP}+ — 상한까지만 backfill 한다(나머지는 다음 실행).")
    return periods or [now]


@dataclass
class Due:
    task: Task
    periods: list[date]        # 실행할 날짜들(coalesce=1개, backfill=N개)


def due_tasks(tasks: list[Task], *, now: date | None = None) -> list[Due]:
    """지금 실행해야 할 스케줄 task 들과 각 실행 날짜(catch-up 반영)."""
    now = now or _utc_today()
    out: list[Due] = []
    for t in tasks:
        if not t.scheduled:
            continue                               # 온디맨드·슬롯은 스케줄 대상 아님
        if t.runner != "local" and not t.shadow:
            continue                               # 외부 러너(GH Actions 등)가 돌린다 — 이중실행 금지(규칙 01).
                                                   # shadow 는 예외: 검증용 병행 실행(저장소 분리 전제) [W6]
        last = _last_run(t, read_watermark(t))
        if cadence.interval_elapsed(t.cadence, last, today=now, where=t.name):
            out.append(Due(t, _missed_periods(t, last, now)))
    return out


# runner: (task, period) -> 성공 여부. 실제 실행은 스킬 래퍼가 제공(run_pipeline 호출).
Runner = Callable[[Task, date], bool]


def run_due(tasks: list[Task], runner: Runner, *, now: date | None = None) -> list[dict] | None:
    """due task 들을 실행하고 워터마크를 갱신한다.

    반환: 실행 리포트. **`None` 은 "물러났다"**(다른 인스턴스가 도는 중) —
    `[]`("돌았는데 due 가 없었다")와 다른 사실이다. 둘을 같은 값으로 돌려주면
    호출부가 구분할 수 없고, 실제로 후퇴 직후 "전부 최신"이라는 거짓 로그가 나갔다(E-F10).

    last_attempt 는 시도할 때마다 전진. last_success 는 성공했을 때만 전진(결정 B).
    한 task 의 주기 실행 중 실패하면 **그 task 의 이후 주기를 진행하지 않는다** — 나중 주기의
    성공이 워터마크를 덮어써 실패 주기가 영구 구멍이 되는 것을 막는다(리뷰 H3, 순차 드레인).
    스케줄러 자체는 배타 락으로 이중 기동을 막는다(규칙 08 — 오발사 방지가 자가복구보다 먼저).
    """
    now = now or _utc_today()
    try:
        slot = job.heavy_slot("scheduler")
        slot.__enter__()
    except job.SlotBusy:
        print("⚠️  다른 스케줄러 인스턴스가 실행 중 — 이번 기동은 물러난다(이중 발화 방지, 규칙 08).")
        # 🔴 [2026-08-13 W12 · 리뷰 E-F10] 여기서 `[]` 를 돌려주면 호출부가 후퇴와
        #    "due 없음"을 **구분할 수 없다.** 실제로 물러난 직후 로그에
        #    `[날짜] 실행할 배선 task 없음(전부 최신).` 이라는 **거짓 문장**이 찍혔다.
        #    나중에 로그를 읽는 사람(정본 A4 가 "타임라인 재구성용"이라 적은 그 로그)은
        #    낙관적인 두 번째 줄을 믿는다 — 정본 R2 의 *"사람은 두 답이 다르면 낙관적인
        #    쪽을 믿는다"* 가 같은 파일 안에서 재현됐다.
        #    `None` = 후퇴, `[]` = 돌았는데 due 가 없었다.
        return None
    try:
        report: list[dict] = []
        for due in due_tasks(tasks, now=now):
            t = due.task
            try:                                   # [중간점검 A7] 한 task 의 워터마크 I/O 오류가
                for period in due.periods:         # 다음 task·다이제스트까지 죽이면 안 된다
                    ok = False
                    try:
                        ok = runner(t, period)
                    except Exception as e:         # noqa: BLE001  러너 실패 = 이 실행 실패(다음에 재시도)
                        print(f"🔴 {t.name} {period} 실행 예외: {e!r}")
                    # [W9 점검] first_attempt 는 **최초 1회만** 기록하고 이후 보존한다.
                    # 이게 없으면 alerts 의 never-succeeded 판정이 last_attempt 로 폴백하는데,
                    # 그 값은 매일 갱신되므로 "배선 후 경과일"이 영원히 0 이 되어
                    # **한 번도 성공 못 한 task 가 영영 알림에 안 걸린다**(실환경에서 실증됨).
                    #
                    # 🔴 [W12 · E-F6] 예전엔 `read → 러너 실행(최대 3시간) → write` 였다.
                    #    그 사이의 다른 쓰기가 통째로 사라졌다(lost update, 진짜 프로세스로 실증).
                    #    이제 읽기-병합-쓰기를 **flock 안에서** 한다. 전진/보존 규칙도
                    #    그 안에서 판단한다 — 밖에서 비교하면 그 사이가 다시 창이 된다.
                    fields = {"first_attempt": period.isoformat(),
                              "last_attempt": period.isoformat()}
                    if ok:
                        fields["last_success"] = period.isoformat()
                    update_watermark(t, **fields)
                    report.append({"task": t.name, "period": period.isoformat(), "ok": ok})
                    if not ok:
                        # 순차 드레인: 실패 주기 이후는 다음 기동에서 여기부터 재시도된다.
                        # (WIRED 전부 coalesce 인 현재는 미도달 경로 — backfill task 등록 시 활성.
                        #  합성 backfill 테스트로 검증됨 [A8, 감수].)
                        print(f"⚠️  {t.name}: {period} 실패 — 이후 주기 진행 중단(구멍 방지).")
                        break
            except Exception as e:                 # noqa: BLE001
                print(f"🔴 {t.name}: 워터마크 처리 오류 — 이 task 만 건너뛰고 계속: {e!r}")
                report.append({"task": t.name, "period": "?", "ok": False})
        return report
    finally:
        slot.__exit__(None, None, None)


# ─────────────────────────── 진입점 ───────────────────────────

def _dry(tasks: list[Task], now: date) -> None:
    ext = [t for t in tasks if t.scheduled and t.runner != "local" and not t.shadow]
    if ext:
        print("· 외부 러너 스킵(이중실행 방지, 규칙 01): "
              + ", ".join(f"{t.name}→{t.runner}" for t in ext))
    dues = due_tasks(tasks, now=now)
    if not dues:
        print(f"[{now}] 지금 due 인 task 없음.")
        return
    print(f"[{now}] due task {len(dues)}개:")
    for d in dues:
        wm = read_watermark(d.task)
        ps = ", ".join(p.isoformat() for p in d.periods)
        tag = " [shadow]" if d.task.shadow else ""
        print(f"  · {d.task.name:18} cadence={d.task.cadence:9} catch_up={d.task.catch_up:8} "
              f"→ 실행 {len(d.periods)}회 [{ps}]{tag}  (last={_last_run(d.task, wm)})")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="kv-core 스케줄러 (기본 dry — 실행 안 함)")
    ap.add_argument("--run", action="store_true", help="실제 실행(스킬 러너 호출). 기본은 dry.")
    args = ap.parse_args(argv)

    tasks = registry.load()
    now = _utc_today()   # [A10, 감수] 1회 캡처 — 장시간 런이 UTC 자정을 넘으면 다음 틱이
                         # 같은 period 를 한 번 더 시도할 수 있으나 멱등 덮어쓰기라 무해.
    if not args.run:
        _dry(tasks, now)
        return 0

    # [W6] 실행 — 배선(WIRED) task 만 run_due 로. 미배선 due 는 **loud 스킵**(워터마크 불변):
    # "안 한 것·못 한 것·잊은 것"을 로그로 구분한다. run_due 앞에서 거르는 이유 = run_due 는
    # 러너 반환 후 무조건 워터마크를 쓰므로, 스킵이 섞이면 last_attempt 가 오염된다.
    from datetime import datetime as _dt, timezone as _tz
    print(f"┌─ scheduler --run 시작 {_dt.now(_tz.utc).isoformat(timespec='seconds')}")  # [A4] 타임라인 재구성용
    from . import workers
    wired = [t for t in tasks if t.name in workers.WIRED]
    unwired = [t for t in tasks if t.name not in workers.WIRED]
    for d in due_tasks(unwired, now=now):
        print(f"🔕 {d.task.name}: due 이나 러너 미배선 — 건너뜀(워터마크 불변). "
              f"배선하려면 kv_core/workers.py WIRED 에 추가.")
    report = run_due(wired, workers.dispatch, now=now)
    # 🔴 [W12 · E-F10] `None`(후퇴)과 `[]`(돌았는데 due 없음)을 **구분해서 말한다.**
    #    예전엔 둘 다 `[]` 라 후퇴 직후에도 "전부 최신"이라는 거짓 문장이 찍혔다.
    #    ⚠️ 아래 다이제스트 로직은 건드리지 않는다 — 그건 이 finding 의 범위가 아니고,
    #       "부재 경보(🔴)의 전달이 로컬 실행 여부와 분리돼야 한다"는 별도 결정이 있다.
    retreated = report is None
    report = report or []
    n_ok = sum(1 for r in report if r["ok"])
    for r in report:
        print(f"  {'✅' if r['ok'] else '🔴'} {r['task']} {r['period']}")
    if retreated:
        print(f"[{now}] 이번 기동은 물러났다 — **아무것도 판정하지 않았다.** "
              f"'전부 최신'이 아니다. 다른 인스턴스의 결과를 보라.")
    elif not report:
        print(f"[{now}] 실행할 배선 task 없음(전부 최신).")

    # [W7·중간점검 A3+D7] 다이제스트 발송 규칙: (성공 1건 이상) or (🔴 존재) 일 때만 —
    # 실패만 반복되는 틱(judge 룰 대기 등)이 매 발화 발송하던 소음 제거 + 부재 경보(🔴)의
    # 전달이 "로컬 실행 있음"과 분리됨(launchd-direct/외부 러너가 죽은 날도 발송된다).
    try:
        from . import orrery, secrets
        secrets.load()                              # 웹훅 env
        s = orrery.gather(with_network=False)       # 발화 컨텍스트에선 외부 조회 생략(빠르게)
        # [W9 C-M1] 게이트는 **state.reds()** 를 본다. 예전엔 `bool(d["failures"])` 였는데
        # 그 목록은 **해결분 포함 원본**이고 렌더러는 미해결만 표시했다 — 해결된 실패
        # 3~4건이 7일간 창에 남아 하루 ~6틱마다 다이제스트를 띄우고 내용은 "미해결 0건"
        # 이었다(alert fatigue). 표시와 게이트가 이제 같은 판단을 공유한다.
        if n_ok > 0 or s.reds():
            sent = orrery.push_slack(s)
            if not sent:                            # [A9] 발송 실패도 침묵하지 않는다
                print("⚠️  다이제스트 발송 실패(웹훅 미설정/네트워크) — 아래 텍스트 폴백:")
                print(orrery.render_slack(s))
    except Exception as e:                          # noqa: BLE001
        print(f"⚠️  orrery 다이제스트 실패(실행에는 무영향): {e!r}")

    # [A9] 요약 + 항상 exit 0: launchd 는 비0 종료를 재시도하지 않으므로 종료코드에 의미를
    # 싣지 않는다 — 실패의 전달 경로는 로그·다이제스트·워터마크(전진 안 함)다.
    print(f"└─ scheduler --run 끝 {_dt.now(_tz.utc).isoformat(timespec='seconds')} · "
          f"{n_ok} ok / {len(report) - n_ok} fail")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
