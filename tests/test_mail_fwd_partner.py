"""심사역 지명 투자 문의 → 그 심사역에게 메일 포워드(초안) [사용자 결정 2026-10-19].
실측 사례: Founder A(홍길동) "김민준 심사역님께 전달 부탁드립니다" — AI 에이전트 창업 전 연락."""
from __future__ import annotations

import base64
import contextlib
import email
import email.header
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kv_transport import gmail
from skills.mail import flow, observe, route, rules
from tests.test_mail_flow import Hands, gate
from tests.test_mail_observe import Reader, mail

ROSTER = [{"names": ["김민준", "제로", "Zero"], "email": "reviewer-a@example.vc", "slack_id": "UZ"},
          {"names": ["이서준", "마이크", "ReviewerSix"], "email": "reviewer-b@example.vc", "slack_id": "UM"},
          {"names": ["정시우"], "email": "", "slack_id": ""}]
BODY = "김민준 심사역님께서 정리하신 AI 에이전트 기준을 보고 연락드립니다. 김민준 심사역님께 전달 부탁드립니다. 20분 통화가 가능할까요?"


def decide(body, subject="AI 에이전트 회사를 시작하기 전에 먼저 연락드립니다"):
    with mock.patch.object(route.people, "load", lambda: ROSTER):
        return route.decide({"sender": "a@example.com", "subject": subject, "body": body}, {"category": rules.INBOUND_KO, "why": ""})


class Route(unittest.TestCase):
    def test_named_partner_gets_the_mail_not_a_form_guide(self):
        act = decide(BODY)
        self.assertEqual((act["action"], act["to"], act["partners"]), ("fwd_mail", ["reviewer-a@example.vc"], ["김민준"]))

    def test_no_name_or_no_honorific_or_no_email_is_still_a_form_guide(self):
        self.assertEqual(decide("투자 검토 부탁드립니다")["action"], "reply")
        self.assertEqual(decide("제로베이스에서 시작한 회사입니다")["action"], "reply")      # 호칭 없는 부분일치는 지명이 아니다
        self.assertEqual(decide("정시우 수석심사역님께 전달 부탁드립니다")["action"], "reply")  # 명부에 메일이 없으면 보낼 곳이 없다


    def test_names_sharing_one_honorific_are_all_named(self):
        """[2026-10-22 Startup F] "이서준·김민준 심사역님" — 호칭은 뒤에 하나뿐이어도 둘 다 지명이다. 나열의 뒷말이 명부 사람이 아니면 아니다."""
        for body in ("안녕하세요, 이서준·김민준 심사역님 & 투자팀 담당자님", "이서준, 김민준 심사역님께"):
            self.assertEqual(sorted(decide(body)["to"]), ["reviewer-a@example.vc", "reviewer-b@example.vc"], body)
        self.assertEqual(decide("ReviewerSix, 대표님께 소개드립니다")["action"], "reply")


class Draft(unittest.TestCase):
    def test_forward_draft_keeps_original_and_attachments(self):
        sent = {}

        def req(method, path, json_body=None, params=None):
            sent["raw"] = json_body["message"]["raw"]; sent["thread"] = json_body["message"].get("threadId")
            return {"id": "d1"}
        got = {"id": "d1", "message": {"payload": {"headers": [{"name": "To", "value": "reviewer-a@example.vc"}]}}}
        msg = {"subject": "AI 에이전트 회사", "sender_raw": "Founder A <a@example.com>", "date": "Fri, 17 Oct 2026", "to": ["deals@example.vc"],
               "body": BODY, "thread_id": "t1"}
        with mock.patch.object(gmail, "_req", req), mock.patch.object(gmail, "_get", lambda *a, **k: got):
            f = gmail.forward_draft(msg, ["reviewer-a@example.vc"], "안녕하세요 김민준 심사역님,", [{"name": "deck.pdf", "mime": "application/pdf", "data": b"%PDF-1"}])
        self.assertEqual(f.require(), "d1")
        m = email.message_from_bytes(base64.urlsafe_b64decode(sent["raw"]))
        subject = str(email.header.make_header(email.header.decode_header(m["Subject"])))
        self.assertEqual((subject, m["To"], sent["thread"]), ("Fwd: AI 에이전트 회사", "reviewer-a@example.vc", "t1"))
        parts = list(m.walk())
        text = next(p for p in parts if p.get_content_type() == "text/plain").get_payload(decode=True).decode()
        self.assertTrue(text.startswith("안녕하세요 김민준 심사역님,"))
        self.assertIn("From: Founder A <a@example.com>", text); self.assertIn("김민준 심사역님께 전달", text)
        self.assertEqual([p.get_filename() for p in parts if p.get_filename()], ["deck.pdf"])

    def test_wrong_recipient_on_readback_is_not_success(self):
        got = {"id": "d1", "message": {"payload": {"headers": [{"name": "To", "value": "someone@else.com"}]}}}
        with mock.patch.object(gmail, "_req", lambda *a, **k: {"id": "d1"}), mock.patch.object(gmail, "_get", lambda *a, **k: got):
            self.assertFalse(gmail.forward_draft({"subject": "s"}, ["reviewer-a@example.vc"], "hi").is_measured)


class FwdHands(Hands):
    def __init__(self):
        super().__init__(); self.fwd = []

    def forward_mail(self, mid, to, intro):
        self.fwd.append((mid, tuple(to), intro.splitlines()[0])); self.calls.append(("fwd", mid)); return "d1"


class FlowCase(unittest.TestCase):
    def setUp(self):
        d = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.enterContext(mock.patch.object(route.people, "load", lambda: ROSTER))
        self.path = str(Path(d) / "mail.sqlite")
        self.con = observe.connect(self.path)
        self.addCleanup(self.con.close)
        observe.run_once(self.con, Reader([mail("ac", sender="person-a916067c398d@example.com", subject="AI 에이전트 회사", body=BODY, ms=10)]),
                         lambda p, s: "CATEGORY: INBOUND_KO\nREASON: r\nFOLLOWUP: no", days=2, max_judge=50, no_judge=False)

    def flow(self, h):
        f = flow.Flow(self.con, self.path, h, now=lambda: "2026-10-19T01:00:00Z")
        self.addCleanup(f.close)
        return f

    def kinds(self):
        return {r[0]: r[1] for r in self.con.execute("SELECT kind, status FROM actions WHERE key='ac'")}

    def test_closed_gate_records_only_and_no_form_guide(self):
        h = FwdHands()
        self.flow(h).act_all()
        self.assertEqual((self.kinds(), h.calls), ({"fwd_mail": "dry"}, []))

    def test_open_kind_makes_one_forward_draft(self):
        h = FwdHands()
        with gate("off", open_kinds=["fwd_mail"]):
            self.flow(h).act_all(); self.flow(h).act_all()
        self.assertEqual(h.fwd, [("ac", ("reviewer-a@example.vc",), "안녕하세요 김민준 심사역님,")])
        self.assertEqual(self.kinds(), {"fwd_mail": "done"})


if __name__ == "__main__":
    unittest.main()
