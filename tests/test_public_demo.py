import unittest

from demo.run_demo import run


class PublicDemoTest(unittest.TestCase):
    def test_retry_approval_deduplication_and_replay(self):
        result = run()
        self.assertEqual(result["action_statuses"], ["failed", "done", "done"])
        self.assertEqual(result["delivery_attempts"], 2)
        self.assertEqual(result["delivered"], ["company-001"])
        self.assertEqual(result["approved_for_action"], ["company-001"])
        self.assertEqual(result["changed_judgments"], 1)


if __name__ == "__main__":
    unittest.main()
