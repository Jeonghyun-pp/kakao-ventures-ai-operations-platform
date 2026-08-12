"""설정 배선 — **코드가 읽는 자리에 값이 실제로 있는가**를 매번 확인한다.

## 왜 이 파일이 생겼나

2026-08-13 전수 감사에서 나온 것 중 유일하게 **8일간 라이브에서 조용히 틀리고 있던** 결함이
코드가 아니라 **YAML 들여쓰기**에서 나왔다.

    커밋 21fb15c (2026-08-05 16:12) 가 `meeting_mail:` 블록을 `judge:` 블록 **한가운데**
    삽입했다. 그 아래 84줄(`llm`·`threads`·`appstore` + 대기업 denylist 48토큰)이
    통째로 `meeting_mail` 의 소유가 됐다.

    코드는 `cfg["judge"]["appstore"]["bigcorp_denylist"]` 를 읽는다 → **빈 리스트**.
    예외도 경고도 없다. `.get(..., {})` 체인이 전부 기본값으로 흘렀을 뿐이다.

실측한 귀결 (`data/cut_*.json` 전수):

    08-03  대기업컷 11 |  08-06  대기업컷 5   ← 여기서 룰이 죽었다
    08-04           18 |  08-07           5
    08-05           16 |  08-10~12        5   (잔여 5는 다른 룰)

그 8일 동안 대기업·은행·대학·공공기관 앱이 극초기 소싱 DB 에 착지했다. 20본짜리 검증
스위트가 전부 초록이었다 — **그 축을 원리적으로 못 봤기 때문이다.** 기존 기계검증은 오히려
회귀를 타고 넘었다(`_synthetic_claude_gate` 가 새로 생긴 `meeting_mail` 키를 그대로 읽어 통과).

## 이 파일이 잡는 것

    C1  코드가 읽는 설정 경로가 **다른 부모 아래로 이사**하지 않았다   ← 위 사고의 형태
    C2  실측으로 값을 확인한 핵심 설정이 살아 있다 (denylist·잠금·컷 스위치)
    C3  경로 추출이 실제로 동작한다 (추출이 죽으면 C1 이 공허해진다)

C1 이 핵심이다. "키가 없다"만 보면 **선택적 설정이 많아 잡음이 되므로**, 없는 키의 이름이
*다른 top-level 블록 아래에 존재하는지* 를 함께 본다. 존재하면 그건 부재가 아니라 **이사**다 —
오타로는 그 상태가 안 만들어지고, 들여쓰기 사고로만 만들어진다.

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_config_wiring.py
YAML 과 파이썬 소스를 읽기만 한다 — 네트워크·발송 0.
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import ast  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import yaml  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

_REPO = Path(__file__).resolve().parents[2]
_SELF = Path(__file__).resolve()

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


# ══════════════════════════════════════════════════════════════════════
# 경로 추출 — 문자열 grep 이 아니라 AST 다.
#
# 🔴 문자열 검사는 이 리포에서 **세 번** 자기 자신을 잡았다(검사 코드에 적힌 리터럴을
#    위반으로 셌다). 그래서 AST 로 뽑고, 이 파일 자신은 스캔 대상에서 뺀다.
# ══════════════════════════════════════════════════════════════════════
def _chain(node: ast.AST) -> list[str] | None:
    """`cfg.get("a", {}).get("b", …)` / `cfg["a"]["b"]` → ["a", "b"].

    바깥에서 안쪽으로 벗겨 들어가며, 뿌리가 설정 이름(cfg/config/conf)일 때만 인정한다.
    뿌리가 다른 변수면 None — 설정이 아닌 dict 의 `.get` 을 잘못 세지 않기 위해서다.
    """
    keys: list[str] = []
    cur: ast.AST = node
    while True:
        if (isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute)
                and cur.func.attr == "get" and cur.args
                and isinstance(cur.args[0], ast.Constant)
                and isinstance(cur.args[0].value, str)):
            keys.append(cur.args[0].value)
            cur = cur.func.value
        elif (isinstance(cur, ast.Subscript) and isinstance(cur.slice, ast.Constant)
                and isinstance(cur.slice.value, str)):
            keys.append(cur.slice.value)
            cur = cur.value
        else:
            break
    if len(keys) < 2:
        return None                       # 1단은 선택적 설정이 흔해 잡음이 크다
    if not (isinstance(cur, ast.Name) and cur.id in ("cfg", "config", "conf")):
        return None
    return list(reversed(keys))


def _paths(root: Path) -> set[tuple[str, ...]]:
    out: set[tuple[str, ...]] = set()
    for p in root.rglob("*.py"):
        if "_archive" in str(p) or "/.venv/" in str(p) or p.resolve() == _SELF:
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.Call, ast.Subscript)):
                ch = _chain(n)
                if ch:
                    out.add(tuple(ch))
    return out


def _resolve(cfg: dict, path: tuple[str, ...]):
    cur = cfg
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None, False
        cur = cur[k]
    return cur, True


def _where(cfg: dict, leaf: str) -> list[str]:
    """`leaf` 이름이 실제로 어느 top-level 블록 아래에 있나 — '이사' 판정의 근거."""
    found = []
    for top, v in cfg.items():
        if isinstance(v, dict) and leaf in v:
            found.append(top)
    return found


SKILLS = sorted(p.parent for p in _REPO.glob("skills/*/project/config.yaml"))

# ══════════════════════════════════════════════════════════════════════
print("=== C1: 코드가 읽는 설정 경로가 다른 부모 아래로 이사하지 않았다 ===\n")
displaced: list[str] = []
absent: list[str] = []
total = 0
for proj in SKILLS:
    cfg = yaml.safe_load((proj / "config.yaml").read_text(encoding="utf-8")) or {}
    skill = proj.parent.name
    for path in sorted(_paths(proj)):
        total += 1
        _, ok = _resolve(cfg, path)
        if ok:
            continue
        # 없다. 부재인가 이사인가 — leaf 이름이 다른 부모 밑에 있으면 이사다.
        homes = [t for t in _where(cfg, path[-1]) if t != path[0]]
        if homes and path[0] in cfg:
            displaced.append(f"{skill}: 코드는 {'.'.join(path)} 를 읽는데 "
                             f"'{path[-1]}' 은 {homes} 아래 있다")
        else:
            absent.append(f"{skill}: {'.'.join(path)}")

check("C1 이사한 설정 블록이 없다", not displaced, "\n       ".join(displaced))
# 부재는 실패가 아니다 — 선택적 설정은 기본값으로 도는 게 정상이다. 다만 보이게 둔다.
print(f"     ↳ 경로 {total}개 검사 · 부재(정상: 기본값 동작) {len(absent)}건")
for a in absent[:12]:
    print(f"       · {a}")
if len(absent) > 12:
    print(f"       · … 외 {len(absent) - 12}건")

# ══════════════════════════════════════════════════════════════════════
print("\n=== C2: 실측으로 확인한 핵심 설정이 살아 있다 ===\n")
ns = yaml.safe_load(
    (_REPO / "skills/net-sourcing/project/config.yaml").read_text(encoding="utf-8"))

deny = ((ns.get("judge") or {}).get("appstore") or {}).get("bigcorp_denylist") or []
check("C2 대기업 denylist 가 judge 아래 살아 있다", len(deny) >= 40,
      f"{len(deny)}토큰 — 08-06~08-13 에 0이었다")

jl = (ns.get("judge") or {}).get("llm") or {}
check("C2 judge.llm 잠금이 judge 아래에 있고 꺼져 있다",
      "enabled" in jl and jl["enabled"] is False, f"enabled={jl.get('enabled', '키없음')}")

jt = (ns.get("judge") or {}).get("threads") or {}
check("C2 스레드 홍보컷 스위치가 judge 아래에 있다", "promo_cut" in jt,
      str(sorted(jt)))

# ⚠️ 이 단언이 사고의 **직접적 역상**이다 — meeting_mail 이 judge 의 것을 삼켰는지 본다.
mm = ns.get("meeting_mail") or {}
stolen = sorted(set(mm) & {"llm", "threads", "appstore"})
check("C2 meeting_mail 이 judge 의 블록을 삼키지 않았다", not stolen,
      f"삼킨 키 {stolen}" if stolen else f"자기 키만: {sorted(mm)}")

# ══════════════════════════════════════════════════════════════════════
print("\n=== C3: 경로 추출이 실제로 동작한다 ===\n")
# 추출기가 조용히 0을 뱉으면 C1 은 항상 초록이 된다 — 검증의 검증.
check("C3 설정 경로를 충분히 뽑았다(추출이 죽으면 C1 이 공허하다)", total >= 20,
      f"{total}개")
probe = _chain(ast.parse('cfg.get("judge", {}).get("appstore", {})').body[0].value)
check("C3 체인 파서가 알려진 형태를 판독한다", probe == ["judge", "appstore"], str(probe))

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 설정 배선 계약 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 설정 배선 계약 통과 — 코드가 읽는 자리에 값이 있다")
