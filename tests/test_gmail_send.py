"""발송 통로 — 망을 안 부른다(`_req`·`_get` 을 가짜로). 이 파일은 check_intent A8b 의 허용 목록에 있다."""
from __future__ import annotations

import unittest
from unittest import mock

from kv_transport import gmail_send
from kv_transport.gmail import GmailError


def patched(req=None, get=None):
    return (mock.patch("kv_transport.gmail_send._req", req or mock.Mock()),
            mock.patch("kv_transport.gmail_send._get", get or mock.Mock()))


class SendDraft(unittest.TestCase):
    def run_with(self, req, get=None):
        a, b = patched(req, get)
        with a, b:
            return gmail_send.send_draft("d1")

    def test_sent_label_means_sent(self):
        req = mock.Mock(return_value={"id": "m9", "threadId": "t1", "labelIds": ["SENT"]})
        f = self.run_with(req)
        self.assertEqual((f.is_measured, f.require()), (True, "m9"))
        req.assert_called_once_with("POST", gmail_send._SEND, json_body={"id": "d1"})

    def test_ok_response_without_sent_label_is_unknown(self):
        """🔴 200 은 발송을 뜻하지 않는다."""
        self.assertFalse(self.run_with(mock.Mock(return_value={"id": "m9", "labelIds": ["DRAFT"]})).is_measured)

    def test_error_but_draft_is_gone_means_it_went_out(self):
        """응답을 못 받았어도 초안이 사라졌으면 나간 것이다 — 그래서 다시 보내지 않는다."""
        f = self.run_with(mock.Mock(side_effect=GmailError("망 계열로 끝까지 실패: timeout")),
                          mock.Mock(side_effect=GmailError("404 /drafts/d1: Not Found")))
        self.assertTrue(f.is_measured)
        self.assertEqual(f.meta["via"], "draft_gone")

    def test_error_and_draft_still_there_is_unknown(self):
        f = self.run_with(mock.Mock(side_effect=GmailError("503 가 계속된다")),
                          mock.Mock(return_value={"id": "d1"}))
        self.assertFalse(f.is_measured)                          # 초안은 남아 있다 — 사람이 보낼 수 있다

    def test_cannot_tell_whether_draft_exists_is_unknown(self):
        f = self.run_with(mock.Mock(side_effect=GmailError("timeout")), mock.Mock(side_effect=GmailError("500 /drafts/d1")))
        self.assertFalse(f.is_measured)


if __name__ == "__main__":
    unittest.main()
