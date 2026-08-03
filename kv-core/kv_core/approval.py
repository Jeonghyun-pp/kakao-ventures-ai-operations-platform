"""승인 브릿지 — 판단·발송 대상을 실행 전 심사역에게 초안/알림 (로드맵 W8).

## 원칙 (사용자 결정 2026-08-03)
- **제품은 발송하지 않는다.** 발송할 메일은 Gmail 초안으로만 두고, 사람이 확인 후 직접 Send(버튼).
  판단 결과는 Slack 으로 알린다. 자동 실행 없음. 완전 무인 발송은 W10.
- **출처 기반 라우팅(결정 10):** origin=system(우리가 시작) → 팀 슬랙방. origin=partner(심사역 트리거) → 그 심사역.
- **채널(결정 9 혼합):** kind=mail → Gmail 초안. kind=judgment|summary → Slack.
- **멱등·감사:** dedup_key 로 큐에 이미 있으면 재생성 안 함. 큐 파일(`$KV_DATA/_approvals/<key>.json`)이 감사 기록.

## dry vs live
기본 **dry** — 큐 파일만 쓴다(실 Slack/Gmail 안 건드림). `live=True` 에서만 notify.slack / gmail.create_draft.
Slack 인터랙티브 버튼(제품 매개 발송)은 상시 수신 엔드포인트가 필요 → 상시서버 없는 전제와 충돌 → W8/W10 후속.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import job, notify, paths

_KINDS = {"mail", "judgment", "summary"}
_ORIGINS = {"system", "partner"}


def _queue_dir() -> Path:
    return paths.data("_approvals")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _assert_no_send() -> None:
    """발송 경로가 불리면 즉시 멈춘다 — 구조로 막는다(선언 아님)."""
    raise RuntimeError("승인 브릿지는 발송하지 않는다 — 초안·알림까지만. 발송은 사람(버튼).")


def _route(kind: str, origin: str, partner: str | None) -> tuple[str, str]:
    """(channel, target) 결정. 결정 9(채널)·10(라우팅)의 구현."""
    if kind == "mail":
        return "gmail", (partner or "")                    # 발송할 메일 → Gmail 초안(수신=담당 심사역)
    # judgment | summary → Slack
    if origin == "partner":
        return "slack", (f"@{partner}" if partner else "@?")   # 웹훅 단일채널 → 텍스트에 멘션
    return "slack", "#team"


def request(task: str, kind: str, payload: dict, *,
            origin: str = "system", partner: str | None = None,
            dedup_key: str | None = None, live: bool = False) -> dict:
    """승인 요청 1건 — 초안/알림을 만들되 **발송하지 않는다**. 반환: 큐 레코드.

    kind:   'mail'(발송할 초안) | 'judgment'(판단 결과) | 'summary'
    origin: 'system'(팀 슬랙방) | 'partner'(담당 심사역)
    payload: mail 이면 {subject, body}, 그 외 {text|summary}
    """
    if kind not in _KINDS:
        raise ValueError(f"kind 는 {_KINDS} (got '{kind}')")
    if origin not in _ORIGINS:
        raise ValueError(f"origin 은 {_ORIGINS} (got '{origin}')")
    if kind == "mail" and not partner:
        raise ValueError("mail 초안은 수신 심사역(partner)이 필요하다")

    channel, target = _route(kind, origin, partner)
    key = dedup_key or f"{task}-{kind}-{_now()}"
    qpath = _queue_dir() / f"{key}.json"
    if qpath.exists():                                     # 멱등: 이미 만든 승인요청은 재생성 안 함
        return json.loads(qpath.read_text(encoding="utf-8"))

    record = {
        "key": key, "task": task, "kind": kind, "origin": origin, "partner": partner,
        "channel": channel, "target": target, "created": _now(),
        "status": "drafted", "sent": False, "delivered": False, "payload": payload,
    }

    if live:                                               # 실제 초안/알림(발송 아님)
        if channel == "gmail":
            from . import gmail
            record["draft_id"] = gmail.create_draft(
                partner, payload.get("subject", ""), payload.get("body", ""))
        else:
            text = payload.get("text") or payload.get("summary") \
                or json.dumps(payload, ensure_ascii=False)
            prefix = f"{target} " if target.startswith("@") else ""
            notify.slack(prefix + text)
        record["delivered"] = True

    job.atomic_write(qpath, json.dumps(record, ensure_ascii=False, indent=2))
    return record


def pending() -> list[dict]:
    """아직 발송(사람)되지 않은 승인 대기 목록 — 큐를 읽는다."""
    d = _queue_dir()
    out: list[dict] = []
    for p in sorted(d.glob("*.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not r.get("sent"):
            out.append(r)
    return out
