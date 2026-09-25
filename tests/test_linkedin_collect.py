"""collect() 의 현재 동작 — 산 사람을 중복 제거·사후 조건·판정을 거쳐 전량 착지. 돈은 가짜다."""
from __future__ import annotations

import unittest

from kvlib import Fetched, ledger
from skills.linkedin import run
from tests._linkedin_fakes import Case, Notion


class Apify:
    def __init__(self, items):
        self.items, self.calls = items, 0

    def run_actor(self, slug, payload, *, skill, est_usd):
        self.calls += 1
        return Fetched.measured(self.items)

    def spent_today(self):
        return 0.2


def summary(it):
    return {"key": it["id"], "name": it["id"], "url": f"https://www.linkedin.com/in/{it['id']}", "public_id": it["id"],
            "headline": "h", "company": "Co", "title": "CEO", "about": "a", "axis": it.get("_axis", ""), "tenure": 3,
            "founding": 2, "gap": 0, "ft_gap": 0, "kr": [], "company_url": ""}


def case(items, judged, **more):
    ap = Apify(items)
    patches = {"apify": ap, "_backlog": lambda: 0, "_summary": summary,
               "axes.AXES": {"ax": {"every_days": 7, "pages": 1, "rewind": False, "payload": {}}},
               "axes.payload": lambda a, p: {}, "axes.reject": lambda a, it: it.get("_bad", ""),
               "profile.match_keys": lambda it: [it["id"]],
               "judge.judge_people": lambda cards, **kw: Fetched.measured([{"key": c["key"], "tier": judged[c["key"]], "reason": "r"}
                                                                     for c in cards if c["key"] in judged])}
    patches.update(more)
    return Case(notion=Notion({run.DB: []}), **patches), ap


class Collect(unittest.TestCase):
    def test_lands_new_people_and_marks_catch(self):
        c, ap = case([{"id": "a"}, {"id": "b"}], {"a": "Catch", "b": "Cut"})
        with c:
            self.assertEqual(run.collect(), 0)
        self.assertEqual(ap.calls, 1)
        self.assertEqual([p["서비스명"] for _, p in c.notion.created], ["a", "b"])
        evs = [(r["ev"], r.get("key")) for r in c.rows()]
        self.assertIn(("landed", "a"), evs)
        self.assertIn(("meeting_candidate", "a"), evs)                                     # Catch 만
        self.assertNotIn(("meeting_candidate", "b"), evs)
        self.assertEqual(c.rows("axis")[0]["next_page"], 2)                                  # 커서 전진

    def test_already_landed_key_is_skipped(self):
        c, _ = case([{"id": "a"}], {"a": "Catch"})
        with c:
            ledger.append("linkedin", "a", ledger.LANDED, page_id="old", keys=["a"])
            run.collect()
        self.assertEqual(c.notion.created, [])

    def test_post_filter_reason_is_counted_in_ledger(self):
        c, _ = case([{"id": "a", "_bad": "리크루팅"}, {"id": "b"}], {"b": "Wait"})
        with c:
            run.collect()
        self.assertEqual([p["서비스명"] for _, p in c.notion.created], ["b"])
        self.assertEqual(c.rows("post_filtered")[0]["by_reason"], {"ax/리크루팅": 1})

    def test_unjudged_person_is_not_landed_and_run_is_degraded(self):
        c, _ = case([{"id": "a"}, {"id": "b"}], {"a": "Catch"})
        with c:
            with self.assertRaises(run.Degraded):
                run.collect()
        self.assertEqual([p["서비스명"] for _, p in c.notion.created], ["a"])
        self.assertEqual(c.rows("unjudged")[0]["keys"], ["b"])

    def test_backlog_at_cap_buys_nothing(self):
        c, ap = case([{"id": "a"}], {"a": "Catch"}, _backlog=lambda: 100)
        with c:
            self.assertEqual(run.collect(), 0)
        self.assertEqual((ap.calls, c.notion.created), (0, []))

    def test_dry_run_spends_nothing(self):
        c, ap = case([{"id": "a"}], {"a": "Catch"})
        with c:
            run.collect(dry_run=True)
        self.assertEqual((ap.calls, c.rows()), (0, []))
        self.assertIn("예상 $", c.out.getvalue())


if __name__ == "__main__":
    unittest.main()
