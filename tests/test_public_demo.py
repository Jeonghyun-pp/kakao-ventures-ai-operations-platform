import unittest
from unittest import mock

from demo.run_demo import run
from demo.evaluate import evaluate


class PublicDemoTest(unittest.TestCase):
    def test_walkthroughs_need_no_socket_or_child_process(self):
        with mock.patch("socket.socket.connect", side_effect=AssertionError("network access")), \
             mock.patch("socket.create_connection", side_effect=AssertionError("network access")), \
             mock.patch("subprocess.run", side_effect=AssertionError("child process")):
            run()
            evaluate()

    def test_retry_approval_deduplication_and_replay(self):
        result = run()
        self.assertEqual(result["action_statuses"], ["failed", "done", "done"])
        self.assertEqual(result["delivery_attempts"], 2)
        self.assertEqual(result["delivered"], ["company-001"])
        self.assertEqual(result["approved_for_action"], ["company-001"])
        self.assertEqual(result["changed_judgments"], 1)
        self.assertEqual(result["withheld"], ["company-002", "company-003"])

    def test_without_approval_no_delivery_is_attempted(self):
        result = run(approved=frozenset())
        self.assertEqual(result["action_statuses"], [])
        self.assertEqual(result["delivery_attempts"], 0)
        self.assertEqual(result["delivered"], [])
        self.assertEqual(result["approved_for_action"], [])


if __name__ == "__main__":
    unittest.main()
