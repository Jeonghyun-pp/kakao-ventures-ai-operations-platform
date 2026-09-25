"""followup() 의 현재 동작 — ✅ 를 걷어 초안을 스레드에 올리고, 7일 무반응은 만기."""
from __future__ import annotations

import unittest

from kvlib import Fetched, ledger
from skills.linkedin import run
from tests._linkedin_fakes import Case, Notion, Slack, row

YES = {"name": "white_check_mark", "users": ["U1"]}


def posted(key="in/a", *, day="2026-09-23", page="p1", old=False):
    ledger.append("linkedin", key, run.POSTED, ch="net", **({"ts": "m1"} if old else {"slack_ts": "m1"}), parent="par", day=day, page_id=page,
                  name="A", company="Aco", brief="b", homepage="https://a.co", url="https://www.linkedin.com/in/a")


def case(replies, **more):
    return Case(notion=Notion({run.DB: [row("p1")]}), slack=Slack(replies),
                **{"_promote": lambda k, r, g=None: "created",
                   "outreach.write": lambda cards, who: Fetched.measured([{"key": c["key"], "short": "짧", "long": "길"} for c in cards]),
                   **more})


class Followup(unittest.TestCase):
    def test_yes_posts_draft_in_thread_with_sender_and_resolves(self):
        with case({"par": [{"ts": "m1", "reactions": [YES]}]}) as c:
            posted()
            rc = run.followup()
        self.assertEqual(rc, 0)
        draft = [p for p in c.slack.posts if p[2] == "par"]
        self.assertEqual(len(draft), 1)
        self.assertIn("Reviewer A 심사역 이름", draft[0][1])
        self.assertEqual([(r["ev"], r.get("by")) for r in c.rows() if r["ev"] != "posted"],
                         [("outreach", "Reviewer A"), ("resolved", "reaction_yes")])
        self.assertEqual(c.rows("resolved")[0]["promoted"], "created")

    def test_old_rows_with_slack_ts_in_ts_column_still_match(self):
        """09-25 전 장부 195줄은 슬랙 ts 가 `ts` 칸에 있다 — 읽기는 둘 다 본다."""
        with case({"par": [{"ts": "m1", "reactions": [YES]}]}) as c:
            posted(old=True)
            self.assertEqual(run.followup(), 0)
        self.assertEqual(c.rows("outreach")[0]["by"], "Reviewer A")

    def test_unknown_reviewer_leaves_sender_blank(self):
        with case({"par": [{"ts": "m1", "reactions": [{"name": "white_check_mark", "users": ["U9"]}]}]}) as c:
            posted()
            run.followup()
        self.assertIn("발신자 이름을 채워주세요", [p for p in c.slack.posts if p[2] == "par"][0][1])

    def test_no_reaction_for_seven_days_expires(self):
        with case({"par": [{"ts": "m1", "reactions": []}]}) as c:
            posted(day="2026-09-10")
            run.followup()
        self.assertEqual([(r["ev"], r.get("by")) for r in c.rows() if r["ev"] != "posted"], [("resolved", "expiry")])
        self.assertEqual([p for p in c.slack.posts if p[2] == "par"], [])

    def test_recent_post_without_reaction_keeps_waiting(self):
        with case({"par": [{"ts": "m1", "reactions": []}]}) as c:
            posted(day="2026-09-23")
            self.assertEqual(run.followup(), 0)
        self.assertEqual([r["ev"] for r in c.rows()], ["posted"])

    def test_unreadable_thread_is_not_expired(self):
        """🔴 읽기에 실패한 스레드는 만기시키지 않는다 — 망 오류 한 번에 사람이 사라지지 않게."""
        with case({}) as c:
            posted(day="2026-09-10")
            self.assertEqual(run.followup(), 1)
        self.assertEqual([r["ev"] for r in c.rows()], ["posted"])

    def test_dry_run_writes_nothing(self):
        with case({"par": [{"ts": "m1", "reactions": [YES]}]}) as c:
            posted()
            run.followup(dry_run=True)
        self.assertEqual(([r["ev"] for r in c.rows()], c.slack.posts), (["posted"], []))

    def test_nothing_waiting(self):
        with case({}) as c:
            self.assertEqual(run.followup(), 0)
        self.assertIn("기다리는 건이 없다", c.out.getvalue())


if __name__ == "__main__":
    unittest.main()
