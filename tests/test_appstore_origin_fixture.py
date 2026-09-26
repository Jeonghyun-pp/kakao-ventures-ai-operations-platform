"""국적 규칙 — 고정 정답 199건(2026 w34 실측). 통과선: **한국 유실 0** · 외국 제거 100건 이상(지금 105/126).
규칙을 고치면 여기서 먼저 잡힌다. `tools/kr_origin_eval.py --verbose` 가 같은 걸 건별로 보여준다."""
from __future__ import annotations

import json
import unittest

from skills.appstore import kr_origin


class OriginFixture(unittest.TestCase):
    def test_no_korean_app_is_lost_and_most_foreign_are_removed(self):
        rows = json.loads(kr_origin.FIXTURE.read_text(encoding="utf-8"))["rows"]
        verdicts = {r["app_id"]: kr_origin.classify(r["seller_name"], r["seller_url"], r["app_name"], r["description"])[0] for r in rows}
        lost = [r["app_name"] for r in rows if r["label"] == "kr" and verdicts[r["app_id"]] == "foreign"]
        removed = sum(1 for r in rows if r["label"] == "foreign" and verdicts[r["app_id"]] == "foreign")
        self.assertEqual(lost, [])
        self.assertGreaterEqual(removed, 100)


if __name__ == "__main__":
    unittest.main()
