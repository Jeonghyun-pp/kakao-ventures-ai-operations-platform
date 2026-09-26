"""followup — 카드 스레드의 ⭕/❌/🙋 를 노션 to do·드랍 초안·인수 기록으로. 조용하면 리마인드, 만기 없음."""
from __future__ import annotations

import unittest
from unittest import mock

from kvlib import Fetched, ledger
from skills.bplan import run
from tests._bplan_fakes import Case
from tests.test_bplan_judge import GOOD, ROW


def carded(c, row=ROW):
    """판정 → 카드가 실제로 올라간 상태로 만든다(문 on). 카드 ts 를 돌려준다."""
    run.judge_rows(c.db, [row], [row], llm=lambda crit, data: GOOD, deck=lambda u: Fetched.measured(""))
    run.act_all(c.gate, c.notion.rows)
    card = c.gate.row(row["접수키"], "card")
    return card["result"]["parent"], card["result"]["ts"]


def react(c, parent, ts, name, user="U1"):
    c.slack.replies_by_parent[parent] = [{"ts": ts, "reactions": [{"name": name, "users": [user]}]}]


class Followup(unittest.TestCase):
    def test_yes_moves_todo_to_meeting(self):
        with Case(notion=[ROW], intake=[], cfg={"bplan.outbound": "on"}) as c:
            parent, ts = carded(c)
            react(c, parent, ts, "white_check_mark")
            n = run.followup(c.gate, rows_fn=lambda: c.notion.rows)
        self.assertEqual(n, {"yes": 1})
        self.assertEqual(c.notion.updated[-1], ("p1", {"to do": "미팅요청"}))
        self.assertEqual(c.acts()[(ROW["접수키"], "todo")], "done")

    def test_no_moves_to_drop_and_drafts(self):
        with Case(notion=[ROW], intake=[("m1", "[카카오벤처스]새회사 로부터 접수된 사업계획서")], cfg={"bplan.outbound": "on"}) as c:
            ledger.append("bplan", "m1", run.LABELED, company="새회사", label="3. Evan")
            parent, ts = carded(c)
            react(c, parent, ts, "x")
            n = run.followup(c.gate, rows_fn=lambda: c.notion.rows)
        self.assertEqual(n, {"no": 1, "draft": 1})
        self.assertEqual(c.notion.updated[-1], ("p1", {"to do": "To Drop"}))
        self.assertEqual(c.gmail.drafts[0][:2], ("m1", "a@x.com"))

    def test_raising_hand_is_acknowledged_in_thread(self):
        with Case(notion=[ROW], cfg={"bplan.outbound": "slack"}) as c, \
                mock.patch.object(run.people, "load", lambda: [{"names": ["Reviewer A"], "slack_id": "U1"}]):
            parent, ts = carded(c)
            react(c, parent, ts, "raising_hand::skin-tone-3")
            n = run.followup(c.gate)
            again = run.followup(c.gate)
        self.assertEqual((n, again), ({"ack": 1}, {}))                                           # 맡았으면 끝 — 다시 안 본다
        self.assertIn("🙋 Reviewer A 님이 맡았습니다", c.slack.posts[-1][1])
        self.assertEqual(c.slack.posts[-1][2], parent)                                            # 같은 스레드

    def test_quiet_card_is_reminded_after_remind_days_up_to_max(self):
        with Case(notion=[ROW], cfg={"bplan.outbound": "slack"}) as c:
            parent, ts = carded(c)
            c.slack.replies_by_parent[parent] = [{"ts": ts, "reactions": []}]
            self.assertEqual(run.followup(c.gate), {"reminded": 0})                                 # 오늘은 아직
            c.db.execute("UPDATE actions SET created='2026-09-01T00:00:00Z'")                       # 카드가 오래됐다
            self.assertEqual(run.followup(c.gate), {"reminded": 1})
            self.assertEqual(run.followup(c.gate), {"reminded": 0})                                 # 같은 날 두 번은 안 한다
        self.assertIn("⏰ 아직 답이 없습니다", c.slack.posts[-1][1])

    def test_unreadable_thread_is_counted_not_acted(self):
        with Case(notion=[ROW], cfg={"bplan.outbound": "on"}) as c:
            carded(c)
            c.slack.replies_by_parent["_fail"] = True
            self.assertEqual(run.followup(c.gate), {"unread": 1})
        self.assertEqual(len(c.notion.updated), 1)                                                # score 만. to do 는 안 건드림


if __name__ == "__main__":
    unittest.main()
