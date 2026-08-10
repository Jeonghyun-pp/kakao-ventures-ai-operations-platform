"""[W8 배치1] 승인 코어 합성 검증 — 라벨 스키마·decide 멱등·구스키마 호환·CLI.

외부 부작용 0 (notify 스텁·live=False만). 실행: KV_DATA=<스크래치> python …
"""
from __future__ import annotations

import os
os.environ.setdefault("KV_NO_SEND", "1")   # [W9] 실발송 킬 스위치 — 스텁 누락 시의 최후 방어

import json
import os
import sys
from pathlib import Path

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import approval, notify  # noqa: E402

notify.slack = lambda *a, **k: True   # 스텁 — 실발송 0
approval.notify = notify

FAIL: list[str] = []


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


print("=== 승인 코어 합성 검증 ===\n")

# ① request 가 신필드를 넣는다 + dedup 멱등이 기존 레코드를 안 덮는다
r1 = approval.request("t1", "mail", {"subject": "s", "body": "b"},
                      partner="hello@x", dedup_key="k-mail-1")
# [W9 Batch D] `modified` 는 스키마에서 뺐다 — W8 내내 **항상 None** 이었고 읽는 곳이
# 없었다(무수정 측정은 W10 에서 sent-mail 대조로 한다). 저장된 값과 계산된 값이 공존하면
# 언젠가 어긋나므로, 쓰지 않는 필드는 자리를 비운다.
check("① 결정 필드 초기값(decision/decided_at/decided_by 전부 None)",
      r1["decision"] is None and r1["decided_at"] is None and r1["decided_by"] is None)
check("① 파생 필드는 저장하지 않는다(status/channel/target/sent/delivered/modified)",
      not ({"status", "channel", "target", "sent", "delivered", "modified"} & set(r1)),
      f"잔존={sorted({'status','channel','target','sent','delivered','modified'} & set(r1))}")
check("① 파생값은 계산으로 얻는다",
      approval.status_of(r1) == "drafted" and approval.route_of(r1)[0] == "gmail",
      f"{approval.status_of(r1)} · {approval.route_of(r1)}")
# 기존 레코드에 slack_ts 를 심어두고 재요청 — 덮이면 안 됨
r1["slack_ts"] = "123.456"
approval.save(r1)
r1b = approval.request("t1", "mail", {"subject": "다른", "body": "다른"},
                       partner="hello@x", dedup_key="k-mail-1")
check("① dedup 멱등: 기존 레코드 유지(slack_ts 보존·payload 안 바뀜)",
      r1b.get("slack_ts") == "123.456" and r1b["payload"]["subject"] == "s")

# ② decide 멱등 (first-decision-wins)
approval.decide("k-mail-1", "approved", by="cli")
r2 = approval.decide("k-mail-1", "rejected", by="slack:U1")   # 두 번째 결정 시도
check("② first-decision-wins: approved 유지",
      r2["decision"] == "approved" and r2["decided_by"] == "cli")

# ③ 구 스키마 레코드(필드 없음) → undecided 로 자연 해석
old = {"key": "k-old", "task": "t0", "kind": "judgment", "origin": "system",
       "partner": None, "channel": "slack", "target": "#team",
       "created": "2026-08-01T00:00:00Z", "status": "drafted",
       "sent": False, "delivered": False, "payload": {"text": "old"}}
approval.save(old)
und = {r["key"] for r in approval.undecided()}
check("③ 구스키마 → 미결로 해석(마이그레이션 불요)", "k-old" in und)

# ④ approved_unexecuted: judgment 는 승인돼도 실행 대상 아님 / mail 은 draft_id 없으면 대상
approval.decide("k-old", "approved")
approval.request("t1", "judgment", {"text": "j"}, dedup_key="k-j-1")
approval.decide("k-j-1", "approved")
exe = {r["key"] for r in approval.approved_unexecuted()}
check("④ mail(무 draft_id)만 실행 대상 — judgment 제외",
      "k-mail-1" in exe and "k-j-1" not in exe and "k-old" not in exe, f"exe={exe}")
r1c = approval.load("k-mail-1")
r1c["draft_id"] = "d-1"
approval.save(r1c)
check("④ draft_id 있으면 실행 대상 제외(멱등)",
      "k-mail-1" not in {r["key"] for r in approval.approved_unexecuted()})

# ⑤ CLI rc
check("⑤ CLI list rc=0", approval.main(["list"]) == 0)
check("⑤ CLI approve 없는 키 rc=1", approval.main(["approve", "ghost"]) == 1)
approval.request("t1", "judgment", {"text": "c"}, dedup_key="k-cli-1")
check("⑤ CLI approve rc=0", approval.main(["approve", "k-cli-1"]) == 0)
check("⑤ CLI 결과 반영", approval.load("k-cli-1")["decision"] == "approved")

# ── [W9 리뷰 회귀] A-F1·F2·F9·F12·F13 ──────────────────────────────────────
print()
print("[W9 리뷰 회귀]")

# A-F1: live=True 여도 mail 은 승인 전 초안을 만들지 않는다(draft-after-approve 단일 경로)
import kv_core.gmail as _gm
_drafted: list = []
_gm.create_draft = lambda *a, **k: (_drafted.append(a), "should-not-happen")[1]
r_live = approval.request("t2", "mail", {"subject": "s", "body": "b"},
                          partner="hello@x", dedup_key="k-live-1", live=True)
check("A-F1 live=True 여도 초안 생성 0 (승인 전)",
      not _drafted and not r_live.get("draft_id"), f"drafted={len(_drafted)}")

# A-F2: 수신자 단일 진실 — payload.to 없으면 partner 로 폴백(빈 초안 방지)
check("A-F2 recipient_of: payload.to 우선",
      approval.recipient_of({"payload": {"to": "a@x"}, "partner": "b@x"}) == "a@x")
check("A-F2 recipient_of: to 없으면 partner",
      approval.recipient_of({"payload": {}, "partner": "b@x"}) == "b@x")
check("A-F2 recipient_of: 둘 다 없으면 빈 문자열(호출부가 fail-loud)",
      approval.recipient_of({"payload": {}}) == "")

# A-F9: dedup_key 위생 — 경로 문자 거부 + 기본 key 에 타임스탬프 없음
try:
    approval.request("t", "judgment", {"text": "x"}, dedup_key="a/b")
    check("A-F9 경로 문자 key 거부", False, "통과해버림")
except ValueError:
    check("A-F9 경로 문자 key 거부", True)
# [W9 A-M8] **단언을 뒤집었다.** 예전엔 "기본 key 가 안정적이라 dedup 이 작동한다"가
# 정답이었는데, 바로 그 안정성이 **서로 다른 요청을 조용히 병합**했다: A社·B社 요청이
# 같은 key(`t-def-judgment`)로 뭉쳐 큐 파일 1개만 남고, 반환값에 신규/기존 표시가 없어
# 호출부는 병합 사실조차 몰랐다(mail 이면 수신자까지 첫 요청 것으로 고정).
# 기본값은 편의가 아니라 함정이었다 — 이제 dedup_key 는 필수다.
try:
    approval.request("t-def", "judgment", {"text": "1"})
    check("A-M8 dedup_key 없으면 거부", False, "기본값으로 통과해버림")
except ValueError as e:
    check("A-M8 dedup_key 없으면 거부(조용한 병합 차단)", "dedup_key" in str(e))
d1 = approval.request("t-def", "judgment", {"text": "1"}, dedup_key="t-def-a")
d2 = approval.request("t-def", "judgment", {"text": "2"}, dedup_key="t-def-b")
check("A-M8 서로 다른 key 는 서로 다른 레코드로 남는다",
      d1["key"] != d2["key"] and d1["payload"]["text"] == "1" and d2["payload"]["text"] == "2")

# A-F12: 종결된 건은 pending 에서 빠진다(무한 누적 방지)
approval.decide("k-live-1", "rejected")
keys = {r["key"] for r in approval.pending()}
check("A-F12 거부건은 pending 제외", "k-live-1" not in keys)
r_done = approval.request("t3", "mail", {"to": "x@x", "subject": "s", "body": "b"},
                          partner="x@x", dedup_key="k-done-1")
approval.decide("k-done-1", "approved")
r_done = approval.load("k-done-1"); r_done["draft_id"] = "d-1"; approval.save(r_done)
check("A-F12 실행 완료건도 pending 제외",
      "k-done-1" not in {r["key"] for r in approval.pending()})

# [W9 재설계 회귀] 결정 마커가 **화신(incarnation)** 에 묶이는가
#
# 재설계에서 first-decision-wins 를 O_EXCL 마커로 되살렸는데, 마커 이름을 key 만으로
# 지었더니 새 결함이 생겼다: 레코드가 지워졌다 같은 key 로 재생성되면 **옛 결정이
# 되살아난다**(새 건이 아무도 결정하지 않았는데 자동 거부/자동 승인).
# meeting-mail 의 dedup_key 는 page_id 라, 같은 창업자가 다시 Catch 되거나 스모크
# 뒤처리로 큐 파일을 지운 뒤면 실제로 도달하는 경로다. 프로브로 재현하고 고쳤다.
import time as _time
_K = "reincarnation"
approval.request("t", "judgment", {"text": "1차"}, dedup_key=_K)
approval.decide(_K, "rejected", by="사람1")
check("화신: 1차 결정 기록", approval.load(_K)["decision"] == "rejected")
(approval._queue_dir() / f"{_K}.json").unlink()
_time.sleep(1.1)                                  # created 는 초 단위 — 다음 초로 넘긴다
approval.request("t", "judgment", {"text": "2차(새 건)"}, dedup_key=_K)
check("화신: 재생성 직후는 미결", approval.load(_K)["decision"] is None)
_r = approval.decide(_K, "approved", by="사람2")
check("화신: 옛 결정이 되살아나지 않는다",
      _r["decision"] == "approved" and _r["decided_by"] == "사람2",
      f"{_r['decision']} by {_r.get('decided_by')}")

# A-F13: CLI 가 "이미 결정됨"을 rc 2 로 구분
check("A-F13 이미 결정된 건 approve → rc 2", approval.main(["approve", "k-live-1"]) == 2)

print()
if FAIL:
    print(f"❌ 실패: {FAIL}")
    raise SystemExit(1)
print("✅ 승인 코어 전부 통과")
