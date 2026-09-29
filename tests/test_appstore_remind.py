"""앱스토어 격주 리마인드 — 기준일부터 14일 간격, 쉬는 날이면 다음 평일, 회차는 밀리지 않는다, 한 회차 한 번."""
from __future__ import annotations

import contextlib
import io
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from kvlib import Fetched, judgments, ledger
from kvlib.gate import Gate
from skills.appstore import run


class Slack:
    def __init__(self):
        self.posts = []

    def post(self, ch, text, **kw):
        self.posts.append((ch, text))
        return Fetched.measured(f"ts-{len(self.posts)}")


@contextlib.contextmanager
def day(today: str, *, landed=(), gate_db=None):
    """today 로 시계를 맞추고 장부·슬랙·문을 가짜로. landed = [(ts, src)]"""
    d = Path(tempfile.mkdtemp())
    fixed = date.fromisoformat(today)

    class D(date):
        @classmethod
        def today(cls):
            return fixed

    db = gate_db or sqlite3.connect(":memory:")
    db.executescript(judgments.SCHEMA)
    s = Slack()
    with mock.patch.object(ledger.paths, "ledger", lambda task: d / f"{task}.jsonl"), \
         mock.patch.object(run, "date", D), mock.patch.object(run, "slack", s), \
         mock.patch.object(run, "_gate", lambda dry_run=False: Gate(db, "appstore", lambda k: k in run.SLACK_KINDS,
                                                                    force="off" if dry_run else None)), \
         contextlib.redirect_stdout(io.StringIO()):
        for i, (ts, src) in enumerate(landed):
            ledger.append("appstore", f"a{i}", ledger.LANDED, ts=ts, **({"src": src} if src else {}))
        yield s


class Remind(unittest.TestCase):
    def test_first_slot_is_holiday_so_it_goes_next_workday(self):
        with day("2026-10-05") as s:                       # 월, 개천절 대체
            run.remind()
        self.assertEqual(s.posts, [])
        with day("2026-10-06") as s:                       # 화
            self.assertEqual(run.remind(), 0)
        self.assertEqual(len(s.posts), 1)
        self.assertEqual(s.posts[0][0], "net")

    def test_next_slot_keeps_its_own_monday(self):
        """10/6 로 밀렸어도 다음 회차는 10/19(월) — 요일이 영영 밀리지 않는다."""
        db = sqlite3.connect(":memory:")
        for d, want in (("2026-10-06", 1), ("2026-10-16", 0), ("2026-10-19", 1), ("2026-10-20", 0)):
            with day(d, gate_db=db) as s:
                run.remind()
            self.assertEqual(len(s.posts), want, d)

    def test_once_per_slot_and_overdue_still_sent(self):
        db = sqlite3.connect(":memory:")
        with day("2026-10-21", gate_db=db) as s:           # 10/19 회차를 맥이 건너뛰었다
            run.remind()
            run.remind()
        self.assertEqual(len(s.posts), 1)

    def test_counts_last_14_days_without_seed(self):
        landed = [("2026-10-10T01:00:00Z", None), ("2026-10-18T01:00:00Z", None),
                  ("2026-10-18T01:00:00Z", "seed"), ("2026-10-01T01:00:00Z", None)]
        with day("2026-10-19", landed=landed) as s:
            run.remind()
        self.assertIn("*2개*", s.posts[0][1])
        self.assertIn("|[주차] 유입 추이>", s.posts[0][1])

    def test_dry_run_posts_nothing(self):
        with day("2026-10-19") as s:
            run.remind(dry_run=True)
        self.assertEqual(s.posts, [])


if __name__ == "__main__":
    unittest.main()
