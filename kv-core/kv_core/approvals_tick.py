"""승인 리컨실러 — 10분 경량 틱. [W8]

## reconciliation loop
이벤트를 실시간으로 받는 게 아니라, 주기마다 **선언(결정)과 실제(실행)의 diff** 를 메꾼다:
    announce   미결 레코드를 슬랙 카드로 (ts 저장 — 이후 반응 조회의 열쇠)
    reconcile  카드의 ✅/❌ 반응 → decision 기록 (first-decision-wins, CLI 와 경합 안전)
    execute    approved mail → Gmail 초안 생성 + read-back ("보냈다≠반영됐다")
    alerts     실패 3클래스 스캔 (kv_core.alerts)

## fast/slow 분리
무거운 스케줄러(4h)와 분리된 싼 루프(10분) — idle 이면 1초에 끝난다(duty cycle).
맥이 자면 멈추고, 깨면 밀린 반응을 수거한다 — pull 이라 놓칠 것이 없다.

## 안전 성질
발송 경로 0(초안까지만 — AST 상시검증 대상) · no-approval-no-execute(executor 입력 =
approved_unexecuted 뿐) · draft_id 가드 멱등 · 실패 보상 = decision 유지 + 다음 틱 재시도 ·
heavy_slot("approvals") 이중기동 후퇴 · 항상 exit 0(launchd 는 비0을 재시도하지 않는다).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import approval, job, slackbot

_ANNOUNCE_MAX_AGE_DAYS = 14   # 구 스키마·묵은 레코드가 설치 첫 틱에 슬랙으로 부활하는 것 방지
_RECONCILE_CAP = 30           # 틱당 반응 조회 상한 (Slack Tier3 rate 방어)


def _age_days(created: str) -> float:
    try:
        dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except ValueError:
        return 9999.0                             # 파손 created = 매우 오래된 것으로 취급(미발송)


def _card(r: dict) -> str:
    p = r.get("payload") or {}
    body = (p.get("body") or p.get("text") or "")[:200]
    return (f"⏳ 승인요청 [{r.get('kind')}] `{r['key']}` ({r.get('task')})\n"
            f"To: {p.get('to', '-')} · Subject: {p.get('subject', '-')}\n"
            f"{body}\n"
            f"✅ 승인 · ❌ 거부 (이 메시지에 이모지)")


def _announce() -> int:
    n = 0
    for r in approval.undecided():
        if r.get("slack_ts"):
            continue
        if _age_days(str(r.get("created", ""))) > _ANNOUNCE_MAX_AGE_DAYS:
            continue                              # 묵은 레코드 부활 방지
        res = slackbot.post(_card(r))
        if res is None:
            return n                              # 미설정/실패 — CLI-only, 이번 틱 전체 스킵
        r["slack_ts"], r["slack_channel"] = res
        approval.save(r)
        n += 1
    return n


def _decision_from_reactions(reactions: list[dict], only_user: str | None) -> tuple[str, str] | None:
    """reactions → (decision, by). ✅+❌ 동시면 **reject 우선**(fail-safe —
    비가역 방향으로 안 기울인다. reactions 엔 선후 타임스탬프가 없다)."""
    approvers: list[str] = []
    rejecters: list[str] = []
    for rx in reactions:
        users = rx.get("users", [])
        if only_user:
            users = [u for u in users if u == only_user]
        if not users:
            continue
        if rx.get("name") in slackbot.APPROVE_EMOJI:
            approvers += users
        elif rx.get("name") in slackbot.REJECT_EMOJI:
            rejecters += users
    if rejecters:
        return "rejected", f"slack:{rejecters[0]}"
    if approvers:
        return "approved", f"slack:{approvers[0]}"
    return None


def _reconcile() -> int:
    from . import secrets
    only_user = secrets.get("SLACK_APPROVAL_USER", required=False)   # 선택: 이 유저 반응만 인정
    n = 0
    for r in approval.undecided()[:_RECONCILE_CAP]:
        ts, ch = r.get("slack_ts"), r.get("slack_channel")
        if not ts or not ch:
            continue
        reactions = slackbot.message_reactions(ch, ts)
        if reactions is None:
            continue                              # 조회 실패 ≠ 반응 0 — 다음 틱 재시도(규칙 05)
        got = _decision_from_reactions(reactions, only_user)
        if got is None:
            continue
        decision, by = got
        if decision == "rejected" and any(rx.get("name") in slackbot.APPROVE_EMOJI
                                          for rx in reactions):
            print(f"⚠️  {r['key']}: ✅+❌ 동시 반응 — reject 우선(fail-safe) 처리")
        approval.decide(r["key"], decision, by=by)  # first-decision-wins — CLI 경합 안전
        n += 1
    return n


def _execute() -> int:
    """approved mail 실행 — **이 함수의 입력은 approved_unexecuted() 뿐이다**
    (no-approval-no-execute 를 호출 구조로 보장)."""
    from . import alerts, gmail
    n = 0
    todo = approval.approved_unexecuted()
    if not todo:
        return 0
    with job.resource_slots(["gmail"]):           # 메인 스케줄러와 gmail 자원 상호배제
        for r in todo:
            p = r.get("payload") or {}
            try:
                draft_id = gmail.create_draft(p.get("to", ""), p.get("subject", ""),
                                              p.get("body", ""))
                got = gmail.get_draft(draft_id)   # read-back — "보냈다 ≠ 반영됐다"
                if not got or got.get("id") != draft_id:
                    raise RuntimeError(f"read-back 불일치: {draft_id}")
                r["draft_id"] = draft_id
                r["executed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                r["status"] = "executed"
                r.pop("execute_error", None)
                approval.save(r)
                print(f"✅ 실행: {r['key']} → Gmail 초안 {draft_id} (발송 아님 — 사람이 Send)")
                n += 1
            except Exception as e:                # noqa: BLE001  보상: 결정 유지 + 재시도
                r["execute_error"] = f"{type(e).__name__}: {e}"
                approval.save(r)
                print(f"🔴 실행 실패(다음 틱 재시도): {r['key']} — {e!r}")
                alerts.push(f"approval-exec-{r['key']}",
                            f"🔴 승인건 실행 실패: {r['key']} — {e}")
    return n


def main(argv: list[str] | None = None) -> int:
    from datetime import datetime as _dt
    from . import alerts, secrets
    secrets.load()
    print(f"┌─ approvals tick {_dt.now(timezone.utc).isoformat(timespec='seconds')}")
    try:
        slot = job.heavy_slot("approvals")
        slot.__enter__()
    except job.SlotBusy:
        print("⚠️  다른 approvals 틱 실행 중 — 물러난다(규칙 08).")
        return 0
    try:
        a = _announce()
        d = _reconcile()
        e = _execute()
        alerted = alerts.scan()
        print(f"└─ announce {a} · decide {d} · execute {e} · alerts {len(alerted)}")
        return 0                                  # launchd 는 비0을 재시도하지 않는다
    except Exception as ex:                       # noqa: BLE001
        print(f"🔴 approvals tick 오류(다음 틱 재시도): {ex!r}")
        return 0
    finally:
        slot.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(main())
