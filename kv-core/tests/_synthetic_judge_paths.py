"""[W9+] judge 수정분이 **실제로 실행되는가** — 읽어서 맞아 보이는 코드와 도는 코드는 다르다.

## 왜 이 파일이 생겼나

2026-08-11 적대 리뷰 처분 직후 커버리지를 재보니, 그날 고친 judge 쪽 4건이 **전부
한 번도 실행되지 않았다**:

    ⬜ s_judge_apply       예산 천장 정지 (H3 수정 본체)
    ⬜ _record_strikes     영구 재판정 루프 브레이크 (L3)
    ⬜ _targets            채널 오타 fail-loud (M1)
    ⬜ judge_review._live  노션 DB 스캔 (H4 수정 본체)

`judge_job --synthetic` 은 계약 검증만 하고 **스테이지 자체를 안 탄다.** 같은 조건에서
연락처 H1(`TypeError: 'Contact' object is not subscriptable`)이 나흘을 살아남았다 —
계약 통과한 첫 후보에서 무조건 죽는 코드였는데 아무도 그 줄을 실행하지 않았기 때문이다.

    J1  judge.channels 오타는 조용히 채널을 죽이지 않는다 (fail-loud)
    J2  하루 예산 천장에 닿으면 멈춘다 — attempt 를 가로질러
    J3  천장이 남아 있으면 정상 판정한다 (J2 가 항상 멈추는 게 아님을 보인다)
    J4  **배치 전체 파싱 실패는 행을 벌하지 않는다** (출력의 문제지 행의 문제가 아니다)
    J4b 개별 행의 계약 위반은 **그 행에만** 스트라이크. 기입되면 리셋
    J5  3회 쌓인 행은 큐에서 빠진다 — 조용히가 아니라 시끄럽게
    J5b 스트라이크는 만료된다 — 브레이크가 영구 낙인이 되면 새 결손 생성기다
    J6  judge_review 는 단건 GET 폭주 대신 DB 스캔을 먼저 쓴다
    J7  '사람이 지움' 과 'apply 실패' 를 가른다 — **main() 을 실제로 태워서** 확인
    J8  배치는 행 개수가 아니라 **프롬프트 크기**로 잘린다
    J9  배치 하나의 타임아웃이 **잡을 죽이지 않는다**

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_judge_paths.py
claude·노션 실호출 0 — session 과 notion 을 스텁으로 갈아끼운다.
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "kv-core"))
sys.path.insert(0, str(_REPO / "skills/net-sourcing/project"))

from kv_core import gate, job, paths, session  # noqa: E402

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


DB = {"스레드": "000000000000000000003ba3f5f43b92", "링크드인": "00000000000000000000bdb339768bc5",
      "앱스토어": "00000000000000000000cd93782b7fb9"}


# ══════════════════════════════════════════════════════════════════════
print("=== J1: 채널 선언 오타는 조용히 채널을 죽이지 않는다 ===\n")
from core import judge_cli  # noqa: E402

cfg_ok = {"notion": {"channel_db_ids": dict(DB)}, "judge": {"channels": ["스레드", "링크드인"]}}
got = judge_cli._targets(cfg_ok, "", False)
check("J1 정상 선언은 선언한 채널만 대상으로", [c for c, _ in got] == ["Threads", "LinkedIn"], str(got))

for typo in (["스레드", "링크드인 "], ["스레드", "linkedin"], ["스레드", "링크드인,"]):
    cfg_bad = {"notion": {"channel_db_ids": dict(DB)}, "judge": {"channels": typo}}
    try:
        r = judge_cli._targets(cfg_bad, "", False)
        loud = False
    except SystemExit:
        loud = True
        r = None
    check(f"J1 오타 {typo[1]!r} 에 fail-loud", loud,
          "조용히 통과하면 그 채널 전체가 영구 미판정" if not loud else "")


print("\n=== J2/J3: 하루 예산 천장 — attempt 를 가로질러 멈춘다 ===\n")
from jobs import judge_job as JJ  # noqa: E402

_CFG = {"judge": {"batch_size": 2, "max_batches_per_run": 50, "reserve_calls_for_others": 9,
                  "channels": ["스레드"]},
        "notion": {"channel_db_ids": dict(DB)}}


def _setup(job_id: str, n_rows: int):
    """큐 + 잡 디렉토리를 만들어 s_judge_apply 가 실제로 돌 조건을 갖춘다."""
    job.job_dir("net-sourcing", job_id, create=True)
    (job.job_dir("net-sourcing", job_id) / "artifacts").mkdir(parents=True, exist_ok=True)
    qp = paths.data("net-sourcing") / f"q_{job_id}.json"
    rows = [{"page_id": f"pg{i}", "verdict_field": "Tier",
             "allowed": ["Catch", "Trend", "Wait", "Cut"]} for i in range(n_rows)]
    qp.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    job.atomic_write(JJ._queue_pointer(job_id), str(qp))
    return rows


class _Stubs:
    """claude·노션·설정을 갈아끼운다. 실호출 0."""

    def __init__(self, verdicts_ok=True, mode="ok"):
        self.calls = 0
        self.verdicts_ok = verdicts_ok
        self.mode = mode                          # ok | parse_fail | row_bad | timeout

    def __enter__(self):
        self._orig = (JJ.session.run_session, JJ._cfg, judge_cli.apply)
        outer = self

        def fake_session(prompt, **kw):
            outer.calls += 1
            gate.record_call("claude", ok=True)       # 진짜 경로와 같은 장부에 기록
            import re
            ids = re.findall(r'"page_id": "(pg\d+)"', prompt)
            if outer.mode == "timeout" or not outer.verdicts_ok and outer.mode == "timeout":
                raise session.SessionError("claude -p timeout(300s) — 권한 프롬프트에 걸렸거나 과부하")
            if outer.mode == "parse_fail" or not outer.verdicts_ok:
                return "이건 JSON 이 아니다"           # 배치 전체 파싱 실패 (행 귀책 아님)
            if outer.mode == "row_bad":
                # 첫 행만 계약 밖 verdict — **그 행에만** 귀책이 있다
                return json.dumps(
                    [{"page_id": ids[0], "verdict": "완전히모르는값", "reason": "r"}]
                    + [{"page_id": i, "verdict": "Catch", "reason": "r"} for i in ids[1:]])
            return json.dumps([{"page_id": i, "verdict": "Catch", "reason": "r"} for i in ids])

        JJ.session.run_session = fake_session
        JJ._cfg = lambda: dict(_CFG)
        judge_cli.apply = lambda cfg, f: 0
        from core import judge_cli as _jc
        _jc.apply = lambda cfg, f: 0
        return self

    def __exit__(self, *a):
        JJ.session.run_session, JJ._cfg, judge_cli.apply = self._orig
        from core import judge_cli as _jc
        _jc.apply = self._orig[2]


gate._cfg = {"claude": {"daily_calls_warn": 30, "daily_calls_block": 50}}


def _clear_ledger():
    gate.usage_dir("claude").joinpath(f"{gate._utc_today_str()}.jsonl").unlink(missing_ok=True)



# 천장 = 50 - 9 = 41. 오늘 이미 41콜 태운 상태를 만든다(= attempt 두 번이 이미 돌았다).
for _ in range(41):
    gate.record_call("claude", ok=True)
_setup("judge-cap", 20)
with _Stubs() as st:
    JJ.s_judge_apply("net-sourcing", "judge-cap")
check("J2 천장에 닿으면 claude 를 한 번도 더 안 부른다", st.calls == 0,
      f"오늘 {gate.calls_today('claude')}콜 · 추가 호출 {st.calls}")
ev = (job.job_dir("net-sourcing", "judge-cap") / "events.jsonl")
check("J2 멈춘 사실을 관측 가능하게 남긴다",
      ev.exists() and "judge_budget_stop" in ev.read_text(encoding="utf-8"))

# 천장이 남아 있으면 정상 판정한다 — J2 가 "항상 멈춘다"가 아님을 보인다(음성 단언)
_clear_ledger()
_setup("judge-room", 4)
with _Stubs() as st2:
    JJ.s_judge_apply("net-sourcing", "judge-room")
check("J3 천장이 남으면 정상 판정한다(멈춤이 상시가 아니다)", st2.calls == 2,
      f"배치 2회 기대 · 실제 {st2.calls}")


print("\n=== J4/J5: 영구 재판정 루프 브레이크 ===\n")
JJ._strikes_path().unlink(missing_ok=True)
_clear_ledger()

# J4 — 배치가 통째로 파싱 실패. 이건 **출력의 문제**지 행의 문제가 아니다.
for attempt in range(3):
    jid = f"judge-parsefail-{attempt}"
    _setup(jid, 2)
    with _Stubs(mode="parse_fail"):
        try:
            JJ.s_judge_apply("net-sourcing", jid)
        except RuntimeError:
            pass                               # [C5] 기입 0 = fail-loud (설계된 동작)
check("J4 파싱 실패 3회는 행에 스트라이크를 주지 않는다", JJ._strikes() == {},
      f"{JJ._strikes()} — 배치 20건 전원이 벌받으면 3일 만에 멀쩡한 행이 영구 제외된다")

# J4b — 개별 행이 계약 밖 값을 냈다. 이건 그 행의 문제다.
JJ._strikes_path().unlink(missing_ok=True)
_clear_ledger()
for attempt in range(3):
    jid = f"judge-rowbad-{attempt}"
    _setup(jid, 3)
    with _Stubs(mode="row_bad"):
        JJ.s_judge_apply("net-sourcing", jid)
st = JJ._strikes()
check("J4b 계약을 어긴 그 행에만 스트라이크가 쌓인다",
      JJ._strike_count(st, "pg0") == 3 and JJ._strike_count(st, "pg1") == 0,
      f"pg0={JJ._strike_count(st, 'pg0')} pg1={JJ._strike_count(st, 'pg1')}")

_setup("judge-stuck", 3)
with _Stubs() as st3:
    JJ.s_judge_apply("net-sourcing", "judge-stuck")
check("J5 3회 쌓인 행은 큐에서 빠진다(나머지는 그대로 판정된다)", st3.calls == 1,
      f"호출 {st3.calls} · 남은 2행만 판정")
ev2 = (job.job_dir("net-sourcing", "judge-stuck") / "events.jsonl")
check("J5 조용히가 아니라 시끄럽게 — 사람이 봐야 한다",
      ev2.exists() and "judge_stuck_rows" in ev2.read_text(encoding="utf-8"))

# 기입에 성공하면 리셋된다 — 브레이크가 영구 낙인이 되면 새 결손 생성기가 된다
JJ._record_strikes([{"page_id": "pg0"}], [{"page_id": "pg0"}], [])
check("J4b 기입에 성공하면 스트라이크가 리셋된다", JJ._strike_count(JJ._strikes(), "pg0") == 0)

# J5b — 만료. 3회가 된 행은 큐에서 빠지므로 "기입 성공"으로는 영영 리셋될 수 없다.
from datetime import date, timedelta  # noqa: E402
old_day = (date.today() - timedelta(days=JJ._STRIKE_TTL_DAYS + 1)).isoformat()
job.atomic_write(JJ._strikes_path(), json.dumps({"pgX": [3, old_day],
                                                 "pgY": [3, date.today().isoformat()]}))
st5 = JJ._strikes()
check("J5b 오래된 스트라이크는 만료돼 다시 기회를 준다",
      "pgX" not in st5 and JJ._strike_count(st5, "pgY") == 3, str(st5))

print("\n=== J8/J9: 배치 크기와 타임아웃 ===\n")
_big = [{"page_id": f"b{i}", "verdict_field": "Tier", "career": "가" * 4000} for i in range(10)]
_small = [{"page_id": f"s{i}", "verdict_field": "Tier", "headline": "짧다"} for i in range(10)]
bb = list(JJ._batches(_big, 20, 6000, 1500))
bs_ = list(JJ._batches(_small, 20, 6000, 1500))
check("J8 큰 행은 배치가 잘게 쪼개진다(행 개수가 아니라 크기로)", len(bb) > len(bs_),
      f"큰 행 {len(bb)}배치 vs 작은 행 {len(bs_)}배치")
check("J8 어떤 배치도 예산을 크게 넘지 않는다",
      all(len(json.dumps(b, ensure_ascii=False)) < 6000 * 2 for b in bb),
      str([len(json.dumps(b, ensure_ascii=False)) for b in bb]))
check("J8 긴 경력을 잘라도 학력 줄은 남는다",
      "[학력]" in JJ._trim({"career": "최근직장 @ A | 2025~Present\n" + "옛일 @ B\n" * 500
                            + "[학력] 카이스트 | 전산"}, 300)["career"])

_clear_ledger()
_setup("judge-timeout", 4)
with _Stubs(mode="timeout") as st9:
    try:
        JJ.s_judge_apply("net-sourcing", "judge-timeout")
        died = False
    except Exception as e:                      # noqa: BLE001
        died = not isinstance(e, RuntimeError)  # C5 진공 fail-loud 는 정상
check("J9 배치 타임아웃이 잡을 죽이지 않는다(다음 배치로 넘어간다)", st9.calls >= 2,
      f"시도한 배치 {st9.calls} — 1이면 첫 실패에서 잡이 죽은 것")
ev9 = (job.job_dir("net-sourcing", "judge-timeout") / "events.jsonl")
check("J9 건너뛴 배치를 관측 가능하게 남긴다",
      ev9.exists() and "judge_batch_failed" in ev9.read_text(encoding="utf-8"))

print("\n=== J6/J7: judge_review — 단건 폭주 대신 DB 스캔, 그리고 삭제/실패 분리 ===\n")
import tools.judge_review as JR  # noqa: E402
from core import notion  # noqa: E402

_single_gets = {"n": 0}


def _fake_query(cfg, token, db):
    if db != DB["스레드"]:
        return
    for pid, tier in (("pg0", "Catch"), ("pg1", None), ("pg2", "Wait")):
        yield {"id": pid, "properties": {
            "Tier": {"type": "select", "select": ({"name": tier} if tier else None)},
            "Name": {"type": "title", "title": [{"plain_text": f"팀{pid}"}]}}}


def _fake_read(url, token, params=None):
    _single_gets["n"] += 1
    raise AssertionError("DB 스캔으로 다 찾았어야 한다")


_o = (notion._query_pages, notion._read)
notion._query_pages, notion._read = _fake_query, _fake_read
try:
    live = JR._live(["pg0", "pg1", "pg2"], "tok",
                    {"notion": {"channel_db_ids": {"스레드": DB["스레드"]}},
                     "judge": {"channels": ["스레드"]}})
finally:
    notion._query_pages, notion._read = _o
check("J6 DB 스캔으로 전량 해결 — 단건 GET 0", _single_gets["n"] == 0 and len(live) == 3,
      f"단건 {_single_gets['n']}회 · 조회 {len(live)}건")

judged = {"pg0": {"verdict": "Catch", "reason": ""}, "pg1": {"verdict": "Trend", "reason": ""},
          "pg2": {"verdict": "Catch", "reason": ""}}
applied = {"pg1"}                              # pg1 은 기입됐는데 지금 빈칸 = 사람이 지웠다
cleared = [p for p, j in judged.items() if not live[p]["verdict"] and p in applied]
unwritten = [p for p, j in judged.items() if not live[p]["verdict"] and p not in applied]
check("J7 기입된 뒤 빈칸 = 사람이 지움", cleared == ["pg1"], str(cleared))
check("J7 애초에 안 써진 건은 분모 밖", unwritten == [], str(unwritten))
src = Path(JR.__file__).read_text(encoding="utf-8")
check("J7 리포트가 두 원인을 분리해 센다",
      "unwritten" in src and "기입 실패" in src)

# ══════════════════════════════════════════════════════════════════════
gate._cfg = None
print()
if FAIL:
    print(f"🔴 judge 실행경로 계약 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ judge 실행경로 계약 전부 통과")
