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


if __name__ == "__main__":
    print(f"=== 리뷰 회귀 테스트 (KV_DATA={os.environ['KV_DATA']}) ===\n")
    test_h1_validating_resume()
    print()
    test_h2_torn_tail()
    print()
    test_run_error_event()
    print()
    if FAIL:
        print(f"❌ 실패 {len(FAIL)}건: {FAIL}")
        raise SystemExit(1)
    print("✅ 배치 1 회귀 전부 통과")
