"""Doctor checks local files and describes its limits; no provider calls."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from kvlib import constraints
from tools import doctor


class Doctor(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.object(doctor.paths, 'config_root', return_value=self.root))
        self.addCleanup(constraints._deployment.cache_clear)
        example = yaml.safe_load(doctor.connections.EXAMPLE.read_text())
        def fill(value):
            if isinstance(value, dict):
                return {k: fill(v) for k, v in value.items()}
            if isinstance(value, list):
                return [fill(v) for v in value]
            return 'synthetic-value'
        self.write('connections.yaml', fill(example))
        self.write('people.yaml', [{'names': ['Reviewer'], 'email': 'reviewer@test.invalid', 'slack_id': 'U12345'}])
        self.write('deployment.yaml', {})
        token = self.root / 'dummy.json'
        token.write_text('{}')
        self.env = self.root / '.env'
        self.env.write_text(f'NOTION_TOKEN=synthetic\nSLACK_BOT_TOKEN=synthetic\nGMAIL_TOKEN_FILE={token}\nGOOGLE_CREDENTIALS_FILE={token}\n')
        self.env.chmod(0o600)

    def write(self, name, value):
        (self.root / name).write_text(yaml.safe_dump(value))

    def run_doctor(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = doctor.main()
        return result, output.getvalue()

    def test_valid_local_files_do_not_claim_remote_readiness(self):
        result, out = self.run_doctor()
        self.assertEqual(result, 0)
        self.assertIn('none (closed)', out)
        self.assertIn('remote credentials, permissions and model tools were not verified', out)

    def test_enabled_switches_are_reported_as_enabled(self):
        self.write('deployment.yaml', {'appstore': {'outbound': 'on'}, 'mail_observe': {'open_kinds': ['archive']}})
        result, out = self.run_doctor()
        self.assertEqual(result, 0)
        self.assertIn('appstore.outbound', out)
        self.assertIn('mail_observe.open_kinds', out)
        self.assertNotIn('closed', out)

    def test_optional_reviewer_slack_id_and_empty_roster_are_allowed(self):
        self.write('people.yaml', [{'names': ['Reviewer'], 'email': 'reviewer@test.invalid'}])
        self.assertEqual(self.run_doctor()[0], 0)
        self.write('people.yaml', [])
        self.assertEqual(self.run_doctor()[0], 0)

    def test_incomplete_connections_and_loose_secret_permissions_fail(self):
        self.write('connections.yaml', {})
        self.env.chmod(0o644)
        result, out = self.run_doctor()
        self.assertEqual(result, 1)
        self.assertIn('organization', out)
        self.assertIn('chmod 600', out)

    def test_missing_invalid_and_empty_yaml_are_diagnostics_not_tracebacks(self):
        for name, content in [('connections.yaml', ''), ('connections.yaml', '[broken'),
                              ('people.yaml', '{}'), ('deployment.yaml', 'appstore: {outbound: true}')]:
            with self.subTest(name=name, content=content):
                before = (self.root / name).read_text()
                (self.root / name).write_text(content)
                result, out = self.run_doctor()
                self.assertEqual(result, 1)
                self.assertIn(name, out)
                (self.root / name).write_text(before)
        (self.root / 'deployment.yaml').unlink()
        self.assertEqual(self.run_doctor()[0], 1)

    def test_example_and_missing_credential_file_fail(self):
        self.write('connections.yaml', yaml.safe_load(doctor.connections.EXAMPLE.read_text()))
        (self.root / 'dummy.json').unlink()
        result, out = self.run_doctor()
        self.assertEqual(result, 1)
        self.assertIn('replace example value', out)
        self.assertIn('credential file does not exist', out)


if __name__ == '__main__':
    unittest.main()
