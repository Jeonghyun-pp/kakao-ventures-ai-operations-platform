"""appstore 기준선 — 국적 규칙의 실측 사례와 collect 흐름(가짜 손)."""
from __future__ import annotations

import unittest

from kvlib import Fetched
from skills.appstore import kr_origin, run
from tests._appstore_fakes import Case, app


class Origin(unittest.TestCase):
    """kr_origin.classify — 실측에서 나온 판정 순서(L1~L7)."""

    def test_domain_first(self):
        self.assertEqual(kr_origin.classify("Foo", "https://foo.co.kr")[0], "kr")
        self.assertEqual(kr_origin.classify("Foo", "https://foo.jp")[0], "foreign")
        self.assertEqual(kr_origin.classify("Foo", "https://foo.github.io")[0], "unknown")          # 플랫폼 도메인은 정보 없음

    def test_seller_name_signals(self):
        self.assertEqual(kr_origin.classify("주식회사 가나다")[0], "kr")
        self.assertEqual(kr_origin.classify("Foo Pte. Ltd")[0], "foreign")
        self.assertEqual(kr_origin.classify("株式会社フー")[0], "foreign")

    def test_text_and_ratio_with_korean_surname_veto(self):
        long_en = "This is an English only description. " * 10
        self.assertEqual(kr_origin.classify("Acme Labs", "", "App", long_en)[0], "foreign")          # 한글 0%
        self.assertEqual(kr_origin.classify("Minsu Kim", "", "App", long_en)[0], "unknown")       # 한국식 성씨 — veto → LLM
        self.assertEqual(kr_origin.classify("Foo", "", "앱", "짧다")[0], "unknown")                   # 신호 없음

    def test_health_warns_on_over_deletion(self):
        self.assertTrue(kr_origin.check_health([("foreign", "")] * 10, 10))
        self.assertEqual(kr_origin.check_health([("kr", "")] * 10, 10), [])


class Collect(unittest.TestCase):
    def test_lands_fresh_korean_apps_and_records_ledger(self):
        with Case([app("1"), app("2", released="2025-01-01"), app("3", seller="Foo Pte. Ltd", url="")]) as c:
            self.assertEqual(run.collect(), 0)
        self.assertEqual([p["App ID"] for p in c.notion.created], ["1"])                             # 6개월 초과·외국 제외
        row = [r for r in c.ledger if r["ev"] == "landed"][0]
        self.assertEqual((row["key"], row["origin"], row["page_id"]), ("1", "kr", "pg-1"))
        self.assertIn("6개월 초과 1 제외", c.out.getvalue())

    def test_known_apps_from_ledger_or_notion_are_skipped(self):
        from kvlib import ledger
        with Case([app("1"), app("2")], notion_rows=[{"App ID": "2"}]) as c:
            ledger.append("appstore", "1", ledger.LANDED, page_id="old")
            run.collect()
        self.assertEqual(c.notion.created, [])

    def test_unknown_origin_goes_to_llm_and_foreign_is_dropped(self):
        seen = []
        llm = lambda unknowns, criteria=None: seen.append([u["app_id"] for u in unknowns]) or Fetched.measured([{"app_id": "9", "verdict": "foreign", "reason": "LLM: x", "raw": "9\tforeign\tx"}])
        with Case([app("9", seller="Minsu Kim", url="", desc="English only text here. " * 10)], llm=llm) as c:
            run.collect()
        self.assertEqual((seen, c.notion.created), ([["9"]], []))

    def test_one_liner_is_summary_or_falls_back_to_description(self):
        with Case([app("1"), app("2", desc="첫 줄\n둘째 줄 " * 30)], liners=lambda details: {"1": "요약된 한 줄"}) as c:
            run.collect()
        got = {p["App ID"]: p["One Liner"] for p in c.notion.created}
        self.assertEqual(got["1"], "요약된 한 줄")
        self.assertTrue(got["2"].startswith("첫 줄 둘째 줄") and "\n" not in got["2"])                # 요약 실패분은 원문(줄바꿈 없이)

    def test_dry_run_writes_nothing(self):
        with Case([app("1")], dry=True) as c:
            run.collect(dry_run=True)
        self.assertEqual((c.notion.created, c.ledger), ([], []))
        self.assertIn("dry-run — 착지하지 않았다", c.out.getvalue())

    def test_chart_failure_is_degraded_not_zero(self):
        with Case([app("1")], fail_charts=("top-paid",)) as c:
            with self.assertRaises(run.Degraded):
                run.collect()
        self.assertEqual(len(c.notion.created), 1)                                                   # 산 것은 착지하고, 미상은 시끄럽게
        self.assertIn("미상 1/4", c.out.getvalue())

    def test_props_carry_origin_reason(self):
        p = run._props("1", {"chart": "top-free", "rank": 3, "genre": 6014}, app("1")["detail"], "run-x", origin=("kr", "seller_url .kr"))
        self.assertIn("국적 kr(seller_url .kr", p["Match Reason"])
        self.assertEqual((p["Entry Type"], p["Chart Rank"]), ("chart_new", 3))


if __name__ == "__main__":
    unittest.main()
