"""Local rollout policy, including missing/partial files, without any external effects."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from kvlib import constraints
from kvlib.gate import Gate


class Deployment(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(constraints.paths, 'config_root', return_value=self.root))
        constraints._deployment.cache_clear()
        self.addCleanup(constraints._deployment.cache_clear)

    def write(self, value):
        (self.root / 'deployment.yaml').write_text(yaml.safe_dump(value))
        constraints._deployment.cache_clear()

    def test_missing_file_closes_actions_and_independent_bypasses(self):
        for key, expected in {
            'appstore.outbound': 'off', 'linkedin.outbound': 'off', 'linkedin.promote': False,
            'appstore.report_slack': 'off', 'ops.digest_slack': 'off',
            'mail_observe.open_kinds': [], 'mail_observe.open_categories': {},
            'mail_observe.send_branches': [],
        }.items():
            self.assertEqual(constraints.get(key), expected, key)
        con = sqlite3.connect(':memory:')
        self.addCleanup(con.close)
        callback = mock.Mock()
        self.assertEqual(Gate(con, 'appstore').out('case', 'deliver', {}, callback), 'dry')
        callback.assert_not_called()

    def test_partial_file_enables_only_explicit_switches(self):
        self.write({'linkedin': {'outbound': 'slack'}})
        self.assertEqual(constraints.get('linkedin.outbound'), 'slack')
        self.assertIs(constraints.get('linkedin.promote'), False)
        self.assertEqual(constraints.get('linkedin.report_slack'), 'off')
        self.assertEqual(constraints.get('appstore.outbound'), 'off')
        self.assertEqual(constraints.get('http.timeout_sec'), constraints._all()['http']['timeout_sec'])

    def test_explicit_enable_is_preserved(self):
        self.write({'linkedin': {'outbound': 'on', 'promote': True}, 'ops': {'digest_slack': 'on'}})
        con = sqlite3.connect(':memory:')
        self.addCleanup(con.close)
        callback = mock.Mock(return_value='local-result')
        self.assertEqual(Gate(con, 'linkedin').out('case', 'deliver', {}, callback), 'done')
        callback.assert_called_once_with()
        self.assertIs(constraints.get('linkedin.promote'), True)
        self.assertEqual(constraints.get('ops.digest_slack'), 'on')

    def test_bad_shapes_and_rollout_types_fail_without_running_callback(self):
        for value in ([], {'appstore': None}, {'appstore': {'outbound': True}},
                      {'linkedin': {'promote': 'false'}}, {'mail_observe': {'open_kinds': 'archive'}},
                      {'mail_observe': {'open_categories': {'archive': 'AD_NOISE'}}},
                      {'mail_observe': {'send_branches': None}}):
            with self.subTest(value=value):
                self.write(value)
                with self.assertRaises(ValueError):
                    constraints.get('appstore.outbound')

    def test_mail_explicit_open_kind_keeps_optional_category_contract(self):
        from skills.mail.flow import Flow
        con = sqlite3.connect(':memory:')
        self.addCleanup(con.close)
        flow = Flow.__new__(Flow)
        Gate.__init__(flow, con, 'mail_observe')
        callback = mock.Mock(return_value='local-result')
        # Missing local policy must not inherit the historical archive exception.
        self.assertEqual(flow.out('missing', 'archive', {'category': 'AD_NOISE'}, callback), 'dry')
        callback.assert_not_called()
        self.write({'mail_observe': {'open_kinds': ['archive']}})
        self.assertEqual(flow.out('explicit', 'archive', {'category': 'AD_NOISE'}, callback), 'done')
        self.write({'mail_observe': {'open_kinds': ['archive'], 'open_categories': {'archive': ['SYSTEM_NOTICE']}}})
        self.assertEqual(flow.out('outside', 'archive', {'category': 'AD_NOISE'}, callback), 'dry')
        self.assertEqual(flow.out('inside', 'archive', {'category': 'SYSTEM_NOTICE'}, callback), 'done')
        self.assertEqual(callback.call_count, 2)
        flow.force = 'off'
        self.assertEqual(flow.out('forced', 'archive', {'category': 'SYSTEM_NOTICE'}, callback), 'dry')
        self.assertEqual(callback.call_count, 2)


if __name__ == '__main__':
    unittest.main()
