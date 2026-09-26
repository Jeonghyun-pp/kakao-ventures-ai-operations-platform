"""하루 보고서 — 첫 줄은 '드랍이라 했는데 심사역이 살린 건'."""
from __future__ import annotations

import unittest
from unittest import mock

from kvlib import Fetched, ledger
from skills.bplan import run
from tests._bplan_fakes import Case
from tests.test_bplan_judge import ROW
from tests.test_bplan_act import RED


class Report(unittest.TestCase):
    def test_saved_by_partner_is_the_headline(self):
        red = {**ROW, "_page_id": "p2", "접수키": "k2", "to do": "미팅요청"}                      # 모델은 Red, 심사역은 미팅요청
        with Case(notion=[red]) as c, mock.patch.object(run.paths, "data", lambda *a, **k: (c.dir.joinpath(*a).mkdir(parents=True, exist_ok=True) or c.dir.joinpath(*a))):
            run.judge_rows(c.db, [red], [red], llm=lambda crit, data: RED, deck=lambda u: Fetched.measured(""))
            run.label(c.db, [red])
            run.daily_report(c.gate, {"score": 1})
            md = (c.dir / f"{ledger.today()}.md").read_text()
        self.assertIn("드랍이라 했는데 심사역이 살린 건 1", md)
        self.assertIn("- `k2`", md.split("## 🚩")[1].split("##")[0])
        self.assertIn("아직", md.split("## 발송 승격")[1])
        self.assertTrue(any("*비플랜 하루*" in p[1] and p[0] == "test" for p in c.slack.posts))


if __name__ == "__main__":
    unittest.main()
