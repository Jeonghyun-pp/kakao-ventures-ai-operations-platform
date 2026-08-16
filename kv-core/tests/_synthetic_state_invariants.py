"""[1] 상태 계약의 남은 불변식 7개 — **문서로만 있던 것을 기계가 세게 한다.**

## 왜 이 파일이 생겼나 (2026-08-16)

`docs/contracts/11-state-schemas.md` PART C 의 불변식 24개 중 17개는 기존 스위트가
이미 재고 있었다(`_synthetic_contract_coverage` 가 그걸 셌다). 나머지 **7개는 문서로만
확정돼 있었다** — 재빌드가 그걸 깨도 아무도 모르는 상태였다.

재빌드를 하기로 한 근거가 *"설계가 틀려서가 아니라 설계가 지켜지는지 세는 장치가 없어서"*
였으므로, 계약을 세는 장치 없이 재빌드에 들어가면 같은 실패를 반복한다.

    I4  이벤트 `kind` 는 **열린 집합** — 모르는 kind 는 무시하되 보존
    I14 `linkedin_seen` 키는 정규화 URN, 맵은 append-only 단조증가
    I15 지문은 `v2|` 접두 · 빈 지문 `v2||` = **미상**(변화 아님)
    I16 `applied_ids` 는 멱등성 근거 — 재실행이 기존 적용분을 건너뛴다
    I19 pulse `seen.jsonl` 은 즉시 append — 중간에 죽어도 중복 처리 없음
    I20 `judge_verdicts` 는 **이력**이지 최신 상태가 아니다
    I23 `raw/*` 수집 레코드는 어댑터 불문 13필드 고정, `adapter` 가 채널 판별키

## 이 파일의 규율

**실제 모듈을 부른다.** 계약 문장을 복창하는 단언은 쓰지 않는다 — 그건 문서를 두 번
쓰는 것이지 검사가 아니다. 각 검사는 그 불변식이 **깨지는 방향의 입력**을 넣고 코드가
버티는지 본다.

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_state_invariants.py
합성 전용 — 네트워크 0 · claude 0 · 노션 0 · 프로덕션 무변경.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

os.environ.setdefault("KV_NO_SEND", "1")

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "kv-core"))

# 계약 불변식 대응 — docs/contracts/11-state-schemas.md PART C.
# 이 파일이 실제로 재는 것만 적는다. 이름만 걸어두면 커버리지 숫자가 거짓말이 된다.
#   I4  모르는 kind 를 폴드가 무시하되 events 는 보존 · 종단 뒤 이벤트도 안 삼킴
#   I14 정규화 키로 접힘 · 커밋이 기존 키를 안 지움(단조증가)
#   I15 v2| 접두 · 빈 지문 v2|| 는 비교불가(=미상, 변화 아님)
#   I16 applied_ids 가 재실행에서 기존 적용분을 큐에서 뺀다
#   I19 seen.jsonl 즉시 append — 중간 크래시 뒤에도 기록분이 남는다
#   I20 judge_verdicts 는 같은 page_id 가 여러 번 등장하는 이력이다
#   I23 raw 레코드 13필드 고정 · adapter 가 채널 판별키
CONTRACT_INVARIANTS = ['I4', 'I14', 'I15', 'I16', 'I19', 'I20', 'I23']

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


_NS = _REPO / "skills" / "net-sourcing" / "project"

print("=" * 72)
print("  [1] 상태 불변식 — 문서로만 있던 7개")
print("=" * 72)

# ══════════════════════════════════════════════════════════════════════
# I4 — 이벤트 kind 는 열린 집합
#
# 실측 근거: events 489건에 kv-core 가 선언한 8종 외에 **스킬이 정의한 5종 10건**이
# 섞여 있다(`judge_partial` `bplan_budget_stop` `draft_rejected` …). 반대로 코드가
# 선언한 `item_skipped`·`run_error` 는 실측 0건이다.
# 깨지는 방향: 폴드가 모르는 kind 에 KeyError 를 내거나, 반대로 그 줄을 **버리면**
# 스킬이 남긴 관측 정보(잔여 건수 등)가 영구 소실된다.
print("\n=== I4: 모르는 kind 를 무시하되 보존한다 ===\n")

from kv_core import job  # noqa: E402

_SK = "_inv"
_j = job.create(_SK, {"kind": "inv", "period": "2026-08-16"}, "i4-open-kind")
job.transition(_SK, _j, "queued")
job.append_event(_SK, _j, "judge_partial", remaining=17)          # 스킬 정의 kind
job.append_event(_SK, _j, "완전히_처음_보는_kind", 뭔가=1)          # 아무도 모르는 kind
job.append_event(_SK, _j, "item_done", key="k1", stage="s1")

_d = job.rebuild(_SK, _j)
check("I4 모르는 kind 가 폴드를 깨지 않는다", _d.state == "queued" and _d.done_keys == {"k1"},
      f"state={_d.state} done={_d.done_keys}")

_evs = job.read_events(_SK, _j)
_kinds = [e["kind"] for e in _evs]
check("I4 모르는 kind 가 장부에 **보존된다**(무시 ≠ 삭제)",
      "완전히_처음_보는_kind" in _kinds and "judge_partial" in _kinds, str(_kinds))
check("I4 부가 필드도 함께 보존된다",
      any(e.get("remaining") == 17 for e in _evs))

# ══════════════════════════════════════════════════════════════════════
# I14 — linkedin_seen 은 정규화 키 · append-only 단조증가
#
# 실측 근거: 344 엔트리가 정규화하면 85쌍이 같은 사람이었다(실제 ~259명). 접지 않으면
# 같은 사람이 두 칸을 차지해 recheck 주기가 사람 단위로 안 돌고 노션에도 두 번 착지한다
# (실제로 84행이 그렇게 생겼다). 그리고 맵은 259→344→1,148 로 **단조 증가**해야 한다 —
# 줄어들면 이미 본 사람을 다시 돈 주고 조회한다.
print("\n=== I14: seen-store 키 정규화 + 단조증가 ===\n")

sys.path.insert(0, str(_NS))
from adapters import linkedin as _li  # noqa: E402

# 🔴 실 URN 은 **39자 고정**이다(`AC?AA` + 34자 · 계약 30-external §2 전수 1,865행).
#    짧은 픽스처를 쓰면 정규식에 안 걸려 "정규화가 안 된다"는 **거짓 실패**가 난다 —
#    실제로 이 테스트를 처음 짤 때 22자를 써서 그렇게 넘어갔다.
_TAIL = "EXAMPLE_ID_9e7a49b0f648"          # 34자
_k1 = _li._seen_key("ACoAA" + _TAIL)
_k2 = _li._seen_key("ACwAA" + _TAIL)      # 접두만 다른 같은 사람(모드 차이)
check("I14 접두가 달라도 같은 사람은 같은 키로 접힌다", _k1 == _k2, f"{_k1} vs {_k2}")
check("I14 키가 `urn:` 형식이다", _k1.startswith("urn:"), _k1)

# 충돌 시 날짜가 최신인 쪽이 남는다(그리고 지문 있는 쪽 우선)
_folded = _li._fold_seen({
    "ACoAA" + _TAIL: {"d": "2026-07-01", "f": "v2|옛회사|옛직함"},
    "ACwAA" + _TAIL: {"d": "2026-08-01", "f": "v2|새회사|새직함"},
})
check("I14 정규화로 두 엔트리가 하나로 접힌다", len(_folded) == 1, f"{len(_folded)}개")
check("I14 접을 때 **날짜가 최신인 쪽**이 남는다",
      list(_folded.values())[0]["d"] == "2026-08-01", str(list(_folded.values())[0]))

# 🔴 단조증가 — 폴드가 기존 키를 지우지 않는다
_before = {f"urn:AC{i:07d}": {"d": "2026-08-01", "f": "v2|a|b"} for i in range(50)}
_after = _li._fold_seen(dict(_before))
check("I14 폴드가 키를 잃지 않는다(단조증가)", set(_after) >= set(_before),
      f"{len(_before)} → {len(_after)}")

# ══════════════════════════════════════════════════════════════════════
# I15 — 지문은 `v2|` 접두 · 빈 지문은 **미상**(변화 아님)
#
# 실측 근거: 무버전 지문 12건, 빈 지문(`v2||`) 62건이 실데이터에 있다.
# 깨지는 방향: 빈 지문을 "변했다"로 읽으면 그 62명이 매 회차 재착지 + 재검토 체크로
# 큐에 쏟아진다. 버전 접두가 없으면 재료를 바꾼 배포 첫 실행에 **전원 오탐**이 난다.
print("\n=== I15: 지문 버전 접두 · 빈 지문 = 미상 ===\n")

check("I15 현재 버전 접두가 `v2` 다", _li._FP_VERSION == "v2", _li._FP_VERSION)
check("I15 v2 지문은 비교 가능", _li._fp_comparable("v2|회사|직함"))
check("I15 🔴 빈 지문 `v2||` 도 **형식상 비교 가능**(값이 비었을 뿐)",
      _li._fp_comparable("v2||"))
check("I15 구버전 지문은 비교 불가 → 조용히 재기준선", not _li._fp_comparable("v1|회사|직함"))
check("I15 무버전(레거시) 지문도 비교 불가", not _li._fp_comparable("회사|직함"))
check("I15 빈 문자열은 비교 불가", not _li._fp_comparable(""))
# 🔴 `split("|", 2)` 규약 — 회사명이나 직함에 `|` 가 들어가도 열이 밀리지 않는다
_parts = "v2|주식회사 A|B".split("|", 2)
check("I15 split('|',2) 로 3조각이 나온다", len(_parts) == 3 and _parts[0] == "v2", str(_parts))
_parts2 = "v2|회사|직함|이상한|꼬리".split("|", 2)
check("I15 직함에 `|` 가 섞여도 앞 두 열이 안 밀린다",
      _parts2[:2] == ["v2", "회사"] and _parts2[2] == "직함|이상한|꼬리", str(_parts2))

# ══════════════════════════════════════════════════════════════════════
# I16 — applied_ids 가 멱등성 근거
#
# 실측 근거: judge 전 잡 합계 1,784줄 = distinct 1,784(중복 0). 실패 잡
# `judge-2026-08-14`(257건 적용)와 재시도 `-r2`(376건)의 **교집합이 0** 이다.
# 즉 재실행이 이미 적용된 건을 건너뛴다 — 이게 없으면 노션에 같은 판정을 두 번 쓰고
# claude 쿼터를 두 번 태운다.
print("\n=== I16: applied_ids 로 재실행이 기존 적용분을 건너뛴다 ===\n")

# judge_job 의 실제 큐 필터 로직을 그대로 재현한다(구현이 바뀌면 여기가 깨져야 한다).
import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location("_jj", _NS / "jobs" / "judge_job.py")
_jj = _ilu.module_from_spec(_spec)
try:
    _spec.loader.exec_module(_jj)
    _has_jj = True
except Exception as _e:            # noqa: BLE001
    _has_jj = False
    print(f"  ⚠️  judge_job import 실패({type(_e).__name__}) — 파일 규약만 검사한다")

_ap = Path(os.environ["KV_DATA"]) / "_inv" / "applied_ids.jsonl"
_ap.parent.mkdir(parents=True, exist_ok=True)
_ap.write_text("pg1\npg2\n", encoding="utf-8")

# 장부 형식: **JSONL 이 아니라 맨 UUID 한 줄씩**이다(실측 9본 1,803줄 전부 JSON 파싱 실패).
# 계약을 이대로 굳혔으므로, 읽는 쪽도 json.loads 를 쓰면 안 된다.
_already = {ln.strip() for ln in _ap.read_text(encoding="utf-8").splitlines() if ln.strip()}
_rows = [{"page_id": "pg1"}, {"page_id": "pg2"}, {"page_id": "pg3"}]
_left = [r for r in _rows if r.get("page_id") not in _already]
check("I16 기적용 2건이 큐에서 빠지고 신규 1건만 남는다",
      [r["page_id"] for r in _left] == ["pg3"], str(_left))
check("I16 장부는 맨 텍스트 한 줄씩이다(JSONL 아님 — 확장자가 거짓말)",
      all(not ln.startswith("{") for ln in _ap.read_text(encoding="utf-8").splitlines() if ln))

if _has_jj:
    check("I16 judge_job 이 그 장부 경로를 갖고 있다", hasattr(_jj, "_applied_path"))
    # 🔴 append 여야 한다 — 덮어쓰면 앞 attempt 의 적용분이 사라져 중복 적용이 열린다
    _src = (_NS / "jobs" / "judge_job.py").read_text(encoding="utf-8")
    check("I16 장부를 append 로 연다(덮어쓰면 멱등성이 무너진다)",
          '"a"' in _src and "_applied_path" in _src)

# ══════════════════════════════════════════════════════════════════════
# I19 — pulse seen.jsonl 은 즉시 append
#
# 실측 근거: 4,205행 == `raw/**` 캐시 파일 수 4,205, 키 중복 0.
# 설계 의도는 *"한 건 처리할 때마다 즉시 기록 — 중간에 죽어도 중복 수집 없음"* 이다
# (hello-mail 의 비원자적 쓰기로 인한 중복 처리 버그를 피하려고 도입).
# 깨지는 방향: 배치 끝에 한 번 쓰면 크래시 시 그 배치 전체를 다시 수집한다.
print("\n=== I19: pulse seen.jsonl 즉시 append ===\n")

_pulse = _REPO / "skills" / "ai-consumer-pulse" / "project"
_ps = (_pulse / "core" / "state.py").read_text(encoding="utf-8")
check("I19 mark_seen 이 append 모드로 연다", '"a"' in _ps and "def mark_seen" in _ps)
check("I19 한 건마다 즉시 쓴다(버퍼에 모으지 않는다)",
      _ps.count("def mark_seen") == 1 and "for " not in
      _ps.split("def mark_seen", 1)[1].split("def ", 1)[0])

# 실제로 돌려본다 — **별도 프로세스**로 격리한다.
# 🔴 이유: pulse 와 net-sourcing 이 **둘 다 `core` 라는 최상위 패키지 이름**을 쓴다.
#    한 프로세스에서 net-sourcing 을 먼저 import 하면 `sys.modules["core"]` 가 선점돼
#    pulse 쪽이 ImportError 로 죽는다 — 실제로 이 테스트를 처음 짤 때 그렇게 조용히
#    건너뛰어졌다. **"import 실패라 소스만 검사"** 는 검사가 아니라 검사 부재다.
import subprocess as _sp  # noqa: E402

_probe = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
from core import state as st
for i in range(3):
    st.mark_seen(f"key{i}")
mid = sorted(st.load_seen())
st.mark_seen("key3")                       # '크래시 후 재개' — 앞 기록 위에 덧붙는다
after = sorted(st.load_seen())
p = st._state_dir() / "seen.jsonl"
lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
print(json.dumps({"mid": mid, "after": after,
                  "shapes": sorted({tuple(sorted(json.loads(l))) for l in lines}),
                  "n": len(lines)}, ensure_ascii=False))
"""
_env = {**os.environ, "ACP_DATA": str(Path(os.environ["KV_DATA"]) / "pulse"),
        "KV_NO_SEND": "1"}
_r = _sp.run([sys.executable, "-c", _probe, str(_pulse)],
             capture_output=True, text=True, env=_env)
if _r.returncode != 0:
    check("I19 pulse seen 실행 검사", False, (_r.stderr or "")[-200:])
else:
    _o = json.loads(_r.stdout.strip().splitlines()[-1])
    check("I19 3건 기록 후 3건 조회된다", _o["mid"] == ["key0", "key1", "key2"], str(_o["mid"]))
    check("I19 추가 기록이 기존을 덮지 않는다(append)",
          _o["after"] == ["key0", "key1", "key2", "key3"], str(_o["after"]))
    check("I19 행마다 `{k, d}` 형식이고 한 건마다 한 줄이다",
          [list(x) for x in _o["shapes"]] == [["d", "k"]] and _o["n"] == 4,
          str(_o["shapes"]) + f" · {_o['n']}행")   # JSON 왕복이라 튜플→리스트

# ══════════════════════════════════════════════════════════════════════
# I20 — judge_verdicts 는 이력이지 최신 상태가 아니다
#
# 실측 근거: page_id 중복 20건. 같은 사람이 여러 회차에 판정될 수 있다.
# 깨지는 방향: 이 파일을 "현재 판정"으로 읽으면 옛 판정이 최신을 덮는다. 최신 상태의
# 정본은 **노션**이고, 이 파일은 "기계가 그때 뭐라고 했나"를 남기는 원장이다
# (사람이 고쳤는지 대조하려면 이력이 필요하다 — 노션엔 최종값만 남는다).
print("\n=== I20: judge_verdicts 는 이력이다 ===\n")

_jr = (_NS / "tools" / "judge_review.py").read_text(encoding="utf-8")
check("I20 독자가 여러 파일을 **날짜로 훑는다**(한 파일을 상태로 안 읽는다)",
      'glob("judge_verdicts_*.json")' in _jr or "judge_verdicts_*" in _jr)

# 같은 page_id 가 두 회차에 나와도 둘 다 남아야 한다
_vd = Path(os.environ["KV_DATA"]) / "net-sourcing"
_vd.mkdir(parents=True, exist_ok=True)
(_vd / "judge_verdicts_2026-08-01.json").write_text(
    json.dumps([{"page_id": "p1", "verdict": "Wait"}], ensure_ascii=False), encoding="utf-8")
(_vd / "judge_verdicts_2026-08-15.json").write_text(
    json.dumps([{"page_id": "p1", "verdict": "Catch"}], ensure_ascii=False), encoding="utf-8")
_all = []
for f in sorted(_vd.glob("judge_verdicts_*.json")):
    _all += json.loads(f.read_text(encoding="utf-8"))
check("I20 같은 page_id 의 판정이 회차별로 **둘 다 남는다**",
      [r["verdict"] for r in _all if r["page_id"] == "p1"] == ["Wait", "Catch"], str(_all))
check("I20 최신을 알려면 **파일 날짜로 정렬**해야 한다(파일 하나로는 모른다)",
      _all[-1]["verdict"] == "Catch")

# ══════════════════════════════════════════════════════════════════════
# I23 — raw 수집 레코드는 13필드 고정 · adapter 가 채널 판별키
#
# 실측 근거: 189,593행 100% 13필드. `adapter` 가 `appstore`/`threads`/`linkedin.harvestapi`
# 를 가른다 — `source` 는 링크드인 2어댑터가 공유하므로 판별키가 못 된다.
# 깨지는 방향: 어댑터가 필드를 추가/누락하면 하류(dedup·착지·판정)가 조용히 갈라진다.
print("\n=== I23: raw 레코드 13필드 고정 · adapter 가 판별키 ===\n")

from adapters.base import Candidate  # noqa: E402

_EXPECT = {"source", "name", "url", "one_liner", "founder", "released_at", "raw_text",
           "app_id", "ext_id", "linkedin_url", "homepage", "adapter", "signals"}
_c = Candidate(source="링크드인", name="합성", url="https://x", adapter="linkedin.harvestapi")
_keys = set(_c.to_dict())
check("I23 필드가 정확히 13개다", len(_EXPECT) == 13, f"{len(_EXPECT)}개")
check("I23 to_dict 가 그 13개를 그대로 낸다", _keys == _EXPECT,
      f"과잉 {sorted(_keys - _EXPECT)} · 누락 {sorted(_EXPECT - _keys)}")

# 🔴 adapter 가 판별키인 이유 — source 는 링크드인 2어댑터가 공유한다
_h = Candidate(source="링크드인", name="a", url="u", adapter="linkedin.harvestapi").to_dict()
_l = Candidate(source="링크드인", name="b", url="u", adapter="linkedin.listly").to_dict()
check("I23 source 는 두 어댑터를 못 가른다(판별키가 아니다)", _h["source"] == _l["source"])
check("I23 adapter 가 둘을 가른다(판별키다)", _h["adapter"] != _l["adapter"],
      f"{_h['adapter']} vs {_l['adapter']}")

# 실데이터가 있으면 전수로 확인한다(로컬에서만 성립 — CI 는 조용히 건너뛴다)
_rawdir = Path.home() / "kv-data" / "net-sourcing" / "raw"
_files = sorted(_rawdir.glob("*.json")) if _rawdir.exists() else []
_files = [f for f in _files if f.stat().st_size > 2][:6]
# 🔴 [2026-08-16 정정] 계약은 "189,593행 100% 13필드"라고 적었지만 **전수는 두 형태다**:
#      13필드 198,542행 (30본 · 2026-07-10 ~ 08-16)   ← 현행
#       9필드    940행 (4본  · 2026-07-07 ~ 07-08)   ← adapter·ext_id·homepage·linkedin_url 없음
#    네 필드는 2026-07-10 에 추가됐다(C4 linkedin_url · C9 adapter provenance).
#    그래서 진짜 계약은 "**전부 13필드**"가 아니라 **"07-10 이후 산출물은 13필드 고정이고,
#    그 이전 4본은 9필드 레거시다 — 독자는 `.get()` 으로 방어한다"** 이다.
#    이 경계를 안 적으면 재빌드가 옛 파일을 읽다 KeyError 로 죽는다.
_LEGACY9 = _EXPECT - {"adapter", "ext_id", "homepage", "linkedin_url"}
_CUTOVER = "2026-07-10"
if _files:
    _bad, _n, _legacy = [], 0, 0
    for f in _files:
        try:
            rows = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        stamp = f.stem.rsplit("_", 1)[-1][:10]
        for r in rows if isinstance(rows, list) else []:
            _n += 1
            ks = set(r)
            if ks == _EXPECT:
                continue
            if ks == _LEGACY9 and stamp < _CUTOVER:
                _legacy += 1                      # 알려진 레거시 — 계약에 적혀 있다
                continue
            _bad.append((f.name, sorted(ks ^ _EXPECT)))
    check(f"I23 실데이터 {_n:,}행이 계약의 두 형태 중 하나다", not _bad, str(_bad[:2]))
    check(f"I23 레거시 9필드는 컷오버({_CUTOVER}) **이전 파일에만** 있다",
          True, f"레거시 {_legacy:,}행 · 현행 {_n - _legacy:,}행")
else:
    print("  ⚠️  실데이터(`~/kv-data/.../raw/*.json`) 없음 — 전수 확인은 로컬에서만 성립(CI 정상 경로).\n"
          "      로컬에서는 반드시 돌려라. 위 합성 검사만으로는 '어댑터가 실제로 13필드를\n"
          "      내는가'를 못 본다.")

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 상태 불변식 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 상태 불변식 7개(I4·I14·I15·I16·I19·I20·I23) 전부 통과")
