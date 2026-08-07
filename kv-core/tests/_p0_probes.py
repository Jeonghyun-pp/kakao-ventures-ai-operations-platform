"""[W9 Phase0] 재현 프로브 3종 — 리뷰가 지목한 high 결함을 **먼저 재현**하고 수정을 확인한다.

이 파일의 존재 이유: 오늘 하루의 교훈이 "수정이 새 결함을 만든다"였다. 그 방지책은
수정마다 **실패하는 재현 케이스를 먼저 갖는 것**이다. 각 프로브는 수정 전 코드에서
실패하도록 설계했다(주석에 수정 전 관측 결과를 남긴다).

실행: KV_DATA=<스크래치> python kv-core/tests/_p0_probes.py
외부 부작용 0 — gmail·slack 전부 스텁 + KV_NO_SEND.
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import json  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import alerts, approval, approvals_tick, gmail, notify, orrery, paths, slackbot  # noqa: E402

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


# ══════════════════════════════════════════════════════════════════════
print("=== A-H1: 실행 중 거부 — 결정이 스냅샷에 덮이지 않는가 ===\n")
# 수정 전: _execute 가 `load()` 한 스냅샷에 draft_id 를 붙여 `save(r)` → 그 사이 CLI 로
# 들어온 decision="rejected" 가 **통째로 사라지고** status="executed" 가 됐다.

_drafts: list[str] = []


def _stub_create(to, subject, body_text, **kw):
    _drafts.append(to)
    return f"draft-{len(_drafts)}"


gmail.create_draft = _stub_create
gmail.get_draft = lambda did, **kw: {"id": did}
notify.slack = lambda *a, **k: True
slackbot.post = lambda text: ("1.0", "C-TEST")
slackbot.message_reactions = lambda ch, ts: None
approvals_tick.slackbot = slackbot

# ── A-1. 선정 후·실행 전 거부 → 초안 0건 ──
k1 = approval.request("probe-a1", "mail",
                      {"to": "a@x", "subject": "s", "body": "b"}, partner="evan", dedup_key="a1")["key"]
approval.decide(k1, "approved", by="test")
todo = approval.approved_unexecuted()
check("A-1 준비: 실행 대상 1건", len(todo) == 1, str(len(todo)))
approval.decide(k1, "rejected", by="cli-race")     # 실행 직전 거부 (first-decision-wins 이므로
r1 = approval.load(k1)                             # approved 유지가 정상 — 아래에서 확인)
check("A-1 first-decision-wins: 승인 유지", r1["decision"] == "approved", str(r1["decision"]))

# ── A-2. 실행 도중(초안 생성 중) 거부 → 결정 필드가 살아남는가 ──
before = len(_drafts)
k2 = approval.request("probe-a2", "mail",
                      {"to": "b@x", "subject": "s", "body": "b"}, partner="evan", dedup_key="a2")["key"]
approval.decide(k2, "approved", by="test")


def _create_then_reject(to, subject, body_text, **kw):
    """초안 생성 시점에 **다른 프로세스**가 같은 레코드를 갱신하는 상황을 정확히 재현.

    일부러 구 API(load→save)를 쓴다 — 이 프로브는 수정 전 코드에서도 실행돼야
    "재현 프로브"이지, 신 API 를 쓰면 AttributeError 로 죽어 아무것도 증명하지 못한다.
    """
    other = approval.load(k2)
    other["decided_by"] = "cli-during"
    other["note"] = "이 필드는 살아남아야 한다"
    approval.save(other)
    _drafts.append(to)
    return f"draft-{len(_drafts)}"


gmail.create_draft = _create_then_reject
approvals_tick._execute()
gmail.create_draft = _stub_create
r2 = approval.load(k2)
check("A-2 실행 중 기록된 필드가 보존됨", r2.get("note") == "이 필드는 살아남아야 한다",
      f"note={r2.get('note')!r}")
check("A-2 실행 결과도 함께 기록됨", r2.get("status") == "executed" and r2.get("draft_id"),
      f"status={r2.get('status')} draft={r2.get('draft_id')}")
check("A-2 성공 시 executing_at 잔재 없음", "executing_at" not in r2,
      str(r2.get("executing_at")))

# ── A-3. decision 이 approved 가 아니면 초안을 만들지 않는다 ──
k3 = approval.request("probe-a3", "mail",
                      {"to": "c@x", "subject": "s", "body": "b"}, partner="evan", dedup_key="a3")["key"]
approval.decide(k3, "approved", by="test")
n_before = len(_drafts)
_orig_unexec = approval.approved_unexecuted


def _stale_todo():
    """리컨실러가 목록을 만든 뒤 거부가 들어온 상황 — 목록은 낡았다(구 API 로 갱신)."""
    rows = _orig_unexec()
    r = approval.load(k3)
    r["decision"] = "rejected"                     # 목록 확보 후 상태 변경
    approval.save(r)
    return rows


approval.approved_unexecuted = _stale_todo
approvals_tick._execute()
approval.approved_unexecuted = _orig_unexec
check("A-3 실행 직전 재확인으로 초안 0건", len(_drafts) == n_before,
      f"{len(_drafts) - n_before}건 생성됨")
check("A-3 거부 상태 유지", approval.load(k3)["decision"] == "rejected",
      str(approval.load(k3)["decision"]))

# ══════════════════════════════════════════════════════════════════════
print("\n=== B-H1: 킬 스위치가 알림을 파괴하지 않는가 ===\n")
# 수정 전: notify.slack 이 KV_NO_SEND 에서 True(성공)를 반환 → alerts.push 가 delivered
# 마커를 태움 → 스위치를 끈 뒤에도 **그날 그 알림은 영구 유실**(당일 재시도도 막힘).

sent_dir = paths.data_root() / "_alerts" / "sent"
_orig_slack = notify.slack
notify.slack = _orig_slack                         # 스텁 해제 — 실 경로(킬 스위치)를 탄다
import importlib  # noqa: E402
importlib.reload(notify)                           # 위에서 덮어쓴 slack 원복
alerts.notify = notify

os.environ["KV_NO_SEND"] = "1"
res = alerts.push("probe-b1", "테스트 경보")
check("B-1 억제 반환값이 'suppressed'", res == "suppressed", str(res))
markers = list(sent_dir.glob("probe-b1-*")) if sent_dir.exists() else []
check("B-1 dedup 마커를 만들지 않음", not markers, str([m.name for m in markers]))

_delivered: list[str] = []
os.environ.pop("KV_NO_SEND", None)
notify.slack = lambda text, **k: (_delivered.append(text), True)[1]
res2 = alerts.push("probe-b1", "테스트 경보")
check("B-1 스위치를 끄면 같은 날에도 발송됨", res2 == "sent" and len(_delivered) == 1,
      f"{res2} · {len(_delivered)}건")

os.environ["KV_NO_SEND"] = "1"
# 아래는 신 API(is_suppressed) 검증 — 수정 전 코드에는 없다. 프로브가 거기서 죽으면
# 뒤의 C 섹션이 아예 안 돌아 "수정 전에도 통과했는지"를 증명 못 한다(측정 자체가 막힌다).
if not hasattr(notify, "is_suppressed"):
    check("B-2/B-3 억제 판정 API 부재(수정 전 코드)", False, "is_suppressed 없음")
for bad in ("0", "false", "no", "") if hasattr(notify, "is_suppressed") else ():
    os.environ["KV_NO_SEND"] = bad
    if not notify.is_suppressed():
        continue
    check(f"B-2 KV_NO_SEND={bad!r} 를 '켜짐'으로 오해하지 않음", False, "억제됨")
    break
else:
    check("B-2 0/false/no/'' 는 억제 아님", True)
os.environ["KV_NO_SEND"] = "1"
if hasattr(notify, "is_suppressed"):
    check("B-2 KV_NO_SEND=1 은 억제", notify.is_suppressed())

    _orig_post = slackbot.post
    importlib.reload(slackbot)
    check("B-3 억제 시 SUPPRESSED 센티널 반환",
          slackbot.post("x") is getattr(slackbot, "SUPPRESSED", object()), repr(slackbot.post("x")))
    check("B-3 센티널은 falsy(기존 truthiness 검사 호환)", not slackbot.SUPPRESSED)
    slackbot.post = _orig_post

# ══════════════════════════════════════════════════════════════════════
print("\n=== C-H1: 해결된 실패가 미해결을 밀어내지 않는가 ===\n")
# 수정 전: rows.sort()[:cap=10] 이 resolved 분리보다 **앞**에 있어, 최신 해결건 12개가
# 상위 10칸을 채우면 미해결 2건이 잘려 다이제스트가 "미해결 0건"이라 답했다.

root = paths.data_root()
for i in range(12):                                # 해결됨 12건 — 더 최신(재시도 성공)
    base = f"resolvedjob-2026-08-07"
    for jid, state in ((f"{base}-{i}", "failed"), (f"{base}-{i}-r2", "completed")):
        d = root / "probe-skill" / "jobs" / jid
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(json.dumps(
            {"state": state, "updated": f"2026-08-07T1{i % 10}:00:00Z"}), encoding="utf-8")
for i in range(2):                                 # 미해결 2건 — 더 오래됨
    d = root / "probe-skill" / "jobs" / f"openjob-2026-08-05-{i}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "state.json").write_text(json.dumps(
        {"state": "failed", "updated": f"2026-08-05T0{i}:00:00Z"}), encoding="utf-8")

fails = orrery._recent_failures()
unresolved = [f for f in fails if not f.get("resolved")]
check("C-1 미해결 2건이 살아남음", len(unresolved) == 2,
      f"미해결 {len(unresolved)}건 / 전체 {len(fails)}건")
check("C-1 해결건도 cap 안에서 함께 보고됨", len(fails) - len(unresolved) > 0,
      str(len(fails) - len(unresolved)))

print("\n" + ("🔴 실패 " + ", ".join(FAIL) if FAIL else "✅ 프로브 3종 통과"))
sys.exit(1 if FAIL else 0)
