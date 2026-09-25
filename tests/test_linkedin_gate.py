"""문(kvlib.gate) 이 링크드인의 다섯 통로 앞에 있다 — off 면 기록만, slack 이면 슬랙 글만, on 이면 전부."""
from __future__ import annotations

import unittest

from kvlib import Fetched, ledger
from skills.linkedin import run
from tests._linkedin_fakes import Case, Notion, Slack, row
from tests.test_linkedin_card import brief_ok
from tests.test_linkedin_collect import Apify, summary
from tests.test_linkedin_followup import YES, posted

AX = {"axes.AXES": {"ax": {"every_days": 7, "pages": 1, "rewind": False, "payload": {}}}, "axes.payload": lambda a, p: {},
      "axes.reject": lambda a, it: "", "profile.match_keys": lambda it: [it["id"]]}


def card_case(level, rows):
    return Case(notion=Notion({run.DB: rows, run.EARLY: []}), cfg={"linkedin.outbound": level},
                **{"brief.write": brief_ok, "axes.card_reject": lambda live: ""})


class GateLevels(unittest.TestCase):
    def test_off_records_nothing_outward(self):
        with card_case("off", [row("p1"), row("p2", "https://www.linkedin.com/in/b")]) as c:
            self.assertEqual(run.card(n=5), 0)
        self.assertEqual((c.slack.posts, c.rows()), ([], []))                              # 슬랙도 장부도 안 건드림
        self.assertIn("이름p1", c.out.getvalue())                                           # 무엇을 올렸을지는 화면에

    def test_slack_level_posts_cards_but_buys_nothing(self):
        with card_case("slack", [row("p1")]) as c:
            run.card(n=5)
        self.assertEqual([p[0] for p in c.slack.posts if p[0] == "net"], ["net", "net"])   # 헤더 + 1명 — 슬랙 글은 나간다(재고 경고는 test 로)
        self.assertEqual(c.acts()[("in/aaa", "card")], "done")
        ap = Apify([{"id": "a"}])
        with Case(notion=Notion({run.DB: []}), cfg={"linkedin.outbound": "slack"}, apify=ap, _backlog=lambda: 0, _summary=summary,
                  **{**AX, "judge.judge_people": lambda cards, **kw: Fetched.measured([])}) as c2:
            run.collect()
        self.assertEqual((ap.calls, c2.notion.created), (0, []))                             # 🔴 돈은 안 나간다
        self.assertEqual(c2.acts()[(f"ax/p1/{ledger.today()}", "buy")], "dry")

    def test_on_buys_lands_and_records_done(self):
        ap = Apify([{"id": "a"}])
        with Case(notion=Notion({run.DB: []}), apify=ap, _backlog=lambda: 0, _summary=summary,
                  **{**AX, "judge.judge_people": lambda cards, **kw: Fetched.measured([{"key": "a", "verdict": "Wait", "reason": "r"}])}) as c:
            run.collect()
        self.assertEqual(c.acts(), {(f"ax/p1/{ledger.today()}", "buy"): "done", ("a", "land"): "done"})
        self.assertEqual(len(c.notion.created), 1)
        j = c.db.execute("SELECT run, verdict, ok, page_id, input_text!='' FROM judgments WHERE key='a'").fetchone()
        self.assertEqual(j, ("live", "Wait", 1, "pg-1", 1))                                   # 판정이 입력 줄·노션 행과 함께 저장됐다

    def test_promote_is_dry_below_on(self):
        with Case(notion=Notion({run.DB: [row("p1")], run.EARLY: []}), slack=Slack({"par": [{"ts": "m1", "reactions": [YES]}]}),
                  cfg={"linkedin.outbound": "slack"},
                  **{"outreach.write": lambda cards, who: Fetched.measured([{"key": c["key"], "short": "짧", "long": "길"} for c in cards])}) as c:
            posted()
            run.followup()
        self.assertEqual((c.acts()[("in/a", "outreach")], c.acts()[("in/a", "promote")]), ("done", "dry"))
        self.assertEqual(c.notion.created, [])
        self.assertEqual(c.rows("resolved")[0]["promoted"], "dry")


if __name__ == "__main__":
    unittest.main()
