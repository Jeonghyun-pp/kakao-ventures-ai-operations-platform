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
    P5  자격증명 정본이 하나다 (스킬별 `.env` 부활 금지 — 파일도, 읽는 코드도)
    P6  백업이 **선언이 아니라 배선**이다 (문서 4곳이 rsync 를 선언했는데 실체가 없었다)\n    P7  **스킬 지식은 [4]에만** — 코어 실행 코드에 도메인 어휘가 없다\n    P8  `.venv/bin/python` 하드코딩 금지 (CI 에는 그 경로가 없다 · 3회 반복한 함정)

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
    "docs/STATUS.md",          # [2026-08-16] 리포의 진입점 — 사라지면 아무도 어디부터 읽을지 모른다
    "kv-core/tests/run_suite.py",  # 프로덕션 가드가 붙은 로컬 러너. 빠지면 clone 이 가드 없이 돈다
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
# P5 [2026-08-16] 자격증명 정본이 하나다 — **스킬별 `.env` 부활 금지**
#
# 2026-07-27 에 `.env` 6개(NOTION_TOKEN 3벌 · APIFY_TOKEN 2벌)를 `$KV_CONFIG/.env`
# 하나로 접었다. 그런데 2026-08-16 까지 `net-sourcing/tools/` 10본이 **옛 인라인 파서**
# (`(PROJ / ".env").read_text()`)를 그대로 들고 있었다. 통합 때 그 파일을 지웠으므로
# 그 도구들은 **실행 즉시 FileNotFoundError 로 죽는 상태**였고, 손으로 돌리는 도구라
# 아무도 몰랐다. **사람이 가끔 부르는 경로는 테스트가 아니면 영영 안 드러난다.**
#
# `kv_core.secrets` 는 이 리포에서 **어떤 테스트도 안 건드리던 유일한 모듈**이었다
# (`_synthetic_contract_coverage` X5 가 그 사실을 셌다).
print("\n=== P5: 자격증명 정본이 하나다 ===\n")

import re  # noqa: E402

from kv_core import paths as _paths  # noqa: E402
from kv_core import secrets  # noqa: E402

check("P5 `.env` 정본이 `$KV_CONFIG` 아래 하나다",
      secrets.env_file() == _paths.config_root() / ".env", str(secrets.env_file()))

# `load()` 는 메모이제이션이라 두 번째 호출이 no-op 다. 이걸 모르면 테스트에서 환경변수를
# 지우고 재호출했을 때 "안 올라온다"로 오진한다(2026-08-16 에 실제로 한 번 그렇게 봤다).
secrets.load()
check("P5 load() 는 한 번만 읽는다(두 번째 호출은 no-op)", secrets._loaded)

try:
    secrets.get("__없는_자격증명__")
    check("P5 required 자격증명이 없으면 즉시 죽는다", False, "조용히 None 을 줬다")
except RuntimeError as _e:
    check("P5 required 자격증명이 없으면 즉시 죽는다", "__없는_자격증명__" in str(_e))
check("P5 required=False 는 None 을 준다",
      secrets.get("__없는_자격증명__", required=False) is None)

# 🔴 스킬 디렉토리에 `.env` 가 되살아나면 복제본 시대로 돌아간다(같은 토큰 N벌).
_revived = sorted(str(p.relative_to(_REPO)) for p in (_REPO / "skills").rglob(".env")
                  if "_archive" not in str(p))
check("P5 스킬 안에 `.env` 가 되살아나지 않았다", not _revived, f"발견: {_revived}")

# 🔴 그리고 코드가 스킬 로컬 `.env` 를 **읽으려 하지도** 않아야 한다 — 파일이 없어도
#    그런 코드는 남아 있을 수 있고, 그게 정확히 위 10본의 상태였다.
_inline = sorted(str(p.relative_to(_REPO)) for p in (_REPO / "skills").rglob("*.py")
                 if "_archive" not in str(p)
                 and re.search(r'\(\s*PROJ\s*/\s*"\.env"\s*\)\s*\.read_text',
                               p.read_text(encoding="utf-8", errors="ignore")))
check("P5 스킬 로컬 `.env` 를 직접 읽는 코드가 없다", not _inline, f"발견: {_inline}")

# ══════════════════════════════════════════════════════════════════════
# P6 [2026-08-16] 백업이 **선언이 아니라 배선**이다
#
# 문서 4곳(README · kv_core/__init__ · ADR 0001 · W1 교훈)이 `~/kv-data` 를 *"rsync 단위"*
# 라고 선언하고, `W1-공통-런타임.md` 표는 **"데이터 | 영구 손실 | 없음 | 정기 rsync"** 라고
# 적어 뒀다. 그런데 2026-08-16 실측 결과 crontab 은 비어 있고 launchd 에도 없고 디스크
# 어디에도 사본이 없었다 — **981MB 유일본이 4개월간 무방비였다.**
#
# 백업은 조용히 멈추는 것이 가장 위험한 종류의 잡이다(멈춰도 증상이 없고, 필요해지는
# 순간에야 없다는 걸 안다). 그래서 여기서 재는 것은 "백업이 돌았나"가 아니라
# **"백업이 돌지 않으면 알아챌 구조가 있나"** 다.
print("\n=== P6: 백업이 배선돼 있다 ===\n")

from kv_core import registry as _reg  # noqa: E402
from kv_core import workers as _wk  # noqa: E402

_script = _REPO / "scripts" / "backup-kv-data.sh"
check("P6 백업 스크립트가 리포에 있다(clone 에 딸려온다)", _script.exists(), str(_script.name))
check("P6 백업 스크립트가 추적된다", "scripts/backup-kv-data.sh" in tracked)

_tasks = {t.name: t for t in _reg.load()}
check("P6 backup 이 task 로 선언돼 있다", "backup" in _tasks)
if "backup" in _tasks:
    _b = _tasks["backup"]
    check("P6 스케줄러가 실제로 돌린다(runner=local · WIRED)",
          _b.runner == "local" and "backup" in _wk.WIRED,
          f"runner={_b.runner} wired={'backup' in _wk.WIRED}")
    # 🔴 last_attempt 면 rsync 가 실패해도 워터마크가 전진해 **다음날까지 백업이 없고
    #    아무도 모른다.** 백업에서 이 한 줄은 가드 전체의 유무를 가른다.
    check("P6 워터마크가 last_success 다(실패가 전진을 못 한다)",
          _b.watermark == "last_success", f"watermark={_b.watermark}")
    check("P6 주기와 신선도 SLO 가 선언돼 있다(부재 감지의 근거)",
          _b.cadence == "daily" and _b.freshness_days, 
          f"cadence={_b.cadence} freshness_days={_b.freshness_days}")
    check("P6 외부 자원을 안 쓴다(백업이 쿼터·과금에 얽히지 않는다)",
          _b.resources == [], f"resources={_b.resources}")

# 🔴 스크립트 자체의 안전장치 — 빈 원본을 성실히 반영하면 그게 오늘 스냅샷이 되고,
#    보관 기간이 지나면 멀쩡한 스냅샷이 전부 밀려난다. **백업이 데이터를 지우는 경로다.**
_src = _script.read_text(encoding="utf-8") if _script.exists() else ""
check("P6 원본 부재 시 중단한다", 'if [ ! -d "$SRC" ]' in _src)
check("P6 원본 파일 수 급감 시 중단한다", "SRC_FILES" in _src and "-lt 100" in _src)
check("P6 복사 후 파일 수를 대조한다", "DST_FILES" in _src and "$DST_FILES" in _src)

# ══════════════════════════════════════════════════════════════════════
# P7 [2026-08-16 · 결정 ⑫] **스킬 지식은 [4]에만 있다**
#
# 사용자 결정: *"스킬 관련된 지식은 4인 스킬 레이어에만 있게 하고 싶다."*
#
# `kv-core` 는 [2]통로 + [3]컨트롤 플레인이다. 여기에 스킬 도메인이 섞이면 두 가지가 깨진다:
#   · 스킬 쪽 사실이 바뀔 때(애플이 차트를 84 → 90) **코어를 고쳐야** 한다
#   · 재빌드 계획의 *"[3]은 유지, [4]는 재빌드"* 가 참이 아니게 된다
#
# 실제로 `workers._board_files_today()` 45줄 + 임계 3개(84·80·0.5)가 코어에 있었다
# (254줄 중 85줄 = 33%). 결정 ⑫ 로 `tools/board_health.py` 에 옮겼다.
#
# 🔴 여기서 재는 것은 **인스턴스가 아니라 부류**다 — 앱스토어 하나를 옮긴 것으로 끝내면
#    다음 도메인이 같은 자리에 다시 자란다.
#
# ⚠️ 판정 대상은 **실행되는 코드만**이다. 주석·독스트링에 "왜 옮겼나"를 남기는 것은
#    옳은 일이고, 그걸 금지하면 근거가 사라진다.
print("\n=== P7: 스킬 지식이 코어에 없다 ===\n")


def _executable_only(src: str) -> str:
    """주석 **과 독스트링**을 뺀 실행 코드."""
    tree = ast.parse(src)
    skip = set()
    for n in ast.walk(tree):
        if (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                and isinstance(n.value.value, str)):
            skip.update(range(n.lineno, (n.end_lineno or n.lineno) + 1))
    return "\n".join(ln.split("#", 1)[0]
                     for i, ln in enumerate(src.splitlines(), 1) if i not in skip)


# 스킬 **도메인**의 어휘. 스킬 **이름·경로**는 여기 없다 — 디스패치 표가 "무엇을 부르나"를
# 아는 것은 [3]의 본질이라 정당하다. 금지하는 것은 "그 산출물을 어떻게 판정하나"다.
_DOMAIN_TERMS = [
    "appstore_board", "_board_files", "MIN_NONEMPTY",     # 앱스토어 보드 판정
    "judge_queue", "judge_verdicts", "linkedin_seen",     # 그물망 산출물 형식
    "bplan_queue", "bplan_verdicts", "drop_ledger",       # 비플랜 산출물 형식
    "top-free", "top-paid", "차트",                        # 스토어 어휘
]
_KVC = _REPO / "kv-core" / "kv_core"
_leaks = []
for _p in sorted(_KVC.glob("*.py")):
    _code = _executable_only(_p.read_text(encoding="utf-8"))
    for _i, _ln in enumerate(_code.splitlines(), 1):
        for _term in _DOMAIN_TERMS:
            if _term in _ln:
                _leaks.append(f"{_p.name}:{_i} [{_term}] {_ln.strip()[:60]}")
check("P7 코어 실행 코드에 스킬 도메인 어휘가 없다", not _leaks, "\n       ".join(_leaks[:5]))
check("P7 어휘표를 실제로 훑었다(0이면 위 검사가 공허하다)", len(_DOMAIN_TERMS) >= 10)

# 🔴 반대 방향 — 스킬 **이름·경로**는 코어에 있어야 정상이다(디스패치 표).
#    이걸 같이 금지하면 P7 이 러너를 못 만들게 막는 규칙이 된다.
_wsrc = (_KVC / "workers.py").read_text(encoding="utf-8")
check("P7 스킬 경로·task 이름은 코어에 남아 있다(디스패치는 [3]의 일)",
      "net-sourcing" in _wsrc and "WIRED" in _wsrc)

# ══════════════════════════════════════════════════════════════════════
# P8 [2026-08-16] `.venv/bin/python` 을 **경로로 박지 않는다**
#
# CI 는 `setup-python` + `pip install -e` 로 돌아 리포에 `.venv` 가 생기지 않는다.
# 그 경로를 하드코딩하면 로컬은 초록인데 **CI 에서만** FileNotFoundError 로 죽는다.
#
# 🔴 같은 함정에 **세 번** 빠졌다(전부 2026-08-16 하루에):
#     ① `_synthetic_loud_failure.py:198` — 첫 푸시를 막았다
#     ② 결정 ⑫ 의 `workers._PY` — 판정기를 subprocess 로 부르면서
#     ③ 결정 ⑫ 의 `_synthetic_dispatch` — 판정기 자체 검사를 걸면서
# 한 번은 실수, 세 번은 부류다. 그래서 인스턴스가 아니라 **부류**를 막는다.
#
# 허용: 사람이 읽는 문서·주석의 안내(`.venv/bin/python tools/x.py` 실행 예시).
# 금지: **실행되는 코드**가 그 경로를 파이썬 인터프리터로 쓰는 것.
print("\n=== P8: 인터프리터 경로를 하드코딩하지 않는다 ===\n")

_hard = []
_SELF = Path(__file__).resolve()
for _p in list((_REPO / "kv-core").rglob("*.py")) + list((_REPO / "skills").rglob("*.py")):
    # 자기 자신은 뺀다 — 탐지 규칙 자체가 그 패턴 문자열을 담고 있어서 스스로를 잡는다.
    if "/.venv/" in str(_p) or "_archive" in str(_p) or _p.resolve() == _SELF:
        continue
    try:
        _src = _p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        continue
    if ".venv" not in _src:
        continue
    for _i, _ln in enumerate(_executable_only(_src).splitlines(), 1):
        if ".venv" not in _ln:
            continue
        # 🔴 규칙을 좁힌다. `.venv` 가 나온다고 다 문제는 아니다 — 처음엔 넓게 잡았다가
        #    **제외 필터**(`if "/.venv/" in str(p)`)와 **사람에게 주는 실행 안내 문구**까지
        #    걸려 거짓 실패가 났다. 잡아야 하는 건 딱 하나: **인터프리터 경로를 만들어
        #    subprocess 에 넘기는 것**이다.
        _is_pathjoin = '/ ".venv"' in _ln or "/ '.venv'" in _ln
        _is_cmd = ".venv/bin/python" in _ln and any(
            k in _ln for k in ("subprocess", "Popen", "run(", "_PY", "cmd", "["))
        if not (_is_pathjoin or _is_cmd):
            continue
        # `.exists()` 로 **존재를 확인한 뒤 폴백**하는 형태는 정당하다(그게 고친 모양이다).
        if ".exists()" in _ln or "_VENV_PY" in _ln:
            continue
        _hard.append(f"{_p.relative_to(_REPO)}:{_i} {_ln.strip()[:70]}")
check("P8 실행 코드에 `.venv` 인터프리터 경로가 박혀 있지 않다", not _hard,
      "\n       ".join(_hard[:6]))

# 반대 방향 — 검사가 공허하지 않은지. `.venv` 를 언급하는 파일이 실제로 있어야 한다
# (문서·주석 안내는 남아 있는 게 정상이다).
_mentions = sum(1 for _p in (_REPO / "kv-core").rglob("*.py")
                if ".venv" in _p.read_text(encoding="utf-8", errors="ignore"))
check("P8 `.venv` 를 언급하는 파일을 실제로 훑었다", _mentions >= 1, f"{_mentions}본")

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 이식성 계약 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 이식성 계약 통과 — clone 에 실행 자산이 다 있고 밖의 것은 선언돼 있다")
