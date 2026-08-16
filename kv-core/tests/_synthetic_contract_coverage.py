"""계약 ↔ 테스트 대응 게이트 — **계약에 적힌 것이 실제로 검사되는가.**

## 왜 이 파일이 생겼나 (2026-08-16)

2026-08-15~16 전수 실측으로 `docs/contracts/` 가 생겼다. 거기엔
**재빌드가 깨면 데이터가 손상되는 불변식 24개**(`11-state-schemas.md` PART C)와
외부 제약 대장이 들어 있다. 그런데 계약은 **문서**다 — 문서는 코드가 어긋나도 조용하다.

이 리포의 지배적 실패 모드가 정확히 그것이었다: *"선언은 있는데 발동하지 않는 가드"*.
계약을 써 놓고 테스트에 연결하지 않으면 계약도 같은 종류의 선언이 된다.

## 검사하는 것

    X1  계약 문서에서 불변식 목록을 실제로 뽑았다 (0개면 아래가 전부 공허하다)
    X2  각 테스트가 **자기가 재는 불변식**을 선언한다 (`CONTRACT_INVARIANTS`)
    X3  선언된 ID 가 계약에 실재한다 (오타·삭제된 ID 를 잡는다)
    X4  미검증 불변식 수가 **늘지 않았다** (상한. 줄면 내리고 늘면 실패한다)
    X5  `kv_core` 의 모든 모듈이 최소 한 테스트에 닿는다 (새 모듈이 무검증으로 태어나지 않게)
    X6  외부 제약 대장의 항목 수가 줄지 않았다 (계약이 조용히 사라지지 않게)

## 이 게이트의 한계 — 반드시 알고 써라

**"선언했다"와 "실제로 잰다"는 다르다.** 이 파일은 선언을 셀 뿐, 그 테스트가 정말 그
불변식을 재는지는 **증명하지 못한다.** 그래서 각 테스트의 `CONTRACT_INVARIANTS` 위에는
"이 파일이 그 불변식의 무엇을 재는가"를 한 줄씩 적어 두었다. 이름만 걸어두면
**커버리지 숫자가 거짓말이 된다** — 그게 이 리포가 반복해서 당한 함정이다.

숫자를 올리고 싶으면 선언을 늘리지 말고 **검사를 늘려라.**

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_contract_coverage.py
파일만 읽는다 — 네트워크 0 · 부작용 0.
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("KV_NO_SEND", "1")

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

_REPO = Path(__file__).resolve().parents[2]
_TESTS = Path(__file__).resolve().parent
_CONTRACTS = _REPO / "docs" / "contracts"
_SELF = Path(__file__).name

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


# ══════════════════════════════════════════════════════════════════════
print("=" * 72)
print("  계약 ↔ 테스트 대응")
print("=" * 72)
print("\n=== X1: 계약에서 불변식을 뽑는다 ===\n")

# PART C 표: `| I7 | <불변식> | <실측 근거> |`
_md = (_CONTRACTS / "11-state-schemas.md").read_text(encoding="utf-8")
_INV: dict[str, str] = {}
for m in re.finditer(r"^\|\s*(I\d+)\s*\|\s*(.+?)\s*\|", _md, re.M):
    _INV[m.group(1)] = m.group(2)

check("X1 계약 문서에서 불변식을 뽑았다", len(_INV) >= 20, f"{len(_INV)}개")
check("X1 번호가 I1 부터 연속이다(중간이 빠지면 표가 깨진 것)",
      sorted(_INV, key=lambda s: int(s[1:])) == [f"I{i}" for i in range(1, len(_INV) + 1)],
      f"{sorted(_INV, key=lambda s: int(s[1:]))[:3]}…")

# ══════════════════════════════════════════════════════════════════════
print("\n=== X2/X3: 테스트가 자기가 재는 불변식을 선언한다 ===\n")

_declared: dict[str, list[str]] = {}
for p in sorted(_TESTS.glob("_*.py")):
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except SyntaxError as e:            # noqa: PERF203
        check(f"X2 {p.name} 파싱", False, str(e)); continue
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "CONTRACT_INVARIANTS"):
            try:
                _declared[p.name] = list(ast.literal_eval(node.value))
            except (ValueError, SyntaxError):
                check(f"X2 {p.name} 의 CONTRACT_INVARIANTS 가 리터럴이 아니다", False)

_covered = {i for ids in _declared.values() for i in ids}
_unknown = sorted(_covered - set(_INV), key=str)
check("X3 선언된 ID 가 전부 계약에 실재한다", not _unknown, f"계약에 없는 ID: {_unknown}")

print(f"     ↳ 선언한 테스트 {len(_declared)}본:")
for fn in sorted(_declared):
    print(f"       · {' '.join(sorted(_declared[fn], key=lambda s: int(s[1:]))):24} {fn}")

# ══════════════════════════════════════════════════════════════════════
print("\n=== X4: 미검증 불변식이 늘지 않았다 ===\n")

# 🔴 상한이다. **줄면 내리고 늘면 실패한다.** 새 불변식이 계약에 추가되면 이 숫자가
#    올라가므로, 추가한 사람이 테스트를 붙이거나 최소한 여기를 만지며 알게 된다.
#
#    [2026-08-16] 7 → **0**. 남아 있던 I4·I14·I15·I16·I19·I20·I23 을
#    `_synthetic_state_invariants.py` 로 채웠다. 이제 **24/24 전부** 기계가 센다.
#    0 이라는 것은 "계약에 적힌 모든 불변식에 최소 한 개의 검사가 붙어 있다"는 뜻이지
#    "완벽하게 검증됐다"는 뜻이 아니다 — 이 파일 상단의 한계를 읽어라.
_UNCOVERED_MAX = 0
_uncovered = sorted(set(_INV) - _covered, key=lambda s: int(s[1:]))

check("X4 미검증 불변식이 상한을 넘지 않았다", len(_uncovered) <= _UNCOVERED_MAX,
      f"{len(_uncovered)}개 / 상한 {_UNCOVERED_MAX}")
print(f"     ↳ 검사 {len(_covered)}/{len(_INV)} · 미검증 {len(_uncovered)}개:")
for i in _uncovered:
    print(f"       🔴 {i:4} {_INV[i][:82]}")

# ══════════════════════════════════════════════════════════════════════
print("\n=== X5: 새 모듈이 무검증으로 태어나지 않는다 ===\n")

_KVC = _REPO / "kv-core" / "kv_core"
_mods = sorted(p.stem for p in _KVC.glob("*.py") if p.stem != "__init__")
_blob = "\n".join(p.read_text(encoding="utf-8") for p in _TESTS.glob("_*.py")
                  if p.name != _SELF)
_untouched = [m for m in _mods
              if not re.search(rf"\b(?:import|from)\s+(?:kv_core\.)?{re.escape(m)}\b", _blob)
              and f"kv_core import" not in _blob.split(m)[0][-200:]]
# 위 근사는 `from kv_core import a, b` 형태를 놓치므로 한 번 더 걸러 낸다.
_untouched = [m for m in _untouched
              if not re.search(rf"from kv_core import[^\n]*\b{re.escape(m)}\b", _blob)
              and not re.search(rf"kv_core\.{re.escape(m)}\b", _blob)]

# [2026-08-16] 4 → 0. 유일하게 안 닿던 `secrets` 를 `_synthetic_portability` P5 가 잡았다.
# 배치 C 로 도구 10본이 이 모듈에 의존하게 됐으니 무검증으로 둘 수 없었다.
_MOD_DEBT_MAX = 0
check("X5 어떤 테스트도 안 건드리는 kv_core 모듈이 상한 이하", len(_untouched) <= _MOD_DEBT_MAX,
      f"{len(_untouched)}본 / 상한 {_MOD_DEBT_MAX} — {_untouched}")
check("X5 모듈을 실제로 훑었다(0이면 위 검사가 공허하다)", len(_mods) >= 15, f"{len(_mods)}본")
print(f"     ↳ kv_core {len(_mods)}본 중 테스트가 안 건드리는 것 {len(_untouched)}본: "
      f"{_untouched or '없음'}")

# ══════════════════════════════════════════════════════════════════════
print("\n=== X6: 외부 제약 대장이 줄지 않았다 ===\n")

# `제약        …` 로 시작하는 블록의 수. 계약 항목이 조용히 삭제되는 것을 막는다.
_ext = (_CONTRACTS / "30-external-constraints.md").read_text(encoding="utf-8")
_n_ext = len(re.findall(r"^제약\s{2,}", _ext, re.M))
_EXT_MIN = 56          # 2026-08-16 실측. 줄면 계약이 조용히 삭제된 것이다.
check("X6 외부 제약 항목이 줄지 않았다", _n_ext >= _EXT_MIN, f"{_n_ext}개 / 최소 {_EXT_MIN}")

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 계약 대응 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print(f"✅ 계약 대응 통과 — 불변식 {len(_covered)}/{len(_INV)} 검사 · "
      f"미검증 {len(_uncovered)}(상한 {_UNCOVERED_MAX}) · 외부 제약 {_n_ext}항목")
