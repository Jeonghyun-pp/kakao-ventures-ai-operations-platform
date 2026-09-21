"""의도 검사 — `docs/INTENT.md` 의 A 항목을 기계로 센다.

## 왜 이게 있나

옛 시스템의 진단은 "설계가 틀렸다"가 아니라 **"설계가 지켜지는지 세는 장치가 없었다"**였다.
그래서 선언은 있는데 발동하지 않는 가드가 반복해서 생겼다.

이 파일이 그 장치다. **못 지킨 건 지켰다고 적지 않는다.**

## 🔴 아직 못 세는 검사는 '통과'로 세지 않는다

지금 단계에 해당 없는 검사를 초록으로 칠하면, 그게 바로 "선언은 있는데 발동 안 하는 가드"다.
`미적용` 으로 따로 세고, 그 수를 화면에 남긴다.

    ~/kv/.venv/bin/python check_intent.py
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# 외부에 닿아도 되는 파일 = 통로. 여기 밖에서 망을 부르면 A1 위반.
TRANSPORTS = {
    "kv_transport/itunes.py",
    "kv_transport/notion.py",
    "kv_transport/slack.py",
    "kv_transport/claude.py",
    "kv_transport/apify.py",
    "kv_transport/gmail.py",
    "kv_transport/sheets.py",
}
# 외부 접촉 모듈
NET_MODULES = {"requests", "urllib", "urllib3", "http", "socket", "httpx", "aiohttp"}
SPAWN_MODULES = {"subprocess"}

# 🔴 프로세스를 띄워도 되는 파일. **명단은 작게 유지하고 한 줄씩 이유를 적는다.**
#    "tools/ 는 전부 예외" 같은 카테고리 면제를 두지 않는다 — 그러면 그 안에서 뭐가
#    생기는지 아무도 안 본다. 눈에 보이는 명단이 안 보이는 면제보다 낫다.
# 🔴 내용이 '코드'가 아니라 '시험 재료'인 파일. 줄 단위 검사에서만 뺀다.
#    변이 테스트는 위반 문자열을 픽스처로 들고 있어야 하므로, 그걸 위반으로 세면
#    검사가 자기 시험지를 채점하는 꼴이 된다. 이것도 카테고리 면제가 아니라 명단이다.
FIXTURE_FILES = {
    "tools/mutation_test.py": "방어를 되돌린 코드를 문자열로 들고 있다",
}

SPAWN_ALLOWED = {
    "kv_transport/claude.py": "claude -p 통로. 이게 claude 를 부르는 유일한 자리다",
    "tools/mutation_test.py": "검사 러너. check_intent 를 자식 프로세스로 돌려야 한다",
    "schedule.py": "launchd 통로. launchctl 을 부르는 유일한 자리다",
}

PASS, FAIL, SKIP = [], [], []


def check(cid: str, name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append((cid, name, detail))


def skip(cid: str, name: str, why: str) -> None:
    SKIP.append((cid, name, why))


def a0_parseable() -> None:
    """🔴 모든 파일이 파싱되나.

    검사기가 파싱 오류로 죽으면 아무 실패도 출력되지 않아 **다 통과한 것처럼 보인다.**
    (08-17 실측: 변이 테스트가 문법 오류를 넣자 check_intent 가 죽었고,
     그 결과 '방어를 없앴는데 검사가 통과했다'로 잘못 읽혔다.)
    검사기가 조용히 죽는 것이 검사가 없는 것보다 나쁘다 — 먼저 이걸 센다.
    """
    bad = []
    for p in py_files():
        try:
            ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError as e:
            bad.append(f"{rel(p)}:{e.lineno} {e.msg}")
    check("A0", "모든 파일이 파싱된다", not bad, f"{len(bad)}건: {bad[:3]}" if bad else "")
    return not bad


def py_files() -> list[Path]:
    return [p for p in ROOT.rglob("*.py")
            if ".venv" not in p.parts and p.name != "check_intent.py"]


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


# ---------------------------------------------------------------- A1
def a1_single_transport() -> None:
    bad = []
    for p in py_files():
        r = rel(p)
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except SyntaxError:
            continue   # A0 가 이미 실패로 셌다
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module.split(".")[0]]
            for m in mods:
                if m in NET_MODULES and r not in TRANSPORTS:
                    bad.append(f"{r}:{node.lineno} {m} (망)")
                elif m in SPAWN_MODULES and r not in SPAWN_ALLOWED:
                    bad.append(f"{r}:{node.lineno} {m} (프로세스)")
    check("A1", "통로 밖에서 외부에 닿는 코드 0건", not bad,
          f"{len(bad)}건: {bad[:5]}" if bad
          else f"망: 통로 {len(TRANSPORTS)}개만 · 프로세스: 명단 {len(SPAWN_ALLOWED)}개만")


# ---------------------------------------------------------------- A2
def a2_fetched_unwrap() -> None:
    """`._value` 직접 접근 0건 — 미상을 모른 척 지나가는 길을 막는다."""
    bad = []
    for p in py_files():
        if rel(p).endswith("kvlib/fetched.py") or rel(p) in FIXTURE_FILES:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"\._value\b", line):
                bad.append(f"{rel(p)}:{i}")
    check("A2", "Fetched 를 안 벗기고 쓰는 곳 0건", not bad, f"{len(bad)}건: {bad[:5]}" if bad else "")


# ---------------------------------------------------------------- A3
def a3_no_rc_success() -> None:
    """rc == 0 을 성공 근거로 쓰지 않는다. rc 0 + 빈 출력 = 인증 만료다."""
    bad = []
    for p in py_files():
        if rel(p) in FIXTURE_FILES:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"returncode\s*==\s*0", line) and "!=" not in line:
                bad.append(f"{rel(p)}:{i}")
    check("A3", "rc 로 성공을 판정하는 곳 0건", not bad, f"{len(bad)}건: {bad[:5]}" if bad else "")


# ---------------------------------------------------------------- A4
def a4_readback() -> None:
    """노션 쓰기 뒤에 되읽기가 붙어 있나."""
    src = (ROOT / "kv_transport/notion.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    writers = {"create", "update"}
    missing = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name in writers:
            calls = {n.func.id for n in ast.walk(fn)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            if "_verify" not in calls:
                missing.append(fn.name)
    check("A4", "노션 쓰기에 되읽기가 붙어 있다", not missing,
          f"빠진 함수: {missing}" if missing else f"{sorted(writers)} 전부 _verify 호출")


# ---------------------------------------------------------------- A5
def a5_option_source() -> None:
    """select 값을 코드 상수로 두지 않는다 — 정본은 라이브 스키마다."""
    src = (ROOT / "kv_transport/notion.py").read_text(encoding="utf-8")
    has_check = "_check_option" in src and "allowed_options" in src
    tree = ast.parse(src)
    called = False
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == "_build":
            called = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                         and n.func.id == "_check_option" for n in ast.walk(fn))
    check("A5", "select 허용값을 라이브 스키마에서 대조한다", has_check and called,
          "쓰기 전 _check_option 호출 확인" if called else "_build 에서 대조 안 함")


# ---------------------------------------------------------------- A6
def a6_no_magic_constraints() -> None:
    """제약 숫자를 코드에 박지 않는다. 두 진실원을 원천 차단한다."""
    magic = {"2000": "노션 글자 한계", "0.34": "노션 rate limit",
             "180": "claude timeout", "29": "apify 월 천장"}
    bad = []
    for p in py_files():
        if "constraints" in p.name or rel(p) in FIXTURE_FILES:
            continue
        src = p.read_text(encoding="utf-8")
        # 🔴 문자열/독스트링 안의 숫자는 코드 상수가 아니다. 설명하려면 실제 숫자를 써야 하고,
        #    그걸 위반으로 세면 "정확한 문서"가 검사를 실패시킨다.
        in_string = set()
        try:
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    in_string.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        except SyntaxError:
            continue   # A0 가 이미 셌다
        for i, line in enumerate(src.splitlines(), 1):
            if i in in_string:
                continue
            code = line.split("#")[0]
            for m, why in magic.items():
                if re.search(rf"(?<![\w.]){re.escape(m)}(?![\w.])", code) and "constraints.get" not in code:
                    bad.append(f"{rel(p)}:{i} {m}({why})")
    check("A6", "제약 숫자가 코드에 박혀 있지 않다", not bad, f"{len(bad)}건: {bad[:4]}" if bad else "")


# ---------------------------------------------------------------- A8
# 🔴 **바깥으로 나가거나 되돌릴 수 없는** 지메일 엔드포인트. 하나라도 있으면 위반이다.
#
#    경계를 여기 그은 이유:
#        아카이브(`/modify`)  인박스 라벨만 뗀다. 되돌릴 수 있다        → 허용
#        초안(`POST /drafts`) 만들 뿐 나가지 않는다                  → 허용
#        발송(`/send`)        회사 이름으로 바깥에 나간다             → 🔴 금지
#        영구삭제(`/trash` 등) 되돌리기 어렵다. 쓸 일이 없다            → 🔴 금지
#
#    `POST /drafts` 와 `POST /drafts/{id}/send` 는 접미사 하나 차이다.
#    그래서 `/send` 를 잡되 `/drafts` 는 통과시킨다.
#
#    옛 검사는 `.send(` 와 `messages()` 가 같은 줄에 있는지만 봤다. 그건 googleapiclient
#    전용이고, requests 로 짜면 **아무것도 못 잡는다** — 통로가 생기는 순간 무력해지는
#    검사였다. 게다가 `drafts().send(id)` 로도 메일이 나가는데 그것도 못 잡았다.
#    (옛 리포에 `drafts().send` 는 0건이었지만, 막아서가 아니라 우연히 없었다.)
GMAIL_FORBIDDEN_ENDPOINTS = ("/send", "/trash", "/batchDelete", "/import", "/insert")
SMTP_MODULES = {"smtplib", "aiosmtplib"}


def _docstring_lines(tree: ast.AST) -> set[int]:
    """독스트링이 차지하는 줄 번호.

    🔴 문자열 **전부**를 빼면 안 된다. A8 이 잡으려는 위반이 바로 문자열(URL)이기 때문이다.
       독스트링만 뺀다 — 규칙을 설명하는 글이 그 규칙을 위반한 것으로 세지 않기 위해서다.
       (08-17 실측: gmail 통로의 '왜 SDK 를 안 쓰나' 설명이 A8 에 걸렸다.)
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            out.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return out


def a8_no_send() -> None:
    """메일이 바깥으로 나가는 경로가 코드에 없나.

    스코프로는 못 막는다 — 토큰이 가진 gmail.modify 에 send 가 포함된다. 막는 자리는 여기뿐이다.
    """
    bad = []
    for p in py_files():
        r = rel(p)
        if r in FIXTURE_FILES:
            continue
        src = p.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue                      # A0 가 이미 셌다
        # smtplib 는 지메일 API 를 통째로 우회하는 발송 경로다
        for node in ast.walk(tree):
            mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            if any(str(m).split(".")[0] in SMTP_MODULES for m in mods):
                bad.append(f"{r}:{node.lineno} smtp")
        skip_lines = _docstring_lines(tree)
        for i, line in enumerate(src.splitlines(), 1):
            if i in skip_lines:
                continue
            code = line.split("#")[0]
            for ep in GMAIL_FORBIDDEN_ENDPOINTS:
                if ep in code:
                    bad.append(f"{r}:{i} {ep}")
            # 옛 SDK 패턴도 계속 본다 — 누가 googleapiclient 를 다시 들여올 수 있다
            if re.search(r"\.send\s*\(", code) and ("messages()" in code or "drafts()" in code):
                bad.append(f"{r}:{i} sdk-send")
    check("A8", "메일 발송 경로 0건", not bad,
          f"{len(bad)}건: {bad[:4]}" if bad
          else f"금지 엔드포인트 {len(GMAIL_FORBIDDEN_ENDPOINTS)}종 · SDK send · smtplib — 전부 0 "
               f"(아카이브·초안은 허용)")


# ---------------------------------------------------------------- 아직 못 세는 것
def a9_schedule() -> None:
    """선언과 실제 설치가 맞나. 어긋나면 그게 '선언은 있는데 발동 안 하는 가드'다."""
    r = subprocess.run([sys.executable, "schedule.py", "check"], cwd=ROOT,
                       capture_output=True, text=True)
    missing = "선언했는데 설치가 안 됐다" in r.stdout
    check("A9", "스케줄 선언 ↔ 실제 설치 일치", not missing,
          "선언한 것이 전부 설치돼 있다" if not missing
          else "선언만 있고 안 도는 항목이 있다 — schedule.py check 로 확인")


def a7_paid_tag() -> None:
    """유료 호출은 skill 꼬리표 없이 불가능해야 한다."""
    src = (ROOT / "kv_transport/apify.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    ok = False
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name == "run_actor":
            # keyword-only 인자에 skill 이 있고, 기본값이 없어야 한다
            names = [a.arg for a in fn.args.kwonlyargs]
            required = [a.arg for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults) if d is None]
            ok = "skill" in names and "skill" in required
    check("A7", "유료 호출에 skill 꼬리표 필수", ok,
          "run_actor 가 skill 을 키워드 필수로 받는다" if ok else "skill 이 필수가 아니다")


def a13_portability() -> None:
    """맥 전용 코드가 schedule.py 밖으로 새지 않았나.

    다른 기계로 옮길 때 고칠 곳이 한 파일이어야 이식이 싸다.
    지금 0건인 상태를 잠근다 — 새로 생기면 즉시 잡힌다.
    """
    marks = ("launchctl", "LaunchAgents", "/Users/", "Library/")
    bad = []
    for p in py_files():
        r = rel(p)
        if r in ("schedule.py",) or r.startswith("tools/"):
            continue        # 일회성 도구는 그 기계에서만 쓴다
        src = p.read_text(encoding="utf-8")
        try:
            in_string = set()
            for node in ast.walk(ast.parse(src)):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    in_string.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        except SyntaxError:
            continue
        for i, line in enumerate(src.splitlines(), 1):
            if i in in_string:
                continue
            for m in marks:
                if m in line.split("#")[0]:
                    bad.append(f"{r}:{i} {m}")
    check("A13", "맥 전용 코드가 schedule.py 밖에 없다", not bad,
          f"{len(bad)}건: {bad[:4]}" if bad else "옮길 때 고칠 곳이 schedule.py 하나다")


def not_yet() -> None:


    skip("A10", "검사가 실제로 문다(변이 테스트)", "별도 러너 필요 — 다음 단계")
    skip("A11", "테스트가 망을 안 부른다", "테스트 러너가 아직 없다")
    skip("A12", "테스트가 프로덕션을 안 건드린다", "테스트 러너가 아직 없다")


def main() -> int:
    a0_parseable()
    a1_single_transport()
    a2_fetched_unwrap()
    a3_no_rc_success()
    a4_readback()
    a5_option_source()
    a6_no_magic_constraints()
    a8_no_send()
    a7_paid_tag()
    a9_schedule()
    a13_portability()
    not_yet()

    print("=" * 68)
    for cid, name, detail in PASS:
        print(f"  ✅ {cid}  {name}" + (f"\n        {detail}" if detail else ""))
    for cid, name, detail in FAIL:
        print(f"  🔴 {cid}  {name}\n        {detail}")
    for cid, name, why in SKIP:
        print(f"  ⚪ {cid}  {name}  — 미적용: {why}")
    print("=" * 68)
    print(f"  통과 {len(PASS)} · 실패 {len(FAIL)} · 🔴 미적용 {len(SKIP)}")
    print("  미적용을 통과로 세지 않는다 — 그게 '선언은 있는데 발동 안 하는 가드'다.")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
