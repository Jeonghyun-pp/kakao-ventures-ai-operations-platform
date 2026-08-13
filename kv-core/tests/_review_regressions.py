"""W0-W5 코드 리뷰(docs/reviews/W0-W5-review.md)에서 실측 재현된 결함들의 회귀 테스트.

리뷰의 교훈: 빌더가 만든 해피패스 증명은 파인더의 적대 재현을 못 대신한다.
여기 각 테스트는 "수정 전에 실제로 뚫렸던 그 시나리오"를 그대로 보존한다 —
같은 잣대로 "어제 뚫리던 게 오늘 안 뚫림"을 확인한다(exit0 ≠ 성공).

실행: KV_DATA=<스크래치> python kv-core/tests/_review_regressions.py
전부 합성 — 외부 부작용 0(발송·과금·노션·claude 없음).
"""
from __future__ import annotations

import os
os.environ.setdefault("KV_NO_SEND", "1")   # [W9] 실발송 킬 스위치 — 스텁 누락 시의 최후 방어

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

    # 실 tasks.yaml 에 외부 러너 선언이 살아 있는지 (appstore-snapshot 은 2026-08-07 컷오버로
    # runner: local 승격 — 이제 launchd-direct 인 appstore-sourcing 이 이 규율의 대표 사례).
    from kv_core import registry
    tasks = registry.load()
    src = next(t for t in tasks if t.name == "appstore-sourcing")
    check("tasks.yaml appstore-sourcing runner=launchd-direct(외부 실행 주체)",
          src.runner == "launchd-direct", f"runner={src.runner}")
    app = next(t for t in tasks if t.name == "appstore-snapshot")
    check("컷오버 완료: appstore-snapshot runner=local·shadow 해제",
          app.runner == "local" and not app.shadow, f"runner={app.runner} shadow={app.shadow}")


# ── 규칙08: 스케줄러 이중 기동 → 두 번째 인스턴스가 물러나는가 ──────────────

def test_scheduler_double_start() -> None:
    print("[규칙08] 스케줄러 락 점유 중 재기동 → 빈 리포트로 물러남")
    from datetime import date
    from kv_core import scheduler

    with job.heavy_slot("scheduler"):               # 첫 인스턴스가 락 보유 중이라고 가정
        rep = scheduler.run_due([], lambda t, p: True, now=date(2026, 8, 4))
        # 🔴 [W12 · E-F10] 예전엔 `[]` 를 기대했다. 그런데 `[]` 는 "돌았는데 due 가 없었다"
        #    이기도 해서 호출부가 후퇴와 구분할 수 없었고, 실제로 물러난 직후 로그에
        #    "실행할 배선 task 없음(전부 최신)" 이라는 거짓 문장이 찍혔다.
        #    이제 후퇴는 `None` 이다 — **두 사실이 두 값이어야 한다.**
        check("두 번째 인스턴스는 물러난다(None — '실행 0'과 구분됨)", rep is None,
              f"report={rep!r}")

    # 락이 풀린 뒤엔 정상적으로 돌고, due 가 없으면 `[]` 를 준다(후퇴와 다른 값).
    rep2 = scheduler.run_due([], lambda t, p: True, now=date(2026, 8, 4))
    check("락이 풀리면 실제로 돌고 due 없음은 [] 로 답한다", rep2 == [], f"report={rep2!r}")


# ── D4: 슬롯 배타성 (2026-08-13 W12 에 flock 으로 재작성) ────────────────────

def test_d4_stale_lock() -> None:
    """슬롯이 **동시 1개**를 실제로 지키는가.

    🔴 [W12 E-F1] 이 테스트는 원래 "파일에 pid 를 쓰면 점유"라는 전제로 짜여 있었다.
       `heavy_slot` 이 `fcntl.flock` 으로 바뀌면서 그 전제가 사라졌다 —
       파일에 pid 를 적는 것은 이제 **진단 정보**일 뿐 소유권이 아니다.

       전제만 바뀐 게 아니라 **테스트가 덮던 범위가 좁았다.** 옛 D4 는
         ① 죽은 pid 회수(회수 대상이 죽었으니 `__exit__` 이 영영 안 돈다)
         ② 산 pid 존중(회수 자체가 없다)
       두 가지만 봤고, `MAX_HOLD_SEC` 분기를 **한 번도 실행하지 않았다**(mtime 을 안 건드림).
       그래서 "회수된 쪽이 살아 있고 나중에 정상 종료한다"는 조합이 사각으로 남았고,
       그게 정확히 동시 1개 계약을 깨던 경로였다. 이번엔 그 조합을 직접 넣는다.
    """
    print("[D4] 슬롯 배타성 — 실제 점유·자동해제·오래된 락")
    kind = "d4-test-slot"
    p = job._slot_path(kind)
    p.parent.mkdir(parents=True, exist_ok=True)

    # ① 아무도 안 쥔 락 파일(pid 만 적혀 있음) → 획득돼야 한다.
    #    죽은 프로세스가 남긴 상태가 정확히 이것이다 — 커널이 이미 풀었다.
    p.write_text("99999999 0")
    try:
        with job.heavy_slot(kind):
            check("아무도 안 쥔 락은 획득된다(죽은 프로세스 = 커널이 이미 해제)", True)
    except job.SlotBusy:
        check("아무도 안 쥔 락은 획득된다(죽은 프로세스 = 커널이 이미 해제)", False, "SlotBusy")

    # ② **실제로 flock 을 쥐고 있으면** SlotBusy.
    #    flock 은 open file description 단위라 같은 프로세스의 다른 fd 와도 배타적이다.
    import fcntl
    import time
    with open(p, "a+") as holder:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with job.heavy_slot(kind):
                check("실제 점유 중이면 SlotBusy", False, "뚫고 들어감")
        except job.SlotBusy:
            check("실제 점유 중이면 SlotBusy", True)

        # ③ **6시간을 넘겨도 회수하지 않는다.** 옛 구현은 여기서 회수했고, 그 회수가
        #    "원소유자가 나중에 정상 종료하며 회수자의 락을 지운다"를 만들었다.
        old = time.time() - 7 * 3600
        os.utime(p, (old, old))
        try:
            with job.heavy_slot(kind):
                check("7시간 된 락도 회수하지 않는다(살아있는 소유자 존중)", False,
                      "회수함 — 동시 1개 계약이 깨진다")
        except job.SlotBusy:
            check("7시간 된 락도 회수하지 않는다(살아있는 소유자 존중)", True)
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)

    # ④ 소유자가 놓으면 다시 잡힌다(영구 정지가 아니다 — A-H3 의 반대 방향 회귀 가드).
    try:
        with job.heavy_slot(kind):
            check("소유자가 놓으면 다시 잡힌다", True)
    except job.SlotBusy:
        check("소유자가 놓으면 다시 잡힌다", False, "영구 정지")

    # ⑤ **__exit__ 이 남의 락을 지우지 않는다** — 이번 결함의 본체.
    a = job.heavy_slot(kind)
    a.__enter__()
    a.__exit__()
    check("정상 종료 후에도 락 파일이 남는다(파일이 곧 락 대상)", p.exists(),
          "지우면 그 사이 남이 만든 파일을 지우게 된다")


# ── W5-med: validate.py 경계 5종 (리뷰 프로브의 상설화) ─────────────────────

def test_validate_boundaries() -> None:
    print("[W5-med] validate 경계 — 오추출·bool·results:[]·DLQ append·진공")
    from pathlib import Path
    from kv_core import validate as v

    # P6: 프로즈 괄호 2개 → 예전엔 틀린 부분구조를 조용히 반환. 이제 모호=ValueError(fail-closed)
    txt = '먼저 [1, 2] 를 보고, 판정: [{"id": 0, "verdict": "keep"}]'
    try:
        got = v.parse_json(txt)
        check("모호한 다중 JSON → fail-closed", False, f"조용히 반환: {got!r}")
    except ValueError:
        check("모호한 다중 JSON → fail-closed", True)
    # 깨끗한 단일 배열은 여전히 통과(관대함 유지)
    clean = v.parse_json('판정 결과:\n[{"id": 0, "verdict": "keep"}]')
    check("단일 JSON 은 여전히 관대하게 추출", clean == [{"id": 0, "verdict": "keep"}])

    # P7: bool 이 int 로 통과하던 것 → 위반
    errs = v.check_record({"id": True, "verdict": "keep"},
                          {"id": {"type": "int", "required": True},
                           "verdict": {"type": "str", "required": True}})
    check("bool 은 int 계약 위반", any("bool" in e for e in errs), f"errs={errs}")

    # P12: {"results": []} = 정당한 0건 — 통짜 dict 로 둔갑해 부당 위반이 되지 않아야
    c0 = v.Contract(schema={"to": {"type": "str", "required": True}}, on_invalid="halt")
    ok, bad = v.validate_text('{"results": []}', c0)
    check('{"results": []} → 0건으로 정상 해석(부당 halt 없음)', ok == [] and bad == [])

    # P10b: DLQ 가 회차 간 append 로 보존되는가
    dlq = Path(os.environ["KV_DATA"]) / "dlq-test" / "q.jsonl"
    cq = v.Contract(schema={"id": {"type": "int", "required": True}}, on_invalid="quarantine")
    v.validate_records([{"bad": 1}], cq, dlq_path=dlq)
    v.validate_records([{"bad": 2}], cq, dlq_path=dlq)
    lines = [ln for ln in dlq.read_text(encoding="utf-8").splitlines() if ln.strip()]
    check("DLQ 회차 간 보존(append)", len(lines) == 2, f"lines={len(lines)}")

    # 진공 통과: min_records=1 인 halt 계약에서 0건 → ValidationHalt
    c1 = v.Contract(schema={"id": {"type": "int", "required": True}},
                    on_invalid="halt", min_records=1)
    try:
        v.validate_records([], c1)
        check("min_records 미달 halt → ValidationHalt", False, "통과해버림")
    except v.ValidationHalt:
        check("min_records 미달 halt → ValidationHalt", True)
    # meeting-mail(min_records 0)은 0건이 정당 — contracts.yaml 로드로 확인
    cs = v.load_contracts(Path(__file__).resolve().parents[2] / "config" / "contracts.yaml")
    check("contracts: judge=1·meeting-mail=0",
          cs["judge"].min_records == 1 and cs["meeting-mail"].min_records == 0,
          f"judge={cs['judge'].min_records}, mail={cs['meeting-mail'].min_records}")


def test_no_send_guard() -> None:
    """[W9] 실발송 킬 스위치 메타 검증 — 스텁 누락이 실 채널로 새는 것을 **구조로** 막는다.

    두 번 데였다: T3(게이트 테스트가 가짜 차단 경보 발송) · W9(dispatch 테스트가 부분결손
    경보 발송). 원인은 매번 "이 테스트에도 스텁이 필요한지" 사람이 기억해야 했던 것.
    이제 ①전 테스트가 KV_NO_SEND 를 켜고 ②발송 함수가 그 스위치를 존중하는지 기계가 단언한다.
    """
    print("[W9] 실발송 킬 스위치 (메타 검증)")
    tdir = Path(__file__).resolve().parent
    missing = [p.name for p in sorted(tdir.glob("*.py"))
               if "KV_NO_SEND" not in p.read_text(encoding="utf-8")]
    check("전 테스트가 KV_NO_SEND 를 켠다", not missing, f"누락={missing}")

    from kv_core import alerts, notify, slackbot
    for mod, fn in ((notify, "slack"), (slackbot, "post")):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        body = src.split(f"def {fn}(", 1)[-1]
        check(f"{mod.__name__}.{fn} 가 킬 스위치를 존중", "is_suppressed" in body)

    os.environ["KV_NO_SEND"] = "1"
    # [W9 Phase0] 계약을 정정했다. 예전 단언은 "notify.slack → True"·"slackbot.post → None"
    # 이었는데, 그 두 값이 각각 **거짓 성공**과 **거짓 실패**를 뜻해 실해를 냈다:
    #   True  → alerts.push 가 delivered 마커를 태워 그날 알림이 영구 유실(B-H1)
    #   None  → _announce 가 전건 실패로 세어 slackbot-down 가짜 경보(A-M10)
    # 억제는 성공도 실패도 아닌 제3의 상태다 — 그렇게 표현되는지를 단언한다.
    check("킬 스위치 → notify.slack 은 False(미발송)", notify.slack("메타검증(발송 안 됨)") is False)
    check("킬 스위치 → slackbot.post 는 SUPPRESSED(실패 아님)",
          slackbot.post("메타검증") is slackbot.SUPPRESSED)
    check("킬 스위치 → alerts.push 는 'suppressed'", alerts.push("meta-x", "메타검증") == "suppressed")

    # 억제 판정은 **한 곳**에서만 한다 — 모듈마다 제 방식으로 환경변수를 읽으면
    # `KV_NO_SEND=0` 이 어떤 모듈엔 '켜짐'이고 어떤 모듈엔 '꺼짐'이 된다(오늘 실제로 그랬다).
    others = [p.name for p in (Path(notify.__file__).parent).glob("*.py")
              if p.name != "notify.py" and "KV_NO_SEND" in p.read_text(encoding="utf-8")
              and "environ" in p.read_text(encoding="utf-8").split("KV_NO_SEND")[0][-200:]]
    check("KV_NO_SEND 를 직접 읽는 모듈은 notify.py 뿐", not others, f"직접읽기={others}")
    for bad in ("0", "false", "no", ""):
        os.environ["KV_NO_SEND"] = bad
        if notify.is_suppressed():
            check(f"KV_NO_SEND={bad!r} 를 '켜짐'으로 오해하지 않음", False)
            break
    else:
        check("KV_NO_SEND 의 0/false/no/'' 는 억제 아님(truthy 함정 제거)", True)
    os.environ["KV_NO_SEND"] = "1"


if __name__ == "__main__":
    print(f"=== 리뷰 회귀 테스트 (KV_DATA={os.environ['KV_DATA']}) ===\n")
    test_no_send_guard()
    print()
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
    test_validate_boundaries()
    print()
    if FAIL:
        print(f"❌ 실패 {len(FAIL)}건: {FAIL}")
        raise SystemExit(1)
    print("✅ 리뷰 회귀 전부 통과")
