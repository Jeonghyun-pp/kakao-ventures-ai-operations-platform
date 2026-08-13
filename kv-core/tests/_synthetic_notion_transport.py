"""노션 전송 규율 — **정본이 하나이고, 실제로 쓰인다.**

## 왜 이 파일이 생겼나

2026-08-13 전수 감사 리뷰 B(F1). 브리핑의 전제가 틀렸던 건이라 기록해 둔다.

    브리핑(내가 쓴 것):  kv_core/notion.py 는 _RATE_SLEEP 0 · _MAX_RETRY 0 = 규율 없음
    실측:                RATE_SLEEP 0.34 · MAX_RETRY 4 · NET_BACKOFF 15.0

`kv_core` 는 상수에 언더스코어를 안 쓴다. `_RATE_SLEEP` 으로 grep 한 인공물이었다.
리뷰가 로컬 서버로 두 구현을 나란히 때려 **429/500/Retry-After/연결거부 전 시나리오에서
동작이 완전히 동일**함을 보였다.

**진짜 문제는 정본이 아무에게도 안 쓰인다는 것이었다** — `kv_core.notion` 임포터 0건.
그래서 노션을 부르는 13개 모듈이 각자 클라이언트를 만들었고 **11본에 백오프가 0줄**이었다.
그중엔 매일 도는 `bplan_score_job._apply` 의 쓰기 루프가 있었다 — 2026-07-11 에 415행을
무지연 POST 해서 한도를 때린 것과 **같은 코드 모양**이고, 그 사고 **이후에** 쓰였다.

## 검사하는 것

    T1  살아있는 실행 경로가 전송 정본을 지난다 (직접 requests 0건)
    T2  정본이 실제로 임포트된다 (임포터 0 이면 정본이 아니다)
    T3  망 계열 예외를 재시도하되 프로그래밍 오류는 즉시 올린다
    T4  아직 안 옮긴 모듈이 **목록으로 보인다** (부채를 조용히 두지 않는다)

T4 가 이 파일의 요점 중 하나다. 도구 9본은 사람이 손으로 돌리는 것이라 이번 배치에서
안 옮겼는데, **그 사실을 목록으로 남기지 않으면 "다 고쳤다"로 기억된다.** 목록이 줄면
숫자를 내리고, 늘면 실패한다(새 파일이 옛 방식으로 태어나는 것을 막는다).

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_notion_transport.py
AST 와 소스만 읽는다 — 네트워크 0.
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import ast  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

import requests  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

_REPO = Path(__file__).resolve().parents[2]
_SELF = Path(__file__).resolve()
sys.path.insert(0, str(_REPO / "kv-core"))

from kv_core import notion  # noqa: E402

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


def _direct_calls(p: Path) -> int:
    """이 파일이 `requests.<verb>(...)` 를 직접 부르는 횟수."""
    try:
        t = ast.parse(p.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return 0
    return sum(1 for n in ast.walk(t)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr in ("get", "post", "patch", "request", "delete")
               and isinstance(n.func.value, ast.Name) and n.func.value.id == "requests")


def _notion_modules() -> list[Path]:
    out = []
    for p in sorted(_REPO.rglob("*.py")):
        s = str(p)
        if "/.venv/" in s or "_archive" in s or "/tests/" in s or p.resolve() == _SELF:
            continue
        try:
            if "api.notion.com" in p.read_text(encoding="utf-8"):
                out.append(p)
        except (OSError, UnicodeDecodeError):
            continue
    return out


_MODS = _notion_modules()

# ══════════════════════════════════════════════════════════════════════
print("=== T1: 살아있는 실행 경로가 전송 정본을 지난다 ===\n")
# 스케줄러가 무인으로 돌리는 것 + 그 경로가 부르는 것. 사람이 손으로 돌리는 도구는 T4.
_LIVE = [
    "kv-core/kv_core/notion.py",                                   # 정본 자신
    "skills/net-sourcing/project/core/notion.py",
    "skills/mail-harness/project/jobs/bplan_score_job.py",
]
for rel in _LIVE:
    p = _REPO / rel
    n = _direct_calls(p)
    allowed = 2 if rel.endswith("kv_core/notion.py") else 0     # 정본은 자기가 부른다
    check(f"T1 {Path(rel).name} 직접 호출 {allowed}건 이하", n <= allowed,
          f"{n}건 — kv_core.notion.write/read 로 보내라")

check("T1 실행 경로 파일을 실제로 찾았다", all((_REPO / r).exists() for r in _LIVE))

# ══════════════════════════════════════════════════════════════════════
print("\n=== T2: 정본이 실제로 임포트된다 ===\n")
# 🔴 감사 시점엔 이 숫자가 **0** 이었다. 정본이 있는데 아무도 안 쓰면 정본이 아니다 —
#    새 파일을 열었을 때 검색해도 용례가 안 나오니 각자 클라이언트를 만든다.
_importers = [p for p in _MODS
              if "kv_core import notion" in p.read_text(encoding="utf-8")
              or "kv_core.notion" in p.read_text(encoding="utf-8")]
check("T2 kv_core.notion 임포터가 있다", len(_importers) >= 2,
      f"{len(_importers)}본: {[p.name for p in _importers]} (감사 시점 0본)")

# ══════════════════════════════════════════════════════════════════════
print("\n=== T3: 망 계열만 재시도한다 ===\n")
# 🔴 [B-F6] `ChunkedEncodingError` 는 `ConnectionError` 의 서브클래스가 **아니다** —
#    헤더는 왔는데 본문 도중 끊긴 경우다. 노션 query 응답(수백 KB)에서 가장 흔하다.
#    2026-07-13 에 "ReadTimeout 한 번에 904행 착지가 죽었다"를 고쳤을 때 Timeout·
#    ConnectionError 둘만 넓혔고, **모양이 같고 클래스만 다른 사고가 남아 있었다.**
for _name in ("ChunkedEncodingError", "ContentDecodingError", "ConnectionError", "Timeout"):
    check(f"T3 {_name} 는 재시도한다",
          issubclass(getattr(requests.exceptions, _name), notion._NET_ERRORS))
for _name in ("MissingSchema", "InvalidURL"):
    # 프로그래밍 오류까지 재시도하면 105초를 기다렸다 같은 실패를 반복한다 — 고쳐야 할
    # 것을 숨긴다. `RequestException` 통째로 잡기를 하지 않은 이유다.
    check(f"T3 {_name} 는 즉시 올린다(프로그래밍 오류를 숨기지 않는다)",
          not issubclass(getattr(requests.exceptions, _name), notion._NET_ERRORS))

# ══════════════════════════════════════════════════════════════════════
print("\n=== T4: 아직 안 옮긴 모듈이 목록으로 보인다 ===\n")
# 이 배치는 **살아있는 경로만** 옮겼다. 사람이 손으로 돌리는 도구 9본은 남아 있고,
# 그 사실을 숫자로 고정한다 — 목록이 줄면 이 숫자를 내리고, **늘면 실패한다**
# (새 파일이 옛 방식으로 태어나는 것을 막는 게 이 단언의 목적이다).
_DEBT_MAX = 10
_debt = sorted((str(p.relative_to(_REPO)), _direct_calls(p)) for p in _MODS
               if _direct_calls(p) > 0 and str(p.relative_to(_REPO)) not in _LIVE)
check("T4 전송 정본을 안 쓰는 모듈이 늘지 않았다", len(_debt) <= _DEBT_MAX,
      f"{len(_debt)}본 / 상한 {_DEBT_MAX}")
print(f"     ↳ 남은 부채 {len(_debt)}본 (전부 사람이 손으로 돌리는 도구 · W12-disposition §4):")
for _rel, _n in _debt:
    print(f"       · {_n}회  {_rel}")

check("T4 노션 모듈을 실제로 훑었다(0이면 위 검사가 공허하다)", len(_MODS) >= 10,
      f"{len(_MODS)}본")

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 노션 전송 계약 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 노션 전송 계약 통과 — 정본이 하나이고 실행 경로가 그걸 쓴다")
