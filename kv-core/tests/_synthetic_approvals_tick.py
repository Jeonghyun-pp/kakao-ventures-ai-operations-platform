"""[W8 배치3] 승인 리컨실러 합성 검증 — 외부 부작용 0 (slackbot·gmail·notify 전부 스텁).

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_approvals_tick.py
"""
from __future__ import annotations

import os
os.environ.setdefault("KV_NO_SEND", "1")   # [W9] 실발송 킬 스위치 — 스텁 누락 시의 최후 방어

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

# ── [W9 리뷰 회귀] A-F2·F3·F4·F7 ───────────────────────────────────────────
print()
print("[W9 리뷰 회귀]")
slackbot.post = _stub_post          # 스텁 복구

# A-F3: 묵은 미결이 **사전순 앞자리**를 점유해도 신규 승인이 수거된다(굶주림 방지).
# 예전 결함: 상한 슬라이스가 필터보다 앞에 있어 announce 안 된 레코드가 슬롯을 먹었다.
for i in range(12):                 # 사전순으로 zzz-new 보다 앞 — 필터 순서가 틀리면 여기 막힌다
    approval.request("t", "judgment", {"text": f"old{i}"}, dedup_key=f"aaa-old-{i:02d}")
approval.request("t", "judgment", {"text": "신규"}, dedup_key="zzz-new")
approvals_tick._announce()
ts_z = approval.load("zzz-new").get("slack_ts")
check("A-F3 사전순 뒷자리 신규도 announce 됨", ts_z is not None, f"ts={ts_z}")
_reactions[("C-TEST", ts_z)] = [{"name": "white_check_mark", "users": ["U9"]}]
approvals_tick._reconcile()
check("A-F3 묵은 미결 뒤에서도 신규 승인 수거됨",
      approval.load("zzz-new")["decision"] == "approved",
      f"decision={approval.load('zzz-new')['decision']}")

# A-F3b: 상한 초과는 조용히 자르지 않고 **정체 신호로 알린다**
# [W9 재설계 Batch A] 스텁을 **끝까지 남기지 않는다**(검증 사각 ④). 예전엔 여기서 갈아끼운
# `alerts.push` 가 복구되지 않아, 이후 시나리오에서 실제로 발화한 알림(A-M9 의 executing_at
# 오탐 등)이 전부 보이지 않았다 — "테스트가 만든 전제가 결함을 가린" 정확한 사례다.
# 이제 ①원본을 보관하고 ②관찰이 끝나면 되돌리며 ③"울려선 안 되는 알림"을 음성 단언한다.
# [W9 Batch C] 개별 모듈은 이제 push 가 아니라 observe 를 부른다 — 발송 판단이 없다.
# 그래서 관찰 지점도 observe 로 옮긴다(무엇을 '사실로 남겼나'가 이 층의 계약이다).
_alerted: list = []
import kv_core.alerts as _alm
_ORIG_PUSH = _alm.push
_ORIG_OBSERVE = _alm.observe
_alm.push = lambda ident, text: (_alerted.append(ident), "sent")[1]
_alm.observe = lambda kind, ident, text, **m: _alerted.append(ident)
for i in range(approvals_tick._ANNOUNCE_CAP + 3):
    approval.request("t", "judgment", {"text": f"flood{i}"}, dedup_key=f"flood-{i:03d}")
approvals_tick._announce()
check("A-F3b 미발송 적체 → announce-backlog 알림",
      "announce-backlog" in _alerted, f"{_alerted}")

# A-F2: payload.to 없어도 partner 로 초안 생성(수신자 빈 초안 방지)
_drafts_created.clear()
approval.request("t", "mail", {"subject": "s", "body": "b"},   # payload 에 to 없음
                 partner="person-016b3a1e6ca1@example.com", dedup_key="m-nofield")
approval.decide("m-nofield", "approved")
approvals_tick._execute()
check("A-F2 payload.to 없어도 partner 로 실행(빈 수신자 0)",
      _drafts_created and _drafts_created[-1]["to"] == "person-016b3a1e6ca1@example.com",
      f"{_drafts_created[-1] if _drafts_created else None}")

# [W9 재설계 Batch A] **음성 단언** — 정상 경로가 알림을 만들어선 안 된다 (검증 사각 ④)
# A-M9 의 교훈: 실패 보상 경로는 **정상 설계 경로**인데 그때마다 "초안 중복 가능" 경보가
# 울렸다. 그런데 테스트는 스텁으로 알림을 삼켜 그 오탐을 한 번도 못 봤다.
# 이제 "이 시나리오에서 발생해선 안 되는 알림"을 명시적으로 단언한다.
_alerted.clear()
approval.request("t", "mail", {"to": "quiet@x", "subject": "s", "body": "b"},
                 partner="quiet@x", dedup_key="m-quiet")
approval.decide("m-quiet", "approved")
approvals_tick._execute()                    # 깨끗한 성공 1건
check("음성단언: 정상 성공 경로는 알림 0건", _alerted == [], f"발화={_alerted}")
check("음성단언: 성공 후 executing_at 마커 미잔존",
      approval.load("m-quiet").get("executing_at") is None,
      str(approval.load("m-quiet").get("executing_at")))

# 실패→복구 왕복도 그 자체로는 '중복 초안' 오탐을 만들지 않아야 한다
_alerted.clear()
approval.request("t", "mail", {"to": "retry@x", "subject": "s", "body": "b"},
                 partner="retry@x", dedup_key="m-retry")
approval.decide("m-retry", "approved")
_orig2 = gmail.create_draft
gmail.create_draft = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("일시 장애"))
approvals_tick._execute()                    # 1차 실패(보상 경로)
gmail.create_draft = _orig2
approvals_tick._execute()                    # 2차 성공
check("음성단언: 정상 재시도가 '중복 초안 가능' 오탐을 만들지 않음",
      not any("중복" in a for a in _alerted), f"발화={_alerted}")
check("음성단언: 재시도 성공 후 draft_id 확정", approval.load("m-retry").get("draft_id") is not None)

# A-F2b: 수신자가 아예 없으면 fail-loud (executed 확정 금지)
approval.request("t", "judgment", {"text": "x"}, dedup_key="m-norecip")
r_nr = approval.load("m-norecip"); r_nr["kind"] = "mail"; r_nr["partner"] = None
r_nr["decision"] = "approved"; approval.save(r_nr)
n_before = len(_drafts_created)
approvals_tick._execute()
check("A-F2b 수신자 없음 → 초안 0 + execute_error 기록",
      len(_drafts_created) == n_before
      and "수신자 없음" in (approval.load("m-norecip").get("execute_error") or ""))

# A-F4: 한 단계가 죽어도 alerts.scan 은 돈다(관측 격리)
_scanned = {"n": 0}
import kv_core.alerts as _al
_ORIG_SCAN = _al.dispatch
_al.dispatch = lambda *a, **k: (_scanned.__setitem__("n", _scanned["n"] + 1), [])[1]
_orig_rec = approvals_tick._reconcile
approvals_tick._reconcile = lambda: (_ for _ in ()).throw(RuntimeError("주입 장애"))
try:
    approvals_tick.main([])
finally:
    approvals_tick._reconcile = _orig_rec
    _al.dispatch = _ORIG_SCAN                # [Batch A] 스텁은 반드시 되돌린다(사각 ④)
check("A-F4 단계 실패해도 alerts.dispatch 실행됨", _scanned["n"] >= 1, f"scan 호출={_scanned['n']}")

# A-F7: created 포맷이 달라도(isoformat) announce 대상이 된다
approval.request("t", "judgment", {"text": "iso"}, dedup_key="m-iso")
r_iso = approval.load("m-iso")
r_iso["created"] = "2026-08-07T01:00:00+00:00"      # isoformat 변형
r_iso.pop("slack_ts", None)
approval.save(r_iso)
approvals_tick._announce()
check("A-F7 isoformat created 도 announce 됨(9999 뭉갬 제거)",
      approval.load("m-iso").get("slack_ts") is not None)

_alm.push = _ORIG_PUSH                       # [Batch A] 스텁 원복 — 다음 시나리오를 가리지 않는다
_alm.observe = _ORIG_OBSERVE

print()
if FAIL:
    print(f"❌ 실패: {FAIL}")
    raise SystemExit(1)
print("✅ 승인 리컨실러 전부 통과")
