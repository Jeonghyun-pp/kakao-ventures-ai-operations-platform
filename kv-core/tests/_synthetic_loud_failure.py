"""침묵 폴백 — **실패를 낙관으로 번역하지 않는다.**

## 왜 이 파일이 생겼나

`cadence.py` 는 파일 첫머리에 이렇게 적었다 —
*"조용한 폴백을 만들지 마라 — 이 파일의 존재 이유다."*

2026-08-13 전수 감사(리뷰 E)가 찾은 것은 **그 원칙이 옆 모듈로 전개되지 않았다**는 것이다.
같은 리포 안에서 세 로더의 엄격도가 제각각이었다:

    registry.load        미지 필드면 raise          ← 엄격
    cadence.gap_days     모르는 이름이면 크게 경고    ← 보수적 폴백
    validate.load_contracts  **완전 침묵**           ← 오타가 그냥 통과

그 침묵이 만든 상태 넷을 여기서 잠근다.

    L1  계약 로더 — 키·타입 오타가 검증을 조용히 무력화하지 않는다        (E-F5)
    L2  워터마크 판독 — 부재와 손상을 구분하고, 손상은 보존한다            (E-F8)
    L3  registry — status·resources·routine/cadence·skill 을 검증한다   (E-F9)
    L4  run_due — "물러났다"와 "due 없다"가 다른 값이다                  (E-F10)

## 각각이 실제로 무엇을 만들었나

L1 `shema:` 한 글자 → 스키마 `{}` → `on_invalid: halt` 계약이 **쓰레기 전량 통과**.
   ADR 0003 ④ 가 "생명줄"로 승격한 validating 스테이지가 한 글자로 꺼진다.
L2 워터마크 손상 1회 → `first_attempt` 가 오늘로 리셋 → never-succeeded 에스컬레이션
   시계가 되감긴다. 정본 R1 이 이미 한 번 고친 **알림 영구 침묵**의 재발 경로다.
L3 `resources: [cluade]` → `_locks/cluade.lock` 이라는 별도 락 → claude 직렬화가
   조용히 갈라진다. `cadence` 오타는 fail-loud 인데 **한 필드 옆은 무검증**이었다.
L4 SlotBusy 후퇴와 "due 없음"이 둘 다 `[]` → 물러난 직후 로그에
   `실행할 배선 task 없음(전부 최신).` 이라는 **거짓 문장**이 찍혔다.

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_loud_failure.py
전부 합성 — 외부 부작용 0.
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import sys  # noqa: E402
import tempfile  # noqa: E402
from datetime import date  # noqa: E402
from pathlib import Path  # noqa: E402

import yaml  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "kv-core"))

from kv_core import job, registry, scheduler, validate  # noqa: E402
from kv_core.registry import Task  # noqa: E402

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


def _raises(fn) -> str | None:
    try:
        fn()
    except Exception as e:                       # noqa: BLE001
        return f"{type(e).__name__}"
    return None


# ══════════════════════════════════════════════════════════════════════
print("=== L1: 계약 로더가 오타를 삼키지 않는다 ===\n")
_d = Path(tempfile.mkdtemp())


def _load(body: str):
    f = _d / "c.yaml"
    f.write_text(body, encoding="utf-8")
    return validate.load_contracts(f)


_OK = 'judge:\n  schema:\n    id: {type: str, required: true}\n  on_invalid: halt\n  min_records: 1\n'
check("L1 정상 계약은 로드된다(엄격도가 실사용을 죽이지 않는다)",
      list(_load(_OK)["judge"].schema) == ["id"])

for _label, _body in [
    ("schema 오타(shema)", _OK.replace("schema:", "shema:")),
    ("min_records 오타", _OK.replace("min_records:", "min_recods:")),
    ("모르는 type 이름(integer)", _OK.replace("type: str", "type: integer")),
    ("모르는 필드 키(requird)", _OK.replace("required:", "requird:")),
    ("빈 schema", 'judge:\n  schema: {}\n  on_invalid: halt\n'),
]:
    check(f"L1 {_label} → raise", _raises(lambda b=_body: _load(b)) is not None,
          "조용히 통과하면 계약이 있는데 안 돈다")

# 실 계약이 새 엄격도를 통과하는지 — 검사를 강화하다 운영을 깨뜨리면 안 된다.
_real = validate.load_contracts(_REPO / "config" / "contracts.yaml")
check("L1 실 contracts.yaml 이 통과한다", len(_real) >= 5, f"{len(_real)}종: {sorted(_real)}")

# ══════════════════════════════════════════════════════════════════════
print("\n=== L2: 워터마크 판독이 부재와 손상을 구분한다 ===\n")
_t = Task(name="wm-loud", skill="net-sourcing", kind="routine", cadence="quarterly",
          catch_up="coalesce", watermark="last_success")
check("L2 파일이 없으면 조용히 {} (첫 실행은 정상이다)", scheduler.read_watermark(_t) == {})

scheduler.write_watermark(_t, {"first_attempt": "2026-01-01", "last_success": "2026-08-01"})
_p = scheduler._wm_path(_t)
_p.write_text("{{{깨짐", encoding="utf-8")
_wm = scheduler.read_watermark(_t)
_kept = sorted(_p.parent.glob("wm-loud.corrupt-*"))
check("L2 손상은 {} 를 주되 원본을 보존한다", _wm == {} and len(_kept) == 1,
      f"보존={[k.name for k in _kept]} — 지우면 사람이 복구할 근거가 사라진다")
check("L2 보존된 원본이 실제 내용을 담고 있다",
      bool(_kept) and _kept[0].read_text(encoding="utf-8") == "{{{깨짐")

# ══════════════════════════════════════════════════════════════════════
print("\n=== L3: registry 가 선언을 검증한다 ===\n")
_base = {"name": "t", "skill": "net-sourcing", "kind": "routine", "cadence": "daily",
         "catch_up": "coalesce", "watermark": "last_success", "status": "done"}


def _reg(**over):
    spec = {**_base, **over}
    spec = {k: v for k, v in spec.items() if v is not None}
    f = _d / "t.yaml"
    f.write_text(yaml.safe_dump([spec], allow_unicode=True), encoding="utf-8")
    return registry.load(f)


check("L3 정상 선언은 로드된다", len(_reg()) == 1)
for _label, _over in [
    ("status 오타", {"status": "complete"}),
    ("resources 오타(cluade)", {"resources": ["cluade"]}),
    ("routine 인데 cadence 없음", {"cadence": None}),
    ("실재하지 않는 skill", {"skill": "nonexistent-skill"}),
]:
    check(f"L3 {_label} → raise", _raises(lambda o=_over: _reg(**o)) is not None)

# slot 은 아직 안 만든 자리 — 스킬이 없어도 정상이다(과잉 엄격 방지).
check("L3 status: slot 은 스킬이 없어도 통과한다(미구현 자리)",
      _raises(lambda: _reg(skill="future-thing", status="slot",
                           kind="ondemand", cadence=None)) is None)

check("L3 watermark 기본값이 보수적인 쪽이다(last_success)",
      Task(name="x", skill="y").watermark == "last_success",
      "last_attempt 가 기본이면 실패해도 워터마크가 전진한다(ADR 0004 위반)")

check("L3 실 tasks.yaml 이 통과한다", len(registry.load()) >= 10)

# ══════════════════════════════════════════════════════════════════════
print("\n=== L4: 후퇴와 'due 없음'이 다른 값이다 ===\n")
with job.heavy_slot("scheduler"):
    _r = scheduler.run_due([], lambda t, p: True, now=date(2026, 8, 4))
check("L4 락 점유 중이면 None(= 물러났다)", _r is None, f"got {_r!r}")

_r2 = scheduler.run_due([], lambda t, p: True, now=date(2026, 8, 4))
check("L4 실제로 돌았는데 due 가 없으면 []", _r2 == [], f"got {_r2!r}")
check("L4 두 값이 서로 다르다(같으면 호출부가 구분할 수 없다)", _r is not _r2)

# ══════════════════════════════════════════════════════════════════════
print("\n=== L5: 트리아지 처리이력 — 자기가 피하겠다던 결함을 안 갖는다 ===\n")
# 🔴 [W12] `triage/core/state.py` 의 독스트링은 *"bplan 의 알려진 결함(비원자적 쓰기 →
#    재실행 시 중복 생성)을 되풀이하지 않는다"* 였는데, 실제로는 형태만 바꿔 셋 다 있었다:
#      ① `except Exception: return {}`  손상을 조용히 삼킨다 → **전 메일이 미처리로 보인다**
#      ② `write_text()`                 원자적이 아니다 → 반쪽 파일 → ①이 `{}` 로 만든다
#      ③ 락 없는 read-merge-write       두 프로세스가 겹치면 한쪽 기록이 사라진다
#    `mail-triage` 는 매일 무인으로 돈다. 컷오버 후엔 초안까지 가므로 그때 중복이 나간다.
import subprocess  # noqa: E402
import tempfile  # noqa: E402

sys.path.insert(0, str(_REPO / "skills/mail-harness/project/triage"))
from core import state as _st  # noqa: E402

_st.STORE.parent.mkdir(parents=True, exist_ok=True)
_st.STORE.unlink(missing_ok=True)
_st._CACHE = None
_st.mark("m1", "c", "a")
check("L5 기본 동작 — 기록·조회", _st.seen("m1") and not _st.seen("m9"))

_st.STORE.write_text("{{{깨짐", encoding="utf-8")
_loaded = _st.load(refresh=True)
_bak = list(_st.STORE.parent.glob("*.corrupt-*"))
check("L5 손상은 원본을 보존하고 시끄럽게 알린다(조용히 {} 를 주지 않는다)",
      _loaded == {} and len(_bak) == 1, f"보존 {[b.name for b in _bak]}")

# 동시 쓰기 — **진짜 프로세스** 5개. 스레드로는 이 결함이 재현되지 않는다.
_st.STORE.unlink(missing_ok=True)
_st._CACHE = None
_code = ('import sys;sys.path.insert(0,"kv-core");'
         'sys.path.insert(0,"skills/mail-harness/project/triage")\n'
         'from core import state\nstate.mark(sys.argv[1],"c","a")\n')
_f = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
_f.write(_code)
_f.close()
_ps = [subprocess.Popen([".venv/bin/python", _f.name, f"msg{i}"],
                        cwd=str(_REPO), env={**os.environ}) for i in range(5)]
for _p in _ps:
    _p.wait()
_final = _st.load(refresh=True)
check("L5 동시 기록 5개가 전부 살아남는다(lost update 없음)", len(_final) == 5,
      f"{len(_final)}/5 — 락이 없으면 1~2개만 남는다")

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 침묵 폴백 계약 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 침묵 폴백 계약 통과 — 실패가 낙관으로 번역되지 않는다")
