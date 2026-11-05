"""Comparison denominators survive failures, limited runs and mutable live inputs."""
import sqlite3
import unittest

from kvlib import Fetched, judgments


class ComparisonReport(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.addCleanup(self.con.close)
        judgments.ensure(self.con)

    def save(self, run, inputs, answers):
        judgments.save(self.con, run, run, inputs,
                       [{'key': key, 'verdict': value} for key, value in answers.items()])

    def test_failed_absent_and_changed_inputs_never_disappear_from_scope(self):
        inputs = {key: key for key in ('good', 'failed', 'absent', 'changed', 'base-failed')}
        self.save('live', inputs, {key: 'yes' for key in inputs if key != 'base-failed'})
        self.save('candidate', {k: v for k, v in inputs.items() if k != 'absent'},
                  {'good': 'yes', 'changed': 'no', 'base-failed': 'yes'})
        self.save('live', {'changed': 'new input'}, {'changed': 'no'})
        report = judgments.comparison_report(self.con, 'live', 'candidate')
        self.assertEqual((report['total'], report['compared']), (5, 1))
        self.assertEqual(report['a']['failed'], ['base-failed'])
        self.assertEqual(report['b']['failed'], ['failed'])
        self.assertEqual(report['b']['absent'], ['absent'])
        self.assertEqual(report['input_changed'], ['changed'])
        self.assertFalse(report['complete'])
        text = judgments.comparison_text(report, 'live', 'candidate')
        self.assertIn('같은 입력 비교: 1/5건', text)
        self.assertIn('기록 없음 1건', text)
        self.assertIn('주의:', text)
        # Existing low-level callers retain their overlapping-results contract.
        self.assertEqual({r['key'] for r in judgments.diff(self.con, 'live', 'candidate')}, {'good', 'changed'})

    def test_limited_replay_is_complete_only_for_explicit_sample(self):
        self.save('live', {'a': 'A', 'b': 'B', 'c': 'C'}, dict.fromkeys('abc', 'yes'))
        result = judgments.replay(self.con, lambda lines, keys, criteria:
                                 Fetched.measured([{'key': k, 'verdict': 'yes'} for k in keys]),
                                 'criteria', 'sample', limit=1)
        full = judgments.comparison_report(self.con, 'live', result['run'])
        self.assertEqual((full['total'], full['compared'], len(full['b']['absent'])), (3, 1, 2))
        self.assertEqual(full['b']['failed'], [])
        selected = [full['rows'][0]['key']]
        sample = judgments.comparison_report(self.con, 'live', result['run'], keys=selected * 2)
        self.assertEqual((sample['total'], sample['compared']), (1, 1))
        self.assertTrue(sample['complete'])

    def test_empty_or_unknown_scope_is_not_complete(self):
        self.save('live', {'a': 'A'}, {'a': 'yes'})
        for a, keys in [('live', []), ('typo', None)]:
            report = judgments.comparison_report(self.con, a, 'candidate', keys=keys)
            self.assertEqual(report['total'], 0)
            self.assertFalse(report['complete'])
            self.assertIn('평가 대상 없음', judgments.comparison_text(report, a, 'candidate'))
        report = judgments.comparison_report(self.con, 'live', 'candidate', keys=['unknown'])
        self.assertEqual(report['a']['absent'], ['unknown'])
        self.assertEqual(report['b']['absent'], ['unknown'])

    def test_labels_and_buckets_use_only_comparable_successful_rows(self):
        inputs = {'a': 'A', 'b': 'B', 'c': 'C'}
        self.save('live', inputs, {'a': 'Catch', 'b': 'Wait', 'c': 'Catch'})
        self.save('candidate', inputs, {'a': 'Wait', 'b': 'Catch', 'c': 'Catch'})
        self.save('human', inputs, {'a': 'yes', 'b': 'yes'})
        bucket = lambda v: 'yes' if v == 'Catch' else 'no'
        report = judgments.comparison_report(self.con, 'live', 'candidate', bucket)
        self.assertEqual((report['labeled'], report['a_matches'], report['b_matches']), (2, 1, 1))
        self.assertTrue(report['complete'])  # Complete results do not imply complete labels.
        self.assertIn('Catch', judgments.comparison_text(report, 'live', 'candidate', raw=True))
        reverse = judgments.comparison_report(self.con, 'candidate', 'live', bucket)
        self.assertEqual((reverse['total'], reverse['compared']), (3, 3))

    def test_retry_restores_completeness_without_counting_unrelated_candidate_rows(self):
        self.save('live', {'a': 'A', 'b': 'B'}, {'a': 'yes', 'b': 'no'})
        self.save('r-new', {'a': 'A', 'b': 'B', 'outside': 'X'}, {'a': 'yes', 'outside': 'yes'})
        self.assertFalse(judgments.comparison_report(self.con, 'live', 'r-new')['complete'])
        called = []
        def judge(lines, keys, criteria):
            called.extend(keys)
            return Fetched.measured([{'key': k, 'verdict': 'no'} for k in keys])
        judgments.replay(self.con, judge, 'new', 'new')
        self.assertEqual(called, ['b'])
        report = judgments.comparison_report(self.con, 'live', 'r-new')
        self.assertEqual((report['total'], report['compared']), (2, 2))
        self.assertTrue(report['complete'])


if __name__ == '__main__':
    unittest.main()
