"""CLI parsing and local operations run against disposable configuration/data."""
import ast
import contextlib
import importlib
import io
import os
import sqlite3
import types
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from kvlib import Fetched, connections, constraints, judgments, lock, paths, secrets
from tools import bootstrap


class OfflineCLI(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(mock.patch.dict(os.environ, {'KV_CONFIG': str(root / 'config'), 'KV_DATA': str(root / 'data')}))
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        for clear in (connections.clear_cache, constraints._deployment.cache_clear, secrets._env.cache_clear):
            clear()
            self.addCleanup(clear)
        self.enterContext(mock.patch('socket.socket.connect', side_effect=AssertionError('network')))
        self.enterContext(mock.patch('socket.create_connection', side_effect=AssertionError('network')))
        self.enterContext(mock.patch('subprocess.run', side_effect=AssertionError('process')))
        self.enterContext(mock.patch('subprocess.Popen', side_effect=AssertionError('process')))

    def test_all_workflow_subcommands_expose_help_without_providers(self):
        for source in sorted((paths.repo_root() / 'skills').glob('*/run.py')):
            module = importlib.import_module(f'skills.{source.parent.name}.run')
            commands = [node.args[0].value for node in ast.walk(ast.parse(source.read_text()))
                        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr == 'add_parser' and node.args and isinstance(node.args[0], ast.Constant)]
            for argv in [['--help'], *[[command, '--help'] for command in commands]]:
                with self.subTest(skill=source.parent.name, argv=argv), self.assertRaises(SystemExit) as stopped:
                    module.main(argv)
                self.assertEqual(stopped.exception.code, 0)

    def test_mail_local_status_report_backup_restore_and_invalid_inputs(self):
        from skills.mail import run
        for op in ('status', 'report', 'backup', 'restore-test'):
            with self.subTest(op=op):
                self.assertEqual(run.main(['observe', '--op', op]), 0)
        self.assertTrue((paths.data_root() / 'mail/reports/latest.md').exists())
        self.assertEqual(run.main(['observe', '--op', 'diff']), 1)
        self.assertEqual(run.main(['observe', '--op', 'label', '--id', 'absent', '--category', 'invalid']), 1)

    def test_workflow_status_and_dashboard_use_only_local_or_fake_reads(self):
        from kv_transport import notion
        import schedule
        from skills.ops import run as ops
        with mock.patch.object(notion, 'query', return_value=Fetched.unknown('synthetic offline')), \
             mock.patch.object(schedule, 'loaded', return_value=set()):
            for skill in ('linkedin', 'appstore', 'bplan', 'memo', 'fund', 'ops'):
                with self.subTest(skill=skill):
                    self.assertEqual(importlib.import_module(f'skills.{skill}.run').main(['status']), 0)
            self.assertEqual(ops.digest(ops.datetime(2026, 11, 3, 19, tzinfo=ops._KST)), 0)
        self.assertTrue((paths.data_root() / '_reports/dashboard.html').exists())

    def test_empty_replay_and_diff_need_no_model_for_linkedin_and_appstore(self):
        for skill in ('linkedin', 'appstore'):
            module = importlib.import_module(f'skills.{skill}.run')
            with self.subTest(skill=skill):
                self.assertEqual(module.main(['replay']), 0)
                self.assertEqual(module.main(['diff', '--b', 'r-absent']), 0)

    def test_all_three_diff_commands_show_incomplete_scope(self):
        con = sqlite3.connect(':memory:')
        self.addCleanup(con.close)
        judgments.ensure(con)
        judgments.save(con, 'live', 'a', {'a': 'A', 'b': 'B'},
                       [{'key': k, 'verdict': 'Catch'} for k in ('a', 'b')])
        judgments.save(con, 'candidate', 'b', {'a': 'A', 'b': 'B'},
                       [{'key': 'a', 'verdict': 'Catch'}])
        for skill in ('linkedin', 'bplan', 'appstore'):
            module = importlib.import_module(f'skills.{skill}.run')
            out = io.StringIO()
            with self.subTest(skill=skill), contextlib.redirect_stdout(out), \
                 mock.patch.object(module, '_gate', return_value=types.SimpleNamespace(con=con)):
                self.assertEqual(module.main(['diff', '--b', 'candidate']), 0)
            self.assertIn('판정 실패·누락 1', out.getvalue())
            self.assertIn('같은 입력 비교: 1/2', out.getvalue())
            self.assertIn('주의:', out.getvalue())

    def test_overlapping_workflow_cli_returns_busy_without_provider_call(self):
        cases = [('linkedin', 'collect', 'linkedin-collect'), ('appstore', 'collect', 'appstore-collect'),
                 ('bplan', 'sync', 'bplan-sync'), ('memo', 'followup', 'memo-followup'), ('fund', 'followup', 'fund-followup')]
        for skill, command, name in cases:
            with self.subTest(skill=skill), lock.hold(name):
                self.assertEqual(importlib.import_module(f'skills.{skill}.run').main([command]), 3)

    def test_setup_closes_gates_and_preserves_existing_configuration(self):
        self.assertEqual(bootstrap.main(), 0)
        env = paths.config_root() / '.env'
        self.assertEqual(env.stat().st_mode & 0o777, 0o600)
        env.write_text('SYNTHETIC=preserve\n')
        self.assertEqual(bootstrap.main(), 0)
        self.assertEqual(env.read_text(), 'SYNTHETIC=preserve\n')
        self.assertEqual(constraints.get('mail_observe.outbound'), 'off')
        self.assertEqual(constraints.get('linkedin.outbound'), 'off')


if __name__ == '__main__':
    unittest.main()
