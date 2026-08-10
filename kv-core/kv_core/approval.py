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
import os
from datetime import datetime, timezone
from pathlib import Path

from . import job, notify, paths

_KINDS = {"mail", "judgment", "summary"}
_ORIGINS = {"system", "partner"}
_KEY_OK = __import__("re").compile(r"^[A-Za-z0-9._-]{1,120}$")   # [W9 A-F9] 파일명 안전 문자만


def _assert_key(key: str) -> str:
    """dedup_key 위생 — 파일명으로 직결되므로 경로 문자를 거부한다.

    [W9 A-F9] 검증이 없던 시절 `a/b` 는 하위 디렉토리에 기록돼 pending() 의 비재귀 glob 에
    안 잡혔다(승인요청이 조용히 증발). `..` 은 큐 밖 쓰기까지 가능했다.
    """
    if not _KEY_OK.match(key or ""):
        raise ValueError(f"dedup_key 는 [A-Za-z0-9._-] 1~120자 (got {key!r})")
    return key


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
    # [W9 A-M8] **dedup_key 는 필수다.** 기본값 `f"{task}-{kind}"` 가 있던 시절, 같은 task 의
    # 서로 다른 요청(A社·B社)이 같은 key 로 뭉쳐 **큐 파일 1개**만 남았다. 게다가 반환값에
    # "신규/기존" 표시가 없어 호출부는 병합 사실조차 몰랐다 — mail 이면 수신자까지 첫 요청
    # 것으로 고정된 채 나머지가 성공처럼 보이며 증발한다. 기본값은 편의가 아니라 함정이었다.
    if not dedup_key:
        raise ValueError(
            f"dedup_key 는 필수다(task={task!r}, kind={kind!r}) — 요청을 유일하게 식별하는 "
            f"값을 주어야 한다(예: page_id). 기본값은 서로 다른 요청을 조용히 병합한다.")
    key = _assert_key(dedup_key)
    qpath = _queue_dir() / f"{key}.json"
    if qpath.exists():                                     # 멱등: 이미 만든 승인요청은 재생성 안 함
        # [W9 A-M11] 파손 레코드에 raise 하지 않는다 — `_iter_queue`/`load` 는 관용하는데
        # 여기만 맨몸 json.loads 라, 틱이 save 중 SIGKILL 된 파일 하나가 이후 매 회차의
        # request-approval 스테이지를 통째로 죽였다. 파손이면 새로 만든다(loud).
        try:
            return json.loads(qpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️  approval: 기존 레코드 파손 {key}.json — 재생성한다 ({e!r})")

    # [W9 재설계 Batch D] **파생·죽은 필드를 저장하지 않는다.**
    # 뺀 것과 이유:
    #   channel/target  `_route(kind, origin, partner)` 로 언제든 계산된다 → route_of()
    #   status          decision·draft_id 에서 계산된다 → status_of()
    #   sent            True 로 만드는 코드가 리포에 없는 죽은 필드였다(A-F12)
    #   delivered       읽는 곳이 없었다
    #   modified        W8 내내 항상 None — 무수정 측정은 W10 에서 sent-mail 대조로 한다
    # 저장된 값과 계산된 값이 공존하면 언젠가 어긋나고, 그때 사람은 저장된 쪽을 믿는다.
    record = {
        "key": key, "task": task, "kind": kind, "origin": origin, "partner": partner,
        "created": _now(), "payload": payload,
        # [W8] 결정 라벨 — 큐 파일이 곧 라벨 저장소(label-as-byproduct).
        "decision": None,      # None(미결) | "approved" | "rejected"
        "decided_at": None,
        "decided_by": None,    # "cli" | "slack:<user_id>"
        # slack_ts/slack_channel/draft_id/executed_at/execute_error 는 approvals_tick 이 나중에 써넣는다
    }

    # [W9 A-F1] **mail 은 live 여도 초안을 만들지 않는다** — draft-after-approve 원칙.
    # 예전엔 live=True 가 decision=None 상태에서 Gmail 초안을 만들었다: 거부해도 고아 초안이
    # 남고, draft_id 가드 때문에 executor 가 그 레코드를 영영 못 봤다(좀비).
    # 초안 생성 지점은 approvals_tick._execute **하나뿐**이어야 한다.
    if live:
        if channel == "gmail":
            print(f"· [{key}] mail 은 승인 후 생성된다(draft-after-approve) — 큐만 적재")
        else:
            text = payload.get("text") or payload.get("summary") \
                or json.dumps(payload, ensure_ascii=False)
            prefix = f"{target} " if target.startswith("@") else ""
            notify.slack(prefix + text)   # [Batch D] `delivered` 저장 제거 — 읽는 곳이 없었다

    job.atomic_write(qpath, json.dumps(record, ensure_ascii=False, indent=2))
    return record


def route_of(r: dict) -> tuple[str, str]:
    """(channel, target) — **저장하지 않고 계산한다.**"""
    return _route(r.get("kind", ""), r.get("origin", "system"), r.get("partner"))


def status_of(r: dict) -> str:
    """레코드 상태 — 파생값. 저장된 `status` 필드를 없앤 자리."""
    if r.get("decision") == "rejected":
        return "rejected"
    if r.get("draft_id"):
        return "executed"
    if r.get("execute_error"):
        return "execute_failed"
    if r.get("decision") == "approved":
        return "approved"
    return "drafted"


def _iter_queue() -> list[dict]:
    out: list[dict] = []
    for p in sorted(_queue_dir().glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            print(f"⚠️  approval: 레코드 파손 {p.name} — 건너뜀")
    return out


def _is_closed(r: dict) -> bool:
    """더 이상 살아있지 않은 건 — 거부됐거나, 할 일이 끝났거나.

    [W9 A-F12] 예전엔 `sent` 를 먼저 봤는데 그건 **True 로 만드는 코드가 리포에 없는 죽은
    필드**였다(= 항상 False, 즉 아무 역할도 안 하면서 스키마만 차지).
    [W9 A-M7] 그리고 세 조건 어디에도 **승인된 judgment/summary** 가 걸리지 않았다.
    judgment 는 실행이 없으므로 `draft_id` 가 영원히 안 생기고, 그래서 승인해도 pending 에
    무한 누적됐다 — A-F12 가 고치려던 "거짓 백로그"가 kind 만 바꿔 재발한 것이다.

    지금 규칙은 kind 의 의미론 그대로다:
      mail       초안이 만들어지면 끝(사람이 보내는 건 이 시스템 밖의 일)
      그 외      결정되면 끝(라벨이 목적이고 실행이 없다)
    """
    if r.get("decision") == "rejected":
        return True
    if r.get("kind") == "mail":
        return bool(r.get("draft_id"))
    return r.get("decision") is not None


def pending() -> list[dict]:
    """아직 살아있는 승인 건 — 미결 + 승인됐으나 미실행."""
    return [r for r in _iter_queue() if not _is_closed(r)]


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
    """⚠️ 전체 덮어쓰기 — **레코드를 오래 들고 있다가 저장하면 안 된다.**

    W9 리뷰 A-H1/H2 실증: `_execute` 가 스냅샷을 들고 Gmail API(초 단위)를 건넌 뒤 저장하면
    그 사이 내려진 결정(CLI 거부)이 사라진다 — 거부건에 초안이 생성됐다.
    **새 코드는 `update()` 를 쓴다.** 이 함수는 신규 생성 경로 전용으로만 남긴다.
    """
    job.atomic_write(_queue_dir() / f"{record['key']}.json",
                     json.dumps(record, ensure_ascii=False, indent=2))


def update(key: str, **fields) -> dict | None:
    """부분 갱신 — **재읽기 후 필드 병합** 저장. 락 없이 lost-update 를 막는다.

    [W9 Phase0 0-2/0-3] 락(heavy_slot)을 쓰면 유령 락 하나로 승인 전체가 영구 no-op 이
    될 수 있었다(A-H3, pid 재사용 시). 락 대신 **쓰기 창을 최소화**하는 쪽이 옳다:
    읽기→병합→원자 쓰기가 μs 단위라 실질 경합이 사라지고, 무엇보다 **다른 필드를
    덮어쓰지 않는다**(경합해도 서로의 갱신을 지우지 않는다).
    """
    r = load(key)
    if r is None:
        return None
    for k, v in fields.items():
        if v is _DELETE:
            r.pop(k, None)
        else:
            r[k] = v
    save(r)
    return r


class _Delete:
    """update(key, field=DELETE) — 필드 제거 표식."""
    def __repr__(self) -> str:
        return "DELETE"


_DELETE = _Delete()
DELETE = _DELETE


def _decision_claim_dir() -> Path:
    return paths.data("_approvals", "decisions")


def decide(key: str, decision: str, *, by: str = "cli") -> dict | None:
    """결정 기록 — **멱등(first-decision-wins)**, 경합에서도.

    ## 왜 이 모양인가 (두 번 틀렸던 자리다)

    ① 처음엔 `load → 검사 → save` 였다. 락이 없으니 실제로는 **last-write-wins** 였다
       (A-F5 실증: CLI 가 먼저 승인했는데 슬랙 거부가 덮었고, 반대 방향이면 거부가
       승인으로 덮여 초안이 생성됐다).
    ② W9 Phase0 에서 `heavy_slot` 을 붙였다가 뺐다. 붙이면 **유령 락(pid 재사용) 하나로
       모든 decide 가 영구 no-op** 이 되는 새 단일 장애점이 생겼기 때문이다(A-H3).
       그런데 빼고 나니 ①이 조용히 되살아났다 — `_synthetic_approval_decide` 는 단일
       프로세스라 통과시켰고, **동시성 랩이 8/8 전부 불일치**로 잡았다.

    ## 지금: 락이 아니라 **원자 선점(O_EXCL)**

    락은 "잡았다가 놓는 것"이라 놓지 못하면(크래시·슬립) 시스템이 멈춘다. 결정 마커는
    **한 번 생기면 영원히 남는 것**이 정상이다 — 그게 곧 "이 건은 결정됐다"는 사실이라
    회수(stale) 개념 자체가 없다. `alerts` 의 dedup 마커와 같은 관용구다.

    진 쪽은 마커 내용을 읽어 **이긴 결정을 그대로 돌려준다**. 승자가 레코드에 쓰기 전
    찰나에는 파일이 아직 미결일 수 있으므로 짧게 기다린다(수렴 대기, 최대 1초).
    """
    if decision not in ("approved", "rejected"):
        raise ValueError(f"decision 은 approved|rejected (got '{decision}')")
    _assert_key(key)
    r = load(key)
    if r is None:
        return None
    if r.get("decision") is not None:              # 빠른 경로 — 이미 결정됨
        return r

    d = _decision_claim_dir()
    d.mkdir(parents=True, exist_ok=True)
    claim = d / f"{key}.decision"
    try:
        fd = os.open(claim, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{decision}|{by}".encode())
        os.close(fd)
    except FileExistsError:
        return _await_decision(key, claim) or r    # 졌다 — 이긴 결정을 돌려준다
    return update(key, decision=decision, decided_at=_now(), decided_by=by)


def _await_decision(key: str, claim: Path, *, tries: int = 20, wait: float = 0.05) -> dict | None:
    """이긴 쪽이 레코드에 결정을 반영할 때까지 짧게 기다린다.

    마커가 진실이고 레코드는 그 반영이다. 반영 전 찰나에 `decision=None` 을 돌려주면
    호출부가 "아직 미결"로 오해한다 — 그게 경합에서 first-decision-wins 가 깨져 보이는
    이유였다. 끝내 반영이 안 되면(승자 크래시) **마커 내용으로 답한다**.
    """
    import time
    for _ in range(tries):
        r = load(key)
        if r and r.get("decision") is not None:
            return r
        time.sleep(wait)
    try:
        won, _, who = claim.read_text(encoding="utf-8").partition("|")
    except OSError:
        return None
    r = load(key)
    if r is None:
        return None
    # 승자가 쓰지 못하고 죽었다 — 마커를 근거로 우리가 반영한다(결정 자체는 승자의 것).
    print(f"⚠️  {key}: 결정 마커({won})가 레코드에 반영되지 않았다 — 마커 기준으로 복구한다")
    return update(key, decision=won, decided_at=_now(), decided_by=who or "unknown")


def undecided() -> list[dict]:
    """미결 목록 — 슬랙 announce·수거의 대상."""
    return [r for r in pending() if r.get("decision") is None]


def approved_unexecuted() -> list[dict]:
    """승인됐으나 아직 실행 안 된 mail 건 — executor 의 유일한 입력.

    kind!=mail 은 실행이 없다(decision=라벨일 뿐) — no-approval-no-execute 의 절반은
    이 필터의 kind 의미론이 담당한다.
    """
    return [r for r in _iter_queue()
            if r.get("decision") == "approved" and r.get("kind") == "mail"
            and not r.get("draft_id")]


def recipient_of(r: dict) -> str:
    """[W9 A-F2] 수신자 단일 진실 — payload.to 우선, 없으면 partner.

    스키마가 갈라져 있었다: request(kind=mail) 은 `partner` 를 필수로 검증하는데
    executor 는 `payload["to"]` 를 썼다 → payload 에 to 가 없으면 **수신자 빈 초안**이
    만들어지고 read-back 도 통과해 executed 로 확정됐다(실증). 여기 한 곳으로 모은다.
    """
    return str((r.get("payload") or {}).get("to") or r.get("partner") or "").strip()


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
    want = "approved" if args.cmd == "approve" else "rejected"
    before = load(args.key)
    if before is None:
        print(f"🔴 없음: {args.key}")
        return 1
    r = decide(args.key, want, by="cli")
    if r is None:
        # [W9 A-M5] 가드가 절반만 있었다 — rc 2 분기는 `r and` 로 막았는데 그 아래
        # `r['decision']` 는 무방비라 TypeError 로 크래시했다(레코드가 그 사이 삭제된 경우).
        print(f"🔴 {args.key}: 결정 기록 실패(레코드가 사라졌거나 읽을 수 없다)")
        return 1
    if r.get("decision") != want:
        # [W9 A-F13] "내가 결정했다"와 "이미 다른 결정이 있다"를 rc 로 구분 — 자동화가
        # rc 0 을 승인 성공으로 오인하지 않게.
        print(f"⚠️  {args.key}: 이미 {r['decision']}({r.get('decided_by')}) — 변경 없음")
        return 2
    print(f"✅ {args.key} → {r['decision']} (by {r['decided_by']}, {r['decided_at']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
