"""learn — 표본 문턱 · 사람 이름 유출 검사 · 반응 정답만 센다."""
from __future__ import annotations

import unittest
from unittest import mock

from kvlib import ledger
from skills.linkedin import learn, recheck, run
from tests._linkedin_fakes import Case


def card(key, by, name="홍길동", company="Aco", day="2026-10-01"):
    ledger.append("linkedin", key, run.POSTED, page_id=f"p-{key}", parent="x", slack_ts="t", ch="net", day=day, name=name, company=company)
    ledger.append("linkedin", key, run.RESOLVED, by=by)


class Learn(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(recheck, "raw_by_key", lambda: {})
        p.start()
        self.addCleanup(p.stop)

    def test_small_bins_are_marked_not_rated(self):
        with Case(cfg={"linkedin.learn_min_n": 15}) as c:
            for i in range(4):
                card(f"k{i}", "reaction_yes")
            card("k9", "reaction_no")
            card("k7", "expiry")                                   # 본 날(같은 스레드에 반응)의 무반응 = 안 고름
            ledger.append("linkedin", "k8", run.POSTED, page_id="p8", parent="y", slack_ts="t", ch="net", day="2026-10-01")
            ledger.append("linkedin", "k8", run.RESOLVED, by="expiry")  # 아무도 안 본 날 — 정답 아님
            rows, s = learn.table(c.db)
        self.assertEqual((s["n"], s["yes"], s["no"]), (6, 4, 2))
        self.assertTrue(all(not r["enough"] and not r["signal"] for r in rows))

    def test_leak_check_finds_labeled_people(self):
        with Case():
            card("k1", "reaction_yes", name="Example Person H", company="SampleTeam")
            self.assertEqual(learn.leaks("스텔스 2년차는 Cut. SampleTeam 같은 팀은 Catch"), ["SampleTeam"])
            self.assertEqual(learn.leaks("창업 4~12개월은 가점 (✅ 9/10)"), [])

    def test_evaluate_rebuilds_lines_and_scores_catch(self):
        """같은 입력(원본에서 다시 만든 줄)으로 기준 글마다 Catch 정밀도를 잰다."""
        items = {k: {"publicIdentifier": k, "headline": "CEO", "currentPosition": [{"companyName": "A", "position": "CEO"}],
                     "experience": [], "firstName": k} for k in ("k1", "k2", "k3")}
        def fake(lines, keys, text):
            from kvlib import Fetched
            return Fetched.measured([{"key": k, "verdict": "Catch" if (text == "A" or k == "k1") else "Wait"} for k in keys])
        with Case() as c, mock.patch.object(recheck, "raw_by_key", lambda: items), mock.patch.object(learn.judge, "judge_lines", fake):
            card("k1", "reaction_yes")
            card("k2", "expiry")
            card("k3", "expiry")
            m = learn.evaluate({"A": "A", "B": "B"})
        self.assertEqual((m["A"]["catch"], m["A"]["catch_yes"]), (3, 1))
        self.assertEqual((m["B"]["catch"], m["B"]["catch_yes"], m["B"]["agree"]), (1, 1, 3))

    def test_wilson_bounds(self):
        lo, hi = learn._wilson(9, 10)
        self.assertTrue(0.5 < lo < 0.9 < hi <= 1.0)


if __name__ == "__main__":
    unittest.main()
