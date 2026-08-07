"""실패 푸시 — 즉시 알아야 할 실패 3클래스 + 1일 1회 dedup. [W8]

## 다이제스트와의 분업
orrery 다이제스트 = pull 요약(하루 ~1회). 여기는 **event-driven 즉시 푸시** — 사람이 하루를
못 기다리는 것만: ①인증 열화 의심 ②게이트 차단 ③task 구조적 실패. 임계는 config/alerts.yaml.

## dedup 이 구조인 이유
같은 실패가 틱마다 울리면 늑대소년이 된다(T3 사고의 교훈). 마커 파일을 **O_EXCL 로 원자
선생성** → 성공한 프로세스만 발송 — 동시 틱의 이중 발송을 락 없이 구조로 차단.

의존 방향: alerts → {notify, paths, registry, scheduler, cadence}. gate → alerts (단방향, 순환 0).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import notify, paths

_cfg: dict | None = None


def config(force: bool = False) -> dict:
    global _cfg
    if _cfg is None or force:
        import yaml
        p = paths.repo_root() / "config" / "alerts.yaml"
        _cfg = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _cfg or {}


def _utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _sent_dir() -> Path:
    return paths.data_root() / "_alerts" / "sent"


_ID_BAD = __import__("re").compile(r"[^A-Za-z0-9._-]")


def _safe_id(identity: str) -> str:
    """[W9 B-F8] identity 는 파일명이 된다 — 경로 문자가 있으면 push 가 raise 하고,
    그 raise 가 호출부(_execute 의 except 안)에서 터져 틱 나머지를 죽였다. 위생 처리."""
    return _ID_BAD.sub("_", str(identity))[:100]


def push(identity: str, text: str) -> str:
    """즉시 푸시 — identity 당 하루 1회. 반환: "sent" | "deduped" | "failed".

    [W9 B-F2] **claim(동시성) 과 delivered(dedup) 를 분리**한다. 예전엔 마커를 먼저 만들고
    발송했는데, 웹훅이 실패해도 마커가 남아 ①당일 재시도를 막고 ②다음 날은 today 필터가
    배제해 **보상 재푸시가 실행 가능한 경로 자체가 없었다**(실증: 영구 유실).
    이제 발송 실패 시 claim 을 되돌려 다음 틱이 다시 시도한다.

    [W9 B-F6] bool 이 "dedup 스킵"과 "발송 실패" 둘 다였다 — 장부가 거짓말을 했다. 3값으로.
    """
    ident = _safe_id(identity)
    d = _sent_dir()
    d.mkdir(parents=True, exist_ok=True)
    marker = d / f"{ident}-{_utc_today()}"
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)   # claim (동시 틱 배타)
        os.close(fd)
    except FileExistsError:
        return "deduped"                          # 오늘 이미 발송됨 — no-op
    if notify.slack(text):
        marker.write_text(f'{{"delivered":true,"ts":"{datetime.now(timezone.utc).isoformat()}"}}',
                          encoding="utf-8")
        return "sent"
    marker.unlink(missing_ok=True)                # 미발송 → claim 반납(다음 틱 재시도 가능)
    # [W9 Phase0 0-5] **억제 판정은 발송 경계(notify.slack)에만 두고, 여기서는 결과를 해석한다.**
    # 처음엔 이 함수 맨 앞에서 is_suppressed() 를 검사했는데, 그러면 notify.slack 을 스텁으로
    # 갈아끼운 테스트까지 억제돼 알림 로직 자체가 검증 불가가 됐다(_synthetic_alerts 4건 전멸).
    # 킬 스위치의 목적은 "스텁을 깜빡했을 때"의 방어다 — 스텁이 걸렸으면 방어할 대상이 없다.
    # 억제와 실패를 구분하는 건 유지한다(억제를 실패로 세면 가짜 경보가 된다, B-H2).
    if notify.is_suppressed():
        print(f"  🚫 알림 억제됨(KV_NO_SEND) — {identity}: {text[:80]}")
        return "suppressed"
    return "failed"


def append_event(kind: str, **meta) -> None:
    """알림 이벤트 장부 — $KV_DATA/_alerts/events.jsonl (torn-tail 규율 = job.py 와 동일)."""
    from .job import _repair_torn_tail
    d = paths.data_root() / "_alerts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "events.jsonl"
    _repair_torn_tail(p)
    line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "kind": kind, **meta}, ensure_ascii=False)
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ─────────────────────────── 스캔 3클래스 ───────────────────────────

def _usage_tail(n: int) -> list[dict]:
    """claude 사용 로그의 마지막 n건 — 어제+오늘을 이어 읽는다(UTC 자정 경계)."""
    d = paths.data_root() / "_usage" / "claude"
    today = datetime.now(timezone.utc).date()
    lines: list[dict] = []
    for day in (today - timedelta(days=1), today):
        p = d / f"{day.isoformat()}.jsonl"
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                lines.append(json.loads(ln))
            except json.JSONDecodeError:
                continue                          # torn 줄은 무시(카운터 규율이 복구)
    return lines[-n:]


def _auth_suspect() -> str | None:
    """[W9 B-F7] **오늘 발생한 실패가 포함될 때만** 발동 — 어제의 실패로 오늘 또 울리면
    (identity 가 날짜별이라 dedup 이 못 막는다) 그게 alert fatigue 의 재생산이다."""
    n = int((config().get("auth_suspect") or {}).get("consecutive_session_errors", 3))
    tail = _usage_tail(n)
    if len(tail) < n or not all(not e.get("ok") for e in tail):
        return None
    today = _utc_today()
    if not any(str(e.get("ts", "")).startswith(today) for e in tail):
        return None                               # 전부 어제 이전 — 이미 알린 사건
    errs = {str(e.get("err") or "?")[:40] for e in tail}
    return (f"🔴 claude 세션 연속 {n}회 실패({', '.join(sorted(errs))}) — "
            f"인증 만료 의심. 맥에서 `claude` 로그인 상태 확인 요망.")


def _task_failures() -> list[tuple[str, str]]:
    """워터마크 격차 기반 — last_attempt 는 전진하는데 last_success 가 주기×N 이상 뒤짐."""
    from datetime import date as _date
    from . import cadence, registry, scheduler
    mult = int((config().get("task_failure") or {}).get("consecutive_periods", 3))
    out: list[tuple[str, str]] = []
    for t in registry.load():
        if not t.scheduled or (t.runner != "local" and not t.shadow):
            continue
        wm = scheduler.read_watermark(t)
        la, ls = wm.get("last_attempt"), wm.get("last_success")
        if not la:
            continue                              # 시도 자체가 없음(미배선) — 다이제스트 몫
        if not ls:
            # [W9 B-F3] **never-succeeded 클래스** — 예전엔 "회귀만 알린다"며 이 상태를
            # 온보딩과 같은 통에 넣어 제외했는데, 실측 결과 **매일 시도하고 매일 실패하는
            # judge 가 영원히 침묵**했다(알림 커버리지 8개 중 2개). 소음을 줄이려다 신호를
            # 죽인 것. 유예를 넉넉히 주되(기본 주기×N일) 결국은 알린다.
            first = str(wm.get("first_attempt") or la)[:10]
            try:
                days = (_date.today() - _date.fromisoformat(first)).days
            except ValueError:
                continue
            grace = cadence.freshness_limit(t) * mult
            if days >= grace:
                out.append((f"never-succeeded-{t.name}",
                            f"🔴 {t.name}: 배선 후 {days}일간 **한 번도 성공하지 못했다** "
                            f"(마지막 시도 {la}) — 배선/전제 조건 확인 요망."))
            continue
        # [W9] 신선도 한계는 선언(freshness_days) 우선 — orrery 와 같은 함수를 공유
        limit = cadence.freshness_limit(t)
        try:
            la_d = _date.fromisoformat(str(la)[:10])
            ls_d = _date.fromisoformat(str(ls)[:10])
        except ValueError:
            continue
        behind = (la_d - ls_d).days
        if behind >= limit * mult:
            tail = f"성공 {ls or '없음'} · 시도 {la}"
            out.append((f"task-fail-{t.name}",
                        f"🔴 {t.name}: 주기×{mult} 이상 연속 실패 의심 ({tail}) — 로그 확인 요망."))
    return out


def scan() -> list[str]:
    """3클래스 스캔 → **실제 발송된** identity 목록.

    [W9 B-F9] 각 클래스를 격리한다 — registry.load() 는 fail-loud 라 tasks.yaml 오타 하나가
    알림 체계 전체를 정지시켰다(그리고 상위 broad except 가 삼켜 겉으론 정상이었다).
    한 클래스가 죽어도 나머지는 돌고, 죽었다는 사실 자체가 알림이 된다.
    """
    sent: list[str] = []

    def _try(name, fn):
        try:
            return fn()
        except Exception as e:                    # noqa: BLE001
            print(f"🔴 alerts.{name} 실패: {e!r}")
            if push(f"alerts-{name}-broken", f"🔴 알림 스캔 {name} 실패: {e!r}") == "sent":
                sent.append(f"alerts-{name}-broken")
            return None

    msg = _try("auth_suspect", _auth_suspect)
    if msg and push("auth-suspect-claude", msg) == "sent":
        sent.append("auth-suspect-claude")

    for ident, text in (_try("task_failures", _task_failures) or []):
        if push(ident, text) == "sent":
            sent.append(ident)

    # 게이트 차단 보상: events 에 pushed 되지 않은 오늘 건 재푸시(claim 반납 덕에 실행 가능)
    ev = paths.data_root() / "_alerts" / "events.jsonl"
    if ev.exists():
        today = _utc_today()
        for ln in ev.read_text(encoding="utf-8").splitlines()[-500:]:   # [B-F16] tail 만
            try:
                e = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if (e.get("kind") == "gate_block" and e.get("pushed") != "sent"
                    and str(e.get("ts", "")).startswith(today)):
                ident = f"gate-block-{e.get('resource', '?')}"
                if push(ident, f"🔴 게이트 차단: {e.get('resource')} — {e.get('reason')}") == "sent":
                    sent.append(ident)
    return sent
