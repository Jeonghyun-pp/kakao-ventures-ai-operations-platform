"""notion.page_text — 속성 + 블록(토글 자식·표·모르는 타입)을 md 로. `_req` 만 가짜로."""
from __future__ import annotations

import unittest
from unittest import mock

from kv_transport import notion

PAGE = {"properties": {"회사명": {"type": "title", "title": [{"plain_text": "Corpus02"}]}, "✅ 심사": {"type": "rich_text", "rich_text": [{"plain_text": "최서연"}]},
                       "빈칸": {"type": "rich_text", "rich_text": []}}}
BLOCKS = {
    "pg": {"results": [{"id": "h", "type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "1. 미팅록"}]}},
                       {"id": "t", "type": "toggle", "has_children": True, "toggle": {"rich_text": [{"plain_text": "최초미팅 2026-04-15"}]}},
                       {"id": "x", "type": "synced_block"}], "has_more": False},
    "t": {"results": [{"id": "b", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "대표: 김광민"}]}},
                      {"id": "r", "type": "table_row", "table_row": {"cells": [[{"plain_text": "a"}], [{"plain_text": "b"}]]}}], "has_more": False},
}


class PageText(unittest.TestCase):
    def test_renders_props_and_nested_blocks(self):
        def req(method, path, **kw):
            if path == "/pages/pg": return PAGE
            return BLOCKS[path.split("/")[2]]
        with mock.patch.object(notion, "_req", req):
            out = notion.page_text("pg").require()
        self.assertEqual(out.splitlines()[:3], ["# Corpus02", "- ✅ 심사: 최서연", ""])          # 빈 속성은 안 적는다
        self.assertIn("## 1. 미팅록", out); self.assertIn("▸ 최초미팅 2026-04-15", out)
        self.assertIn("  - 대표: 김광민", out); self.assertIn("  a | b", out); self.assertIn("[synced_block]", out)   # 모르는 타입은 이름으로 남긴다

    def test_error_is_unknown(self):
        with mock.patch.object(notion, "_req", side_effect=notion.NotionError("403")):
            self.assertFalse(notion.page_text("pg").is_measured)


if __name__ == "__main__":
    unittest.main()
