"""The example must reject attractive aggregate scores and incomplete results."""
import unittest

from demo.evaluate import evaluate, judge
from kvlib import Fetched


class PublicEvaluation(unittest.TestCase):
    def test_higher_agreement_does_not_hide_new_priority_miss(self):
        result = evaluate()
        self.assertEqual((result["inputs"], result["compared"], result["labeled"]), (6, 6, 6))
        self.assertEqual((result["baseline_matches"], result["candidate_matches"]), (3, 5))
        self.assertEqual(result["new_priority_misses"], ["prototype"])
        self.assertEqual(result["decision"], "hold")

    def test_missing_high_cost_case_cannot_be_hidden_by_diff_join(self):
        def omit(lines, keys, criteria):
            return Fetched.measured([r for r in judge(lines, keys, criteria).require() if r["key"] != "prototype"])
        result = evaluate(omit)
        self.assertEqual(result["missing"], ["prototype"])
        self.assertEqual((result["candidate_matches"], result["labeled"]), (5, 5))
        self.assertEqual(result["decision"], "hold")
        self.assertIn("incomplete candidate results", result["reasons"])

    def test_no_improvement_is_not_a_rollout_recommendation(self):
        from demo.evaluate import BASELINE
        result = evaluate(lambda lines, keys, _: judge(lines, keys, BASELINE))
        self.assertEqual(result["decision"], "hold")
        self.assertIn("no agreement improvement on comparable labeled cases", result["reasons"])

    def test_improvement_without_new_priority_miss_is_still_human_review(self):
        def corrected(lines, keys, criteria):
            rows = judge(lines, keys, criteria).require()
            for row in rows:
                if row["key"] == "prototype":
                    row.update(verdict="priority", raw="priority")
            return Fetched.measured(rows)
        result = evaluate(corrected)
        self.assertEqual(result["candidate_matches"], 6)
        self.assertEqual(result["decision"], "review_for_rollout")
        self.assertEqual(result["reasons"], [])
