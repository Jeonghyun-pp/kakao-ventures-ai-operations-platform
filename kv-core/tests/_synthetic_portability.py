"""이식성 — **clone 한 것이 도는가**를 매번 확인한다.

## 왜 이 파일이 생겼나

로드맵이 정한 제품 완성의 세 조건 중 하나가 *"새 사람이 clone→run 으로 즉시 운전한다"* 다.
그런데 그건 **한 번도 측정된 적이 없었다** — "내 맥에서는 돌아요"는 DE 에서 가장 오래된
실패이고, 2026-08-12 에 실제로 걸렸다:

    `.gitignore` 의 `jobs/` 규칙이 런타임 디렉토리를 겨냥한 것인데 **소스**
    `skills/*/project/jobs/` 까지 잡아서, 새로 만든 잡 파일이 커밋에서 조용히 빠졌다.
    로컬은 멀쩡히 돌고 테스트도 통과하는데 **clone 하면 스테이지가 없다.**

문서(런북 00)로 적어뒀지만 문서는 썩는다. 실행 파일이 리포에 실제로 들어 있는지는
**기계가 매번 확인해야** 한다.

    P1  실행에 필요한 소스가 전부 추적된다 (잡·러너·테스트·계약·기준문서)
    P2  `.gitignore` 가 소스 디렉토리를 먹지 않는다
    P3  리포 밖 의존이 **선언돼 있다** (부트스트랩 문서가 실제 환경변수를 덮는다)
    P4  스킬 requirements 가 부트스트랩 문서와 어긋나지 않는다

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_portability.py
git 인덱스를 읽기만 한다 — 네트워크·설치 0.
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import ast  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "kv-core"))
BOOTSTRAP = _REPO / "docs" / "runbook" / "00-bootstrap.md"

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


def _tracked() -> set[str]:
    """git 이 추적하는 파일. **`core.quotepath=false` 가 필수다.**

    기본값이면 한글 파일명을 8진 이스케이프로 **인용해서** 출력한다
    (`"docs/adr/0001-\\352\\263\\265…"`). 그걸 raw 경로와 비교하면 전부 불일치로 보여
    "ADR 11개가 리포에 없다"는 **거짓 경보**가 난다 — 실제로 한 번 그렇게 오진했다.
    """
    out = subprocess.run(["git", "-c", "core.quotepath=false", "ls-files"],
                         cwd=_REPO, capture_output=True, text=True, check=True).stdout
    return set(out.splitlines())


# ══════════════════════════════════════════════════════════════════════
print("=== P1: 실행에 필요한 소스가 전부 추적된다 ===\n")
tracked = _tracked()

# 스케줄러가 실제로 부르는 잡 — `workers.WIRED` 가 정본이다(목록을 손으로 적으면 썩는다).
from kv_core import workers  # noqa: E402

job_files = sorted(p for p in _REPO.glob("skills/*/project/jobs/*.py"))
untracked_jobs = [str(p.relative_to(_REPO)) for p in job_files
                  if str(p.relative_to(_REPO)) not in tracked]
check("P1 잡 파일이 전부 추적된다", not untracked_jobs,
      f"{len(job_files)}개 중 미추적 {untracked_jobs}")
check("P1 배선된 러너가 5개 이상 살아 있다", len(workers.WIRED) >= 5, str(sorted(workers.WIRED)))

# 실행 경로의 나머지 — 하나라도 빠지면 clone 이 안 돈다
essential = [
    "config/tasks.yaml", "config/contracts.yaml", "config/resources.yaml",
    "kv-core/kv_core/workers.py", "kv-core/kv_core/session.py", "kv-core/kv_core/gate.py",
    "skills/net-sourcing/references/judge-criteria.md",
    "skills/mail-harness/references/bplan-criteria.md",
    "docs/runbook/00-bootstrap.md",
]
missing = [f for f in essential if f not in tracked]
check("P1 계약·기준문서·핵심 모듈이 추적된다", not missing, str(missing))

tests = sorted(p for p in _REPO.glob("kv-core/tests/_*.py"))
untracked_tests = [str(p.relative_to(_REPO)) for p in tests
                   if str(p.relative_to(_REPO)) not in tracked]
check("P1 테스트가 전부 추적된다", not untracked_tests, str(untracked_tests))

print("\n=== P2: .gitignore 가 소스 디렉토리를 먹지 않는다 ===\n")
# 실제로 새 파일을 만들어 물어본다 — 규칙을 읽고 추론하지 않는다.
# (기존 파일은 규칙보다 먼저 add 돼 살아남았을 수 있어, 추적 여부로는 알 수 없다.)
# 🔴 **아직 없는 스킬 경로를 반드시 넣는다.** 기존 스킬은 중첩 `.gitignore` 에 자기 negation 을
#    갖고 있어서, 루트 규칙을 훼손해도 그 스킬은 안 걸린다 — 변이 시험에서 실제로 생존했다.
#    다음 사람이 만들 스킬에는 그 보호가 없으므로, **루트 규칙을 지키는 것은 이 프로브뿐**이다.
probes = [
    ("skills/_probe_newskill/project/jobs/x.py", "새 **스킬**의 잡 파일(루트 규칙만 적용)"),
    ("skills/_probe_newskill/project/tools/x.py", "새 스킬의 도구"),
    ("skills/net-sourcing/project/jobs/_probe_new_job.py", "기존 스킬의 새 잡 파일"),
    ("skills/mail-harness/project/jobs/_probe_new_job.py", "새 잡 파일(다른 스킬)"),
    ("skills/net-sourcing/project/tools/_probe_new_tool.py", "새 도구"),
    ("kv-core/tests/_probe_new_test.py", "새 테스트"),
    ("docs/runbook/99-probe.md", "새 런북"),
]
ignored = []
for rel, label in probes:
    rc = subprocess.run(["git", "check-ignore", "-q", rel], cwd=_REPO).returncode
    if rc == 0:                                   # 0 = 무시된다
        ignored.append(f"{label}({rel})")
check("P2 새 소스 파일이 무시되지 않는다", not ignored, "\n       ".join(ignored))

# 런타임 데이터는 **여전히** 무시돼야 한다(반대 방향 회귀 — 규칙을 다 풀어버리면 안 된다).
# ⚠️ `skills/net-sourcing/project/data` 는 ~/kv-data 로 가는 **심볼릭 링크**라
#    `git check-ignore` 가 `fatal: beyond a symbolic link` 를 낸다(rc≠0). 그걸 "무시 안 됨"으로
#    읽으면 거짓 실패다 — 심링크가 아닌 경로로 묻는다.
data_probes = ["skills/_probe_newskill/project/data/y.json",   # 루트 규칙만 적용
               "skills/_probe_newskill/project/logs/z.log",
               "skills/mail-harness/project/data/raw/y.json",
               "skills/ai-consumer-pulse/project/data/raw/z.json"]
not_ignored = []
for f in data_probes:
    r = subprocess.run(["git", "check-ignore", "-q", f], cwd=_REPO,
                       capture_output=True, text=True)
    if r.returncode == 1:                         # 1 = 무시되지 않음(0=무시, 128=오류)
        not_ignored.append(f)
# ⚠️ **이 단언은 약한 보증이다.** 변이 시험에서 `data`·`**/data`·`*.log` 를 전부 지워도
#    프로브가 여전히 무시됐다 — 데이터 무시 규칙이 루트·중첩에 여러 겹 중복돼 있기 때문이다.
#    즉 이 줄은 "규칙 하나가 사라졌다"를 못 잡는다. 그래도 남기는 이유는 **방향**이다:
#    P2 위쪽(소스가 무시되면 안 된다)을 고치다가 규칙을 통째로 풀어버리는 반대 방향 사고를
#    막는 최소한의 표식이다. 강하게 만들려면 임시 리포를 만들어 격리 검증해야 하는데,
#    지금 비용 대비 이득이 낮다고 판단했다.
check("P2 런타임 데이터는 여전히 무시된다(음성 단언 · 약한 보증)", not not_ignored, str(not_ignored))

print("\n=== P3: 리포 밖 의존이 선언돼 있다 ===\n")
# 코드가 실제로 읽는 환경변수를 AST 로 뽑아, 부트스트랩 문서가 덮는지 본다.
env: set[str] = set()
for p in list((_REPO / "kv-core").rglob("*.py")) + list((_REPO / "skills").rglob("*.py")):
    if "/.venv/" in str(p) or "_archive" in str(p) or "/tests/" in str(p):
        continue
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        continue
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr in ("get", "getenv") and n.args
                and isinstance(n.args[0], ast.Constant)
                and isinstance(n.args[0].value, str)):
            v = n.args[0].value
            if v.isupper() and len(v) > 2 and "environ" in ast.dump(n.func.value)[:200]:
                env.add(v)

doc = BOOTSTRAP.read_text(encoding="utf-8")
# 개발·테스트 전용 스위치는 부트스트랩 대상이 아니다(운영자가 채울 값이 아니다).
_DEV_ONLY = {"INJECT_BAD", "W3_N", "W3_SLEEP", "W3_FAIL_IDX", "BPLAN_DRY",
             "BPLAN_RESERVE_CALLS", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}
undocumented = sorted(v for v in env - _DEV_ONLY if v not in doc)
check("P3 코드가 읽는 환경변수가 부트스트랩 문서에 전부 있다", not undocumented,
      f"미문서화 {undocumented}")
check("P3 환경변수를 실제로 뽑았다(추출이 죽으면 위 검사가 공허해진다)",
      len(env) >= 8, f"{len(env)}종 발견")

print("\n=== P4: requirements 가 문서와 어긋나지 않는다 ===\n")
reqs = [p for p in _REPO.rglob("requirements*.txt")
        if "/.venv/" not in str(p) and "_archive" not in str(p)]
pkgs: set[str] = set()
for p in reqs:
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            pkgs.add(ln.split("[")[0].split(">")[0].split("=")[0].split("<")[0].strip().lower())
missing_doc = sorted(x for x in pkgs if x not in doc.lower())
check("P4 requirements 의 패키지가 부트스트랩 문서에 있다", not missing_doc, str(missing_doc))
check("P4 requirements 파일을 실제로 찾았다", len(reqs) >= 4, f"{len(reqs)}개")

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 이식성 계약 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 이식성 계약 통과 — clone 에 실행 자산이 다 있고 밖의 것은 선언돼 있다")
