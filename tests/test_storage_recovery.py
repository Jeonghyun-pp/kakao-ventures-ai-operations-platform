"""Synthetic crash tails and snapshot changes must not silently lose records."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from kvlib import ledger
from skills.ops import run


class LedgerRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {'KV_DATA': self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_torn_utf8_tail_is_ignored_then_repaired_on_append(self):
        ledger.append('test', 'first', 'landed')
        with ledger.paths.ledger('test').open('ab') as out:
            out.write(b'{"key":"\xed\x95')  # interrupted inside a Korean UTF-8 codepoint
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ledger.keys('test'), {'first'})
            ledger.append('test', 'second', 'landed')
        self.assertEqual(ledger.keys('test'), {'first', 'second'})

    def test_corrupt_utf8_in_middle_fails_loudly(self):
        ledger.paths.ledger('test').write_bytes(b'{"key":"\xff"}\n{"key":"valid"}\n')
        with self.assertRaises(ledger.LedgerCorrupt):
            ledger.rows('test')

    def test_corrupt_json_in_middle_fails_without_rewriting(self):
        path = ledger.paths.ledger('test')
        raw = b'{"key":"first"}\n{broken}\n{"key":"last"}\n'
        path.write_bytes(raw)
        with self.assertRaises(ledger.LedgerCorrupt):
            ledger.rows('test')
        self.assertEqual(path.read_bytes(), raw)


class SnapshotRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src, self.dst = Path(self.tmp.name) / 'data', Path(self.tmp.name) / 'backup'
        self.src.mkdir()
        self.file = self.src / 'record.json'
        self.file.write_text('old')
        cfg = mock.patch.object(run, '_cfg', lambda key: {'backup_keep': 7, 'backup_min_files': 1}[key])
        cfg.start()
        self.addCleanup(cfg.stop)
        quiet = contextlib.redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)

    def test_same_size_same_timestamp_content_is_not_reused(self):
        first = run.backup(self.src, self.dst, today='2026-11-01')
        stamp = self.file.stat()
        self.file.write_text('new')
        os.utime(self.file, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        second = run.backup(self.src, self.dst, today='2026-11-02')
        self.assertEqual((second / self.file.name).read_text(), 'new')
        self.assertEqual((first / self.file.name).read_text(), 'old')

    def test_external_file_symlink_is_skipped_without_blocking_snapshot(self):
        outside = Path(self.tmp.name) / 'outside.txt'
        outside.write_text('private')
        (self.src / 'outside-link').symlink_to(outside)
        snapshot = run.backup(self.src, self.dst, today='2026-11-02')
        self.assertEqual((snapshot / self.file.name).read_text(), 'old')
        self.assertFalse((snapshot / 'outside-link').exists())

    def test_nested_destination_is_rejected_before_copy(self):
        nested = self.src / 'backups'
        with mock.patch.object(run.shutil, 'copytree', side_effect=AssertionError('must reject before copy')):
            with self.assertRaisesRegex(RuntimeError, 'overlap'):
                run.backup(self.src, nested, today='2026-11-02')
        self.assertFalse(nested.exists())

    def test_copy_failure_preserves_existing_snapshot_and_success_mark(self):
        previous = run.backup(self.src, self.dst, today='2026-11-02')
        mark = (self.dst / '.last-success').read_bytes()
        self.file.write_text('new')
        with mock.patch.object(run.shutil, 'copytree', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                run.backup(self.src, self.dst, today='2026-11-02')
        self.assertEqual((previous / self.file.name).read_text(), 'old')
        self.assertEqual((self.dst / '.last-success').read_bytes(), mark)


if __name__ == '__main__':
    unittest.main()
