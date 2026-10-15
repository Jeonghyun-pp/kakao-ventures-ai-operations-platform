"""실제 기록 재생 — 2026-09-03~09-21 계획 파일에서 얼린 판정을 지금 코드에 다시 넣는다.

자료는 리포 밖에 있다(제목·주소가 들어 있어서). 없으면 건너뛰고 **그 사실을 말한다.**

    재는 것    규칙 경로: 기록된 (발신·제목 + 기록된 근거가 가리키는 신호) → 같은 카테고리·같은 근거
              LLM 경로: 같은 (발신·제목) 이 규칙에 안 걸린다
              라우팅:   기록된 (카테고리·근거) → 같은 행동·수신자·템플릿 본문·메모
    못 재는 것 본문·수신자 수·헤더는 계획 파일에 없다. 규칙 경로의 입력은 **기록된 근거에서 되짚은 최소 신호**다.
              그래서 "그 신호가 있으면 그 판정이 나온다"까지만 말한다
"""
from __future__ import annotations

import json
import re
import unittest

from kvlib import paths
from skills.mail import route, rules

FIXTURE = paths.data("mail", "fixtures") / "recorded_decisions_20260903_20260921.json"
# 🔴 녹음 뒤에 사용자가 정책을 바꾼 갈래 — 옛 행동을 재현하면 오히려 틀린다. 새 행동은 test_mail_route 가 잰다.
#    2026-10-08 INBOUND_EN 폐지 · 출자/채용/폼 오류/사칭 아카이브 → 슬랙 공유 · 2026-10-13 제휴 → 7일 뒤 거절
CHANGED = {rules.INBOUND_EN, rules.RECRUIT, rules.FORM_ERROR, rules.IMPERSONATION, rules.PARTNERSHIP}   # LP_CUSTODY 는 10-15 에 녹음과 같은 아카이브로 돌아왔다
COMPARE = ("action", "to", "body", "note", "also_reply", "kind", "ch", "lead", "unknown")     # ask 는 09-21 에 '무반응 7일' 문구를 뺐다 — 녹음은 옛 문구


def _load() -> list[dict]:
    if not FIXTURE.exists():
        return []
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]


def _signal(why: str) -> dict:
    """기록된 근거가 가리키는 최소 입력."""
    if why == "List-Unsubscribe 헤더":
        return {"list_unsubscribe": True}
    m = re.match(r"수신자 (\d+)명", why)
    if m:
        return {"recipient_count": int(m.group(1))}
    m = re.match(r"사칭·검증 문의 신호 '(.+)'", why)
    if m:
        return {"body": f"… {m.group(1)} …"}
    return {}


@unittest.skipUnless(FIXTURE.exists(), f"고정 자료가 없다: {FIXTURE} — 기록 재생을 건너뛴다")
class Recorded(unittest.TestCase):
    rows = _load()

    def test_fixture_is_not_empty(self):
        self.assertGreater(len(self.rows), 100)

    def test_rule_path_reproduces(self):
        n = 0
        for r in self.rows:
            if r["path"] != "rule":
                continue
            m = {"subject": r["subject"], "sender": r["sender"], "body": "",
                 "recipient_count": 1, "list_unsubscribe": False, **_signal(r["why"])}
            hit = rules.classify(m)
            self.assertIsNotNone(hit, r["subject"][:40])
            self.assertEqual((hit["category"], hit["why"]), (r["category"], r["why"]), r["subject"][:40])
            n += 1
        self.assertGreater(n, 50)

    def test_llm_path_was_not_caught_by_rules(self):
        n = 0
        for r in self.rows:
            # 웹플로우 변환 행은 원래 발신이 webflow 라 규칙에 걸린 뒤 넘어간 것 — 여기서 제외
            if r["path"] != "llm" or r["webflow"]:
                continue
            m = {"subject": r["subject"], "sender": r["sender"], "body": "",
                 "recipient_count": 1, "list_unsubscribe": False}
            self.assertIsNone(rules.classify(m), r["subject"][:40])
            n += 1
        self.assertGreater(n, 50)

    def test_routing_reproduces(self):
        n = 0
        for r in self.rows:
            if r["deduped"]:
                continue          # 수신자 중복으로 사후에 바뀐 행 — test_mail_route 가 따로 잰다
            if r["category"] in CHANGED:
                continue
            m = {"id": "x", "thread_id": "t", "subject": r["subject"], "sender": r["sender"], "body": ""}
            act = route.decide(m, {"category": r["category"], "why": r["why"]})
            for k in COMPARE:
                if act.get("action") == "forward" and k in ("to", "body"):
                    continue                                       # 포워드는 이제 메일 초안이 아니라 슬랙 게시다 — 수신 주소·본문이 없다
                self.assertEqual(act.get(k), r["expect"].get(k), f"{k} · {r['category']} · {r['subject'][:30]}")
            n += 1
        self.assertGreater(n, 100)


if __name__ == "__main__":
    unittest.main()
