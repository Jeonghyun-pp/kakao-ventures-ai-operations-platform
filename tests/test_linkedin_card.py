"""card() 의 현재 동작 — 기준선. 노션 행에서 5명을 고르고 헤더+스레드로 올린다."""
from __future__ import annotations

import unittest

from kvlib import Fetched, ledger
from skills.linkedin import run
from tests._linkedin_fakes import Case, Notion, Slack, row, SAT


def brief_ok(cards):
    return Fetched.measured([{"key": c["key"], "brief": f"소개 {c['name']}", "company": c["company"] or "(미상)"} for c in cards])


def case(rows, early=(), **more):
    return Case(notion=Notion({run.DB: rows, run.EARLY: [{"링크": u} for u in early]}),
                **{"brief.write": brief_ok, "axes.card_reject": lambda live: live.get("_reject", ""), **more})


class Card(unittest.TestCase):
    def test_picks_good_tiers_first_and_posts_header_plus_threads(self):
        rows = [row("p1", "https://www.linkedin.com/in/a", "Wait"), row("p2", "https://www.linkedin.com/in/b", "Catch"),
                row("p3", "https://www.linkedin.com/in/c", "Trend")]
        with case(rows) as c:
            rc = run.card(n=2)
        self.assertEqual(rc, 0)
        net = [p for p in c.slack.posts if p[0] == "net"]
        self.assertEqual([p[2] for p in net], [None, net[0] and "ts-2", "ts-2"])          # 헤더 + 스레드 답글 2 (앞의 test 채널 글은 재고 경고)
        self.assertIn("이름p2", net[1][1])                                                 # Catch 먼저
        self.assertIn("이름p3", net[2][1])
        self.assertEqual([r["ev"] for r in c.rows()], ["alerted", "carded", "posted", "carded", "posted"])
        self.assertEqual(c.rows("posted")[0]["ts"], "ts-3")                                 # 🔴 슬랙 ts 가 장부 ts 를 덮는다 (알려진 결함)

    def test_skips_already_posted_target_updated_and_early_db(self):
        rows = [row("p1", "https://www.linkedin.com/in/a"), row("p2", "https://www.linkedin.com/in/b", **{"타깃": True}),
                row("p3", "https://www.linkedin.com/in/c", **{"DB로 업데이트": True}), row("p4", "https://www.linkedin.com/in/d"),
                row("p5", "https://www.linkedin.com/in/e")]
        with case(rows, early=["https://www.linkedin.com/in/d"]) as c:
            ledger.append("linkedin", "k1", run.POSTED, page_id="p1", parent="x", ts="t", ch="net", day="2026-09-20")
            run.card(n=5)
        names = [p[1] for p in c.slack.posts if p[0] == "net" and p[2]]                    # 스레드 답글 = 사람
        self.assertEqual(len(names), 1)
        self.assertIn("이름p5", names[0])
        self.assertIn("이미 본 사람이라 뺐다", c.out.getvalue())

    def test_card_reject_reason_is_counted_not_silent(self):
        rows = [row("p1", "https://www.linkedin.com/in/a", _reject="재직 36개월 초과"), row("p2", "https://www.linkedin.com/in/b")]
        with case(rows) as c:
            run.card(n=5)
        self.assertIn("재직 36개월 초과: 1명", c.out.getvalue())
        self.assertEqual(len([p for p in c.slack.posts if p[0] == "net"]), 2)             # 헤더 + 1명

    def test_empty_pool_alerts_once_and_returns_4(self):
        with case([]) as c:
            self.assertEqual(run.card(n=5), 4)
            self.assertEqual(run.card(n=5), 4)
        self.assertEqual([p[0] for p in c.slack.posts], ["test"])                            # 알림은 test 채널, 하루 한 번
        self.assertIn("재고 소진", c.slack.posts[0][1])

    def test_low_stock_warns(self):
        rows = [row(f"p{i}", f"https://www.linkedin.com/in/{i}") for i in range(6)]
        with case(rows) as c:
            run.card(n=5)
        warn = [p for p in c.slack.posts if p[0] == "test"]
        self.assertEqual(len(warn), 1)
        self.assertIn("재고 부족", warn[0][1])

    def test_weekend_sends_nothing(self):
        with case([row("p1")], today=SAT) as c:
            self.assertEqual(run.card(n=5), 0)
        self.assertEqual(c.slack.posts, [])
        self.assertIn("주말", c.out.getvalue())

    def test_dry_run_touches_nothing(self):
        with case([row("p1")]) as c:
            run.card(n=5, dry_run=True)
        self.assertEqual((c.slack.posts, c.rows()), ([], []))

    def test_notion_unreadable_stops(self):
        with Case(notion=Notion({})) as c:
            self.assertEqual(run.card(n=5), 1)
        self.assertEqual(c.slack.posts, [])


if __name__ == "__main__":
    unittest.main()
