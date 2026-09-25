"""판정 저장·정답 동기화·replay·diff — 모델은 가짜, 노션은 행 목록."""
from __future__ import annotations

import sqlite3
import unittest

from kvlib import Fetched
from kvlib import judgments


def con():
    c = sqlite3.connect(":memory:"); c.executescript(judgments.SCHEMA); return c


class Judgments(unittest.TestCase):
    def test_save_keeps_unjudged_as_not_ok(self):
        c = con()
        judgments.save(c, "live", "s1", {"a": "a\tline", "b": "b\tline"}, [{"key": "a", "verdict": "Catch", "reason": "r", "raw": "a\tCatch\tr"}])
        rows = {r[0]: r[1:] for r in c.execute("SELECT key, verdict, ok, error, raw_output FROM judgments")}
        self.assertEqual(rows["a"], ("Catch", 1, None, "a\tCatch\tr"))
        self.assertEqual(rows["b"], (None, 0, "판정 누락", None))

    def test_label_follows_notion_tier_both_ways(self):
        c = con()
        judgments.save(c, "live", "s1", {"a": "l"}, [{"key": "a", "verdict": "Catch", "reason": "r", "raw": ""}])
        judgments.set_page(c, "a", "p1")
        self.assertEqual(judgments.label(c, [{"_page_id": "p1", "Tier": "Cut"}], "Tier"), 1)         # 사람이 Cut 으로 고쳤다
        self.assertEqual(c.execute("SELECT verdict FROM judgments WHERE run='human'").fetchone()[0], "Cut")
        self.assertEqual(judgments.label(c, [{"_page_id": "p1", "Tier": "Catch"}], "Tier"), 0)       # 되돌리면 갈림 0 — 정답(Catch)은 남는다
        self.assertEqual(c.execute("SELECT verdict FROM judgments WHERE run='human'").fetchone()[0], "Catch")
        self.assertEqual(judgments.label(c, [{"_page_id": "없는", "Tier": "Cut"}], "Tier"), 0)        # 모르는 행은 무시

    def test_replay_uses_stored_lines_and_diff_shows_the_gap(self):
        c = con()
        judgments.save(c, "live", "s1", {"a": "a\tline", "b": "b\tline"},
                       [{"key": "a", "verdict": "Catch", "reason": "", "raw": ""}, {"key": "b", "verdict": "Wait", "reason": "", "raw": ""}])
        seen = {}

        def fake(lines, valid, criteria):
            seen["lines"] = sorted(lines)
            return Fetched.measured([{"key": "a", "verdict": "Trend", "reason": "", "raw": ""}, {"key": "b", "verdict": "Wait", "reason": "", "raw": ""}])
        r = judgments.replay(c, fake, "새 기준", "s2")
        self.assertEqual((r["run"], r["judged"], seen["lines"]), ("r-s2", 2, ["a\tline", "b\tline"]))   # 저장된 줄 그대로
        self.assertEqual(judgments.replay(c, fake, "새 기준", "s2")["judged"], 0)                        # 이어서 — 다시 안 부른다
        d = {r["key"]: (r["a"], r["b"]) for r in judgments.diff(c, "live", "r-s2")}
        self.assertEqual(d, {"a": ("Catch", "Trend"), "b": ("Wait", "Wait")})


if __name__ == "__main__":
    unittest.main()
