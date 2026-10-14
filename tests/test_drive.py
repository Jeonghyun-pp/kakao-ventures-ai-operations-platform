"""drive 통로 — 링크 해석과 형식 분기. 망·pdftotext 는 가짜."""
from __future__ import annotations

import unittest
from unittest import mock

from kv_transport import drive
from kvlib import Fetched


class R:
    def __init__(self, js=None, content=b""):
        self._js, self.content = js, content

    def json(self):
        return self._js


class Drive(unittest.TestCase):
    def test_file_id_forms(self):
        for u in ("https://drive.google.com/open?id=EXAMPLE_ID_4849e86934d1",
                  "https://drive.google.com/file/d/EXAMPLE_ID_4849e86934d1/view?usp=sharing"):
            self.assertEqual(drive.file_id(u), "EXAMPLE_ID_4849e86934d1")
        self.assertEqual(drive.file_id("https://example.com/deck.pdf"), "")

    def test_pdf_is_downloaded_and_read(self):
        calls = []

        def get(path, params=None):
            calls.append((path, params))
            return R({"name": "덱.pdf", "mimeType": "application/pdf", "size": "1000"}) if "fields" in (params or {}) else R(content=b"%PDF")
        with mock.patch.object(drive, "_get", get), mock.patch.object(drive, "_pdf_text", lambda data: "문제 정의 " * 100):
            f = drive.text("https://drive.google.com/open?id=ABCDEFGHIJKLMNOP")
        self.assertTrue(f.is_measured and f.require().startswith("문제 정의"))
        self.assertEqual(calls[1][1], {"alt": "media"})

    def test_google_slides_are_exported_as_pdf(self):
        def get(path, params=None):
            return R({"name": "덱", "mimeType": "application/vnd.google-apps.presentation", "size": "0"}) if "fields" in (params or {}) else R(content=b"%PDF")
        with mock.patch.object(drive, "_get", get), mock.patch.object(drive, "_pdf_text", lambda data: "x" * 500):
            self.assertTrue(drive.text("https://drive.google.com/open?id=ABCDEFGHIJKLMNOP").is_measured)

    def test_image_only_deck_and_unsupported_type_are_facts_not_failures(self):
        with mock.patch.object(drive, "_get", lambda p, params=None: R({"name": "d", "mimeType": "application/pdf", "size": "10"}) if "fields" in (params or {}) else R(content=b"%PDF")), \
                mock.patch.object(drive, "_pdf_text", lambda data: "  "):
            f = drive.text("https://drive.google.com/open?id=ABCDEFGHIJKLMNOP")
        self.assertEqual((f.require(), f.meta["why"][:6]), ("", "텍스트 없음"))
        with mock.patch.object(drive, "_get", lambda p, params=None: R({"name": "d.pptx", "mimeType": "application/vnd.openxmlformats-officedocument.presentationml.presentation", "size": "10"})):
            f = drive.text("https://drive.google.com/open?id=ABCDEFGHIJKLMNOP")
        self.assertIn("지원하지 않는 형식", f.meta["why"])

    def test_image_deck_and_image_file_go_to_transcribe_in_a_folder_holding_only_that_file(self):
        seen = []

        def tr(p):
            seen.append(sorted(x.name for x in p.parent.iterdir()))           # 🔴 Read 권한이 묶이는 폴더에 그 파일 하나뿐
            return Fetched.measured("--- 1쪽 ---\n월매출 1.3억")
        pdf = lambda p, params=None: R({"name": "d.pdf", "mimeType": "application/pdf", "size": "10"}) if "fields" in (params or {}) else R(content=b"%PDF")
        with mock.patch.object(drive, "_get", pdf), mock.patch.object(drive, "_pdf_text", lambda data: " "):
            f = drive.text("https://drive.google.com/open?id=ABCDEFGHIJKLMNOP", transcribe=tr)
        self.assertEqual((f.require()[:10], f.meta["transcribed"], seen[0]), ("--- 1쪽 ---", True, ["deck.pdf"]))
        png = lambda p, params=None: R({"name": "d.png", "mimeType": "image/png", "size": "10"}) if "fields" in (params or {}) else R(content=b"\x89PNG")
        with mock.patch.object(drive, "_get", png):
            self.assertTrue(drive.text("https://drive.google.com/open?id=ABCDEFGHIJKLMNOP", transcribe=tr).meta["transcribed"])
            self.assertIn("이미지 파일", drive.text("https://drive.google.com/open?id=ABCDEFGHIJKLMNOP").meta["why"])   # transcribe 없으면 전과 같다

    def test_failed_transcription_is_a_fact_with_its_reason(self):
        pdf = lambda p, params=None: R({"name": "d.pdf", "mimeType": "application/pdf", "size": "10"}) if "fields" in (params or {}) else R(content=b"%PDF")
        with mock.patch.object(drive, "_get", pdf), mock.patch.object(drive, "_pdf_text", lambda data: ""):
            f = drive.text("https://drive.google.com/open?id=ABCDEFGHIJKLMNOP", transcribe=lambda p: Fetched.unknown("timeout"))
        self.assertEqual(f.require(), "")                                     # 판정은 폼만으로 이어진다
        self.assertIn("옮겨 적기 실패: timeout", f.meta["why"])

    def test_sharing_problem_is_unknown_not_empty(self):
        def get(path, params=None):
            raise drive.DriveError("403 /files/x — 공유")
        with mock.patch.object(drive, "_get", get):
            f = drive.text("https://drive.google.com/open?id=ABCDEFGHIJKLMNOP")
        self.assertFalse(f.is_measured)                                                 # 🔴 0자가 아니라 미상이다
        self.assertIn("403", f.reason)


if __name__ == "__main__":
    unittest.main()
