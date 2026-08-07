"""[W8 배치1] 승인 코어 합성 검증 — 라벨 스키마·decide 멱등·구스키마 호환·CLI.

외부 부작용 0 (notify 스텁·live=False만). 실행: KV_DATA=<스크래치> python …
"""
from __future__ import annotations

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
check("① 신필드 존재(decision/decided_at/modified None)",
      r1["decision"] is None and r1["decided_at"] is None and r1["modified"] is None)
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

print()
if FAIL:
    print(f"❌ 실패: {FAIL}")
    raise SystemExit(1)
print("✅ 승인 코어 전부 통과")
