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


# (구) _assert_no_send 마커 함수는 삭제 — 어디서도 호출되지 않는 데드코드였다(리뷰 D2).
# 실제 무발송 보장 = 이 모듈과 kv_core.gmail 에 send 호출이 없다는 사실이며,
# tests/_synthetic_slice.py ④ 가 AST 로 상시 검증한다.


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
        # [W8] 결정 라벨 — 큐 파일이 곧 라벨 저장소(label-as-byproduct).
        "decision": None,      # None(미결) | "approved" | "rejected"
        "decided_at": None,
        "decided_by": None,    # "cli" | "slack:<user_id>"
        "modified": None,      # W8 은 항상 None — 무수정 여부 측정은 W10(sent-mail 대조)
        # slack_ts/slack_channel/draft_id/executed_at/execute_error 는 approvals_tick 이 나중에 써넣는다
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


# ─────────────────────────── [W8] 결정(라벨) API ───────────────────────────
# 구 스키마 레코드(decision 필드 없음)는 .get("decision") → None = 미결로 자연 해석 —
# 마이그레이션 불필요(테스트가 보증).

def load(key: str) -> dict | None:
    """큐 레코드 1건. 없거나 파손이면 None (파손은 loud)."""
    p = _queue_dir() / f"{key}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️  approval: 레코드 파손 {p.name} — {e!r}")
        return None


def save(record: dict) -> None:
    job.atomic_write(_queue_dir() / f"{record['key']}.json",
                     json.dumps(record, ensure_ascii=False, indent=2))


def decide(key: str, decision: str, *, by: str = "cli") -> dict | None:
    """결정 기록 — **멱등(first-decision-wins)**: 이미 결정된 레코드는 그대로 반환.

    CLI 와 슬랙 수거가 경합해도 먼저 기록된 결정이 이긴다(재결정 없음 — 감사 기록 불변).
    """
    if decision not in ("approved", "rejected"):
        raise ValueError(f"decision 은 approved|rejected (got '{decision}')")
    r = load(key)
    if r is None:
        return None
    if r.get("decision") is not None:              # first-decision-wins
        return r
    r["decision"] = decision
    r["decided_at"] = _now()
    r["decided_by"] = by
    save(r)
    return r


def undecided() -> list[dict]:
    """미결 목록 — 슬랙 announce·수거의 대상."""
    return [r for r in pending() if r.get("decision") is None]


def approved_unexecuted() -> list[dict]:
    """승인됐으나 아직 실행 안 된 mail 건 — executor 의 유일한 입력.

    kind!=mail 은 실행이 없다(decision=라벨일 뿐) — no-approval-no-execute 의 절반은
    이 필터의 kind 의미론이 담당한다.
    """
    out = []
    for p in sorted(_queue_dir().glob("*.json")):
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if (r.get("decision") == "approved" and r.get("kind") == "mail"
                and not r.get("draft_id")):
            out.append(r)
    return out


def main(argv: list[str] | None = None) -> int:
    """CLI — 슬랙 폴백 겸 토큰 전 기본 승인 경로. `python -m kv_core.approval …`"""
    import argparse
    ap = argparse.ArgumentParser(description="승인 큐 CLI (list|show|approve|reject)")
    ap.add_argument("cmd", choices=["list", "show", "approve", "reject"])
    ap.add_argument("key", nargs="?", default=None)
    args = ap.parse_args(argv)

    if args.cmd == "list":
        rows = pending()
        if not rows:
            print("승인 큐 비어 있음")
            return 0
        for r in rows:
            d = r.get("decision") or "⏳미결"
            print(f"  [{r.get('kind')}] {r.get('key')}  {d}  ({r.get('task')}, {r.get('created')})")
        return 0
    if not args.key:
        print("🔴 key 가 필요하다")
        return 1
    if args.cmd == "show":
        r = load(args.key)
        print(json.dumps(r, ensure_ascii=False, indent=2) if r else f"🔴 없음: {args.key}")
        return 0 if r else 1
    r = decide(args.key, "approved" if args.cmd == "approve" else "rejected", by="cli")
    if r is None:
        print(f"🔴 없음: {args.key}")
        return 1
    print(f"✅ {args.key} → {r['decision']} (by {r['decided_by']}, {r['decided_at']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
