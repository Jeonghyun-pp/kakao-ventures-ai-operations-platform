"""Apify 관문 — **가드가 호출부가 아니라 자원 앞에 있다.**

## 왜 이 파일이 생겼나

2026-08-13 전수 감사 리뷰 A(F3·F4·F5). 세 결함이 같은 함수에서 만난다.

**F3 — 관문이 호출부 옵트인이었다.** `run_actor` 호출부는 **23곳 / 13파일**인데 월 게이트를
지나는 건 `pipeline.collect` 하나뿐이었다. 나머지는 도구이고, 그중 둘은 스킬 문서가
**주 1회 정식 실행**이라 적은 커맨드다(`recheck`·`recheck-linkedin`, 회당 $1.6~$5.2).
`land` 안에서 무조건 도는 `tools/backfill_bio.py` 도 게이트 밖이었다 — 설계상 $0 스테이지인데.
ADR 0002 결정 ②("공유 자원은 결정 지점을 하나로")를 claude 축에만 이행한 상태였다.

**F4 — 천장의 진실이 두 곳이었다.** `resources.yaml $29` vs `config.yaml $45`.
`pipeline.py` 가 후자를 **폴백**으로 읽어서, gate 조회가 한 번 실패하면 유효 천장이 조용히
$45 가 됐다. W2 에서 닫았다고 기록한 "$29~$45 = 가드가 있다고 믿지만 없는 구간"의 재개통이다.

**F5 — 계상 단위가 과금 단위와 달랐다.** `_SPENT += _estimate(...)` 가 `r.json()` **뒤**에
있었다. Apify 는 **런이 시작되면 과금한다** — 타임아웃·429·5xx 로 응답을 못 받아도 돈은 나갔다.
실측(대역): 10회 연속 ReadTimeout → 장부 `$0.00`, `budget_tripped()` False, 화면엔
"누적 추정 $0.00". 실제로는 최대 $4.00. `tools/recheck.py:197` 이 배치 실패를
`except: continue` 로 넘기므로 조용히 반복된다 — **"돈만 나가고 결과가 0인" 경우를 회당
차단선이 원리적으로 못 본다.** 2026-07-10 사고(6런 $5.94, 결과는 403)와 같은 형태다.

## 검사하는 것 — 두 어댑터 모두에 대해

    A1  타임아웃이어도 start 비용이 장부에 남는다      (F5)
    A2  429 여도 남는다                               (F5)
    A3  정상 응답은 start + 아이템, 중복 계상 없음     (F5)
    A4  월 천장을 넘으면 **POST 자체를 안 보낸다**     (F3)
    A5  `--force-budget` 는 통과한다                  (사람 우회로가 chokepoint 에도 있어야 한다)
    A6  `run_actor` 를 부르는 모든 곳이 관문을 지난다  (F3 — AST)

A5 가 없으면 강행을 승인한 사람이 **돈을 쓴 뒤 런 중간에** 막힌다. 가드를 자원 앞으로
내릴 때 사람 우회로도 같이 내려와야 한다.

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_apify_gate.py
`requests.post` 를 대역으로 갈아끼운다 — **Apify 실호출 0 · 과금 0.**
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import ast  # noqa: E402
import importlib.util  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402
from pathlib import Path  # noqa: E402

import requests as _rq  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

_REPO = Path(__file__).resolve().parents[2]
_SELF = Path(__file__).resolve()

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


# 두 어댑터는 사실상 복제본이다(341줄 중 221줄 공통). 같은 결함을 같이 가졌으므로 같이 잰다.
# ⚠️ 이 중복 자체가 계열 ③("진실이 두 곳")이고 kv_core 승격 후보다 — W12-disposition §4 참조.
ADAPTERS = [
    ("net-sourcing", "skills/net-sourcing/project/adapters/apify.py"),
    ("ai-consumer-pulse", "skills/ai-consumer-pulse/project/adapters/apify_client.py"),
]

OPTS = {"slug": {"start_usd": 0.04, "memory_mb": 4096, "price_per_item": 0.005}}
START = 0.04 * 4          # $0.16 — 4GB × start 단가
ITEMS = 10 * 0.005        # $0.05


def _load(rel: str):
    """파일 경로로 직접 로드한다.

    ⚠️ `sys.path` + `import adapters.apify_client` 로 짰다가 실패했다 — 두 스킬 모두
       `adapters` 라는 같은 패키지 이름을 쓰므로, 첫 번째로 넣은 경로에 고정되어
       두 번째 어댑터를 `ModuleNotFoundError` 로 못 찾는다. 둘 다 형제 모듈 의존이
       없으므로(둘 다 표준 라이브러리 + requests 뿐) 파일 로드가 안전하고 정확하다.
    """
    p = _REPO / rel
    spec = importlib.util.spec_from_file_location(f"_probe_{p.stem}", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._archive = lambda *a, **k: None
    m._OPTS = dict(OPTS)
    os.environ["APIFY_TOKEN"] = "fake-for-test"
    return m


def _stub(m, behavior: str, posts: list):
    def post(url, params=None, json=None, timeout=None):
        posts.append(params)
        if behavior == "timeout":
            raise _rq.exceptions.ReadTimeout("느림")
        if behavior == "429":
            return types.SimpleNamespace(status_code=429, text="", json=lambda: [])
        return types.SimpleNamespace(status_code=200, text="",
                                     json=lambda: [{"i": i} for i in range(10)])
    m.requests = types.SimpleNamespace(post=post, get=None, exceptions=_rq.exceptions)


def _reset(m, used, cap, force=False):
    m._SPENT = 0.0
    m._PROBED_AT_SPENT = 0.0
    m._LIVE_USED, m._LIVE_CAP = used, cap
    m._GATE_OFF, m._FORCE = False, force


# ══════════════════════════════════════════════════════════════════════
for label, _rel in ADAPTERS:
    print(f"=== {label} · {_rel} ===\n")
    m = _load(_rel)

    posts: list = []
    _reset(m, 0.0, 100.0)
    _stub(m, "timeout", posts)
    n_exc = 0
    for _ in range(10):
        try:
            m.run_actor("slug", {})
        except Exception:            # noqa: BLE001 — 타임아웃을 세는 게 목적
            n_exc += 1
    check(f"A1 [{label}] 타임아웃 10회여도 start 비용이 장부에 남는다",
          n_exc == 10 and abs(m._SPENT - START * 10) < 1e-6,
          f"예외 {n_exc} · POST {len(posts)} · 장부 ${m._SPENT:.2f} (기대 ${START * 10:.2f}"
          f" · 수정 전엔 $0.00)")

    posts.clear()
    _reset(m, 0.0, 100.0)
    _stub(m, "429", posts)
    try:
        m.run_actor("slug", {})
    except Exception:                # noqa: BLE001
        pass
    check(f"A2 [{label}] 429 여도 남는다(런은 시작됐다)",
          abs(m._SPENT - START) < 1e-6, f"장부 ${m._SPENT:.2f} (기대 ${START:.2f})")

    posts.clear()
    _reset(m, 0.0, 100.0)
    _stub(m, "ok", posts)
    got = m.run_actor("slug", {})
    check(f"A3 [{label}] 정상 응답은 start + 아이템(중복 계상 없음)",
          len(got) == 10 and abs(m._SPENT - (START + ITEMS)) < 1e-6,
          f"아이템 {len(got)} · 장부 ${m._SPENT:.3f} (기대 ${START + ITEMS:.3f})")

    posts.clear()
    _reset(m, 28.9, 29.0)
    _stub(m, "ok", posts)
    blocked = False
    try:
        m.run_actor("slug", {})
    except RuntimeError:
        blocked = True
    check(f"A4 [{label}] 월 천장 초과면 POST 자체를 안 보낸다",
          blocked and len(posts) == 0 and m._SPENT == 0.0,
          f"차단={blocked} · POST {len(posts)}회 · 장부 ${m._SPENT:.2f} — 막는 게 공짜여야 한다")

    posts.clear()
    _reset(m, 28.9, 29.0, force=True)
    _stub(m, "ok", posts)
    forced = True
    try:
        m.run_actor("slug", {})
    except RuntimeError:
        forced = False
    check(f"A5 [{label}] --force-budget 는 통과한다(사람 우회로도 자원 앞에 있다)",
          forced and len(posts) == 1,
          f"통과={forced} · POST {len(posts)}회 — 없으면 강행 승인자가 런 중간에 막힌다")
    print()

# ══════════════════════════════════════════════════════════════════════
print("=== A6: run_actor 를 부르는 모든 곳이 관문을 지난다 ===\n")
# 호출부를 세는 게 아니라 **관문이 자원 앞에 있는지**를 본다.
# 호출부는 늘어나도 되고 그게 요점이다 — 늘어나도 게이트를 못 빠져나가야 한다.
_defs = 0
_ungated = []
for f in sorted(_REPO.glob("skills/*/project/adapters/apify*.py")):
    tree = ast.parse(f.read_text(encoding="utf-8"))
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == "run_actor":
            _defs += 1
            names = {c.func.id for c in ast.walk(n)
                     if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
            if "_monthly_block" not in names:
                _ungated.append(str(f.relative_to(_REPO)))
check("A6 모든 run_actor 정의가 월 관문을 지난다", not _ungated,
      "\n       ".join(_ungated) or f"{_defs}개 정의 전부 통과")
check("A6 run_actor 정의를 실제로 찾았다(0이면 위 검사가 공허하다)", _defs >= 2, f"{_defs}개")

# 호출부 수는 사실로만 보고한다 — 이 숫자가 커지는 건 결함이 아니다(관문이 자원 앞이므로).
_calls = 0
for f in sorted(_REPO.glob("skills/*/project/**/*.py")):
    if "_archive" in str(f) or f.resolve() == _SELF:
        continue
    try:
        t = ast.parse(f.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        continue
    _calls += sum(1 for c in ast.walk(t)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                  and c.func.attr == "run_actor")
print(f"     ↳ run_actor 호출부 {_calls}곳 — 전부 위 관문을 지난다(2026-08-13 이전엔 1곳만)")

# ══════════════════════════════════════════════════════════════════════
print("\n=== A7: 월 천장의 출처가 하나다 ===\n")
# 🔴 A1~A6 을 다 붙이고 변이 시험을 돌렸더니 **F4(천장 이중화)만 아무도 못 잡았다.**
#    `cap = _v.ceiling_usd or float(cfg["apify"]["monthly_budget_usd"])` 를 되돌려도 전부 초록.
#    그래서 이 검사를 추가한다 — 잡히지 않는 변이는 고쳐진 것이 아니다(정본 R4).
#
#    두 축을 본다: ⓐ 코드가 gate 의 천장에 폴백을 붙이지 않는다 ⓑ 스킬 config 에 그 키가 없다.
#    ⓑ 만 보면 코드의 폴백이 남고, ⓐ 만 보면 다음 사람이 config 에 키를 다시 넣는다.
_pipe = _REPO / "skills/net-sourcing/project/core/pipeline.py"
_tree = ast.parse(_pipe.read_text(encoding="utf-8"))
_fallbacks = []
for n in ast.walk(_tree):
    if not isinstance(n, ast.BoolOp) or not isinstance(n.op, ast.Or):
        continue
    # `<...>.ceiling_usd or <무엇이든>` = 천장에 두 번째 출처를 붙인 것
    if any(isinstance(v, ast.Attribute) and v.attr == "ceiling_usd" for v in n.values[:-1]):
        _fallbacks.append(f"pipeline.py:{n.lineno} — ceiling_usd 에 폴백이 붙어 있다")
check("A7 코드가 gate 의 천장에 두 번째 출처를 붙이지 않는다", not _fallbacks,
      "\n       ".join(_fallbacks) or "폴백 0건")

import yaml  # noqa: E402

_skill_cfg = yaml.safe_load(
    (_REPO / "skills/net-sourcing/project/config.yaml").read_text(encoding="utf-8")) or {}
_dup = "monthly_budget_usd" in (_skill_cfg.get("apify") or {})
check("A7 스킬 config 에 월 천장이 중복 선언돼 있지 않다", not _dup,
      "config/resources.yaml 의 apify.monthly_budget_usd 가 정본이다"
      if not _dup else "skills/net-sourcing/project/config.yaml 에 다시 들어왔다")

_res = yaml.safe_load((_REPO / "config/resources.yaml").read_text(encoding="utf-8")) or {}
_cap = ((_res.get("resources") or _res).get("apify") or {}).get("monthly_budget_usd")
check("A7 정본 쪽에는 천장이 실제로 선언돼 있다(양쪽 다 없으면 가드가 없다)",
      bool(_cap), f"resources.yaml apify.monthly_budget_usd = {_cap}")

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 Apify 관문 계약 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ Apify 관문 계약 통과 — 가드가 호출부가 아니라 자원 앞에 있다")
