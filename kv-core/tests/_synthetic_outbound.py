"""외부로 나가는 것이 없다 — **리포 전체를 한 번에 잠근다.**

## 왜 이 파일이 생겼나

"발송 0" 은 이 프로젝트의 가장 큰 약속인데, 그동안 그 검증이 **테스트마다 흩어져** 있었다.
`_synthetic_promotion` 은 promotion.py 다섯 함수만 보고, meeting-mail 은 자기 파일만 보고,
mail-harness 는 아무도 안 봤다. 그래서 2026-08-12 에 mail-harness 를 스케줄러에 붙일 때
`harness.py` 의 `digest_mod.send()` 두 개를 발견하고 **하나씩 손으로 확인**해야 했다
(슬랙 다이제스트라 무해했지만, 그걸 알려면 사람이 읽어야 했다).

스킬이 하나 늘 때마다 사람이 다시 읽어야 하면 그건 보장이 아니라 습관이다.
이 파일은 **리포 전체**를 훑어 새 스킬·새 파일이 들어와도 자동으로 걸리게 한다.

    O1  메일 발송 호출이 없다 (Gmail send · SMTP)
    O2  아는 무해 예외만 허용목록에 있고, 목록은 좁다
    O3  자동화 잡이 노션 위험 칸을 쓰지 않는다
    O4  자율성 설정이 발송을 끈 상태다

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_outbound.py
파일을 읽기만 한다 — 네트워크·프로세스 0.
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import ast  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "kv-core"))

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


# 🔴 **이 검사는 자기를 보면 안 된다.** 오늘만 세 번 걸렸다 —
#    ① bplan 잡의 `"드랍메일" not in src` 가 자기 독스트링에
#    ② 그 다음 버전이 검사 코드 자신의 리터럴 목록에
#    ③ 이 파일의 `smtplib`·`messages().send` 문자열에
#    교훈: **소스를 문자열로 훑는 검사는 반드시 자기를 찾는다.** 그래서 두 가지를 한다 —
#    자기 파일을 제외하고, 문자열 대신 **AST** 로 본다(주석·독스트링은 AST 에 안 남는다).
_SELF = Path(__file__).resolve()


def _py_files():
    for root in ("kv-core", "skills", "config"):
        for p in (_REPO / root).rglob("*.py"):
            if "/.venv/" in str(p) or "/_archive/" in str(p) or p.resolve() == _SELF:
                continue
            yield p


# ── 아는 무해 예외. **좁게 유지한다** — 넓히려면 이유를 여기 적어야 한다. ──
# `digest.send` 는 슬랙 웹훅이다(메일이 아니다). 확인: skills/mail-harness/project/core/digest.py:87
#   """웹훅 발송 + 파일 백업. 반환: 'slack' | 'file-only'."""
_ALLOWED_SEND = {
    ("skills/mail-harness/project/harness.py", "digest_mod"),   # 슬랙 다이제스트
}

# ── 노션에서 **자동화 잡이 쓰면 하류가 움직이는** 칸 ──
# `To do` 를 쓰면 드랍 메일 초안 경로가 열린다(bplan/main.py:query_to_drop_pages).
_NOTION_DANGER = ("To do", "드랍메일")
_JOB_GLOBS = ("skills/*/project/jobs/*.py",)


# ══════════════════════════════════════════════════════════════════════
print("=== O1/O2: 메일 발송 호출이 없다 ===\n")
sends, allowed_hits = [], []
for p in _py_files():
    rel = str(p.relative_to(_REPO))
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        continue
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
            continue
        if n.func.attr != "send":
            continue
        owner = getattr(n.func.value, "id", None) or getattr(
            getattr(n.func.value, "func", None), "attr", "") or "?"
        if (rel, owner) in _ALLOWED_SEND:
            allowed_hits.append(f"{rel}:{n.lineno}({owner})")
        else:
            sends.append(f"{rel}:{n.lineno} — {owner}.send()")

check("O1 허용목록 밖의 .send() 호출이 없다", not sends, "\n       ".join(sends))
check("O2 허용 예외는 실제로 존재한다(죽은 예외를 쌓아두지 않는다)",
      len(allowed_hits) >= 1, str(allowed_hits))
check("O2 허용목록이 넓어지지 않았다", len(_ALLOWED_SEND) <= 2, str(sorted(_ALLOWED_SEND)))

# SMTP 직접 발송 경로 — **import 문**으로 본다(문자열이면 이 설명문에도 걸린다)
smtp = []
for p in _py_files():
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        continue
    for n in ast.walk(tree):
        mods = ([a.name for a in n.names] if isinstance(n, ast.Import)
                else [n.module or ""] if isinstance(n, ast.ImportFrom) else [])
        if any(str(m).split(".")[0] in ("smtplib", "aiosmtplib") for m in mods):
            smtp.append(f"{p.relative_to(_REPO)}:{n.lineno}")
check("O1 smtplib 를 import 하는 파일이 없다", not smtp, str(smtp))

# Gmail API 의 `…messages().send(…)` — 체이닝을 AST 로 정확히 짚는다.
gsend = []
for p in _py_files():
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        continue
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "send"
                and isinstance(n.func.value, ast.Call)
                and isinstance(n.func.value.func, ast.Attribute)
                and n.func.value.func.attr == "messages"):
            gsend.append(f"{p.relative_to(_REPO)}:{n.lineno}")
check("O1 Gmail messages().send 호출이 없다", not gsend, str(gsend))

print("\n=== O3: 자동화 잡이 노션 위험 칸을 쓰지 않는다 ===\n")
# 잡 파일에서 `{"To do": ...}` 형태로 노션 속성을 구성하는지 본다.
# 문자열 등장이 아니라 **dict 키로 쓰였는지**를 본다 — 주석·독스트링에 언급하는 건 정상이다
# (그렇게 안 하면 이 검사가 자기 설명문에 걸린다. 실제로 그 실수를 한 번 했다).
danger = []
for glob in _JOB_GLOBS:
    for p in _REPO.glob(glob):
        rel = str(p.relative_to(_REPO))
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        # 자기검사(`_synthetic`)는 **가드가 막는지 시험하려고** 일부러 위험 칸을 넣는다.
        # 그 함수 안은 제외한다 — 아니면 "가드를 시험하는 것"이 "가드 위반"으로 잡힌다.
        skip = set()
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name.startswith("_synthetic"):
                skip |= {id(x) for x in ast.walk(fn)}
        for n in ast.walk(tree):
            if not isinstance(n, ast.Dict) or id(n) in skip:
                continue
            for k in n.keys:
                if isinstance(k, ast.Constant) and k.value in _NOTION_DANGER:
                    danger.append(f"{rel}:{k.lineno} — {k.value!r}")
check("O3 잡이 `To do`·`드랍메일` 을 dict 키로 쓰지 않는다", not danger,
      "\n       ".join(danger) or "드랍 메일 경로는 사람이 연다")

print("\n=== O4: 자율성 설정이 발송을 끈 상태다 ===\n")
import yaml  # noqa: E402

mh = yaml.safe_load((_REPO / "skills/mail-harness/project/config.yaml").read_text(encoding="utf-8"))
au = mh.get("autonomy") or {}
check("O4 send_enabled 가 꺼져 있다", au.get("send_enabled") is False, str(au.get("send_enabled")))
check("O4 자동 발송 카테고리가 비어 있다", not au.get("auto_send_categories"),
      str(au.get("auto_send_categories")))
check("O4 never_auto_send 목록이 유지된다(승격돼도 사람을 거치는 것들)",
      set(au.get("never_auto_send") or []) >=
      {"PARTNERSHIP_DECLINE", "TRUSTED_REF", "RECRUIT", "IMPERSONATION"},
      str(au.get("never_auto_send")))
check("O4 발송 서킷브레이커가 살아 있다",
      isinstance(au.get("daily_send_cap"), int) and au["daily_send_cap"] > 0,
      f"daily_send_cap={au.get('daily_send_cap')}")

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 외부 발송 경계 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 외부로 나가는 경로 없음 — 리포 전체 확인")
