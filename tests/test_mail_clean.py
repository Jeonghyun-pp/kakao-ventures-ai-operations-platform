"""clean.py — 합성 입력. 실제 폼 3건은 리포 밖 자료로 길이만 잰다(내용은 안 찍는다)."""
from __future__ import annotations

import json
import unittest

from kvlib import paths
from skills.mail import clean, rules

WEBFLOW = (
    "You have a new form submission. 문의 유형 : 투자 문의\n"
    "이메일 : founder@example.com\n"
    "문의 제목 : 투자 검토 요청\n"
    "문의 내용 : 안녕하세요.\n"
    "\n"
    "저희는 B2B SaaS 를 만드는 팀입니다.\n"
    "시드 라운드 검토를 부탁드립니다.\n"
    "\n"
    "If you believe this is a spam submission, please forward it.\n"
    " Unsubscribe  from notifications for this site.\n"
)


class Text(unittest.TestCase):
    def test_strips_tag_and_entity_residue(self):
        out = clean.text("안녕하세요<o:p></o:p>&nbsp;투자&amp;제휴​ 문의\xa0드립니다")
        self.assertEqual(out, "안녕하세요 투자&제휴 문의 드립니다")

    def test_keeps_angle_brackets_that_are_not_tags(self):
        self.assertIn("3 < 5", clean.text("3 < 5 이고 a > b"))

    def test_collapses_blank_runs(self):
        self.assertEqual(clean.text("가\n\n\n\n나"), "가\n\n나")


class SplitQuoted(unittest.TestCase):
    def test_no_marker_is_all_new(self):
        self.assertEqual(clean.split_quoted("새 글만 있다"), ("새 글만 있다", ""))

    def test_markers(self):
        for marker in ("On Mon, Sep 1, 2026 at 9:00 AM Kim <k@example.com> wrote:",
                       "2026년 9월 1일 (월) 오전 9:00, 김 <k@example.com>님이 작성:",
                       "-----Original Message-----",
                       "---------- Forwarded message ---------",
                       "From: Kim <k@example.com>\nSent: Monday",
                       "> 지난 메일\n> 둘째 줄"):
            new, old = clean.split_quoted(f"재문의드립니다.\n\n{marker}\n예전 내용")
            self.assertEqual(new, "재문의드립니다.", marker)
            self.assertTrue(old.startswith(marker.split("\n")[0]), marker)

    def test_marker_at_top_keeps_everything(self):
        """자기 메일을 그대로 재전송한 경우 — 인용문이 읽을 전부다(실측: 기술지주 소개 건)."""
        s = "-----Original Message-----\n투자 검토를 부탁드립니다."
        self.assertEqual(clean.split_quoted(s), (s, ""))


class Webflow(unittest.TestCase):
    def test_content_runs_to_the_footer(self):
        f = clean.webflow(WEBFLOW)
        self.assertEqual(f["content"], "안녕하세요.\n\n저희는 B2B SaaS 를 만드는 팀입니다.\n시드 라운드 검토를 부탁드립니다.")

    def test_other_fields_match_the_production_parser(self):
        new, old = clean.webflow(WEBFLOW), rules.parse_webflow(WEBFLOW)
        for k in ("kind", "reply_to", "title"):
            self.assertEqual(new[k], old[k], k)
        self.assertGreater(len(new["content"]), len(old["content"]))   # 이게 고친 것이다

    def test_no_content_label(self):
        self.assertEqual(clean.webflow("문의 유형 : 기타 문의\n이메일 : a@example.com\n")["content"], "")


class Clip(unittest.TestCase):
    def test_short_is_untouched(self):
        self.assertEqual(clean.clip("짧다", 10), ("짧다", 0))

    def test_keeps_head_and_tail(self):
        s = "머리" + "가" * 100 + "검토 부탁드립니다"
        out, cut = clean.clip(s, 30)
        self.assertTrue(out.startswith("머리") and out.endswith("검토 부탁드립니다"))
        self.assertEqual(cut, len(s) - 30)
        self.assertIn(f"중략 {cut}자", out)


FORMS = sorted(paths.data("mail", "fixtures", create=False).glob("webflow_C*.json"))


@unittest.skipUnless(FORMS, "실제 폼 자료가 없다 (~/kv-data/mail/fixtures/webflow_C*.json) — 건너뛴다")
class RealForms(unittest.TestCase):
    def test_content_is_most_of_the_body(self):
        for p in FORMS:
            body = json.loads(p.read_text(encoding="utf-8"))["body"]
            got = len(clean.webflow(body)["content"])
            self.assertGreater(got, len(body) // 2, p.name)                       # 절반 이상이 문의 내용이다
            self.assertGreater(got, 10 * len(rules.parse_webflow(body)["content"]), p.name)


if __name__ == "__main__":
    unittest.main()
