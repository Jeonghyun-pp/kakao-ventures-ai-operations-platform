"""notion.CLEAR — 갱신에서 칸을 비운다. None 은 여전히 '안 건드림'."""
from __future__ import annotations

import unittest
from unittest import mock

from kv_transport import notion


class Clear(unittest.TestCase):
    def test_build_and_verify(self):
        sch = {"n": {"type": "number"}, "t": {"type": "rich_text"}, "s": {"type": "select"}}
        with mock.patch.object(notion, "schema", lambda db: sch):
            out = notion._build("db", {"n": notion.CLEAR, "t": notion.CLEAR, "s": None})
            self.assertEqual(out, {"n": {"number": None}, "t": {"rich_text": []}})          # None 칸은 안 보낸다
            page = {"properties": {"n": {"type": "number", "number": None}, "t": {"type": "rich_text", "rich_text": []}}}
            with mock.patch.object(notion, "_req", lambda *a, **k: page):
                self.assertEqual(notion._verify("db", "p", {"n": notion.CLEAR, "t": notion.CLEAR}), "")
            page["properties"]["n"]["number"] = 13
            with mock.patch.object(notion, "_req", lambda *a, **k: page):
                self.assertIn("n", notion._verify("db", "p", {"n": notion.CLEAR}))


if __name__ == "__main__":
    unittest.main()
