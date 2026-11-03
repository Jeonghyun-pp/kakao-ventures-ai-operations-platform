"""Later Slack thread pages carry approvals and files, not an optional preview."""
import unittest
from unittest import mock

from kv_transport import slack


class Pagination(unittest.TestCase):
    def setUp(self):
        slack.register('pagination-test', 'C-PAGINATION')

    def test_replies_reads_later_approval_and_preserves_parent_filter(self):
        pages = [
            {'messages': [{'ts': '1'}, {'ts': '2'}], 'has_more': True,
             'response_metadata': {'next_cursor': 'next'}},
            {'messages': [{'ts': '3', 'reactions': [{'name': 'white_check_mark'}]}], 'has_more': False},
        ]
        with mock.patch.object(slack, '_call', side_effect=pages) as call:
            got = slack.replies('pagination-test', '1')
        self.assertEqual([m['ts'] for m in got.require()], ['2', '3'])
        self.assertEqual(call.call_args.kwargs['params']['cursor'], 'next')

    def test_thread_keeps_parent_and_later_page_file(self):
        pages = [
            {'messages': [{'ts': '1'}], 'has_more': True, 'response_metadata': {'next_cursor': 'next'}},
            {'messages': [{'ts': '2', 'files': [{'id': 'F', 'url_private': 'fake'}]}]},
        ]
        with mock.patch.object(slack, '_call', side_effect=pages):
            got = slack.thread('pagination-test', '1')
        self.assertEqual([m['ts'] for m in got.require()], ['1', '2'])
        self.assertEqual(slack.files_of(got.require())[0]['id'], 'F')

    def test_later_page_failure_never_becomes_complete_empty_reactions(self):
        first = {'messages': [{'ts': '1'}], 'has_more': True, 'response_metadata': {'next_cursor': 'next'}}
        with mock.patch.object(slack, '_call', side_effect=[first, slack.SlackError('timeout')]):
            self.assertFalse(slack.replies('pagination-test', '1').is_measured)

    def test_missing_or_repeated_cursor_fails_without_looping(self):
        for cursor in ('', 'same'):
            with self.subTest(cursor=cursor):
                page = {'messages': [], 'has_more': True, 'response_metadata': {'next_cursor': cursor}}
                with mock.patch.object(slack, '_call', return_value=page) as call:
                    self.assertFalse(slack.thread('pagination-test', '1').is_measured)
                self.assertLessEqual(call.call_count, 2)


if __name__ == '__main__':
    unittest.main()
