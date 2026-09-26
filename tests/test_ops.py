"""ops — 스냅샷은 안 변한 파일을 하드링크하고, 빈 원본은 거부하고, 보존은 오늘 백업이 있을 때만 허용 목록 안에서 지운다."""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from skills.ops import run


def _tree(root: Path, n: int = 120):
    for i in range(n):
        f = root / "mail" / "reports" / f"r{i}.md"; f.parent.mkdir(parents=True, exist_ok=True); f.write_text(f"r{i}")
    (root / "mail" / "db").mkdir(parents=True, exist_ok=True); (root / "mail" / "db" / "mail.sqlite").write_bytes(b"live")
    (root / "mail" / "db" / "backup").mkdir(); (root / "mail" / "db" / "backup" / "mail-2026-09-26.sqlite").write_bytes(b"copy")


class Backup(unittest.TestCase):
    def test_snapshot_hardlinks_unchanged_and_skips_live_sqlite(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(run, "_cfg", lambda k: {"backup_keep": 7, "backup_min_files": 100}[k]):
            src, dst = Path(d) / "data", Path(d) / "bak"; _tree(src)
            a = run.backup(src, dst, today="2026-09-25")
            (src / "mail" / "reports" / "r0.md").write_text("changed")
            b = run.backup(src, dst, today="2026-09-26")
            self.assertFalse((b / "mail" / "db" / "mail.sqlite").exists())                          # 열린 DB 는 제외
            self.assertTrue((b / "mail" / "db" / "backup" / "mail-2026-09-26.sqlite").exists())       # 스킬의 사본은 포함
            self.assertEqual(os.stat(a / "mail/reports/r1.md").st_ino, os.stat(b / "mail/reports/r1.md").st_ino)   # 안 변한 것 = 같은 inode
            self.assertNotEqual(os.stat(a / "mail/reports/r0.md").st_ino, os.stat(b / "mail/reports/r0.md").st_ino)
            self.assertIn("files=121", (dst / ".last-success").read_text())
            self.assertLess(run.backup_age_days(dst), 0.01)

    def test_empty_source_is_refused(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(run, "_cfg", lambda k: {"backup_keep": 7, "backup_min_files": 100}[k]):
            src, dst = Path(d) / "data", Path(d) / "bak"; (src / "x").mkdir(parents=True); (src / "x" / "a").write_text("a")
            with self.assertRaises(RuntimeError):
                run.backup(src, dst)
            self.assertFalse((dst / ".last-success").exists())

    def test_keep_prunes_old_snapshots(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(run, "_cfg", lambda k: {"backup_keep": 2, "backup_min_files": 100}[k]):
            src, dst = Path(d) / "data", Path(d) / "bak"; _tree(src)
            for day in ("2026-09-24", "2026-09-25", "2026-09-26"):
                run.backup(src, dst, today=day)
            self.assertEqual([p.name for p in run._snapshots(dst)], ["2026-09-25", "2026-09-26"])


class Retention(unittest.TestCase):
    def test_deletes_only_listed_glob_and_only_after_todays_backup(self):
        rules = [{"name": "usage", "glob": "_usage/claude/*.jsonl", "keep_days": 90, "min_keep": 1}]
        with tempfile.TemporaryDirectory() as d, mock.patch.object(run, "_cfg", lambda k: {"retention": rules}[k]):
            root, dst = Path(d) / "data", Path(d) / "bak"; (root / "_usage" / "claude").mkdir(parents=True); dst.mkdir()
            old, new, other = root / "_usage/claude/a.jsonl", root / "_usage/claude/b.jsonl", root / "_usage/other.jsonl"
            for f in (old, new, other): f.write_text("x")
            os.utime(old, (time.time() - 100 * 86400,) * 2); os.utime(other, (time.time() - 100 * 86400,) * 2)
            self.assertEqual(run.retention(root, dst), {"skipped": "no_backup"}); self.assertTrue(old.exists())       # 백업 없음 → 안 지운다
            (dst / ".last-success").write_text("2099-01-01T00:00:00Z files=1\n")
            self.assertEqual(run.retention(root, dst), {"usage": 1})
            self.assertEqual((old.exists(), new.exists(), other.exists()), (False, True, True))                       # 목록 밖(other) 은 못 건드린다


if __name__ == "__main__":
    unittest.main()
