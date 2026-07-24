"""드랍메일 초안 MIME 생성 테스트."""
import os
import sys
import unittest
from unittest.mock import MagicMock

os.environ.setdefault("SLACK_APP_TOKEN", "xapp-test")
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SPREADSHEET_URL", "https://example.invalid/test")
os.environ.setdefault("NOTION_TOKEN", "secret_test")
os.environ.setdefault("NOTION_DATABASE_ID", "dbid_test")

# slack_bolt.App는 생성 시 auth.test를 호출하므로 import 전에 mock 처리
_fake_slack_bolt = MagicMock()
_fake_socket_mode = MagicMock()
sys.modules.setdefault("slack_bolt", _fake_slack_bolt)
sys.modules.setdefault("slack_bolt.adapter", MagicMock())
sys.modules.setdefault("slack_bolt.adapter.socket_mode", _fake_socket_mode)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main  # noqa: E402


class PathResolutionTests(unittest.TestCase):
    def test_resolve_path_anchors_relative_path_to_main_file(self):
        resolved = main.resolve_path("gmail_credentials.json")
        expected = os.path.join(os.path.dirname(os.path.abspath(main.__file__)), "gmail_credentials.json")
        self.assertEqual(str(resolved), expected)

    def test_resolve_path_keeps_absolute_path(self):
        absolute_path = "/tmp/gmail_credentials.json"
        resolved = main.resolve_path(absolute_path)
        self.assertEqual(str(resolved), absolute_path)


class TemplateToGmailHtmlTests(unittest.TestCase):
    def test_single_line(self):
        self.assertEqual(main._template_to_gmail_html("hello"), "<div>hello</div>")

    def test_empty_string(self):
        self.assertEqual(main._template_to_gmail_html(""), "<div><br></div>")

    def test_blank_line_becomes_br_div(self):
        result = main._template_to_gmail_html("a\n\nb")
        self.assertEqual(result, "<div>a</div><div><br></div><div>b</div>")

    def test_multiple_lines(self):
        result = main._template_to_gmail_html("line1\nline2")
        self.assertEqual(result, "<div>line1</div><div>line2</div>")

    def test_html_escape(self):
        result = main._template_to_gmail_html("a<b>&\"c")
        self.assertIn("&lt;b&gt;", result)
        self.assertIn("&amp;", result)
        self.assertNotIn("<b>", result)

    def test_korean_paragraphs(self):
        text = "안녕하세요 홍길동 대표님,\n카카오벤처스입니다.\n\n먼저 감사합니다."
        result = main._template_to_gmail_html(text)
        self.assertEqual(
            result,
            "<div>안녕하세요 홍길동 대표님,</div>"
            "<div>카카오벤처스입니다.</div>"
            "<div><br></div>"
            "<div>먼저 감사합니다.</div>",
        )

    def test_long_paragraph_stays_one_div(self):
        long_line = "보내주신 이메일을 바탕으로 내부 검토 과정을 거쳤으나" * 10
        result = main._template_to_gmail_html(long_line)
        self.assertEqual(result.count("<div>"), 1)
        self.assertEqual(result.count("</div>"), 1)
        self.assertNotIn("<br>", result)


class BuildDropMailMimeTests(unittest.TestCase):
    def test_mime_is_multipart_alternative(self):
        mime = main._build_drop_mail_mime(
            to_email="ceo@example.com",
            subject="Re: test",
            ceo_name="홍길동",
            lang="ko",
            in_reply_to=None,
        )
        self.assertTrue(mime.is_multipart())
        self.assertEqual(mime.get_content_type(), "multipart/alternative")

    def test_mime_contains_plain_and_html(self):
        mime = main._build_drop_mail_mime(
            to_email="ceo@example.com",
            subject="Re: test",
            ceo_name="홍길동",
            lang="ko",
            in_reply_to=None,
        )
        parts = mime.get_payload()
        self.assertEqual(len(parts), 2)
        self.assertEqual(parts[0].get_content_type(), "text/plain")
        self.assertEqual(parts[1].get_content_type(), "text/html")

    def test_plain_body_preserves_template(self):
        mime = main._build_drop_mail_mime(
            to_email="ceo@example.com",
            subject="Re: test",
            ceo_name="홍길동",
            lang="ko",
            in_reply_to=None,
        )
        plain = mime.get_payload()[0].get_payload(decode=True).decode("utf-8")
        self.assertIn("홍길동 대표님", plain)
        self.assertIn("\n\n", plain)  # 문단 구분 유지
        self.assertIn("카카오벤처스 드림", plain)

    def test_html_body_uses_div_structure(self):
        mime = main._build_drop_mail_mime(
            to_email="ceo@example.com",
            subject="Re: test",
            ceo_name="홍길동",
            lang="ko",
            in_reply_to=None,
        )
        html = mime.get_payload()[1].get_payload(decode=True).decode("utf-8")
        self.assertIn("<div>", html)
        self.assertIn("<div><br></div>", html)
        self.assertIn("홍길동 대표님", html)
        self.assertNotIn("<p>", html)

    def test_en_template_used_when_lang_en(self):
        mime = main._build_drop_mail_mime(
            to_email="ceo@example.com",
            subject="Re: test",
            ceo_name="John",
            lang="en",
            in_reply_to=None,
        )
        plain = mime.get_payload()[0].get_payload(decode=True).decode("utf-8")
        self.assertIn("Hello John", plain)
        self.assertIn("Kakao Ventures", plain)

    def test_ceo_name_escaped_in_html(self):
        mime = main._build_drop_mail_mime(
            to_email="ceo@example.com",
            subject="Re: test",
            ceo_name="<script>",
            lang="ko",
            in_reply_to=None,
        )
        html = mime.get_payload()[1].get_payload(decode=True).decode("utf-8")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_reply_headers_when_in_reply_to_given(self):
        mime = main._build_drop_mail_mime(
            to_email="ceo@example.com",
            subject="Re: test",
            ceo_name="홍길동",
            lang="ko",
            in_reply_to="<person-f83999e669e1@example.com>",
        )
        self.assertEqual(mime["In-Reply-To"], "<person-f83999e669e1@example.com>")
        self.assertEqual(mime["References"], "<person-f83999e669e1@example.com>")

    def test_no_reply_headers_when_in_reply_to_none(self):
        mime = main._build_drop_mail_mime(
            to_email="ceo@example.com",
            subject="Re: test",
            ceo_name="홍길동",
            lang="ko",
            in_reply_to=None,
        )
        self.assertIsNone(mime["In-Reply-To"])
        self.assertIsNone(mime["References"])


if __name__ == "__main__":
    unittest.main()
