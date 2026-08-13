"""진공 ≠ 결손 — **0 이 두 뜻인데 하나만 보던 축들.**

## 왜 이 파일이 생겼나

정본 R5 는 원칙을 세웠다 — *"'전부 비었다'는 개별이 아니라 집계 수준에서 잡는다. 절대값으로
잡으면 규모를 타고, 임계가 규모를 타면 그 자체가 결함이다."* 그런데 그 원칙은 **레코드 수
축에만** 구현됐다. 2026-08-13 감사가 찾은 것은 그 밖의 축들이다.

    V1  컷 사유의 분포 — 0건이 된 룰이 출력에서 사라졌다              (A-F2)
    V2  bplan-score 의 0건 export 가 아무에게도 안 알렸다             (C-F-B3)

## V1 이 실제로 무엇을 숨겼나

`_dump_cut` 이 `Counter(...).most_common()` 만 찍었다. 어떤 사유가 0건이 되면 **줄 자체가
사라진다** — "대기업컷 0건"과 "대기업 룰이 죽었다"가 출력상 같아진다.
`data/cut_*.json` 의 `_cut_reason` 접두사 실측:

    07-26  10 | 07-28   8 | 07-31   9 | 08-03   7 | 08-04  13 | 08-05  11
    08-06   0 | 08-07   0 | 08-10   0 | 08-11   0 | 08-12   0   ← 룰이 죽어 있던 8일
    08-13   1                                                   ← 수정 후 복귀

8일 내내 20본짜리 검증 스위트가 초록이었다. **그 축을 원리적으로 보지 않았기 때문이다.**

## 단위가 왜 "사유"가 아니라 "룰 계열"인가

스레드 홍보컷은 하위 사유가 5종인데 평범한 날에도 1~2종만 발화한다. 사유 단위로 경고하면
**매 실행 경고**가 되고 그건 경보 피로다(사람이 읽기를 멈추면 방어가 0 이 된다).
실제 실패 형태도 "계열 전체가 죽는다"(config 블록 이동·스위치 off)이지 하위 사유 하나가 아니다.

과거 18일 전수 대조에서 경고가 뜬 날은 6일이고 그중 5일이 정확히 그 회귀 구간이다.

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_vacuum.py
전부 합성 — 외부 부작용 0.
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import ast  # noqa: E402
import collections  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402
from pathlib import Path  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills/net-sourcing/project"))

from core import judge, pipeline  # noqa: E402

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


def _dump(cut: list[dict]) -> str:
    pipeline.CAND = Path(tempfile.mkdtemp())
    buf = io.StringIO()
    with redirect_stdout(buf):
        pipeline._dump_cut(cut)
    return buf.getvalue()


_APP = lambda r: {"source": "앱스토어", "_cut_reason": r}          # noqa: E731
_TH = lambda r: {"source": "스레드", "_cut_reason": r}             # noqa: E731

# ══════════════════════════════════════════════════════════════════════
print("=== V1: 0건이 된 컷 룰이 출력에서 사라지지 않는다 ===\n")

# ① 2026-08-06~12 의 실제 상태 — 출시일 컷만 돌고 denylist 는 0건
_out = _dump([_APP("출시 180일 초과(2020-01-01)")] * 100)
check("V1 죽은 룰이 이름과 함께 드러난다", "대기업 denylist" in _out,
      "이 상태가 8일간 유지됐고 아무도 못 봤다")

# ② 정상 — 두 룰이 다 발화하면 조용해야 한다(경보 피로 방지)
_out = _dump([_APP("출시 180일 초과(2020-01-01)")] * 100
             + [_APP("대기업/기관 개발사(NAVER Corp.)")])
check("V1 다 발화하면 조용하다", "발화하지 않은" not in _out, _out.strip().splitlines()[-1])

# ③ 하위 사유 5종 중 1종만 발화한 스레드 — **경고하지 않아야** 한다(단위는 계열)
_out = _dump([_APP("출시 180일 초과(2020)")] * 10 + [_APP("대기업/기관 개발사(X)")]
             + [_TH("홍보성: 스팸/수익인증")])
check("V1 계열 안에서 일부만 발화하면 조용하다(사유 단위면 매 실행 경고 = 경보 피로)",
      "홍보" not in _out.split("발화하지 않은")[-1] if "발화하지 않은" in _out else True,
      "스레드 홍보컷은 하위 5종 중 1~2종만 발화하는 게 정상")

# ④ 스레드 계열이 통째로 죽으면 잡아야 한다
_out = _dump([_APP("출시 180일 초과(2020)")] * 10 + [_APP("대기업/기관 개발사(X)")]
             + [_TH("출시 180일 초과(x)")])       # 스레드 행은 있는데 홍보컷 0
check("V1 계열이 통째로 0 이면 잡는다", "홍보 컷" in _out)

# ⑤ 안 돈 채널은 보지 않는다(오탐 방지)
_out = _dump([_APP("출시 180일 초과(2020)")] * 10 + [_APP("대기업/기관 개발사(X)")])
check("V1 이번에 안 돈 채널은 경고하지 않는다", "스레드" not in _out)

# ══════════════════════════════════════════════════════════════════════
print("\n=== V2: 선언한 컷 룰이 코드와 일치한다 ===\n")
# 🔴 선언 목록이 코드와 어긋나면 V1 이 통째로 무의미해진다 — 없는 사유를 기다리며
#    영원히 경고하거나(오탐), 새 사유가 죽어도 모른다(미탐).
#    그래서 `_hardcut_*` 의 **반환 리터럴을 AST 로 뽑아** 선언과 대조한다.
_src = (_REPO / "skills/net-sourcing/project/core/judge.py").read_text(encoding="utf-8")
_actual: set[str] = set()
for _fn in ast.walk(ast.parse(_src)):
    if not (isinstance(_fn, ast.FunctionDef) and _fn.name.startswith("_hardcut_")):
        continue
    for _n in ast.walk(_fn):
        if not isinstance(_n, ast.Return) or _n.value is None:
            continue
        v = _n.value
        if isinstance(v, ast.Constant) and isinstance(v.value, str) and v.value:
            _actual.add(v.value)
        elif isinstance(v, ast.JoinedStr):
            # f-string → 상수부만 이어붙여 접두사를 얻는다
            head = ""
            for p in v.values:
                if isinstance(p, ast.Constant):
                    head += p.value
                else:
                    break
            if head:
                _actual.add(head)

_declared = {r.split("{")[0] for rules in judge.CUT_RULES.values()
             for rs in rules.values() for r in rs}
_missing = sorted(a for a in _actual if not any(a.startswith(d) for d in _declared))
_extra = sorted(d for d in _declared if not any(a.startswith(d) for a in _actual))

check("V2 코드가 내는 사유가 전부 선언돼 있다", not _missing,
      f"선언 누락 {_missing} — 이 사유가 죽어도 아무도 모른다")
check("V2 선언에 없는 사유를 기다리지 않는다", not _extra,
      f"코드에 없는 선언 {_extra} — 영원히 '발화 안 함' 경고가 뜬다")
check("V2 사유를 실제로 추출했다(추출이 죽으면 위 둘이 공허하다)", len(_actual) >= 5,
      f"{len(_actual)}종: {sorted(_actual)[:3]}…")

# ══════════════════════════════════════════════════════════════════════
print("\n=== V3: 실데이터에서 경고가 신호인가 ===\n")
# 경고가 매일 뜨면 아무도 안 읽는다 — 과거 전수에 돌려 비율을 사실로 확인한다.
_files = sorted((_REPO / "skills/net-sourcing/project/data").glob("cut_*.json"))
_warn_days: list[str] = []
for _f in _files:
    _cut = json.loads(_f.read_text(encoding="utf-8"))
    _tally = collections.Counter(c.get("_cut_reason", "").split("(")[0] for c in _cut)
    _seen = {c.get("source") for c in _cut}
    for _src_name, _rules in judge.CUT_RULES.items():
        if _src_name not in _seen:
            continue
        for _rule, _rs in _rules.items():
            if not any(any(k.startswith(r.split("{")[0]) for k in _tally) for r in _rs):
                _warn_days.append(_f.stem[4:])
                break
        else:
            continue
        break

_regression = {"2026-08-06", "2026-08-07", "2026-08-10", "2026-08-11", "2026-08-12"}
_hit = len(set(_warn_days) & _regression)
check("V1 이 실제 회귀 구간을 전부 잡는다", _hit == len(_regression),
      f"회귀 5일 중 {_hit}일 적중 · 경고일 {len(_warn_days)}/{len(_files)}일")
check("V1 이 수정 이후에는 조용하다", "2026-08-13" not in _warn_days,
      "수정 후 첫 실행에서 대기업컷 1건 발화 확인")
check("V3 실데이터를 실제로 읽었다", len(_files) >= 10, f"{len(_files)}일치")

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 진공/결손 계약 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 진공/결손 계약 통과 — 0 이 두 뜻일 때 둘을 갈라 본다")
