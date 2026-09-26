"""slack 파일 통로 — 봇 파일 제외 · 로그인 HTML 은 실패 · 업로드 3단계. `_call`·`requests` 만 가짜로."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kv_transport import slack


class R:
    def __init__(self, status=200, ctype="application/pdf", content=b"x"):
        self.status_code, self.headers, self.content = status, {"content-type": ctype}, content


class Files(unittest.TestCase):
    def setUp(self):
        slack.register("t", "C1")

    def test_thread_files_skips_bot_files(self):
        msgs = {"messages": [{"ts": "1", "user": "U1", "files": [{"id": "F1", "name": "덱.pdf", "url_private": "u"}]},
                             {"ts": "2", "bot_id": "B", "files": [{"id": "F2", "name": "초안.docx", "url_private": "u2"}]}]}
        with mock.patch.object(slack, "_call", lambda *a, **k: msgs):
            self.assertEqual([f["id"] for f in slack.thread_files("t", "1").require()], ["F1"])

    def test_download_rejects_login_html(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(slack.secrets, "get", lambda *a, **k: "tok"):
            with mock.patch.object(slack.requests, "get", lambda *a, **k: R(ctype="text/html; charset=utf-8")):
                self.assertFalse(slack.download("u", Path(d) / "a.pdf").is_measured)
            with mock.patch.object(slack.requests, "get", lambda *a, **k: R(content=b"%PDF")):
                self.assertEqual(slack.download("u", Path(d) / "a.pdf").require().read_bytes(), b"%PDF")

    def test_upload_three_steps(self):
        calls = []
        def call(method, path, **kw):
            calls.append(path)
            return {"upload_url": "https://up", "file_id": "F9"} if "getUploadURL" in path else {"ok": True}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(slack, "_call", call), \
                mock.patch.object(slack.requests, "post", lambda *a, **k: R()), mock.patch.object(slack.secrets, "no_send", lambda: False):
            p = Path(d) / "v1.docx"; p.write_bytes(b"docx")
            self.assertEqual(slack.upload("t", p, thread_ts="1").require(), "F9")
        self.assertEqual(calls, ["/files.getUploadURLExternal", "/files.completeUploadExternal"])


if __name__ == "__main__":
    unittest.main()
