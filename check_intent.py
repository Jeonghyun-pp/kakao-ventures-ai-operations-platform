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
    "skills/appstore/itunes.py",
    "skills/appstore/notion.py",
    "skills/appstore/slack.py",
    "skills/appstore/claude.py",
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
    "skills/appstore/claude.py": "claude -p 통로. 이게 claude 를 부르는 유일한 자리다",
    "tools/mutation_test.py": "검사 러너. check_intent 를 자식 프로세스로 돌려야 한다",
}

PASS, FAIL, SKIP = [], [], []


def check(cid: str, name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append((cid, name, detail))


def skip(cid: str, name: str, why: str) -> None:
    SKIP.append((cid, name, why))


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
        tree = ast.parse(p.read_text(encoding="utf-8"))
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
    src = (ROOT / "skills/appstore/notion.py").read_text(encoding="utf-8")
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
    src = (ROOT / "skills/appstore/notion.py").read_text(encoding="utf-8")
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
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#")[0]
            for m, why in magic.items():
                if re.search(rf"(?<![\w.]){re.escape(m)}(?![\w.])", code) and "constraints.get" not in code:
                    bad.append(f"{rel(p)}:{i} {m}({why})")
    check("A6", "제약 숫자가 코드에 박혀 있지 않다", not bad, f"{len(bad)}건: {bad[:4]}" if bad else "")


# ---------------------------------------------------------------- A8
def a8_no_send() -> None:
    """Gmail 발송 호출 0건. 지금은 Gmail 통로 자체가 없다."""
    bad = []
    for p in py_files():
        src = p.read_text(encoding="utf-8")
        for i, line in enumerate(src.splitlines(), 1):
            if re.search(r"\.send\s*\(", line.split("#")[0]) and "messages()" in line:
                bad.append(f"{rel(p)}:{i}")
    check("A8", "Gmail 발송 호출 0건", not bad, "Gmail 통로가 아직 없다 — 구조적으로 0")


# ---------------------------------------------------------------- 아직 못 세는 것
def not_yet() -> None:
    skip("A7", "유료 호출에 skill 꼬리표 필수", "Apify 통로가 아직 없다 (링크드인 단계)")
    skip("A9", "스케줄 선언 ↔ 설치 일치", "스케줄 선언·설치가 아직 없다 (A8 단계)")
    skip("A10", "검사가 실제로 문다(변이 테스트)", "별도 러너 필요 — 다음 단계")
    skip("A11", "테스트가 망을 안 부른다", "테스트 러너가 아직 없다")
    skip("A12", "테스트가 프로덕션을 안 건드린다", "테스트 러너가 아직 없다")


def main() -> int:
    a1_single_transport()
    a2_fetched_unwrap()
    a3_no_rc_success()
    a4_readback()
    a5_option_source()
    a6_no_magic_constraints()
    a8_no_send()
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
