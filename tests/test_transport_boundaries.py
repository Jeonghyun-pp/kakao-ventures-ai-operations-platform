"""Exercise real adapter guard/retry code with fake HTTP responses only."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock


from kvlib import lock
from kv_transport import apify, gmail, itunes, notion, slack


def response(status=200, body=None, headers=None):
    return types.SimpleNamespace(status_code=status, json=lambda: body, text=json.dumps(body), headers=headers or {})


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.dict(os.environ, {'KV_DATA': self.tmp, 'KV_CONFIG': self.tmp}))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(mock.patch.object(apify.secrets, 'get', return_value='SYNTHETIC'))

    def test_apify_records_charge_before_timeout_and_does_not_retry_paid_post(self):
        self.enterContext(mock.patch.object(apify, '_spent_this_run', 0.0))
        self.enterContext(mock.patch.object(apify, '_platform_used', 0.0))
        self.enterContext(mock.patch.object(apify, '_gate_off', False))
        def timeout(*args, **kwargs):
            self.assertAlmostEqual(apify.spent_today(), 0.2)
            raise apify.requests.exceptions.ReadTimeout('fake timeout')
        with mock.patch.object(apify.requests, 'post', side_effect=timeout) as post:
            got = apify.run_actor('fake', {}, skill='test', est_usd=0.2)
        self.assertFalse(got.is_measured)
        self.assertEqual(post.call_count, 1)
        self.assertAlmostEqual(apify.spent_today(), 0.2)

    def test_apify_monthly_cap_blocks_before_post(self):
        self.enterContext(mock.patch.object(apify, '_spent_this_run', 0.0))
        self.enterContext(mock.patch.object(apify, '_platform_used', 29.0))
        self.enterContext(mock.patch.object(apify, '_gate_off', False))
        with mock.patch.object(apify.requests, 'post') as post:
            with self.assertRaises(apify.BudgetExceeded):
                apify.run_actor('fake', {}, skill='test', est_usd=0.2)
        post.assert_not_called()
        self.assertEqual(apify.spent_today(), 0)

    def test_apify_unavailable_limits_is_unknown_and_explicit_fail_open(self):
        self.enterContext(mock.patch.object(apify, '_platform_used', None))
        self.enterContext(mock.patch.object(apify, '_gate_off', False))
        with mock.patch.object(apify.requests, 'get', side_effect=apify.requests.exceptions.Timeout):
            got = apify.platform_used()
        self.assertFalse(got.is_measured)
        self.assertTrue(apify._gate_off)

    def test_gmail_repeated_unauthorized_stops_after_one_refresh(self):
        self.enterContext(mock.patch.object(gmail, '_access_token', return_value='fake'))
        self.enterContext(mock.patch.object(gmail, '_token', {'value': 'stale'}))
        with mock.patch.object(gmail.requests, 'request', return_value=response(401, {})) as call:
            with self.assertRaises(gmail.AuthDead):
                gmail._req('GET', '/messages')
        self.assertEqual(call.call_count, 2)
        self.assertEqual(gmail._token, {})

    def test_gmail_network_retry_is_bounded(self):
        self.enterContext(mock.patch.object(gmail, '_access_token', return_value='fake'))
        with mock.patch.object(gmail.requests, 'request', side_effect=apify.requests.exceptions.ChunkedEncodingError) as call, \
             mock.patch.object(gmail.time, 'sleep'):
            with self.assertRaises(gmail.GmailError):
                gmail._req('GET', '/messages')
        self.assertEqual(call.call_count, len(gmail.constraints.get('http.retry_backoff_sec')) + 1)

    def test_notion_rate_limit_retry_is_bounded(self):
        with mock.patch.object(notion, '_headers', return_value={}), \
             mock.patch.object(notion.requests, 'request', return_value=response(429, {}, {'Retry-After': '2'})) as call, \
             mock.patch.object(notion.time, 'sleep') as sleep:
            with self.assertRaises(notion.NotionError):
                notion._req('GET', '/databases/fake')
        self.assertEqual(call.call_count, 5)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2.0] * 4)

    def test_notion_unknown_select_is_rejected_before_write(self):
        sch = {'State': {'type': 'select', 'select': {'options': [{'name': 'Known'}]}}}
        with mock.patch.object(notion, 'schema', return_value=sch), mock.patch.object(notion, '_req') as call:
            with self.assertRaises(notion.NotionError):
                notion._build('fake', {'State': 'Unknown'})
        call.assert_not_called()

    def test_itunes_network_circuit_prevents_unbounded_retry(self):
        itunes.reset_circuit()
        self.addCleanup(itunes.reset_circuit)
        with mock.patch.object(itunes, '_pace'), mock.patch.object(itunes.time, 'sleep'), \
             mock.patch.object(itunes.requests, 'get', side_effect=apify.requests.exceptions.Timeout) as call:
            for _ in range(3):
                with self.assertRaises(itunes.NetDown):
                    itunes._get('https://fake.invalid')
        attempts = len(itunes.constraints.get('http.retry_backoff_sec')) + 1
        self.assertEqual(call.call_count, attempts * 2 + 1)

    def test_itunes_partial_lookup_is_not_complete(self):
        okay = response(body={'results': [{'trackId': 1, 'trackName': '합성'}]})
        okay.raise_for_status = lambda: None
        with mock.patch.object(itunes, '_get', side_effect=[okay, itunes.NetDown('fake')]):
            got = itunes.lookup([str(i) for i in range(51)])
        self.assertTrue(got.partial)
        self.assertEqual(got.meta['failed_batches'], 1)
        self.assertEqual(set(got.require()), {'1'})

    def test_slack_suppression_is_unknown_without_http(self):
        with mock.patch.object(slack.secrets, 'no_send', return_value=True), mock.patch.object(slack, '_call') as call:
            self.assertFalse(slack.post('test', 'synthetic').is_measured)
        call.assert_not_called()

    def test_lock_blocks_overlap_and_releases_without_unlink(self):
        with lock.hold('synthetic'):
            path = Path(self.tmp) / '_locks/synthetic.lock'
            inode = path.stat().st_ino
            with self.assertRaises(lock.Busy):
                with lock.hold('synthetic'):
                    self.fail('overlapping lock acquired')
        with lock.hold('synthetic'):
            self.assertEqual(path.stat().st_ino, inode)


if __name__ == '__main__':
    unittest.main()
