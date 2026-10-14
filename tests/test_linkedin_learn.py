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

    def test_wilson_bounds(self):
        lo, hi = learn._wilson(9, 10)
        self.assertTrue(0.5 < lo < 0.9 < hi <= 1.0)


if __name__ == "__main__":
    unittest.main()
