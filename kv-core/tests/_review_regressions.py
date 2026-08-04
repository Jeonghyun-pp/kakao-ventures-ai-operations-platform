"""W0-W5 코드 리뷰(docs/reviews/W0-W5-review.md)에서 실측 재현된 결함들의 회귀 테스트.

리뷰의 교훈: 빌더가 만든 해피패스 증명은 파인더의 적대 재현을 못 대신한다.
여기 각 테스트는 "수정 전에 실제로 뚫렸던 그 시나리오"를 그대로 보존한다 —
같은 잣대로 "어제 뚫리던 게 오늘 안 뚫림"을 확인한다(exit0 ≠ 성공).

실행: KV_DATA=<스크래치> python kv-core/tests/_review_regressions.py
전부 합성 — 외부 부작용 0(발송·과금·노션·claude 없음).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 경로를 지정하고 실행하라 (실데이터 보호)"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import job  # noqa: E402

SKILL = "_review-regress"
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "✅" if cond else "❌"
    print(f"  {mark} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


# ── H1: validating 에서 죽은 잡이 재개 가능한가 ─────────────────────────────
# 수정 전: _ALLOWED["validating"]에 running 이 없어 재개가 ValueError 로 영구 불가.

def test_h1_validating_resume() -> None:
    print("[H1] validating 상태에서 kill → 재개")
    jid = "h1-validating-kill"
    job.create(SKILL, {"test": "h1"}, jid)
    job.transition(SKILL, jid, "queued")
    job.transition(SKILL, jid, "running")
    job.transition(SKILL, jid, "validating")          # ← 여기서 kill -9 났다고 가정

    # 재개: run() 은 terminal 이 아니면 running 으로 전이해 이어간다
    try:
        d = job.run(SKILL, jid, ["a", "b"], key=str, process=lambda x: None)
        check("재개가 전이 오류 없이 완주", d.state == "completed", f"state={d.state}")
    except ValueError as e:
        check("재개가 전이 오류 없이 완주", False, f"ValueError: {e}")


# ── H2: torn 마지막 줄 위에 append 가 붙어도 events 가 살아있는가 ───────────
# 수정 전: 병합줄이 파일 중간에 생겨 read_events 가 영구 JSONDecodeError.

def test_h2_torn_tail() -> None:
    print("[H2] torn line + 다음 append → 판독 생존")
    jid = "h2-torn-tail"
    job.create(SKILL, {"test": "h2"}, jid)
    job.append_event(SKILL, jid, "marker", n=1)

    # 크래시로 반쯤 써진 줄을 재현: 개행 없이 잘린 JSON 조각을 직접 이어붙임
    p = job._events_path(SKILL, jid)
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"ts": "2026-01-01T00:00:00Z", "kind": "half')   # 개행 없음 = torn

    # 다음 append — 수정 전엔 여기서 병합줄이 생겼다
    job.append_event(SKILL, jid, "marker", n=2)

    try:
        evs = job.read_events(SKILL, jid)
        kinds = [e["kind"] for e in evs]
        check("read_events 크래시 없음", True)
        check("torn 조각 제거·완전한 이벤트만 남음",
              kinds.count("marker") == 2 and "half" not in kinds,
              f"kinds={kinds}")
        raw = p.read_text(encoding="utf-8")
        check("병합줄 없음(모든 줄이 { 로 시작)",
              all(ln.startswith("{") for ln in raw.splitlines() if ln.strip()))
    except Exception as e:  # noqa: BLE001
        check("read_events 크래시 없음", False, repr(e))


# ── W3-med: run() 실패가 이벤트를 남기는가 (삼키지도, 침묵 전파도 아님) ──────

def test_run_error_event() -> None:
    print("[W3-med] run() 실패 → run_error 이벤트 + 전파 + 재개 가능")
    jid = "run-error-event"
    job.create(SKILL, {"test": "run-error"}, jid)

    def boom() -> list:
        raise RuntimeError("이터러블 폭발")

    def items():
        yield "x"
        raise RuntimeError("이터러블 폭발")

    raised = False
    try:
        job.run(SKILL, jid, items(), key=str, process=lambda x: None)
    except RuntimeError:
        raised = True
    check("예외가 전파됨(삼키지 않음)", raised)

    evs = job.read_events(SKILL, jid)
    check("run_error 이벤트 기록됨",
          any(e["kind"] == "run_error" for e in evs))
    d = job.rebuild(SKILL, jid)
    check("잡이 종단이 아님(재개 가능)", d.state not in ("completed", "failed"),
          f"state={d.state}")


# ── H3: backfill 중간 실패가 영구 구멍이 되지 않는가 ────────────────────────
# 수정 전: 8/2✗·8/3✓·8/4✓ → last_success=8/4, 8/2 는 다시는 due 가 안 됨.

def test_h3_backfill_no_hole() -> None:
    print("[H3] backfill 중간 실패 → 이후 주기 중단(순차 드레인)")
    from datetime import date
    from kv_core import scheduler
    from kv_core.registry import Task

    t = Task(name="h3-backfill", skill=SKILL, kind="routine", cadence="daily",
             catch_up="backfill", watermark="last_success")
    # 워터마크: 3일 전 성공 → 밀린 주기 3개
    scheduler.write_watermark(t, {"last_success": "2026-08-01"})

    calls: list[str] = []

    def runner(task, period) -> bool:
        calls.append(period.isoformat())
        return period.isoformat() != "2026-08-02"   # 첫 주기(8/2)만 실패

    rep = scheduler.run_due([t], runner, now=date(2026, 8, 4))
    wm = scheduler.read_watermark(t)
    check("실패 주기에서 중단(이후 주기 미실행)", calls == ["2026-08-02"], f"calls={calls}")
    check("last_success 가 실패 주기를 넘지 않음", wm.get("last_success") == "2026-08-01",
          f"last_success={wm.get('last_success')}")

    # 다음 기동: 실패 주기부터 재시도되는가 (구멍 없음)
    calls.clear()

    def runner_ok(task, period) -> bool:
        calls.append(period.isoformat())
        return True

    scheduler.run_due([t], runner_ok, now=date(2026, 8, 4))
    check("다음 기동이 실패 주기(8/2)부터 재개", calls and calls[0] == "2026-08-02",
          f"calls={calls}")


# ── H4: runner!=local task 를 스케줄러가 건드리지 않는가 (규칙 01) ───────────

def test_h4_external_runner_skip() -> None:
    print("[H4] runner: github-actions → 로컬 스케줄러 스킵")
    from datetime import date
    from kv_core import scheduler
    from kv_core.registry import Task

    ext = Task(name="h4-external", skill=SKILL, kind="routine", cadence="daily",
               catch_up="backfill", watermark="last_success", runner="github-actions")
    dues = scheduler.due_tasks([ext], now=date(2026, 8, 4))
    check("외부 러너 task 는 due 에 없음", dues == [], f"dues={[d.task.name for d in dues]}")

    # 실제 tasks.yaml 의 appstore-snapshot 이 external 로 선언됐는지
    from kv_core import registry
    tasks = registry.load()
    app = next(t for t in tasks if t.name == "appstore-snapshot")
    check("tasks.yaml appstore-snapshot runner=github-actions",
          app.runner == "github-actions", f"runner={app.runner}")


# ── 규칙08: 스케줄러 이중 기동 → 두 번째 인스턴스가 물러나는가 ──────────────

def test_scheduler_double_start() -> None:
    print("[규칙08] 스케줄러 락 점유 중 재기동 → 빈 리포트로 물러남")
    from datetime import date
    from kv_core import scheduler

    with job.heavy_slot("scheduler"):               # 첫 인스턴스가 락 보유 중이라고 가정
        rep = scheduler.run_due([], lambda t, p: True, now=date(2026, 8, 4))
        check("두 번째 인스턴스는 실행 0·빈 리포트", rep == [], f"report={rep}")


# ── D4: stale 락 회수가 안전한가 (죽은 pid 회수 + 산 락 존중) ────────────────

def test_d4_stale_lock() -> None:
    print("[D4] stale 락 회수 + 산 락 SlotBusy")
    kind = "d4-test-slot"
    p = job._slot_path(kind)

    # 죽은 pid(99999999 — 존재 불가)의 락 → 회수돼야 함
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("99999999")
    try:
        with job.heavy_slot(kind):
            check("죽은 pid 락 회수 성공", True)
    except job.SlotBusy:
        check("죽은 pid 락 회수 성공", False, "SlotBusy")

    # 산 프로세스(내 pid)의 락 → SlotBusy 로 존중돼야 함
    p.write_text(str(os.getpid()))
    try:
        with job.heavy_slot(kind):
            check("산 락은 SlotBusy", False, "뚫고 들어감")
    except job.SlotBusy:
        check("산 락은 SlotBusy", True)
    p.unlink(missing_ok=True)


if __name__ == "__main__":
    print(f"=== 리뷰 회귀 테스트 (KV_DATA={os.environ['KV_DATA']}) ===\n")
    test_h1_validating_resume()
    print()
    test_h2_torn_tail()
    print()
    test_run_error_event()
    print()
    test_h3_backfill_no_hole()
    print()
    test_h4_external_runner_skip()
    print()
    test_scheduler_double_start()
    print()
    test_d4_stale_lock()
    print()
    if FAIL:
        print(f"❌ 실패 {len(FAIL)}건: {FAIL}")
        raise SystemExit(1)
    print("✅ 리뷰 회귀 전부 통과")
