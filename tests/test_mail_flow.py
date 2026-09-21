"""flow.py — 가짜 손(io)으로. 슬랙도 메일함도 안 닿는다. 임시 DB 파일을 쓴다(체크포인터가 연결을 따로 연다)."""
from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skills.mail import flow, observe
from tests.test_mail_observe import LLM, Reader, mail


class Hands:
    def __init__(self, reactions=()):
        self.calls, self.reactions = [], list(reactions)

    def slack_post(self, ch, text):
        self.calls.append(("post", ch)); return "ts-1"

    def slack_reactions(self, ch, ts):
        return self.reactions

    def draft_reply(self, mid, to, body):
        self.calls.append(("draft", mid, to, body[:12])); return "draft-1"

    def archive(self, mid):
        self.calls.append(("archive", mid)); return True


def gate(value):
    real = flow.constraints.get
    return mock.patch.object(flow.constraints, "get",
                             lambda k: value if k == "mail_observe.outbound" else real(k))


class FlowCase(unittest.TestCase):
    def setUp(self):
        d = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.path = str(Path(d) / "mail.sqlite")
        self.con = observe.connect(self.path)
        self.addCleanup(self.con.close)
        msgs = [mail("ev", subject="연사 참여 요청", ms=10), mail("tr", sender="ref@example.com", subject="소개드립니다", ms=20),
                mail("ko", sender="f@example.com", ms=30)]
        cats = {"연사 참여 요청": "EVENT_SPEAKER", "소개드립니다": "TRUSTED_REF"}
        llm = LLM()
        llm.__class__ = type("L", (LLM,), {"__call__": lambda s, p, sy: "CATEGORY: %s\nREASON: 한 문장 요약\nFOLLOWUP: no" % next(
            (c for k, c in cats.items() if f"제목: {k}" in p), "INBOUND_KO")})
        observe.run_once(self.con, Reader(msgs), llm, days=2, max_judge=50, no_judge=False)

    def flow(self, hands):
        f = flow.Flow(self.con, self.path, hands, now=lambda: "t")
        self.addCleanup(f.close)
        return f

    def acts(self):
        return {(r[0], r[1]): r[2] for r in self.con.execute("SELECT message_id, kind, status FROM actions")}

    def test_gate_closed_touches_nothing(self):
        """🔴 기본값(off)에서는 어떤 손도 안 불린다. 무엇을 했을지만 남는다."""
        self.assertEqual(flow.constraints.get("mail_observe.outbound"), "off")     # 리포의 실제 기본값
        h = Hands(reactions=["x"])
        f = self.flow(h)
        self.assertEqual(f.advance(), {"asked": 2, "decided": 0, "waiting": 2})    # 인바운드(ko)는 묻지 않는다
        f.decide("tr", "yes", by="manual")
        f.decide("ev", "no", by="manual")
        self.assertEqual(h.calls, [])
        self.assertEqual(self.acts(), {("ev", "post"): "dry", ("tr", "post"): "dry", ("tr", "draft"): "dry", ("ev", "archive"): "dry"})
        text = self.con.execute("SELECT detail FROM actions WHERE message_id='ev' AND kind='post'").fetchone()[0]
        self.assertIn("한 문장 요약", text)                                          # 게시했을 글이 그대로 남는다

    def test_second_advance_asks_nothing_new(self):
        f = self.flow(Hands())
        f.advance()
        self.assertEqual(f.advance()["asked"], 0)

    def test_gate_open_posts_once_and_resumes_in_a_new_process(self):
        h = Hands()
        with gate("on"):
            self.flow(h).advance()
            self.assertEqual([c[0] for c in h.calls], ["post", "post"])
            f2 = self.flow(h)                                                       # 다음 실행 = 새 Flow (체크포인트에서 잇는다)
            self.assertEqual(f2.advance(), {"asked": 0, "decided": 0, "waiting": 2})
            self.assertEqual(len(h.calls), 2)                                       # 🔴 다시 올리지 않았다
            h.reactions = ["white_check_mark"]
            self.assertEqual(f2.advance()["decided"], 2)
        self.assertIn(("draft", "tr", "ref@example.com"), [c[:3] for c in h.calls])  # 신뢰 경로 ⭕ → 접수 안내 초안
        self.assertEqual(self.acts()[("ev", "handoff")], "done")                     # 행사 ⭕ → 사람이 잇는다
        self.assertNotIn("archive", [c[0] for c in h.calls])

    def test_no_reaction_archives_without_reply(self):
        h = Hands(reactions=["x"])
        with gate("on"):
            self.flow(h).advance()
        self.assertEqual(sorted(c[0] for c in h.calls), ["archive", "archive", "post", "post"])

    def test_dry_question_is_posted_when_the_gate_opens(self):
        h = Hands()
        self.flow(h).advance()                                                       # 닫힌 채로 하루
        self.assertEqual(h.calls, [])
        with gate("on"):
            self.flow(h).advance()
        self.assertEqual([c[0] for c in h.calls], ["post", "post"])

    def test_failed_post_is_recorded_and_retried(self):
        h = Hands()
        h.slack_post = mock.Mock(side_effect=[RuntimeError("not_in_channel"), "ts-2", RuntimeError("not_in_channel"), "ts-3"])
        with gate("on"):
            f = self.flow(h)
            f.advance()
            self.assertIn("failed", self.acts().values())
            f.advance()
        self.assertEqual(set(v for (m, k), v in self.acts().items() if k == "post"), {"done"})

    def test_decide_on_a_mail_that_is_not_waiting_is_refused(self):
        with self.assertRaises(ValueError):
            self.flow(Hands()).decide("ko", "yes", by="manual")


if __name__ == "__main__":
    unittest.main()


class Report(unittest.TestCase):
    def test_report_switch(self):
        h = Hands()
        real = flow.constraints.get
        for value, want, n in (("off", "꺼짐", 0), ("on", "게시함", 1)):
            with mock.patch.object(flow.constraints, "get", lambda k, v=value: v if k == "mail_observe.report_slack" else real(k)):
                self.assertEqual(flow.post_report("요약", io=h), want)
            self.assertEqual(len(h.calls), n)
        self.assertEqual(h.calls[0], ("post", "test"))                    # 보고는 test 채널로만

    def test_report_failure_does_not_raise(self):
        h = Hands()
        h.slack_post = mock.Mock(side_effect=RuntimeError("not_in_channel"))
        self.assertTrue(flow.post_report("요약", io=h).startswith("실패("))
