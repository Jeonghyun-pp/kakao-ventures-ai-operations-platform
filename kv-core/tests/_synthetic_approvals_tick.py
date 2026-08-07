"""[W8 배치3] 승인 리컨실러 합성 검증 — 외부 부작용 0 (slackbot·gmail·notify 전부 스텁).

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_approvals_tick.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import approval, approvals_tick, gmail, notify, slackbot  # noqa: E402

# ── 스텁 (모듈 속성 교체 — _synthetic_claude_gate 패턴) ──
notify.slack = lambda *a, **k: True
_posted: list[str] = []
_reactions: dict[tuple, list] = {}
_drafts_created: list[dict] = []


def _stub_post(text):
    _posted.append(text)
    return (f"17{len(_posted)}.000", "C-TEST")


def _stub_reactions(channel, ts):
    return _reactions.get((channel, ts))


slackbot.post = _stub_post
slackbot.message_reactions = _stub_reactions
approvals_tick.slackbot = slackbot


def _stub_create(to, subject, body_text, **kw):
    _drafts_created.append({"to": to, "subject": subject})
    return f"draft-{len(_drafts_created)}"


gmail.create_draft = _stub_create
gmail.get_draft = lambda did, **kw: {"id": did}

FAIL: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


print("=== 승인 리컨실러 합성 검증 ===\n")

# 준비: mail 2건(하나는 15일 묵음) + judgment 1건
r_new = approval.request("t", "mail", {"to": "hello@x", "subject": "s1", "body": "b1"},
                         partner="hello@x", dedup_key="m-new")
r_old = approval.request("t", "mail", {"to": "hello@x", "subject": "old", "body": "b"},
                         partner="hello@x", dedup_key="m-old")
r_old["created"] = "2026-07-01T00:00:00Z"
approval.save(r_old)
approval.request("t", "judgment", {"text": "j"}, dedup_key="j-1")

# ① announce: 신규만 발송·ts 저장, 묵은 건 미발송
n = approvals_tick._announce()
check("① announce 2건(신규 mail+judgment)·묵은 건 제외", n == 2, f"n={n}, posted={len(_posted)}")
check("① slack_ts 저장", approval.load("m-new").get("slack_ts") is not None
      and approval.load("m-old").get("slack_ts") is None)

# ② 반응 → 결정: ✅→approved / ❌→rejected / 동시→rejected(fail-safe)
ts_new = approval.load("m-new")["slack_ts"]
ts_j = approval.load("j-1")["slack_ts"]
_reactions[("C-TEST", ts_new)] = [{"name": "white_check_mark", "users": ["U1"]}]
_reactions[("C-TEST", ts_j)] = [{"name": "white_check_mark", "users": ["U1"]},
                                {"name": "x", "users": ["U2"]}]
approvals_tick._reconcile()
check("② ✅ → approved (by slack:U1)",
      approval.load("m-new")["decision"] == "approved"
      and approval.load("m-new")["decided_by"] == "slack:U1")
check("② ✅+❌ 동시 → rejected(fail-safe)", approval.load("j-1")["decision"] == "rejected")

# ③ no-approval-no-execute: 미결·거부만 있으면 gmail 0회
r_new2 = approval.load("m-new")
r_new2["decision"] = None; r_new2["decided_at"] = None; r_new2["decided_by"] = None
approval.save(r_new2)                      # 일시적으로 미결로 되돌림
before = len(_drafts_created)
approvals_tick._execute()
check("③ 미결/거부만 → gmail 호출 0 (no-approval-no-execute)",
      len(_drafts_created) == before)

# ④ approved → 실행 + read-back + 멱등
approval.decide("m-new", "approved", by="cli")
approvals_tick._execute()
r = approval.load("m-new")
check("④ 실행: 초안 1건 + draft_id·executed_at 저장",
      len(_drafts_created) == before + 1 and r.get("draft_id") and r.get("executed_at"))
approvals_tick._execute()                  # 재틱
check("④ 재틱 멱등: 추가 호출 0", len(_drafts_created) == before + 1)

# ⑤ 실행 실패 → 보상(결정 유지·에러 기록) → 복구 후 재시도 성공
approval.request("t", "mail", {"to": "hello@x", "subject": "s2", "body": "b2"},
                 partner="hello@x", dedup_key="m-fail")
approval.decide("m-fail", "approved")
_orig = gmail.create_draft
gmail.create_draft = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("일시 장애"))
approvals_tick._execute()
rf = approval.load("m-fail")
check("⑤ 실패 보상: decision 유지 + execute_error 기록",
      rf["decision"] == "approved" and "일시 장애" in rf.get("execute_error", ""))
gmail.create_draft = _orig
approvals_tick._execute()
check("⑤ 복구 후 재시도 성공", approval.load("m-fail").get("draft_id") is not None)

# ⑥ CLI-only 모드: post 가 None 이면 announce 전체 스킵(크래시 0)
slackbot.post = lambda text: None
approval.request("t", "judgment", {"text": "cli-only"}, dedup_key="j-cli")
n2 = approvals_tick._announce()
check("⑥ 토큰 미설정 → announce 스킵·크래시 0", n2 == 0)

print()
if FAIL:
    print(f"❌ 실패: {FAIL}")
    raise SystemExit(1)
print("✅ 승인 리컨실러 전부 통과")
