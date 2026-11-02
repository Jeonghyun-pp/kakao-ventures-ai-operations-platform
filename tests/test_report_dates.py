"""KST reports include both UTC dates, but neither adjacent local day."""
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kvlib import judgments, ledger
from kvlib.gate import Gate
from skills.appstore import run as appstore
from skills.bplan import run as bplan
from skills.fund import run as fund
from skills.linkedin import run as linkedin
from skills.memo import run as memo
from skills.ops import status

DAY = "2026-11-03"
STAMPS = ("2026-11-02T14:59:59Z", "2026-11-02T15:00:00Z",
          "2026-11-03T14:59:59Z", "2026-11-03T15:00:00Z")


def seed(con):
    con.executescript(judgments.SCHEMA)
    for i, ts in enumerate(STAMPS):
        key = f"sample{i}"
        con.execute("INSERT INTO actions VALUES(?,?,?,?,?)", (key, "done", "done", "{}", ts))
        extra = json.dumps({"discarded_sample": [key], "changed_in_hwp": [key]})
        for run in (judgments.HUMAN, judgments.LIVE):
            con.execute("INSERT INTO judgments(key,run,created,extra,verdict,reason,input_text,ok) VALUES(?,?,?,?,?,?,?,?)",
                        (key, run, ts, extra, "foreign", key,
                         json.dumps({"app_name": key, "seller_name": "fake", "seller_url": "example.invalid"}), 1))
    con.commit()


class ReportDates(unittest.TestCase):
    def test_bounds_roll_over_month_and_year(self):
        for day, bounds in ((DAY, (STAMPS[1], STAMPS[3])),
                            ("2027-01-01", ("2026-12-31T15:00:00Z", "2027-01-01T15:00:00Z")),
                            ("2024-03-01", ("2024-02-29T15:00:00Z", "2024-03-01T15:00:00Z"))):
            self.assertEqual(ledger.day_bounds(day), bounds)

    @contextlib.contextmanager
    def report_case(self, mod, task):
        with tempfile.TemporaryDirectory() as d, contextlib.ExitStack() as stack:
            folder = Path(d)
            con = sqlite3.connect(":memory:")
            stack.callback(con.close)
            gate = Gate(con, task, force="off")
            seed(con)
            stack.enter_context(mock.patch.object(ledger, "today", return_value=DAY))
            stack.enter_context(mock.patch.object(mod.report, "daily", return_value="off"))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            if mod is linkedin:
                stack.enter_context(mock.patch.object(mod.paths, "data", return_value=folder))
            else:
                stack.enter_context(mock.patch.object(mod, "_reports_dir", return_value=folder))
            yield gate, folder

    def test_memo_actions_and_human_feedback(self):
        with self.report_case(memo, "memo") as (g, folder):
            memo.daily_report(g)
            text = (folder / f"{DAY}.md").read_text()
            for i in (1, 2):
                self.assertIn(f"- sample{i}", text)
                self.assertIn(f"`sample{i}`", text)
            for i in (0, 3):
                self.assertNotIn(f"sample{i}", text)
            self.assertIn("완료 2", text)

    def test_fund_actions_and_human_feedback(self):
        with self.report_case(fund, "fund") as (g, folder):
            fund.daily_report(g)
            text = (folder / f"{DAY}.md").read_text()
            for i in (1, 2):
                self.assertIn(f"- sample{i}", text)
                self.assertIn(f"`sample{i}`", text)
            for i in (0, 3):
                self.assertNotIn(f"sample{i}", text)
            self.assertIn("완료 2", text)

    def test_appstore_judgments(self):
        with self.report_case(appstore, "appstore") as (g, folder):
            appstore.daily_report(g, landed=0, candidates=0, fresh=0, unknowns=[], attempts=0)
            text = (folder / f"{DAY}.md").read_text()
            self.assertIn("foreign 2", text)
            for i in (1, 2):
                self.assertIn(f"sample{i}", text)
            for i in (0, 3):
                self.assertNotIn(f"sample{i}", text)

    def test_bplan_judgments_and_actions(self):
        with self.report_case(bplan, "bplan") as (g, folder):
            bplan.daily_report(g, {})
            text = (folder / f"{DAY}.md").read_text()
            self.assertIn("오늘 판정 2", text)
            self.assertIn("done:done 2", text)

    def test_linkedin_actions_and_ledger_events(self):
        with self.report_case(linkedin, "linkedin") as (g, folder):
            for i, ts in enumerate(STAMPS):
                # Deliberately wrong explicit day must not override the event timestamp.
                with mock.patch.object(ledger, "now", return_value=ts):
                    ledger.append("linkedin", f"date-{i}", "outreach", day=DAY)
            with mock.patch.object(linkedin, "_reaction_week", return_value={"n": 0, "seen_rate": None, "reaction_yes": 0, "reaction_no": 0, "expiry": 0, "explore": (0, 0), "late": 0}):
                linkedin.daily_report(g, pool=0, picked=0)
            text = (folder / f"{DAY}.md").read_text()
            self.assertIn("초안 2", text)
            self.assertIn("done:done 2", text)

    def test_status_counts_and_unacknowledged_requests(self):
        import schedule
        from skills.ops import run as ops
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "test.sqlite"
            con = sqlite3.connect(db)
            Gate(con, "memo")
            seed(con)
            con.execute("UPDATE actions SET kind='request'")
            con.commit(); con.close()
            with mock.patch.object(status, "SKILLS", (("memo", "memo", False),)), mock.patch.object(status, "_db", side_effect=lambda _: sqlite3.connect(db)), mock.patch.object(schedule, "declared", return_value=[]), mock.patch.object(schedule, "loaded", return_value=set()), mock.patch.object(ops, "backup_age_days", return_value=0):
                result = status.gather(DAY)
            self.assertEqual(result["skills"][0]["actions"], {"request:done": 2})
            self.assertEqual(result["skills"][0]["judged_today"], 2)
            self.assertEqual(result["skills"][0]["human"], 4)  # cumulative, not daily
            self.assertIn("접수 답글 없는 요청 2건", result["gaps"][0])
