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

import json
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
    return paths.data(task.skill, "cadence") / f"{task.name}.json"


def read_watermark(task: Task) -> dict:
    p = _wm_path(task)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}                                  # 못 읽으면 빈 상태(= 한 번은 돈다)


def write_watermark(task: Task, wm: dict) -> None:
    job.atomic_write(_wm_path(task), json.dumps(wm, ensure_ascii=False, indent=2))


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
        if t.runner != "local":
            continue                               # 외부 러너(GH Actions 등)가 돌린다 — 이중실행 금지(규칙 01)
        last = _last_run(t, read_watermark(t))
        if cadence.interval_elapsed(t.cadence, last, today=now, where=t.name):
            out.append(Due(t, _missed_periods(t, last, now)))
    return out


# runner: (task, period) -> 성공 여부. 실제 실행은 스킬 래퍼가 제공(run_pipeline 호출).
Runner = Callable[[Task, date], bool]


def run_due(tasks: list[Task], runner: Runner, *, now: date | None = None) -> list[dict]:
    """due task 들을 실행하고 워터마크를 갱신한다. 반환: 실행 리포트.

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
        return []
    try:
        report: list[dict] = []
        for due in due_tasks(tasks, now=now):
            t = due.task
            for period in due.periods:
                ok = False
                try:
                    ok = runner(t, period)
                except Exception as e:             # noqa: BLE001  러너 실패 = 이 실행 실패(다음에 재시도)
                    print(f"🔴 {t.name} {period} 실행 예외: {e!r}")
                wm = read_watermark(t)
                wm["last_attempt"] = period.isoformat()
                if ok:
                    wm["last_success"] = period.isoformat()
                write_watermark(t, wm)
                report.append({"task": t.name, "period": period.isoformat(), "ok": ok})
                if not ok:
                    # 순차 드레인: 실패 주기 이후는 다음 기동에서 여기부터 재시도된다.
                    print(f"⚠️  {t.name}: {period} 실패 — 이후 주기 진행 중단(구멍 방지).")
                    break
        return report
    finally:
        slot.__exit__(None, None, None)


# ─────────────────────────── 진입점 ───────────────────────────

def _dry(tasks: list[Task], now: date) -> None:
    ext = [t for t in tasks if t.scheduled and t.runner != "local"]
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
        print(f"  · {d.task.name:18} cadence={d.task.cadence:9} catch_up={d.task.catch_up:8} "
              f"→ 실행 {len(d.periods)}회 [{ps}]  (last={_last_run(d.task, wm)})")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="kv-core 스케줄러 (기본 dry — 실행 안 함)")
    ap.add_argument("--run", action="store_true", help="실제 실행(스킬 러너 호출). 기본은 dry.")
    args = ap.parse_args(argv)

    tasks = registry.load()
    now = _utc_today()
    if not args.run:
        _dry(tasks, now)
        return 0
    # 실제 실행: 스킬 래퍼가 러너를 제공해야 한다. 아직 배선 전이면 dry 로 안내.
    print("⚠️  --run 은 스킬 래퍼 배선(각 task 의 run_pipeline 러너) 후 동작한다. 지금은 dry 출력만:")
    _dry(tasks, now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
