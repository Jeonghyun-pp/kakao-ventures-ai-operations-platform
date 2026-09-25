"""_promote() 의 현재 동작 — ✅ 받은 사람을 회사 단위로 극초기 DB 에. 중복 대조 둘(회사명·홈페이지)."""
from __future__ import annotations

import unittest

from skills.linkedin import brief, run
from tests._linkedin_fakes import Case, Notion


def case(early, **more):
    return Case(notion=Notion({run.DB: [], run.EARLY: early}), **more)


ROW = {"company": "Newco", "name": "A", "homepage": "https://new.co", "page_id": "p1", "url": "https://www.linkedin.com/in/a", "brief": "b"}


class Promote(unittest.TestCase):
    def test_creates_company_row_and_marks_linkedin_row(self):
        with case([]) as c:
            self.assertEqual(run._promote("in/a", ROW), "created")
        db, props = c.notion.created[0]
        self.assertEqual((db, props["법인(서비스)"], props["Status"], props["슬랙 발송?"]), (run.EARLY, "Newco", "2. 미팅요청", True))
        self.assertEqual(c.notion.updated[0], ("p1", {"Status": "Reviewing"}))
        self.assertEqual(c.rows("promoted")[0]["result"], "created")

    def test_duplicate_by_name_or_homepage_is_not_created(self):
        with case([{"법인(서비스)": "NEWCO", "링크": ""}]) as c:
            self.assertEqual(run._promote("in/a", ROW), "dup")
        with case([{"법인(서비스)": "다른회사", "링크": "https://new.co/"}]) as c2:
            self.assertEqual(run._promote("in/a", ROW), "dup")
        self.assertEqual((c.notion.created, c2.notion.created), ([], []))
        self.assertEqual(c.rows("promoted")[0]["result"], "dup")

    def test_stealth_uses_person_name_marked(self):
        with case([]) as c:
            run._promote("in/a", {**ROW, "company": brief.NO_COMPANY})
        self.assertEqual(c.notion.created[0][1]["법인(서비스)"], "A (스텔스)")

    def test_fake_stealth_homepage_is_not_a_match_key(self):
        with case([{"법인(서비스)": "x", "링크": "https://linkedin.com/company/stealth"}]) as c:
            self.assertEqual(run._promote("in/a", {**ROW, "homepage": "https://linkedin.com/company/stealth"}), "created")
        self.assertEqual(c.notion.created[0][1]["링크"], ROW["url"])                   # 가짜 홈페이지 대신 프로필 URL

    def test_gate_off_does_nothing(self):
        with case([], PROMOTE_ENABLED=False) as c:
            self.assertEqual(run._promote("in/a", ROW), "gated")
        self.assertEqual((c.notion.created, c.rows()), ([], []))

    def test_already_promoted(self):
        with case([]) as c:
            run._promote("in/a", ROW)
            self.assertEqual(run._promote("in/a", ROW), "already")
        self.assertEqual(len(c.notion.created), 1)


if __name__ == "__main__":
    unittest.main()
