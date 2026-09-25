"""꺼짐 알림 — 평일에 보고서가 없으면 한 줄, 주말은 조용히."""
from __future__ import annotations

import unittest
from unittest import mock

from kvlib import ledger
from skills.linkedin import run
from tests._linkedin_fakes import Case, SAT


class Health(unittest.TestCase):
    def test_weekday_without_report_alerts_and_with_report_is_quiet(self):
        with Case() as c, mock.patch.object(ledger, "today", lambda: "2026-09-24"):          # 목요일 — 오늘이 주말이어도 검사가 돈다
            self.assertEqual(run.main(["health"]), 2)
            (c.data / "linkedin" / "reports").mkdir(parents=True, exist_ok=True)
            (c.data / "linkedin" / "reports" / "2026-09-24.md").write_text("보고서")
            self.assertEqual(run.main(["health"]), 0)
        self.assertEqual([p[0] for p in c.slack.posts], ["test"])
        self.assertIn("안 돌았습니다", c.slack.posts[0][1])

    def test_weekend_is_quiet(self):
        with Case(today=SAT) as c:
            with mock.patch.object(ledger, "today", lambda: "2026-09-26"):
                self.assertEqual(run.main(["health"]), 0)
        self.assertEqual(c.slack.posts, [])
