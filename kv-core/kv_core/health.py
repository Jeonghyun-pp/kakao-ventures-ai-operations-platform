"""시스템 상태 계산기 — **판정의 단 하나의 정본.** [W9 재설계 Batch B]

## 왜 이 파일이 생겼나

W9 이전에는 "이 task 는 건강한가"를 **8곳이 각자** 계산했다.

    alerts._task_failures      last_attempt vs last_success 격차
    orrery._freshness          last_success 기준 신선도
    orrery._launchd_direct_health  헬스 파일 + 하드코딩 grace (freshness 선언 무시)
    orrery._recent_failures    실패 해결 여부
    approval._is_closed        승인 건의 생사
    workers._board_files_today 산출물 유효성
    cadence.freshness_limit    신선도 한계
    alerts._auth_suspect       인증 열화

그 결과 같은 질문에 두 답이 나왔다 — **judge 가 4일째 전멸인데 orrery 는 ⚠️, alerts 는 🔴**.
사람은 둘 중 낙관적인 쪽을 믿는다. `cadence.py` 주석이 스스로 "방지하겠다"고 쓴
*"다이제스트는 ✅인데 알림은 🔴"* 이 그대로 일어났다.

**이 모듈의 규칙: 판정은 여기에만 있다.** orrery 는 렌더링만, alerts 는 발송만 한다.
둘 다 `compute()` 의 결과를 소비한다 — 그러면 모순이 **구조적으로 불가능**해진다.

## 설계 원칙

- **순수 읽기.** 아무것도 쓰지 않는다(관측이 상태를 바꾸면 관측이 아니다).
- **`now` 를 인자로 받는다.** 시간 경과를 시뮬레이션할 수 있어야 한다(검증 사각 ⑤).
  `now` 를 함수 안에서 읽으면 "30일 뒤에도 이 알림이 옳은가"를 물을 방법이 없다.
- **보수적 방향.** 모르면 "해결됨"이 아니라 "미해결" 쪽으로 기운다. 거짓 침묵 > 거짓 경보.
- **파생은 저장하지 않는다.** status·icon·alertable 은 전부 계산 결과지 파일에 없다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import cadence, paths, registry, scheduler

try:
    from . import workers
    _WIRED: set | None = set(workers.WIRED)
except Exception as _e:                      # noqa: BLE001
    # [중간점검 D3] 침묵하면 배선된 task 가 "미배선(정상)"으로 위장된다 — loud + 미상 구분.
    print(f"⚠️  health: workers 로드 실패 — 배선 상태 미상으로 표시한다: {_e!r}")
    _WIRED = None


# 상태 어휘 — 이 표가 아이콘·알림 대상의 유일한 정의다.
#   ok        신선하다
#   late      주기는 지났으나 신선도 한계 안 (관측만)
#   stale     신선도 한계 초과 (알림)
#   never     시도는 있으나 성공 0, 유예 안 (관측만)
#   never_bad 시도는 있으나 성공 0, 유예 초과 (알림)
#   idle      배선됐는데 시도 이력이 아예 없음 (관측만)
#   unwired   러너 미배선 — 워터마크 없음이 정상 (침묵)
#   unknown   워터마크·헬스 파손 등 판정 불가 (관측만, loud)
_ICON = {"ok": "✅", "late": "⚠️", "stale": "🔴", "never": "⚠️", "never_bad": "🔴",
         "idle": "⚠️", "unwired": "🔕", "unknown": "⚠️"}
_ALERTABLE = {"stale", "never_bad"}

_NEVER_GRACE_MULT = 3        # never-succeeded 유예 = freshness_limit × N (alerts.yaml 과 공유)
_ATTEMPT_RE = re.compile(r"-r\d+$")
_JOBDATE_RE = re.compile(r"-(\d{4}-\d{2}-\d{2})(?:-r\d+)?$")


# ─────────────────────────── 자료형 ───────────────────────────

@dataclass
class TaskState:
    name: str
    skill: str
    cadence: str | None
    runner: str
    shadow: bool
    status: str
    late_days: int | None
    limit_days: int
    last_success: str | None
    last_attempt: str | None
    first_seen: str | None
    note: str
    first_seen_known: bool = True

    @property
    def icon(self) -> str:
        return _ICON.get(self.status, "⚠️")

    @property
    def alertable(self) -> bool:
        """즉시 푸시 대상인가 — **관측과 알림이 같은 함수를 본다**(이중 진실원 방지)."""
        return self.status in _ALERTABLE


@dataclass
class FailureState:
    skill: str
    job: str
    ts: str
    resolved: bool
    broken: bool
    reason: str = ""


@dataclass
class SystemState:
    now: date
    tasks: list[TaskState] = field(default_factory=list)
    failures: list[FailureState] = field(default_factory=list)
    approvals: dict = field(default_factory=dict)
    dlq: list[dict] = field(default_factory=list)
    alerts_today: list[str] = field(default_factory=list)
    costs: dict = field(default_factory=dict)
    log_last_touch: str | None = None

    # ── 파생 뷰 — 소비자(렌더러·발송기)가 각자 계산하지 않게 여기서 준다 ──
    @property
    def unresolved_failures(self) -> list[FailureState]:
        return [f for f in self.failures if not f.resolved]

    def reds(self) -> list[str]:
        """🔴 사유 목록 — **다이제스트 게이트와 알림이 공유하는 단일 판단**.

        [C-M1] 예전엔 `scheduler` 가 `bool(d["failures"])` 로 게이트했는데 그 목록은
        **해결분 포함 원본**이었다. 렌더러는 미해결만 표시하므로, 해결된 실패 3건이
        7일간 창에 남아 매 틱 다이제스트를 띄우고 내용은 "미해결 0건"이었다.
        """
        out = [f"{t.name}: {t.note}" for t in self.tasks if t.status in _ALERTABLE]
        out += [f"{f.skill}/{f.job}: 미해결 실패" for f in self.unresolved_failures]
        return out

    def alertable_tasks(self) -> list[TaskState]:
        return [t for t in self.tasks if t.alertable]


# ─────────────────────────── 시간·경로 헬퍼 ───────────────────────────

def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _as_date(v) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except (ValueError, TypeError):
        return None


def _ts_to_utc_date(ts: str) -> date | None:
    """이종 원천 ts 정규화 — naive(오프셋 없음)면 **KST 로 간주**해 UTC 로 변환.

    [중간점검 D4] 헬스 파일 writer 는 naive KST 를 쓰고 관측자는 UTC now 와 빼서
    🔴 경보가 하루 늦었다. schema-on-read 는 읽는 쪽이 정규화한다.
    """
    try:
        dt = datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        return (dt - timedelta(hours=9)).date()
    return dt.astimezone(timezone.utc).date()


def _job_base(job_id: str) -> str:
    """attempt-suffix 제거 — `judge-2026-08-06-r2` → `judge-2026-08-06`."""
    return _ATTEMPT_RE.sub("", job_id)


def _attempt_no(job_id: str) -> int:
    """attempt 순번 — 접미사 없음=1, `-r2`=2, `-r3`=3 …

    해결 여부는 **시각이 아니라 순번**으로 판정한다. `updated` 는 초 단위라 같은 초에 실패
    직후 재시도가 성공하면 문자열 비교가 동률이 되어 "미해결"로 굳는다(합성에서 실제로 발생).
    순번은 리포가 재시도에 부여하는 **명시적 순서**라 그런 동률이 없다.
    """
    m = _ATTEMPT_RE.search(job_id)
    return int(m.group(0)[2:]) if m else 1


def _jobs_root() -> Path:
    return paths.data_root()


def _earliest_attempt_from_jobs(skill: str, task_name: str) -> str | None:
    """잡 디렉토리 이름에서 **최초 시도일**을 복원한다.

    [B-L2 · C-H5] `first_attempt` 가 없을 때 `last_attempt` 로 폴백하면 그 값은 매일
    갱신되므로 "배선 후 경과일"이 **영원히 0**이 되어 알림이 영구 침묵한다. 실 judge 가
    도는 건 2026-08-07 에 사람이 손으로 값을 넣었기 때문이지 구조가 아니었다.
    잡 디렉토리는 시도할 때마다 생기고 **지워지지 않으므로** 시계를 리셋하지 않는다.
    """
    d = _jobs_root() / skill / "jobs"
    if not d.exists():
        return None
    best: str | None = None
    for p in d.iterdir():
        if not p.is_dir() or not p.name.startswith(task_name + "-"):
            continue
        m = _JOBDATE_RE.search(p.name)
        if not m:
            continue
        if best is None or m.group(1) < best:
            best = m.group(1)
    return best


def _first_seen(t, wm: dict) -> tuple[str | None, bool]:
    """(최초 시도일, 신뢰 가능한가). 기록값과 잡 흔적 중 **더 이른 쪽**을 쓴다.

    더 이른 쪽을 고르는 것이 자기치유다 — `setdefault` 가 오늘로 리셋해버린 워터마크도
    잡 디렉토리가 남아 있으면 원래 시각으로 되돌아온다(C-H5 의 구조적 해소).
    """
    recorded = str(wm.get("first_attempt") or "")[:10] or None
    derived = _earliest_attempt_from_jobs(t.skill, t.name)
    cands = [c for c in (recorded, derived) if c]
    if cands:
        return min(cands), True
    la = str(wm.get("last_attempt") or "")[:10] or None
    # 시도는 있는데 최초 시각을 모른다 — `la` 로 폴백하면 경과일이 영원히 0이다.
    # 값은 주되 **모른다는 사실을 함께 실어** 소비자가 침묵 대신 한 번 알리게 한다.
    return la, False


# ─────────────────────────── task 상태 판정 (단일 지점) ───────────────────────────

def _task_state(t, now: date) -> TaskState:
    limit = cadence.freshness_limit(t)
    gap = cadence.gap_days(t.cadence, where=t.name)

    def mk(status, note, *, late=None, ls=None, la=None, fs=None, known=True):
        return TaskState(name=t.name, skill=t.skill, cadence=t.cadence, runner=t.runner,
                         shadow=t.shadow, status=status, late_days=late, limit_days=limit,
                         last_success=ls, last_attempt=la, first_seen=fs, note=note,
                         first_seen_known=known)

    # ── 외부 러너(launchd-direct): 워터마크가 없다. 헬스 파일로 판정하되 **같은 규칙**을 쓴다 ──
    # [C-M2] 예전엔 여기만 `freshness_limit` 을 안 보고 하드코딩 grace 를 썼고, 게다가
    # alerts 는 이 러너를 통째로 제외해 **매일 노션에 138행을 착지시키는 잡이 알림 커버리지 0**이었다.
    if t.runner == "launchd-direct":
        p = paths.data_root() / t.skill / "last_run_health.json"
        if not p.exists():
            return mk("unknown", "헬스 파일 없음(관측 불가)")
        try:
            h = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return mk("unknown", "헬스 파일 파손")
        d_utc = _ts_to_utc_date(str(h.get("ts", "")))
        if d_utc is None:
            return mk("unknown", "헬스 ts 없음/파손(관측 불가)")
        late = (now - d_utc).days
        ok = bool(h.get("ok"))
        ls = d_utc.isoformat() if ok else None
        if not ok:
            status = "late" if late <= limit else "stale"
            return mk(status, f"{late}일 전 저하(degraded) · plist 직접 실행", late=late, ls=ls,
                      la=d_utc.isoformat())
        if late > limit:
            return mk("stale", f"{late}일 밀림 (신선도 한계 {limit}일 초과) · plist 직접 실행",
                      late=late, ls=ls, la=d_utc.isoformat())
        if late > gap:
            return mk("late", f"{late}일 경과 (주기 {gap}일) · plist 직접 실행",
                      late=late, ls=ls, la=d_utc.isoformat())
        return mk("ok", f"{late}일 전 정상 (plist 직접 실행)", late=late, ls=ls,
                  la=d_utc.isoformat())

    # ── 스케줄러 관할 ──
    wm = _read_watermark(t)
    ls_raw, la_raw = wm.get("last_success"), wm.get("last_attempt")
    fs, fs_known = _first_seen(t, wm)

    if not ls_raw:
        if la_raw:
            age = 0
            fs_d = _as_date(fs)
            if fs_d:
                age = (now - fs_d).days
            grace = limit * _NEVER_GRACE_MULT
            if not fs_known:
                # [B-L2] 모른다는 사실 자체를 표면화한다 — 조용히 0일로 세면 영구 침묵이다.
                return mk("never_bad",
                          f"성공 0 · 최초 시도 시각 불명(워터마크에 first_attempt 없음) — 확인 요망",
                          ls=None, la=str(la_raw)[:10], fs=fs, known=False)
            if age >= grace:
                return mk("never_bad",
                          f"배선 후 {age}일간 성공 0 (한계 {limit}일×{_NEVER_GRACE_MULT} 초과) — 확인 요망",
                          late=age, ls=None, la=str(la_raw)[:10], fs=fs)
            return mk("never", f"시도는 있으나 성공 0 ({age}일째) — 로그 확인",
                      late=age, ls=None, la=str(la_raw)[:10], fs=fs)
        if _WIRED is None:
            return mk("unknown", "배선 상태 미상(workers 로드 실패) — 로그 확인")
        if t.name not in _WIRED and t.runner == "local":
            return mk("unwired", "러너 미배선(워터마크 없음이 정상)")
        return mk("idle", "가동 이력 없음")

    last = _as_date(ls_raw)
    if last is None:
        return mk("unknown", f"워터마크 파손(last_success={ls_raw!r}) — 파일 확인",
                  la=str(la_raw or "")[:10] or None, fs=fs, known=fs_known)
    late = (now - last).days
    common = {"late": late, "ls": last.isoformat(), "la": str(la_raw or "")[:10] or None,
              "fs": fs, "known": fs_known}
    if late > limit:
        return mk("stale", f"{late}일 밀림 (신선도 한계 {limit}일 초과)", **common)
    if late > gap:
        return mk("late", f"{late}일 경과 (주기 {gap}일)", **common)
    return mk("ok", f"{late}일 전 성공", **common)


def _read_watermark(t) -> dict:
    try:
        return scheduler.read_watermark(t)
    except Exception:                        # noqa: BLE001  파손·부재는 빈 상태
        return {}


# ─────────────────────────── 실패 해결 판정 (단일 지점) ───────────────────────────

def _failures(now: date, tasks, *, days: int = 7, unresolved_cap: int = 50,
              resolved_cap: int = 10) -> list[FailureState]:
    """최근 실패 잡 + **해결 여부**. 판정 규칙은 여기에만 있다.

    두 갈래로 해결을 판정한다.
      ① 같은 base 의 **더 나중** attempt 가 completed  — [C-H3] 예전엔 "completed 가 하나라도
         있으면" 이라 순서를 무시했다. 아침에 성공한 잡을 밤에 재실행해 깨뜨려도 🔴 가 안 떴다.
      ② 그 task 의 `last_success` 가 실패일보다 **엄격히 나중 날짜** — [C-H2] 예전엔 날짜로
         잘라 `>=` 비교라 "같은 날 성공 이후에 난 실패"가 전부 해결로 뒤집혔다. 워터마크는
         날짜뿐이라 시각을 모르므로 **보수적으로 다음 날부터만** 해결로 본다.
    """
    rows: list[dict] = []
    # (skill, base) → 성공한 attempt 중 가장 **나중 순번**과 그 시각
    completed_at: dict[tuple[str, str], tuple[int, str]] = {}
    cutoff = datetime.combine(now, datetime.min.time(), tzinfo=timezone.utc).timestamp() - days * 86400
    root = paths.data_root()
    if not root.exists():
        return []
    for sp in sorted(root.glob("*/jobs/*/state.json")):
        skill, jid = sp.parts[-4], sp.parts[-2]
        try:
            if sp.stat().st_mtime < cutoff:
                continue
            st = json.loads(sp.read_text(encoding="utf-8"))
            state, ts = st.get("state"), str(st.get("updated", ""))
            if state == "completed":
                k = (skill, _job_base(jid))
                cur = (_attempt_no(jid), ts)
                if cur > completed_at.get(k, (-1, "")):
                    completed_at[k] = cur
            elif state == "failed":
                rows.append({"skill": skill, "job": jid, "ts": ts, "broken": False})
        except (json.JSONDecodeError, OSError):
            # [D8] 파손은 ts="" 로 정렬 맨 뒤 — 표기는 broken 플래그로 한다.
            rows.append({"skill": skill, "job": jid, "ts": "", "broken": True})

    last_ok: dict[str, str] = {}
    for t in tasks:
        if not t.scheduled:
            continue
        ls = _read_watermark(t).get("last_success")
        if ls:
            last_ok[f"{t.skill}/{t.name}"] = str(ls)[:10]

    out: list[FailureState] = []
    for r in rows:
        base = _job_base(r["job"])
        reason = ""
        resolved = False
        done = completed_at.get((r["skill"], base))
        if done and not r["broken"]:
            # [C-H3] **순번이 더 뒤인 성공만** 해결로 본다. 예전엔 "completed 가 하나라도
            # 있으면" 이라 아침에 성공한 잡을 밤에 재실행해 깨뜨려도 🔴 가 안 떴다.
            if done > (_attempt_no(r["job"]), r["ts"]):
                resolved, reason = True, f"나중 attempt(r{done[0]}) 성공"
        elif done and r["broken"]:
            reason = "파손 — 순서 미상이라 해결 판정 보류"
        if not resolved and r["ts"]:
            task = base[:-11] if len(base) > 11 and base[-11] == "-" else base
            ls = last_ok.get(f"{r['skill']}/{task}")
            f_d = _as_date(r["ts"])
            l_d = _as_date(ls) if ls else None
            if f_d and l_d and l_d > f_d:        # [C-H2] 엄격히 나중 날짜만
                resolved, reason = True, f"이후 성공(last_success={ls})"
        out.append(FailureState(skill=r["skill"], job=r["job"], ts=r["ts"],
                                resolved=resolved, broken=r["broken"], reason=reason))

    out.sort(key=lambda f: f.ts, reverse=True)
    unresolved = [f for f in out if not f.resolved]
    resolved_l = [f for f in out if f.resolved]
    dropped = len(unresolved) - unresolved_cap
    if dropped > 0:
        # [C-H1] 미해결은 소음이 아니다 — 자를 거면 **잘랐다는 사실을 말한다**.
        print(f"⚠️  health: 미해결 실패 {len(unresolved)}건 중 {dropped}건 생략(cap {unresolved_cap})")
    return unresolved[:unresolved_cap] + resolved_l[:resolved_cap]


# ─────────────────────────── 승인·DLQ·비용 ───────────────────────────

def _approvals(now: date) -> dict:
    """승인 큐 통계 — **전량 기준**.

    [A-M6] 예전엔 `pending()` 만 셌는데 `_is_closed` 가 거부·실행완료를 거기서 빼므로
    `❌거부` 는 **어떤 입력에도 0인 죽은 카운터**였고 실행완료도 즉시 사라졌다.
    "오늘 무슨 일이 있었나"를 보여주는 표면이 통째로 비어 있었다.
    """
    d = paths.data_root() / "_approvals"
    empty = {"undecided": 0, "approved_pending_exec": 0, "today": {"approved": 0, "rejected": 0,
             "executed": 0}, "total": 0, "undecided_rows": []}
    if not d.exists():
        return empty
    try:
        from . import approval
        rows = approval._iter_queue()
    except Exception as e:                   # noqa: BLE001
        print(f"⚠️  health: 승인 큐 조회 실패 — {e!r}")
        return empty
    iso = now.isoformat()

    def _today(v) -> bool:
        return bool(v) and str(v).startswith(iso)

    und = [r for r in rows if r.get("decision") is None]
    return {
        "undecided": len(und),
        "approved_pending_exec": sum(1 for r in rows if r.get("decision") == "approved"
                                     and r.get("kind") == "mail" and not r.get("draft_id")),
        "today": {
            "approved": sum(1 for r in rows if r.get("decision") == "approved"
                            and _today(r.get("decided_at"))),
            "rejected": sum(1 for r in rows if r.get("decision") == "rejected"
                            and _today(r.get("decided_at"))),
            "executed": sum(1 for r in rows if _today(r.get("executed_at"))),
        },
        "total": len(rows),
        "undecided_rows": [{"key": r.get("key", "?"), "kind": r.get("kind", "?"),
                            "task": r.get("task", "?")} for r in und[:5]],
    }


def _dlq(now: date) -> list[dict]:
    out = []
    for p in sorted(paths.data_root().glob("*/dlq/*.jsonl")):
        try:
            n = sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip())
            size = p.stat().st_size
        except OSError:
            n, size = -1, -1
        out.append({"skill": p.parts[-3], "file": p.name, "count": n, "bytes": size})
    return out


def _alerts_today(now: date) -> list[str]:
    d = paths.data_root() / "_alerts" / "sent"
    if not d.exists():
        return []
    iso = now.isoformat()
    return sorted(p.name[: -len(iso) - 1] for p in d.glob(f"*-{iso}"))


def _costs(*, with_network: bool) -> dict:
    from . import gate
    rc = (gate.config().get("claude") or {})
    claude = {"calls": gate.calls_today("claude"),
              "warn": rc.get("daily_calls_warn"), "block": rc.get("daily_calls_block")}
    apify: dict = {"available": False}
    if with_network:
        try:
            v = gate.check("apify", quiet=True)
            apify = {"available": v.used_usd is not None, "used": v.used_usd,
                     "ceiling": v.ceiling_usd}
        except Exception as e:               # noqa: BLE001  관측은 네트워크 장애에 안 죽는다
            print(f"⚠️  health: apify 조회 실패 — {e!r}")
    return {"claude": claude, "apify": apify}


# ─────────────────────────── 진입점 ───────────────────────────

def compute(now: date | None = None, *, with_network: bool = False,
            tasks=None) -> SystemState:
    """지금(또는 지정한 `now`) 시스템 상태. **순수 읽기 · 판정의 유일한 지점.**

    `now` 를 인자로 받는 것이 핵심이다 — "30일 뒤에도 이 알림이 옳은가"를 물을 수 있어야
    백오프·유예 같은 시간 정책을 검증할 수 있다(검증 사각 ⑤).
    """
    now = now or _utc_today()
    tasks = tasks if tasks is not None else registry.load()
    sched = [t for t in tasks if t.scheduled]

    log = paths.data_root() / "_scheduler" / "launchd.log"
    try:
        touch = (datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc)
                 .isoformat(timespec="minutes"))
    except OSError:
        touch = None

    return SystemState(
        now=now,
        tasks=[_task_state(t, now) for t in sched],
        failures=_failures(now, sched),
        approvals=_approvals(now),
        dlq=_dlq(now),
        alerts_today=_alerts_today(now),
        costs=_costs(with_network=with_network),
        log_last_touch=touch,
    )
