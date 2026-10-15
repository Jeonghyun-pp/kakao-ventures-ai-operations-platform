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
            "founding": 2, "gap": 0, "ft_gap": 0, "age": 30, "kr": [], "company_url": ""}


def case(items, judged, **more):
    ap = Apify(items)
    patches = {"apify": ap, "_backlog": lambda: 0, "_summary": summary,
               "axes.AXES": {"ax": {"every_days": 7, "pages": 1, "rewind": False, "payload": {}}},
               "axes.payload": lambda a, p: {}, "axes.reject": lambda a, it: it.get("_bad", ""),
               "profile.match_keys": lambda it: [it["id"]],
               "profile.nationality_input": lambda it: (it["id"], "", "", ""),
               "judge.nationality": lambda lines: Fetched.measured({k: "외국인" for k in lines if k.startswith("f-")}),
               "judge.judge_people": lambda cards, **kw: Fetched.measured([{"key": c["key"], "verdict": judged[c["key"]], "reason": "r"}
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

    def test_large_stock_still_buys(self):
        # [2026-09-29] 재고가 많다고 안 사면 카드가 늙은 재고만 돈다 — 지출 상한은 축 주기가 맡는다
        c, ap = case([{"id": "a"}], {"a": "Catch"}, _backlog=lambda: 1000)
        with c:
            self.assertEqual(run.collect(), 0)
        self.assertEqual(ap.calls, 1)

    def test_parked_axis_is_not_in_regular_run(self):
        c, ap = case([{"id": "a"}], {"a": "Catch"}, **{"axes.AXES": {
            "ax": {"every_days": 7, "pages": 1, "rewind": False, "payload": {}, "parked": "근거"}}})
        with c:
            self.assertEqual(run.collect(), 0)
        self.assertEqual(ap.calls, 0)

    def test_dry_run_spends_nothing(self):
        c, ap = case([{"id": "a"}], {"a": "Catch"})
        with c:
            run.collect(dry_run=True)
        self.assertEqual((ap.calls, c.rows()), (0, []))
        self.assertIn("예상 $", c.out.getvalue())


if __name__ == "__main__":
    unittest.main()


class Legacy(unittest.TestCase):
    """[2026-09-28·29] 외국인·40세 이상은 Tier 판정 없이 Status=Legacy 로 착지 — 재고가 아니고, 장부에 남아 다시 처리 안 된다."""

    def test_foreigner_lands_as_legacy_without_tier(self):
        judged = []
        c, _ = case([{"id": "a"}, {"id": "f-b"}], {"a": "Catch", "f-b": "Catch"},
                    **{"judge.judge_people": lambda cards, **kw: judged.append([x["key"] for x in cards]) or
                       Fetched.measured([{"key": x["key"], "verdict": "Catch", "reason": "r"} for x in cards])})
        with c:
            run.collect()
        made = {p["서비스명"]: p for _, p in c.notion.created}
        self.assertEqual(made["f-b"]["Status"], "Legacy")
        self.assertIsNone(made["f-b"]["Tier"])
        self.assertEqual(made["a"]["Status"], "New")
        self.assertEqual(judged, [["a"]])                                               # Legacy 는 Tier 판정에 안 넣는다
        self.assertEqual(c.rows("post_filtered")[0]["by_reason"], {"ax/Legacy:외국인": 1})
        self.assertIn(("landed", "f-b"), [(r["ev"], r.get("key")) for r in c.rows()])   # 다음 수집에서 seen

    def test_old_person_lands_as_legacy_and_is_not_asked_nationality(self):
        asked = []
        c, _ = case([{"id": "a", "old": 1}], {"a": "Catch"}, **{
            "profile.estimated_age": lambda it: 45 if it.get("old") else 30,
            "judge.nationality": lambda lines: asked.append(lines) or Fetched.measured({})})
        with c:
            run.collect()
        self.assertEqual([p["Status"] for _, p in c.notion.created], ["Legacy"])
        self.assertEqual(asked, [])

    def test_exempt_axis_is_not_asked(self):
        asked = []
        c, _ = case([{"id": "f-b"}], {"f-b": "Catch"}, **{
            "axes.nationality_exempt": lambda a: True,
            "judge.nationality": lambda lines: asked.append(lines) or Fetched.measured({})})
        with c:
            run.collect()
        self.assertEqual(asked, [])
        self.assertEqual([p["Status"] for _, p in c.notion.created], ["New"])

    def test_judgment_failure_drops_nobody(self):
        c, _ = case([{"id": "f-b"}], {"f-b": "Catch"}, **{"judge.nationality": lambda lines: Fetched.unknown("claude:down")})
        with c:
            run.collect()
        self.assertEqual([p["Status"] for _, p in c.notion.created], ["New"])


class Cursor(unittest.TestCase):
    def test_cursor_stops_at_last_bought_page(self):
        # 09-29: 15페이지 중 중간에 502 → 커서가 start+n 으로 넘어가 못 산 페이지를 건너뛰었다
        class Flaky(Apify):
            def run_actor(self, slug, payload, *, skill, est_usd):
                self.calls += 1
                return Fetched.unknown("502") if self.calls == 3 else Fetched.measured(self.items)
        ap = Flaky([{"id": "a"}])
        c, _ = case([{"id": "a"}], {"a": "Catch"}, apify=ap, **{"axes.AXES": {
            "ax": {"every_days": 7, "pages": 5, "rewind": False, "payload": {}}}})
        with c:
            try:
                run.collect()
            except run.Degraded:
                pass
        self.assertEqual(c.rows("axis")[-1]["next_page"], 3)                               # p1·p2 샀고 p3 실패



class Cadence(unittest.TestCase):
    """[2026-10-15] 주기는 주(월요일 기준)로 센다 — 09-29(화) 수동 실행이 10-05(월) 정기 실행을 밀어냈다."""

    def _due(self, last, today, every=7):
        with Case(notion=Notion({run.DB: []}), **{"axes.AXES": {"ax": {"every_days": every, "pages": 1, "rewind": False, "payload": {}}}}):
            ledger.append("linkedin", "ax", run.AXIS_EV, ran_on=last, next_page=2)
            return run._due("ax", today)

    def test_manual_midweek_run_does_not_skip_next_monday(self):
        self.assertTrue(self._due("2026-09-29", "2026-10-05"))      # 화 → 다음 월
        self.assertFalse(self._due("2026-10-12", "2026-10-15"))     # 같은 주

    def test_biweekly_counts_weeks(self):
        self.assertFalse(self._due("2026-10-05", "2026-10-12", 14))
        self.assertTrue(self._due("2026-10-05", "2026-10-19", 14))


class Wrap(unittest.TestCase):
    """[2026-10-15] 목록 끝에 닿으면 1페이지로 — 10-12 s-stealth 가 p19 에서 빈 페이지를 샀다."""

    def test_empty_page_rewinds_cursor(self):
        c, _ = case([], {}, **{"axes.AXES": {"ax": {"every_days": 7, "pages": 3, "rewind": False, "payload": {}}}})
        with c:
            run.collect()
        st = c.rows("axis")[-1]
        self.assertEqual((st["next_page"], st["wrapped"]), (1, True))

    def test_last_page_by_total_rewinds_cursor(self):
        items = [{"id": "a", "_meta": {"pagination": {"totalElements": 30}}}, {"id": "b"}]
        c, ap = case(items, {"a": "Catch", "b": "Catch"}, **{"axes.AXES": {"ax": {"every_days": 7, "pages": 5, "rewind": False, "payload": {}}}})
        with c:
            run.collect()
        self.assertEqual(ap.calls, 2)                                                      # 30명 = 2페이지, 3번째는 안 산다
        self.assertEqual(c.rows("axis")[-1]["next_page"], 1)
        self.assertEqual([p["서비스명"] for _, p in c.notion.created], ["a", "b"])           # 마지막 페이지 사람도 착지
