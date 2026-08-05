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


def push(identity: str, text: str) -> bool:
    """즉시 푸시 — identity 당 하루 1회. 마커 원자 선생성 → 이긴 프로세스만 발송."""
    d = _sent_dir()
    d.mkdir(parents=True, exist_ok=True)
    marker = d / f"{identity}-{_utc_today()}"
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
    except FileExistsError:
        return False                              # 오늘 이미 발송됨 — no-op
    return notify.slack(text)


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
    n = int((config().get("auth_suspect") or {}).get("consecutive_session_errors", 3))
    tail = _usage_tail(n)
    if len(tail) >= n and all(not e.get("ok") for e in tail):
        errs = {e.get("err", "?") for e in tail}
        return (f"🔴 claude 세션 연속 {n}회 실패({', '.join(sorted(errs))}) — "
                f"인증 만료 의심. 맥에서 `claude` 로그인 상태 확인 요망.")
    return None


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
            continue                              # 시도 자체가 없으면 미배선/대기 — 다이제스트 몫
        gap = cadence.gap_days(t.cadence, where=t.name)
        try:
            la_d = _date.fromisoformat(str(la)[:10])
            ls_d = _date.fromisoformat(str(ls)[:10]) if ls else None
        except ValueError:
            continue
        behind = (la_d - ls_d).days if ls_d else 9999
        if behind >= gap * mult:
            tail = f"성공 {ls or '없음'} · 시도 {la}"
            out.append((f"task-fail-{t.name}",
                        f"🔴 {t.name}: 주기×{mult} 이상 연속 실패 의심 ({tail}) — 로그 확인 요망."))
    return out


def scan() -> list[str]:
    """3클래스 스캔 → 발송된 identity 목록. (게이트 차단은 발생 시점에 gate 가 직접 push —
    여기서는 이벤트 장부의 미발송분 보상만.)"""
    sent: list[str] = []
    msg = _auth_suspect()
    if msg and push("auth-suspect-claude", msg):
        sent.append("auth-suspect-claude")
    for ident, text in _task_failures():
        if push(ident, text):
            sent.append(ident)
    # 게이트 차단 보상: events 에 pushed=false 로 남은 오늘 건 재푸시
    ev = paths.data_root() / "_alerts" / "events.jsonl"
    if ev.exists():
        today = _utc_today()
        for ln in ev.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if (e.get("kind") == "gate_block" and not e.get("pushed")
                    and str(e.get("ts", "")).startswith(today)):
                ident = f"gate-block-{e.get('resource', '?')}"
                if push(ident, f"🔴 게이트 차단: {e.get('resource')} — {e.get('reason')}"):
                    sent.append(ident)
    return sent
