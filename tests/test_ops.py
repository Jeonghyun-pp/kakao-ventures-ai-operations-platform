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


class Bundle(unittest.TestCase):
    """묶기 — 스킬의 하루 요약은 digest 가 켜져 있으면 스킬에서 안 나가고(묶음), 꺼져 있으면 예전처럼 나간다. 경보(post)는 키와 무관."""

    def test_daily_is_routed_to_digest_when_on(self):
        from kvlib import report
        sent = []
        real = report.constraints.get
        cfg = {"ops.digest_slack": "on", "x.report_slack": "on", "x.report_channel": "test"}
        with mock.patch.object(report.constraints, "get", lambda k: cfg[k] if k in cfg else real(k)):
            self.assertEqual(report.daily("x", "요약", lambda ch, t: sent.append(t)), "묶음(ops digest)")
            self.assertEqual(report.post("x", "🔴 경보", lambda ch, t: sent.append(t)), "게시함")
        cfg["ops.digest_slack"] = "off"
        with mock.patch.object(report.constraints, "get", lambda k: cfg[k] if k in cfg else real(k)):
            self.assertEqual(report.daily("x", "요약", lambda ch, t: sent.append(t)), "게시함")
        self.assertEqual(sent, ["🔴 경보", "요약"])

    def test_status_gathers_gaps_and_renders(self):
        from skills.ops import status
        import sqlite3, json
        from kvlib import judgments
        from kvlib.gate import SCHEMA as GATE
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            for sk in ("mail", "linkedin", "appstore", "bplan", "memo"):
                (root / sk / "db").mkdir(parents=True); (root / sk / "reports").mkdir()
            con = sqlite3.connect(root / "memo/db/memo.sqlite"); con.executescript(judgments.SCHEMA + GATE)
            con.execute("INSERT INTO actions VALUES('1.1','request','done','{}','2026-09-26T01:00:00Z')"); con.commit(); con.close()
            (root / "mail/reports/2026-09-26.md").write_text("x")
            jobs = [{"skill": "mail", "cmd": "observe", "when": "매일 10:00", "label": "com.kv.mail.observe"},
                    {"skill": "linkedin", "cmd": "card", "when": "매일 11:00", "label": "com.kv.linkedin.card"},
                    {"skill": "ops", "cmd": "backup", "when": "매일 12:45", "label": "com.kv.ops.backup"}]
            import schedule as sched
            with mock.patch.object(status.paths, "data", lambda *a, create=True: root.joinpath(*a)), \
                    mock.patch.object(sched, "declared", lambda: jobs), mock.patch.object(sched, "loaded", lambda: {"com.kv.mail.observe", "com.kv.ops.backup"}), \
                    mock.patch.object(run, "backup_age_days", lambda dst=None: 0.5):
                st = status.gather("2026-09-26")
            self.assertEqual(sorted(st["gaps"]), ["linkedin: 스케줄 미설치 — card 매일 11:00", "memo: 접수 답글 없는 요청 1건"])
            mail = next(s for s in st["skills"] if s["skill"] == "mail")
            self.assertTrue(mail["report_today"]); self.assertEqual(mail["schedule"][0]["installed"], True)
            html_out = status.render_html(st); txt = status.render_text(st); dg = status.digest_text(st)
            self.assertIn("접수 답글 없는 요청", html_out); self.assertIn("kv 현황 2026-09-26", txt); self.assertTrue(dg.startswith("*kv 하루* 2026-09-26 — 🔴"))
            self.assertEqual(json.loads(html_out.split('id="status">')[1].split("</script>")[0])["today"], "2026-09-26")   # 데이터가 페이지 안에 그대로


if __name__ == "__main__":
    unittest.main()
