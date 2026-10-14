"""recheck — 감시 목록 원칙 · 30일 주기 · 지문 변화 → 노션 갱신 + resurfaced · 무변화는 노션 0 · dry 는 0원."""
from __future__ import annotations

import unittest
from unittest import mock

from kvlib import Fetched, ledger
from skills.linkedin import recheck, run
from tests._linkedin_fakes import Case, Notion, row


def item(pub, company, title="CEO"):
    return {"publicIdentifier": pub, "linkedinUrl": f"https://www.linkedin.com/in/{pub}", "headline": f"{title} at {company}",
            "currentPosition": [{"companyName": company, "position": title, "startDate": {"month": "Jan", "year": 2026}}],
            "experience": [], "firstName": pub, "lastName": ""}


def legacy(pid, pub, **kw):
    return row(pid, f"https://www.linkedin.com/in/{pub}", "Cut", Status="Legacy", **{"Captured At": "2026-08-01",
               "Author Username": pub, **kw})


class Fake:
    def __init__(self, items):
        self.items, self.calls = items, []

    def run_actor(self, slug, payload, **kw):
        self.calls.append((slug, payload))
        return Fetched.measured([self.items[p] for p in payload["publicIdentifiers"] if p in self.items])


def case(rows, items, **more):
    fake = Fake(items)
    c = Case(notion=Notion({run.DB: rows, run.EARLY: []}),
             **{"apify": fake, "judge.judge_people": lambda people, criteria=None: Fetched.measured(
                 [{"key": p["key"], "verdict": "Catch", "reason": "r", "raw": ""} for p in people]), **more})
    c.fake = fake
    return c


class Recheck(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(recheck, "raw_by_key", lambda: {})
        p.start()
        self.addCleanup(p.stop)

    def test_targets_principle(self):
        """국적·나이·✅·손댄 행·아직 안 나간 재고는 빼고, 그때 안 맞았던 사람(Legacy)·❌·무반응은 본다."""
        rows = [legacy("p1", "a"), legacy("p2", "b", **{"Korea Signal": ["외국인"]}), legacy("p3", "c", **{"추정 나이": 45}),
                legacy("p4", "d", **{"타깃": True}), row("p5", "https://www.linkedin.com/in/e", Status="New"),
                row("p6", "https://www.linkedin.com/in/f", Status="New", **{"Author Username": "f"}),
                row("p7", "https://www.linkedin.com/in/g", Status="New", **{"Author Username": "g"})]
        with case(rows, {}):
            ledger.append("linkedin", "in/f", run.POSTED, ts="2026-09-01T02:00:00Z", page_id="p6", parent="x", day="2026-09-01")
            ledger.append("linkedin", "in/f", run.RESOLVED, ts="2026-09-08T02:00:00Z", by="reaction_no")
            ledger.append("linkedin", "in/g", run.POSTED, ts="2026-09-01T02:00:00Z", page_id="p7", parent="x", day="2026-09-01")
            ledger.append("linkedin", "in/g", run.RESOLVED, ts="2026-09-08T02:00:00Z", by="reaction_yes")
            got = [t["page_id"] for t in recheck.targets(rows, set())]
        self.assertEqual(sorted(got), ["p1", "p6"])

    def test_thirty_days_since_last_check(self):
        rows = [legacy("p1", "a")]
        with case(rows, {}):
            ledger.append("linkedin", "in/a", run.CHECKED, page_id="p1", fp="v2|X|CEO")
            self.assertEqual(recheck.targets(rows, set()), [])

    def test_username_that_is_a_display_name_is_not_an_id(self):
        """옛 행의 Author Username 엔 표시 이름이 있다 — 넘기면 404(10/14 스모크)."""
        t = {"key": "k", "page_id": "p", "live": {"Author Username": "Minsoo Kim",
                                                   "LinkedIn URL": "https://www.linkedin.com/in/example-2a2c3ab83650"}}
        self.assertEqual(recheck.public_id(t, {}, {}), "")
        self.assertEqual(recheck.public_id(t, {"k": {"publicIdentifier": "example-person"}}, {}), "example-person")

    def test_change_refreshes_notion_and_resurfaces(self):
        rows = [legacy("p1", "a"), legacy("p2", "b")]
        with case(rows, {"a": item("a", "NewCo"), "b": item("b", "Same")}) as c:
            ledger.append("linkedin", "in/a", run.CHECKED, ts="2026-08-01T00:00:00Z", page_id="p1", fp="v2|Stealth|CEO")
            ledger.append("linkedin", "in/b", run.CHECKED, ts="2026-08-01T00:00:00Z", page_id="p2", fp="v2|Same|CEO")
            self.assertEqual(recheck.run(), 0)
        self.assertEqual([u[0] for u in c.notion.updated], ["p1"])                            # 무변화(p2)는 노션 0
        props = c.notion.updated[0][1]
        self.assertEqual((props["Status"], props["Tier"]), ("New", "Catch"))
        self.assertIn("Stealth · CEO → NewCo · CEO", props["변화 요약"])
        rs = c.rows(run.RESURFACED)
        self.assertEqual([(r["page_id"], r["old"], r["new"]) for r in rs], [("p1", "Stealth · CEO", "NewCo · CEO")])
        self.assertEqual(len(c.rows(run.CHECKED)), 4)

    def test_no_baseline_records_only(self):
        rows = [legacy("p1", "a")]
        with case(rows, {"a": item("a", "NewCo")}) as c:
            recheck.run()
        self.assertEqual((c.notion.updated, c.rows(run.RESURFACED)), ([], []))
        self.assertTrue(c.rows(run.CHECKED)[0]["baseline"])

    def test_dry_run_buys_nothing(self):
        rows = [legacy("p1", "a")]
        with case(rows, {"a": item("a", "NewCo")}) as c:
            recheck.run(dry_run=True)
        self.assertEqual((c.fake.calls, c.rows()), ([], []))

    def test_missing_profile_is_retried_next_time(self):
        rows = [legacy("p1", "a")]
        with case(rows, {}) as c:
            recheck.run()
        self.assertEqual(c.rows(run.CHECKED), [])


if __name__ == "__main__":
    unittest.main()
