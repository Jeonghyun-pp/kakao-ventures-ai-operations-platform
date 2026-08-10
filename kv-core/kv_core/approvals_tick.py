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
_ANNOUNCE_CAP = 30            # [W9 A-F8] announce 에도 상한(예전엔 reconcile 에만 있어 비대칭)
_RECONCILE_CAP = 100          # 틱당 반응 조회 상한 — Slack Tier3 실한도(분당 50+)·10분 틱 기준 여유


def _now_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _age_days(created: str) -> float:
    """레코드 나이(일). [W9 A-F7] 파싱 실패 = **신규 취급(0)** + loud.

    예전엔 9999 를 돌려 조용히 미발송했는데, 리포가 `%Y-%m-%dT%H:%M:%SZ` 와 `isoformat()`
    두 포맷을 혼용해서 **정상 레코드가 영구히 슬랙에 안 뜨는** 사고가 됐다(실증).
    모르면 새 것으로 보고 알리는 쪽이 안전하다 — 묵은 것 한 번 더 알리는 비용 < 승인 유실.
    """
    for parse in (lambda s: datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
                  lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))):
        try:
            dt = parse(created)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - dt).total_seconds() / 86400
        except (ValueError, TypeError, AttributeError):
            continue
    print(f"⚠️  created 파싱 실패({created!r}) — 신규로 취급해 announce 한다")
    return 0.0


def _card(r: dict) -> str:
    p = r.get("payload") or {}
    body = (p.get("body") or p.get("text") or "")[:200]
    return (f"⏳ 승인요청 [{r.get('kind')}] `{r['key']}` ({r.get('task')})\n"
            f"To: {p.get('to', '-')} · Subject: {p.get('subject', '-')}\n"
            f"{body}\n"
            f"✅ 승인 · ❌ 거부 (이 메시지에 이모지)")


def _announce() -> int:
    """미결 레코드를 슬랙 카드로. [W9 A-F8] 한 건 실패가 다른 건을 막지 않는다(continue).

    [W9 B-F4] 연속 실패가 임계를 넘으면 **웹훅 경로로 에스컬레이션** — 봇 토큰이 죽으면
    승인 카드가 무한 침묵하고 사람은 "요청이 없구나"로 읽는다(미도착 ≠ 요청 0건, 규칙 05).
    """
    n = fail = 0
    # [W9 A-F3 동형] **필터 먼저, 상한은 그 뒤** — 상한을 앞에 두면 이미 announce 된
    # 레코드가 슬롯을 먹어 신규가 영원히 안 뜬다(reconcile 에서 고친 바로 그 함정을
    # announce 에 새로 만들었다가 테스트가 잡았다. 같은 실수는 같은 모양으로 반복된다).
    # 정렬은 **최신 우선**(created 역순) — reconcile 과 같은 원칙이다. 사전순이면 새 요청이
    # 뒤로 밀려 상한에 걸리고, 그게 정확히 A-F3 굶주림의 announce 판이다(테스트가 잡았다).
    pending_new = sorted((r for r in approval.undecided()
                          if not r.get("slack_ts")
                          and _age_days(str(r.get("created", ""))) <= _ANNOUNCE_MAX_AGE_DAYS),
                         key=lambda r: str(r.get("created", "")), reverse=True)
    targets = pending_new[:_ANNOUNCE_CAP]
    if len(pending_new) > _ANNOUNCE_CAP:
        # 상한을 올려 전부 쏘는 건 스팸이고, 조용히 자르는 건 굶주림이다 —
        # **초과 자체를 정체 신호로 알린다**(카드는 다음 틱에 이어서 나간다).
        from . import alerts
        alerts.observe("announce_backlog", "announce-backlog",
                       f"⚠️ 미발송 승인요청 {len(pending_new)}건 > 틱 상한 {_ANNOUNCE_CAP} — "
                       f"승인 정체 의심. `python -m kv_core.approval list` 로 확인 요망.",
                       pending=len(pending_new))
    for r in targets:
        res = slackbot.post(_card(r))
        if res is slackbot.SUPPRESSED:
            continue          # [0-6] 억제는 실패가 아니다 — 경보를 만들지 않는다(A-M10)
        if res is None:
            fail += 1
            continue                              # [A-F8] head-of-line blocking 제거
        # [W9 Phase0 0-3] 스냅샷 전체 저장 금지 — post 가 15초 걸리는 동안 내려진 결정을
        # 지웠다(A-H2 실증). 부분 갱신만 한다.
        approval.update(r["key"], slack_ts=res[0], slack_channel=res[1])
        n += 1
    if fail:
        from . import alerts
        alerts.observe("slackbot_down", "slackbot-down",
                       f"🔴 승인 카드 발송 실패 {fail}건 — 봇 토큰/채널 확인 요망. "
                       f"승인은 CLI 로 가능: python -m kv_core.approval list", failed=fail)
    return n


def _decision_from_reactions(reactions: list[dict], only_user: str | None) -> tuple[str, str] | None:
    """reactions → (decision, by). ✅+❌ 동시면 **reject 우선**(fail-safe —
    비가역 방향으로 안 기울인다. reactions 엔 선후 타임스탬프가 없다).

    [W9 A-F10] 인가는 **비대칭**이다: 승인(비가역 방향)은 화이트리스트가 있으면 그 사람만,
    거부(안전 방향)는 누구나 당길 수 있다. 안전 스위치를 잠그는 건 안전하지 않다.
    """
    approvers: list[str] = []
    rejecters: list[str] = []
    for rx in reactions:
        users = rx.get("users", [])
        if not users:
            continue
        if rx.get("name") in slackbot.APPROVE_EMOJI:
            allowed = [u for u in users if u == only_user] if only_user else users
            approvers += allowed
        elif rx.get("name") in slackbot.REJECT_EMOJI:
            rejecters += users                    # 거부는 무필터(누구나)
    if rejecters:
        return "rejected", f"slack:{rejecters[0]}"
    if approvers:
        return "approved", f"slack:{approvers[0]}"
    return None


def _reconcile() -> int:
    from . import secrets
    only_user = secrets.get("SLACK_APPROVAL_USER", required=False)   # 선택: 승인 화이트리스트
    n = 0
    # [W9 A-F3] 굶주림 방지 — 두 가지를 고쳤다:
    #   ① **필터 먼저, 상한은 그 뒤** (예전엔 슬라이스가 앞이라 announce 못 한 레코드가 슬롯 점유)
    #   ② **최신 우선 정렬 + 넉넉한 상한** — 사람이 방금 누른 ✅ 가 가장 먼저 잡혀야 한다.
    #      상한은 Slack Tier3 실한도(분당 50+)에 맞춘 값이라 사실상 전량 순회한다.
    #      미결이 이 상한을 넘게 쌓이면 그건 승인 정체 자체가 이상 신호다(다이제스트가 표면화).
    pollable = sorted((r for r in approval.undecided()
                       if r.get("slack_ts") and r.get("slack_channel")),
                      key=lambda r: str(r.get("slack_ts")), reverse=True)
    targets = pollable[:_RECONCILE_CAP]
    if len(pollable) > _RECONCILE_CAP:
        print(f"⚠️  미결 {len(pollable)}건 > 틱 상한 {_RECONCILE_CAP} — 오래된 건은 다음 틱")
    for r in targets:
        reactions = slackbot.message_reactions(r["slack_channel"], r["slack_ts"])
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
    (no-approval-no-execute 를 호출 구조로 보장).

    [W9] 네 가지 정정: ①수신자는 approval.recipient_of 단일 진실(빈 값이면 fail-loud —
    예전엔 To 빈 초안이 executed 로 확정됐다) ②create_draft 전에 의도 마커(`executing_at`)를
    먼저 저장해 크래시 창의 중복 초안을 탐지 가능하게 ③실패 시 부분 기록(draft_id 만 남는
    좀비)을 만들지 않음 ④SlotBusy 는 여기서 잡아 틱 전체를 죽이지 않음.
    """
    from . import alerts, gmail
    n = 0
    todo = approval.approved_unexecuted()
    if not todo:
        return 0
    # [W9 Phase0 0-1] `resource_slots(["gmail-inbox"])` 제거 — 그 자원을 선언한 task
    # (mail-triage·bplan)는 전부 **미배선**이라 지키는 게 없었고, 유령 락 하나로 실행이
    # 영구 침묵할 위험만 순증했다(A-H4 실증: 3틱 연속 execute 0·알림 0).
    for r in todo:
        key = r["key"]
        p = r.get("payload") or {}
        to = approval.recipient_of(r)                     # [A-F2] 단일 진실
        try:
            if not to:
                raise RuntimeError("수신자 없음 — payload.to/partner 둘 다 비어 있다")
            # [W9 Phase0 0-3] 갱신은 항상 재읽기·병합 — 스냅샷 저장은 결정 유실을 만든다(A-H1)
            fresh = approval.update(key, executing_at=_now_z())   # 의도 마커 먼저
            if fresh is None or fresh.get("decision") != "approved":
                # 실행 직전 재확인 — 그 사이 거부됐으면 실행하지 않는다(no-approval-no-execute)
                print(f"⚠️  {key}: 실행 직전 결정이 바뀌었다({fresh and fresh.get('decision')}) — 건너뜀")
                continue
            draft_id = gmail.create_draft(to, p.get("subject", ""), p.get("body", ""))
            got = gmail.get_draft(draft_id)              # read-back — "만들었다 ≠ 반영됐다"
            if not got or got.get("id") != draft_id:
                raise RuntimeError(f"read-back 불일치: {draft_id}")
            approval.update(key, draft_id=draft_id, executed_at=_now_z(), status="executed",
                            execute_error=approval.DELETE,
                            executing_at=approval.DELETE)   # [A-M9] 성공하면 마커를 남기지 않는다
            print(f"✅ 실행: {key} → Gmail 초안 {draft_id} (To: {to} · 발송 아님)")
            n += 1
        except Exception as e:                # noqa: BLE001  보상: 결정 유지 + 재시도
            # [A-M9] create_draft 이전 실패면 초안이 없으므로 마커도 지운다 — 다음 틱의
            # "중복 초안 가능" 오탐을 만들지 않는다(정상 재시도가 경보가 되면 늑대소년).
            pre_draft = isinstance(e, RuntimeError) and "수신자 없음" in str(e)
            approval.update(key, execute_error=f"{type(e).__name__}: {e}",
                            draft_id=approval.DELETE,
                            **({"executing_at": approval.DELETE} if pre_draft else {}))
            print(f"🔴 실행 실패(다음 틱 재시도): {key} — {e!r}")
            alerts.observe("approval_exec_failed", f"approval-exec-{key}",
                           f"🔴 승인건 실행 실패: {key} — {e}", key=key)
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
        # [W9 A-F4] 단계별 격리 — 한 단계의 예외가 나머지(특히 alerts.scan)를 삼키면
        # "실패했는데 아무도 모르는" 상태가 된다. 그리고 틱 실패 자체를 에스컬레이션한다
        # (예전엔 launchd.log 한 줄이 유일한 흔적이었고 exit 0 이라 겉으론 정상이었다).
        counts: dict[str, int] = {}
        errs: list[str] = []
        for name, fn in (("announce", _announce), ("decide", _reconcile), ("execute", _execute)):
            try:
                counts[name] = fn()
            except Exception as ex:               # noqa: BLE001
                counts[name] = -1
                errs.append(f"{name}: {ex!r}")
                print(f"🔴 approvals tick [{name}] 오류(다음 틱 재시도): {ex!r}")
        try:
            # [Batch C] 알릴지 말지를 정하는 **유일한 지점**. 앞 단계 실패와 무관하게 돈다 —
            # 그리고 이 틱에서 observe 된 사실도 여기서 함께 집어간다(kind 특례 없음).
            alerted = alerts.dispatch()
        except Exception as ex:                   # noqa: BLE001
            alerted = []
            errs.append(f"alerts.scan: {ex!r}")
            print(f"🔴 alerts.scan 오류: {ex!r}")
        if errs:
            alerts.observe("tick_error", "approvals-tick-error",
                           "🔴 승인 틱 단계 실패 — " + " · ".join(errs)[:400], errors=errs)
        print(f"└─ announce {counts.get('announce')} · decide {counts.get('decide')} "
              f"· execute {counts.get('execute')} · alerts {len(alerted)}")
        return 0                                  # launchd 는 비0을 재시도하지 않는다
    finally:
        slot.__exit__(None, None, None)


if __name__ == "__main__":
    raise SystemExit(main())
