"""[W9 재설계 Batch A] 실데이터 리플레이 — **검증 사각 ②** 를 닫는다.

## 왜 있나

합성 테스트는 **자기가 데이터를 만든다**. 그래서 항상 완전한 스키마를 본다:
`first_attempt` 가 늘 있고, 승인 레코드엔 늘 `decision` 키가 있고, 잡 디렉토리는 늘 온전하다.
**실환경에는 그런 데이터가 없다.** W9 리뷰의 C-H4·C-H5·B-H4·B-L2 는 전부 "테스트가 만들지
않는 모양"에서만 나타났다 — 대표적으로 `first_attempt` 가 없는 워터마크에서 never-succeeded
알림이 영구 침묵한 것.

그래서 이 파일은 두 가지를 한다.

1. **실환경 모양 픽스처** — 결측 필드·구 스키마·이종 원천을 일부러 갖춘 데이터를 만들어
   판정을 돌린다. 실 `~/kv-data` 가 없어도(CI) **항상 단언한다**. 조용히 skip 하지 않는다.
2. **실 스냅샷 리플레이** — `~/kv-data` 가 있으면 **읽기 전용 복사본**에 대고 같은 단언을 돌린다.
   원본은 절대 건드리지 않고, 끝나면 원본 해시를 재확인한다.

## 지키는 불변식

    R1  관측(orrery)과 알림(alerts)이 **같은 task 에 모순된 답을 내지 않는다**
        — judge 가 🔴 인데 다이제스트 헤더가 🔴0 이던 사고(C-H4)의 회귀 잠금
    R2  `first_attempt` 결측 워터마크에서도 never-succeeded 가 발동한다 (B-L2)
    R3  구 스키마 승인 레코드(`decision` 키 없음)를 미결로 해석한다
    R4  파손 파일 1개가 브리핑 전체를 죽이지 않는다
    R5  관측은 **아무것도 쓰지 않는다** (read-only 계약)

실행: KV_DATA=<스크래치> python kv-core/tests/_realdata_replay.py
외부 부작용 0 — slack 스텁 + KV_NO_SEND. 실 ~/kv-data 는 **읽기만** 한다.
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import hashlib  # noqa: E402
import json  # noqa: E402
import shutil  # noqa: E402
import sys  # noqa: E402
from datetime import date, timedelta  # noqa: E402
from pathlib import Path  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"
_SCRATCH = Path(os.environ["KV_DATA"]).resolve()
assert _SCRATCH != (Path.home() / "kv-data").resolve(), "🔴 운영 KV_DATA 로 실행 금지"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import alerts, notify, orrery, paths, registry, scheduler  # noqa: E402

notify.slack = lambda *a, **k: True                 # 발송 경계 스텁(킬 스위치보다 우선)

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


# 🔴 [2026-08-13 W12] 살아 있는 데몬이 이 트리에 계속 쓴다 — 그건 우리 위반이 아니다.
#    `com.kv.approvals` 는 **10분마다** 돌고 `_approvals/launchd.log` 에 append 한다.
#    그 틱이 이 테스트가 도는 창에 겹치면 "원본 무변경" 이 거짓 실패한다 — 실측 3회 중 1회.
#    (W12 배치 3 작업 중 우연히 드러났다. 오래 오해할 만한 형태다: 실패가 산발적이고
#     실패 메시지는 "우리가 썼다"로 읽힌다. 실제로 나는 처음에 내 변경 탓인 줄 알았다.)
#
#    ⚠️ 제외를 넓히면 검사가 공허해진다. **append-only 로그만** 뺀다 —
#    상태 파일(cadence·events·cursor·_approvals/*.json·_alerts)은 그대로 지문에 든다.
#    즉 "우리가 상태를 건드렸나"는 여전히 잡히고, "데몬이 로그를 늘렸나"만 무시한다.
_VOLATILE = ("*.log",)


def _tree_digest(root: Path) -> str:
    """디렉토리 내용 지문 — read-only 계약 검증용.

    살아 있는 launchd 데몬이 쓰는 로그는 제외한다(위 주석 참조).
    """
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(p.match(pat) for pat in _VOLATILE):
            continue
        h.update(str(p.relative_to(root)).encode())
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
    return h.hexdigest()


def _reset(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)


def _wm(task_name: str, skill: str, **fields) -> None:
    d = _SCRATCH / skill / "cadence"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{task_name}.json").write_text(json.dumps(fields), encoding="utf-8")


# ══════════════════════════════════════════════════════════════════════
print("=== ① 실환경 모양 픽스처 — 결측 필드·구 스키마 ===\n")
_reset(_SCRATCH)

TODAY = date.today()
LONG_AGO = (TODAY - timedelta(days=30)).isoformat()

# 실 judge 가 8/7 이전에 가졌던 모양: 시도는 있는데 first_attempt 가 **없다**.
# 이 모양에서 알림이 침묵한 것이 B-L2/C-H5 였다.
_wm("judge", "net-sourcing", last_attempt=TODAY.isoformat())
_wm("meeting-mail", "net-sourcing", last_attempt=TODAY.isoformat(),
    last_success=TODAY.isoformat(), first_attempt=LONG_AGO)
_wm("appstore-snapshot", "net-sourcing", last_attempt=LONG_AGO, last_success=LONG_AGO,
    first_attempt=LONG_AGO)

# 구 스키마 승인 레코드 — `decision` 키 자체가 없다(W8 이전 생성분)
apq = _SCRATCH / "_approvals"
apq.mkdir(parents=True, exist_ok=True)
(apq / "old-schema.json").write_text(json.dumps({
    "key": "old-schema", "task": "t", "kind": "judgment", "status": "drafted",
    "created": "2026-07-01T00:00:00Z", "payload": {"text": "구 스키마"},
}), encoding="utf-8")
(apq / "broken.json").write_text("{ 이건 JSON 이 아니다", encoding="utf-8")

# 파손된 잡 상태 + 정상 실패 잡
for jid, body in (("judge-2026-08-04", '{"state":"failed","updated":"2026-08-04T00:00:00Z"}'),
                  ("judge-2026-08-05", "{절단됨")):
    d = _SCRATCH / "net-sourcing" / "jobs" / jid
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(body, encoding="utf-8")

tasks = {t.name: t for t in registry.load()}

# ── R2. first_attempt 결측에서도 never-succeeded 가 발동하는가 ──
fired = {ident for ident, _ in alerts._task_failures()}
check("R2 first_attempt 결측 워터마크에서 never-succeeded 발동",
      "never-succeeded-judge" in fired, f"발동={sorted(fired)}")

# ── R1. 관측과 알림이 같은 답을 내는가 (C-H4 회귀 잠금) ──
st = orrery.gather(now=TODAY, with_network=False)
rows = {t.name: t for t in st.tasks}
judge_red = rows["judge"].icon == "🔴" if "judge" in rows else False
judge_alert = "never-succeeded-judge" in fired
check("R1 judge: 관측🔴 ↔ 알림 발동이 일치", judge_red == judge_alert,
      f"orrery={rows['judge'].icon if 'judge' in rows else '?'} · alerts발동={judge_alert}")

# 전 task 에 대해 일반화 — 알림이 울리는데 관측이 ✅ 이면 그게 이중 진실원이다.
# **판정이 한 곳이면 이 리스트는 구조적으로 빌 수밖에 없다** — 그게 이 단언의 의미다.
contradictions = []
for ident, _ in alerts._task_failures(st):
    name = (ident.replace("never-succeeded-", "") if ident.startswith("never-succeeded-")
            else ident.replace("task-fail-", ""))
    row = rows.get(name)
    if row and row.icon == "✅":
        contradictions.append(f"{name}(관측✅·알림🔴)")
check("R1 알림🔴 인데 관측✅ 인 task 0건", not contradictions, str(contradictions))

# ── R3. 구 스키마 승인 레코드를 미결로 해석하는가 ──
check("R3 구 스키마 레코드를 미결로 해석",
      st.approvals["undecided"] == 1 and st.approvals["total"] == 1,
      str(st.approvals))

# ── R4. 파손 1개가 전체를 죽이지 않는가 ──
check("R4 파손 승인 레코드가 있어도 브리핑 조립 성공", isinstance(st.tasks, list))
check("R4 파손 잡 상태가 실패 목록에 broken 으로 남음",
      any(f.broken for f in st.failures), str(st.failures))
txt = orrery.render_text(st)
check("R4 렌더까지 완주", "KV-ORRERY" in txt and len(txt) > 200)

# ── R5. read-only 계약 ──
before = _tree_digest(_SCRATCH)
orrery.render_slack(orrery.gather(now=TODAY, with_network=False))
after = _tree_digest(_SCRATCH)
check("R5 gather/render 가 데이터를 변경하지 않음", before == after)

# ══════════════════════════════════════════════════════════════════════
print("\n=== ② 실 ~/kv-data 스냅샷 리플레이 (읽기 전용 복사본) ===\n")
REAL = Path.home() / "kv-data"
if not REAL.exists():
    print("  ⚠️  실 ~/kv-data 없음 — 픽스처 단언만 수행(CI 정상 경로)")
else:
    real_before = _tree_digest(REAL)
    _reset(_SCRATCH)
    for sub in REAL.iterdir():
        if sub.name in ("_locks",):                  # 락은 복사하지 않는다(유령 락 유발)
            continue
        dst = _SCRATCH / sub.name
        if sub.is_dir():
            shutil.copytree(sub, dst, symlinks=True, ignore_dangling_symlinks=True)
        else:
            shutil.copy2(sub, dst)
    print(f"  · 복사 완료: {sum(1 for _ in _SCRATCH.rglob('*'))} 항목")

    st = orrery.gather(with_network=False)
    rows = {t.name: t for t in st.tasks}
    fired = {ident for ident, _ in alerts._task_failures(st)}
    print("  · 실 상태: " + " ".join(f"{n}{t.icon}" for n, t in rows.items()))
    print(f"  · 알림 발동: {sorted(fired) or '없음'}")

    contradictions = []
    for ident, _ in alerts._task_failures(st):
        name = (ident.replace("never-succeeded-", "") if ident.startswith("never-succeeded-")
                else ident.replace("task-fail-", ""))
        row = rows.get(name)
        if row and row.icon == "✅":
            contradictions.append(f"{name}(관측✅·알림🔴)")
    check("실데이터: 알림🔴 인데 관측✅ 인 task 0건", not contradictions, str(contradictions))
    check("실데이터: 브리핑 렌더 완주", "KV-ORRERY" in orrery.render_text(st))
    check("실데이터: 원본 ~/kv-data 무변경", _tree_digest(REAL) == real_before)

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 실데이터 리플레이 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 실데이터 리플레이 전부 통과")
