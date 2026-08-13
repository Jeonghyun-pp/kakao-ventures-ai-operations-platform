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
    O5  🔴 컷오버 전 HARD-GATE — 스케줄러가 apply·sync·drop 을 부르지 않는다
    O6  🔴 ADR 0003 — 은퇴 대상 API 경로의 잠금이 fail-closed 다

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

def _is_newly_added(v: ast.AST) -> bool:
    """`{"select": {"name": "Newly Added"}}` 리터럴인가 — **값까지 본다.**

    🔴 [2026-08-13 W12 배치 9-2] 이 검사는 원래 `To do` 를 **키로 쓰는 것 자체**를 막았다.
       그런데 재빌드한 sync 잡이 신규 행을 만들면서 걸렸다 — 접수 행의 **초기 상태**가
       `Newly Added` 이고, 그건 드랍 경로를 여는 게 아니라 그 반대다(시작점).

       위험한 것은 `To Drop` 이지 `To do` 자체가 아니다. 그래서 값까지 본다:
       **리터럴 `Newly Added` 만 통과**하고 나머지는 전부 막는다(변수로 넣는 것도 막힌다 —
       변수면 여기서 값을 알 수 없으므로 통과 못 한다. 그게 맞다).
       `드랍메일` 은 값과 무관하게 계속 전면 금지다.

       ⚠️ 검사를 좁힐 때 **왜 좁혔는지**를 안 적으면 다음 사람이 "옛날엔 더 엄격했는데"
          하고 되돌리거나, 반대로 더 넓혀버린다. 좁힌 근거가 곧 경계의 정의다.
    """
    if not isinstance(v, ast.Dict):
        return False
    for k2, v2 in zip(v.keys, v.values):
        if not (isinstance(k2, ast.Constant) and k2.value == "select"):
            continue
        if not isinstance(v2, ast.Dict):
            return False
        for k3, v3 in zip(v2.keys, v2.values):
            if (isinstance(k3, ast.Constant) and k3.value == "name"
                    and isinstance(v3, ast.Constant) and v3.value == "Newly Added"):
                return True
    return False


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
            for k, v in zip(n.keys, n.values):
                if not (isinstance(k, ast.Constant) and k.value in _NOTION_DANGER):
                    continue
                if k.value == "To do" and _is_newly_added(v):
                    continue                      # 아래 주석 참조 — 생성 시 초기 상태만 허용
                danger.append(f"{rel}:{k.lineno} — {k.value!r}")
check("O3 잡이 `To do`(Newly Added 외)·`드랍메일` 을 dict 키로 쓰지 않는다", not danger,
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

print("\n=== O5: 컷오버 전 HARD-GATE — 스케줄러가 부작용 명령을 부르지 않는다 ===\n")
# 🔴 `skills/mail-harness/SKILL.md` HARD-GATE #1:
#   "컷오버가 완료되기 전에는 apply·sync·drop 이 실행되는 어떤 형태(크론·수동 run)도
#    시작하지 않는다. **예외 없음.** 허용되는 것은 --dry-run 과 triage(읽기 전용) 뿐이다."
#
# 2026-08-12 에 이 게이트를 **내가 어겼다** — `triage/main.py run`(= triage + apply)을
# 크론에 올렸고, apply 는 `gmail.archive()` 로 실 메일함을 바꾼다.
# 한 번도 안 돈 이유는 배선이 그날 마지막 발화보다 3분 45초 늦어서였다 —
# **우연이 막았지 설계가 막은 게 아니다.** 그래서 검사로 옮긴다.
#
# 게이트가 해제되면(컷오버 완료) 이 검사의 허용 목록을 넓히고 그 커밋에 근거를 남긴다.
_GATED_SUBCOMMANDS = {"run", "apply", "sync", "drop"}
_GATED_SCRIPTS = ("triage/main.py", "bplan/main.py", "harness.py")

import ast as _a  # noqa: E402

_wsrc = (_REPO / "kv-core/kv_core/workers.py").read_text(encoding="utf-8")
violations = []
for n in _a.walk(_a.parse(_wsrc)):
    if not (isinstance(n, _a.Call) and isinstance(n.func, _a.Attribute)
            and n.func.attr == "run" and n.args):
        continue
    arg = n.args[0]
    if not isinstance(arg, _a.List):
        continue
    parts = [x.value for x in arg.elts if isinstance(x, _a.Constant)]
    script = next((p for p in parts if any(g in str(p) for g in _GATED_SCRIPTS)), None)
    if script and any(str(p) in _GATED_SUBCOMMANDS for p in parts):
        bad = [p for p in parts if str(p) in _GATED_SUBCOMMANDS]
        violations.append(f"workers.py:{n.lineno} — {script} {bad}")

check("O5 스케줄러가 컷오버 게이트 대상 명령을 부르지 않는다", not violations,
      "\n       ".join(violations) or "triage(읽기전용)·--dry-run 만 허용")

# 게이트 문서가 살아 있는지도 본다 — 문서가 사라지면 이 검사의 근거가 사라진다
_gate_doc = (_REPO / "skills/mail-harness/SKILL.md").read_text(encoding="utf-8")
check("O5 HARD-GATE 선언이 문서에 살아 있다",
      "HARD-GATE" in _gate_doc and "컷오버" in _gate_doc,
      "게이트를 해제할 땐 이 테스트의 허용 목록도 함께 넓혀야 한다")

print("\n=== O7: 게이트가 리포 밖 실행 주체(launchd)까지 본다 ===\n")
# 🔴 [W12 리뷰 C-F-B18] O5 는 `workers.py` **소스 하나만** 본다. 그런데 sync/drop 을 실제로
#    실행할 주체는 `com.kv.mail-harness.plist` 다 — 그 진입점은 자연어 프롬프트
#    (`claude -p "…phase1 → phase2…"`) + `--allowedTools "Bash …"` 라서 **AST 로 잡을 대상이 없다.**
#    harness phase1 = bplan sync · phase2 = bplan drop.
#
#    지금 안 터지는 이유는 그 plist 가 미설치라서다 — 설계가 아니라 우연이다.
#    이 파일이 O5 자리에 스스로 적어둔 문장과 같은 상태였다:
#    *"2026-08-12 에 이 게이트를 내가 어겼다 … 우연이 막았지 설계가 막은 게 아니다."*
_PLIST_GATED = ("phase2", "bplan drop", "bplan sync", "/mail-harness")

# ① 리포 안의 템플릿 — CI 에서도 항상 검사된다.
#    템플릿이 게이트 대상 명령을 담는 것 자체는 막지 않는다(컷오버 후엔 그게 정상이다).
#    막는 것은 **경고 없이 담는 것**이다. 로드 금지 경고가 사라지면 사람이 그냥 설치한다.
_tmpl = sorted(_REPO.glob("skills/**/com.kv.*.plist.template"))
_unguarded = [str(p.relative_to(_REPO)) for p in _tmpl
              if any(g in (b := p.read_text(encoding="utf-8")) for g in _PLIST_GATED)
              and "HARD-GATE" not in b]
check("O7 게이트 대상 명령을 담은 plist 템플릿에 로드 금지 경고가 있다", not _unguarded,
      "\n       ".join(_unguarded) or f"템플릿 {len(_tmpl)}본 확인")
check("O7 plist 템플릿을 실제로 찾았다(0본이면 위 검사가 공허하다)", len(_tmpl) >= 1,
      f"{len(_tmpl)}본")

# ② 이 기계에 **실제로 설치된** plist — CI 러너에는 없으므로 있을 때만 본다.
#    ⚠️ 없다고 조용히 통과시키지 않는다. "확인 못 함"과 "확인했고 깨끗함"은 다른 사실이다
#       (불변규칙 05 · R5 의 진공/결손 구분을 이 검사에도 적용한다).
_agents = Path.home() / "Library/LaunchAgents"
if _agents.is_dir():
    _installed = sorted(_agents.glob("com.kv.*.plist"))
    _live = []
    for p in _installed:
        try:
            body = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if hit := [g for g in _PLIST_GATED if g in body]:
            _live.append(f"{p.name} — {hit}")
    check("O7 설치된 launchd 잡이 컷오버 게이트 대상을 부르지 않는다", not _live,
          "\n       ".join(_live)
          or f"설치 {len(_installed)}본 전부 무해: {[p.stem for p in _installed]}")
else:
    print("  ⏭️  O7 설치분 검사 건너뜀 — ~/Library/LaunchAgents 없음(CI 러너로 보인다). "
          "리포 템플릿 검사는 위에서 이미 돌았다.")

print("\n=== O6: 은퇴 대상 API 경로의 잠금이 fail-closed 인가 ===\n")
# ADR 0003 은 "AI = claude -p 구독 단일화 · API 키 제거"를 결정했다. 그런데 `core/llm.py` 두 벌이
# 살아 있고 실제로 import 된다(`net-sourcing/core/judge.py` · `mail-harness/triage/main.py`).
# 진짜 은퇴 전까지 이 경로를 막는 것은 `enabled` 플래그 하나뿐이다.
#
# 🔴 그 하나가 **fail-open 이면 잠금이 아니다.** mail-harness 쪽이 `.get("enabled", True)` 였다 —
#    `llm:` 블록이 사라지거나 키 이름이 바뀌면 상한 없는 API 패스가 열린다.
#    이 경로엔 `gate.check_calls` 도 `resources.yaml` 의 anthropic 축도 없어 **계측조차 0**이다.
_llm_files = sorted(_REPO.glob("skills/*/project/**/llm.py"))
open_defaults = []
for p in _llm_files:
    if "_archive" in str(p):
        continue
    for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
        # cfg...get("enabled", <기본값>) 의 기본값이 True 면 fail-open
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get" and len(n.args) == 2
                and isinstance(n.args[0], ast.Constant) and n.args[0].value == "enabled"
                and isinstance(n.args[1], ast.Constant) and n.args[1].value is True):
            open_defaults.append(f"{p.relative_to(_REPO)}:{n.lineno}")
check("O6 LLM 게이트 기본값이 fail-closed 다", not open_defaults,
      "\n       ".join(open_defaults) or f"llm.py {len(_llm_files)}개 확인")

# 호출부도 본다 — judge.py 처럼 호출 직전에 다시 검사하는 쪽이 있다
_callers = []
for p in _REPO.glob("skills/*/project/**/*.py"):
    if "_archive" in str(p) or p.name == "llm.py":
        continue
    src = p.read_text(encoding="utf-8", errors="ignore")
    if "llm.available()" in src or "from core import llm" in src or ", llm," in src:
        _callers.append(str(p.relative_to(_REPO)))
check("O6 은퇴 대상 경로의 호출부를 파악하고 있다(진짜 은퇴 전까지 추적)",
      len(_callers) >= 1, str(_callers))

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 외부 발송 경계 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 외부로 나가는 경로 없음 — 리포 전체 확인")
