"""[W9 재설계 Batch B] health.compute — **판정 단일화**의 계약 테스트.

여기서 단언하는 것은 "health 가 구 코드와 같다"가 아니다. 실데이터에서는 **차이 0건**임을
별도 프로브로 확인했고, 이 파일은 **구 코드가 틀렸던 입력에서 새 판정이 옳은가**를 본다.
그 입력들은 전부 합성 테스트가 만들지 않던 모양이다(검증 사각 ②③⑤).

    H1  같은 날 성공 **뒤에** 난 실패를 해결로 보지 않는다            [C-H2]
    H2  나중 attempt 가 실패면 이전 성공이 그것을 해결하지 않는다      [C-H3]
    H3  launchd-direct 도 freshness 선언을 따르고 알림 대상이 된다     [C-M2]
    H4  first_attempt 결측이어도 잡 흔적에서 최초 시도일을 복원한다     [B-L2]
    H5  리셋된 first_attempt 를 잡 흔적이 자기치유한다                [C-H5]
    H6  최초 시도일을 정말 모르면 침묵하지 않고 알린다                 [B-L2]
    H7  reds() 는 해결분을 세지 않는다                              [C-M1]
    H8  now 주입으로 시간 경과를 시뮬레이션할 수 있다                  [사각 ⑤]

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_health.py
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import json  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"
_SCRATCH = Path(os.environ["KV_DATA"]).resolve()
assert _SCRATCH != (Path.home() / "kv-data").resolve(), "🔴 운영 KV_DATA 로 실행 금지"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import health, notify  # noqa: E402
from kv_core.registry import Task  # noqa: E402

notify.slack = lambda *a, **k: True

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


TODAY = date(2026, 8, 10)
SK = "hz"


def reset() -> None:
    if _SCRATCH.exists():
        shutil.rmtree(_SCRATCH)
    _SCRATCH.mkdir(parents=True, exist_ok=True)


def wm(name: str, **fields) -> None:
    d = _SCRATCH / SK / "cadence"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(fields), encoding="utf-8")


def jobstate(jid: str, state: str, ts: str) -> None:
    d = _SCRATCH / SK / "jobs" / jid
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps({"state": state, "updated": ts}), encoding="utf-8")


def T(name: str, **kw) -> Task:
    base = dict(name=name, skill=SK, kind="routine", cadence="daily",
                catch_up="coalesce", watermark="last_success")
    base.update(kw)
    return Task(**base)


# ══════════════════════════════════════════════════════════════════════
print("=== H1 · C-H2: 같은 날 성공 뒤에 난 실패 ===\n")
# 구 코드: `resolved = ls >= str(ts)[:10]` — 날짜로 잘라 비교하므로 09:00 성공 워터마크가
# 그날 23:00 실패까지 해결로 뒤집었다. 워터마크는 시각을 모르므로 그 단정은 근거가 없다.
reset()
wm("t1", last_attempt=TODAY.isoformat(), last_success=TODAY.isoformat(),
   first_attempt="2026-08-01")
jobstate(f"t1-{TODAY.isoformat()}", "failed", f"{TODAY.isoformat()}T23:00:00Z")
st = health.compute(now=TODAY, tasks=[T("t1")])
f = {x.job: x.resolved for x in st.failures}
check("H1 같은 날 성공은 그날 밤 실패를 해결하지 않는다",
      f.get(f"t1-{TODAY.isoformat()}") is False, str(f))

print("\n=== H2 · C-H3: 성공 뒤 재실행이 실패 ===\n")
# 구 코드: `(skill, base) in completed_bases` — completed 가 하나라도 있으면 순서 무관 해결.
reset()
wm("t2", last_attempt="2026-08-01", last_success="2026-08-01", first_attempt="2026-08-01")
jobstate("t2-2026-08-09", "completed", "2026-08-09T00:00:00Z")
jobstate("t2-2026-08-09-r2", "failed", "2026-08-09T23:00:00Z")
st = health.compute(now=TODAY, tasks=[T("t2")])
f = {x.job: x.resolved for x in st.failures}
check("H2 나중 attempt 실패는 미해결", f.get("t2-2026-08-09-r2") is False, str(f))

# 반대 방향(정상 케이스)도 지킨다 — 실패 뒤 성공이면 해결이어야 한다
reset()
wm("t2", last_attempt="2026-08-01", last_success="2026-08-01", first_attempt="2026-08-01")
jobstate("t2-2026-08-09", "failed", "2026-08-09T00:00:00Z")
jobstate("t2-2026-08-09-r2", "completed", "2026-08-09T02:00:00Z")
st = health.compute(now=TODAY, tasks=[T("t2")])
f = {x.job: x.resolved for x in st.failures}
check("H2b 실패 뒤 성공은 해결됨", f.get("t2-2026-08-09") is True, str(f))

print("\n=== H3 · C-M2: launchd-direct 도 선언을 따르고 알림 대상이 된다 ===\n")
# 구 코드: _launchd_direct_health 는 freshness_limit 을 안 봤고(하드코딩 grace),
# alerts._task_failures 는 launchd-direct 를 통째로 제외해 알림 커버리지가 0이었다.
reset()
(_SCRATCH / SK).mkdir(parents=True, exist_ok=True)
(_SCRATCH / SK / "last_run_health.json").write_text(
    json.dumps({"ok": True, "ts": "2026-08-05T09:00:00"}), encoding="utf-8")  # naive KST
t_ld = T("t3", runner="launchd-direct", freshness_days=2)
st = health.compute(now=TODAY, tasks=[t_ld])
s = st.tasks[0]
check("H3 launchd-direct 가 freshness_days 선언을 따른다(2일 선언·5일 경과 → stale)",
      s.status == "stale", f"{s.status} · {s.note}")
check("H3 launchd-direct 도 알림 대상(alertable)", s.alertable is True)
check("H3 naive ts 를 KST 로 정규화", s.last_success == "2026-08-05", str(s.last_success))

t_ld2 = T("t3", runner="launchd-direct", freshness_days=30)
st2 = health.compute(now=TODAY, tasks=[t_ld2])
# 주기(daily)는 지났으므로 `late`(⚠️ 관측만)가 옳다 — 신선도 SLO 가 넉넉하다고 ✅ 는 아니다.
# 여기서 지켜야 할 성질은 **선언이 알림 여부를 가른다**는 것이다.
check("H3b 선언이 넉넉하면 같은 데이터가 알림 대상이 아니다",
      st2.tasks[0].status == "late" and st2.tasks[0].alertable is False,
      f"{st2.tasks[0].status} · alertable={st2.tasks[0].alertable}")

print("\n=== H4/H5 · B-L2·C-H5: first_attempt 복원과 자기치유 ===\n")
reset()
# first_attempt 가 아예 없다(구 스키마). 잡 흔적은 8/1 부터 있다.
wm("t4", last_attempt=TODAY.isoformat())
for d in ("2026-08-01", "2026-08-03"):
    jobstate(f"t4-{d}", "failed", f"{d}T00:00:00Z")
st = health.compute(now=TODAY, tasks=[T("t4")])
s = st.tasks[0]
check("H4 first_attempt 결측 → 잡 흔적에서 복원", s.first_seen == "2026-08-01", str(s.first_seen))
check("H4 복원된 시각으로 never_bad 판정(9일 경과 ≥ 1×3)", s.status == "never_bad", s.note)
check("H4 알림 대상", s.alertable is True)

reset()
# setdefault 가 오늘로 리셋해버린 워터마크 + 옛 잡 흔적 → 더 이른 쪽을 택해 자기치유
wm("t5", last_attempt=TODAY.isoformat(), first_attempt=TODAY.isoformat())
jobstate("t5-2026-07-15", "failed", "2026-07-15T00:00:00Z")
st = health.compute(now=TODAY, tasks=[T("t5")])
s = st.tasks[0]
check("H5 리셋된 first_attempt 를 잡 흔적이 자기치유", s.first_seen == "2026-07-15", str(s.first_seen))
check("H5 자기치유 후 never_bad", s.status == "never_bad", s.note)

print("\n=== H6 · B-L2: 정말 모르면 침묵하지 않는다 ===\n")
reset()
wm("t6", last_attempt=TODAY.isoformat())          # 잡 흔적도 없다
st = health.compute(now=TODAY, tasks=[T("t6")])
s = st.tasks[0]
check("H6 최초 시도일 불명이면 침묵 대신 알린다", s.status == "never_bad" and s.alertable,
      f"{s.status} · {s.note}")
check("H6 모른다는 사실이 상태에 실린다", s.first_seen_known is False)

print("\n=== H7 · C-M1: reds() 는 해결분을 세지 않는다 ===\n")
reset()
wm("t7", last_attempt=TODAY.isoformat(), last_success=TODAY.isoformat(),
   first_attempt="2026-08-01")
for i in range(4):                                 # 전부 해결된 실패
    jid = f"t7-2026-08-0{i + 5}"
    jobstate(jid, "failed", f"2026-08-0{i + 5}T00:00:00Z")
    jobstate(jid + "-r2", "completed", f"2026-08-0{i + 5}T02:00:00Z")
st = health.compute(now=TODAY, tasks=[T("t7")])
check("H7 해결된 실패 4건은 reds() 를 만들지 않는다", st.reds() == [],
      f"reds={st.reds()} · failures={len(st.failures)}")
check("H7 그래도 해결분은 목록에 남는다(관측 손실 없음)", len(st.failures) == 4, str(len(st.failures)))

print("\n=== H8 · 사각⑤: now 주입 시간 시뮬레이션 ===\n")
reset()
wm("t8", last_attempt="2026-08-10", last_success="2026-08-10", first_attempt="2026-08-01")
seq = []
for offset in (0, 1, 2, 3, 10, 30):
    st = health.compute(now=TODAY + timedelta(days=offset), tasks=[T("t8")])
    seq.append((offset, st.tasks[0].status))
check("H8 시간이 흐르면 ok → late/stale 로 전이한다",
      seq[0][1] == "ok" and seq[-1][1] == "stale", str(seq))
check("H8 전이가 단조적(되돌아가지 않음)",
      [s for _, s in seq] == sorted([s for _, s in seq], key=lambda x: {"ok": 0, "late": 1, "stale": 2}[x]),
      str(seq))

print("\n=== H10 · C-H6: 스케줄러 침묵 임계는 실측 기반이어야 한다 ===\n")
# 처음엔 "주기 4시간이니 8시간"으로 잡았는데, 실 launchd.log 를 세어보니 발화 간격이
# 최대 18.4시간이었다(맥 슬립). 추측한 임계는 오탐 제조기가 된다 — 그 회귀를 잠근다.
from datetime import datetime as _dt, timedelta as _td, timezone as _tz


def _sched(hours_ago: float) -> dict:
    ref = _dt.now(_tz.utc)
    touch = (ref - _td(hours=hours_ago)).isoformat(timespec="minutes")
    return health._scheduler_health(ref, touch)      # [F3] 기준 시각을 주입한다


check("H10 맥 슬립 구간(18.4h 관측 최대)은 침묵으로 보지 않는다",
      _sched(18.4)["silent"] is False, str(_sched(18.4)))
check("H10 24시간도 아직 아니다(주말 슬립 여유)", _sched(24)["silent"] is False)
check("H10 36시간 넘으면 침묵", _sched(40)["silent"] is True, str(_sched(40)))
check("H10 침묵은 reds() 에 실린다",
      any("scheduler" in r for r in
          health.SystemState(now=TODAY, scheduler=_sched(40)).reds()))
check("H10 로그가 아예 없으면 침묵 아님(최초 기동 전)",
      health._scheduler_health(_dt.now(_tz.utc), None)["silent"] is False)

print("\n=== H11 · 자기리뷰 F1: launchd-direct 의 '매일 실패'가 침묵하지 않는다 ===\n")
# 헬스 파일 ts 는 **실패해도 매일 갱신**된다. 그래서 `late`(마지막 실행 경과)로 등급을
# 매기면 30일을 내리 실패해도 late=0~1 이라 영원히 ⚠️·alertable=False 였다.
# 실측 시나리오: appstore-sourcing(노션 138행/일)이 한 달 실패해도 슬랙 푸시 0건.
reset()
(_SCRATCH / SK).mkdir(parents=True, exist_ok=True)
seq = []
for off in (0, 15, 30):
    d = TODAY + timedelta(days=off)
    (_SCRATCH / SK / "last_run_health.json").write_text(
        json.dumps({"ok": False, "ts": f"{d.isoformat()}T09:00:00"}), encoding="utf-8")
    st = health.compute(now=d, tasks=[T("t11", runner="launchd-direct")])
    seq.append((off, st.tasks[0].status, st.tasks[0].alertable))
check("H11 매일 실패는 첫날부터 알림 대상", seq[0][2] is True, str(seq))
check("H11 30일 뒤에도 여전히 알림 대상(침묵으로 굳지 않음)", seq[-1][2] is True, str(seq))
check("H11 성공 시각을 모르므로 last_success 는 None",
      health.compute(now=TODAY, tasks=[T("t11", runner="launchd-direct")]).tasks[0].last_success is None)
# 반대 방향 — ok=true 는 여전히 신선도 규칙을 탄다(과잉 경보 아님)
(_SCRATCH / SK / "last_run_health.json").write_text(
    json.dumps({"ok": True, "ts": f"{TODAY.isoformat()}T09:00:00"}), encoding="utf-8")
check("H11 ok=true 최신은 알림 대상 아님",
      health.compute(now=TODAY, tasks=[T("t11", runner="launchd-direct")]).tasks[0].alertable is False)

print("\n=== H12 · 자기리뷰 F2: 형제 task 의 잡을 자기 것으로 삼키지 않는다 ===\n")
# `startswith(name + "-")` 는 `sourcing` 이 `sourcing-followup-*` 를 먹는다. 그리고
# `_first_seen` 의 min() 이 그 값을 채택해 **올바른 first_attempt 를 덮는다**(역방향 자기치유).
reset()
wm("sib", last_attempt=TODAY.isoformat(), first_attempt=TODAY.isoformat())
jobstate("sib-2026-08-09", "failed", "2026-08-09T00:00:00Z")
jobstate("sib-followup-2026-01-02", "failed", "2026-01-02T00:00:00Z")   # 형제 task 의 잡
st = health.compute(now=TODAY, tasks=[T("sib")])
check("H12 형제 잡(sib-followup)을 자기 최초 시도로 세지 않는다",
      st.tasks[0].first_seen == "2026-08-09", f"first_seen={st.tasks[0].first_seen}")
check("H12 형제 오염이 없으면 never(⚠️) 유지 — 거짓 🔴 없음",
      st.tasks[0].status == "never", f"{st.tasks[0].status} · {st.tasks[0].note}")
# 자기 잡은 여전히 인식한다(자기치유가 죽지 않았는지)
check("H12 자기 잡은 그대로 복원", health._earliest_attempt_from_jobs(SK, "sib") == "2026-08-09")
check("H12 형제 task 도 자기 잡만 본다",
      health._earliest_attempt_from_jobs(SK, "sib-followup") == "2026-01-02")

print("\n=== H13 · 자기리뷰 F3: 스케줄러 침묵도 now 주입을 따른다 ===\n")
# 알림 원천 4개 중 이것만 벽시계를 읽어, 검증 사각 ⑤가 이 갈래에만 남아 있었다.
reset()
(_SCRATCH / "_scheduler").mkdir(parents=True, exist_ok=True)
(_SCRATCH / "_scheduler" / "launchd.log").write_text("x", encoding="utf-8")
base = health.compute(now=TODAY, tasks=[]).scheduler
far = health.compute(now=TODAY + timedelta(days=30), tasks=[]).scheduler
check("H13 now 를 밀면 스케줄러 침묵 판정도 함께 움직인다",
      base.get("silent") is False and far.get("silent") is True,
      f"기준={base.get('hours')}h · +30일={far.get('hours')}h")

print("\n=== H9: 순수 읽기 계약 ===\n")
reset()
wm("t9", last_attempt=TODAY.isoformat(), last_success=TODAY.isoformat(), first_attempt="2026-08-01")
before = sorted(str(p.relative_to(_SCRATCH)) for p in _SCRATCH.rglob("*"))
health.compute(now=TODAY, tasks=[T("t9")])
after = sorted(str(p.relative_to(_SCRATCH)) for p in _SCRATCH.rglob("*"))
check("H9 compute 는 아무것도 쓰지 않는다", before == after,
      f"신규={set(after) - set(before)}")

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 health 계약 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ health 계약 전부 통과")
