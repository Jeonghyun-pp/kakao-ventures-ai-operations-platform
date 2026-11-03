"""Replay retries missing judgments while preserving successful work and live rows."""
import sqlite3
import unittest

from kvlib import Fetched, judgments


class ReplayRetry(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.addCleanup(self.con.close)
        judgments.ensure(self.con)
        judgments.save(self.con, 'live', 'original', {'a': 'input A', 'b': 'input B'}, [
            {'key': 'a', 'verdict': 'yes'}, {'key': 'b', 'verdict': 'no'}])

    def test_failed_replay_can_be_retried_with_same_criteria(self):
        failed = judgments.replay(self.con, lambda *args: Fetched.unknown('timeout'), 'criteria', 'same')
        self.assertEqual(failed['missing'], 2)
        calls = []
        def judge(lines, keys, criteria):
            calls.append((lines, keys))
            return Fetched.measured([{'key': key, 'verdict': 'yes'} for key in keys])
        retried = judgments.replay(self.con, judge, 'criteria', 'same')
        self.assertEqual(retried['judged'], 2)
        self.assertEqual(set(calls[0][1]), {'a', 'b'})
        self.assertEqual(self.con.execute("SELECT key,verdict FROM judgments WHERE run='live' ORDER BY key").fetchall(), [('a', 'yes'), ('b', 'no')])

    def test_partial_replay_only_retries_missing_and_third_run_calls_nothing(self):
        judgments.replay(self.con, lambda *args: Fetched.measured([{'key': 'a', 'verdict': 'yes'}]), 'criteria', 'same')
        calls = []
        def judge(lines, keys, criteria):
            calls.append(keys)
            return Fetched.measured([{'key': 'b', 'verdict': 'no'}])
        self.assertEqual(judgments.replay(self.con, judge, 'criteria', 'same')['judged'], 1)
        self.assertEqual(calls, [['b']])
        self.assertEqual(judgments.replay(self.con, judge, 'criteria', 'same')['judged'], 0)
        self.assertEqual(calls, [['b']])


if __name__ == '__main__':
    unittest.main()
