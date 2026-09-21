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
        self.calls, self.reactions, self.replies = [], list(reactions), []

    def slack_post(self, ch, text, thread_ts=None):
        self.calls.append(("thread" if thread_ts else "post", ch, text)); return "ts-1"

    def slack_reactions(self, ch, ts):
        return self.reactions

    def slack_replies(self, ch, ts):
        return self.replies

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

    def flow(self, hands, llm=None, now="2026-09-21T01:00:00Z"):
        f = flow.Flow(self.con, self.path, hands, now=lambda: now, llm=llm)
        self.addCleanup(f.close)
        return f

    def acts(self):
        return {(r[0], r[1]): r[2] for r in self.con.execute("SELECT message_id, kind, status FROM actions")}

    def test_gate_closed_touches_nothing(self):
        """🔴 기본값(off)에서는 어떤 손도 안 불린다. 무엇을 했을지만 남는다."""
        self.assertEqual(flow.constraints.get("mail_observe.outbound"), "off")     # 리포의 실제 기본값
        h = Hands(reactions=["x"])
        f = self.flow(h)
        r = f.advance()
        self.assertEqual((r["asked"], r["decided"], r["waiting"]), (2, 0, 2))      # 인바운드(ko)는 묻지 않는다
        f.decide("tr", "yes", by="manual")
        f.decide("ev", "no", by="manual")
        self.assertEqual(h.calls, [])
        self.assertEqual(self.acts(), {("ev", "post"): "dry", ("tr", "post"): "dry", ("tr", "draft"): "dry", ("ev", "archive"): "dry"})

    def test_slack_level_posts_but_never_touches_the_mailbox(self):
        """🔴 시험용 단계. 슬랙 글은 실제로 올라가고, ❌·⭕ 가 와도 아카이브·초안은 기록만 한다."""
        h = Hands()
        with gate("slack"):
            f = self.flow(h)
            f.advance()
            f.decide("tr", "yes", by="manual")
            f.decide("ev", "no", by="manual")
        self.assertTrue(all(c[0] in ("post", "thread") for c in h.calls), h.calls)
        self.assertEqual((self.acts()[("tr", "draft")], self.acts()[("ev", "archive")]), ("dry", "dry"))
        text = self.con.execute("SELECT detail FROM actions WHERE message_id='ev' AND kind='post'").fetchone()[0]
        self.assertIn("한 문장 요약", text)                                          # 게시했을 글이 그대로 남는다
        self.assertNotIn("자동 거절", text)                                          # 🔴 여기선 무응답을 거절로 안 바꾼다

    def test_second_advance_asks_nothing_new(self):
        f = self.flow(Hands())
        f.advance()
        self.assertEqual(f.advance()["asked"], 0)

    def test_gate_open_posts_once_and_resumes_in_a_new_process(self):
        h = Hands()
        with gate("on"):
            self.flow(h).advance()
            self.assertEqual([c[0] for c in h.calls if c[0] == "post"], ["post", "post"])
            self.assertTrue(any(c[0] == "thread" and "검토 부탁드립니다" in c[2] for c in h.calls))   # 전문은 스레드에
            n = len(h.calls)
            f2 = self.flow(h)                                                       # 다음 실행 = 새 Flow (체크포인트에서 잇는다)
            r = f2.advance()
            self.assertEqual((r["asked"], r["decided"], r["waiting"]), (0, 0, 2))
            self.assertEqual(len(h.calls), n)                                       # 🔴 다시 올리지 않았다
            h.reactions = ["white_check_mark"]
            self.assertEqual(f2.advance()["decided"], 2)
        self.assertIn(("draft", "tr", "ref@example.com"), [c[:3] for c in h.calls])  # 신뢰 경로 ⭕ → 접수 안내 초안
        self.assertEqual(self.acts()[("ev", "handoff")], "done")                     # 행사 ⭕ → 사람이 잇는다
        self.assertEqual(self.flow(h).advance()["unclaimed"], 1)                     # 🙋 가 올 때까지 미처리로 남는다
        self.assertNotIn("archive", [c[0] for c in h.calls])

    def test_no_reaction_archives_without_reply(self):
        h = Hands(reactions=["x"])
        with gate("on"):
            self.flow(h).advance()
        self.assertEqual(sorted(c[0] for c in h.calls if c[0] != "thread"), ["archive", "archive", "post", "post"])

    def test_dry_question_is_posted_when_the_gate_opens(self):
        h = Hands()
        self.flow(h).advance()                                                       # 닫힌 채로 하루
        self.assertEqual(h.calls, [])
        with gate("on"):
            self.flow(h).advance()
        self.assertEqual([c[0] for c in h.calls if c[0] == "post"], ["post", "post"])

    def test_failed_post_is_recorded_and_retried(self):
        h = Hands()
        heads = iter([RuntimeError("not_in_channel"), "ts-2", RuntimeError("not_in_channel"), "ts-3"])

        def post(ch, text, thread_ts=None):
            if thread_ts:
                return "t"
            r = next(heads)
            if isinstance(r, Exception):
                raise r
            return r
        h.slack_post = post
        with gate("on"):
            f = self.flow(h)
            f.advance()
            self.assertIn("failed", self.acts().values())
            f.advance()
        self.assertEqual(set(v for (m, k), v in self.acts().items() if k == "post"), {"done"})

    def test_thread_failure_does_not_repost_the_question(self):
        h, heads = Hands(), []

        def post(ch, text, thread_ts=None):
            if thread_ts:
                raise RuntimeError("msg_too_long")
            heads.append(text)
            return "ts-1"
        h.slack_post = post
        with gate("on"):
            f = self.flow(h)
            f.advance()
            f.advance()
        self.assertEqual(len(heads), 2)                                              # 두 통, 각각 한 번씩만
        self.assertEqual(set(v for (m, k), v in self.acts().items() if k == "post"), {"done"})

    def test_handoff_stays_open_until_someone_claims_it(self):
        h = Hands()
        with gate("slack"):
            f = self.flow(h)
            f.advance()
            f.decide("ev", "yes", by="manual", note="온라인이면 가능")
            self.assertIn("온라인이면 가능", [c for c in h.calls if c[0] == "thread"][-1][2])   # 조건이 인계 글에 실린다
            self.assertEqual(f.advance()["unclaimed"], 1)
            h.reactions = ["raising_hand"]
            r = self.flow(h).advance()
        self.assertEqual((r["acked"], r["unclaimed"]), (1, 0))
        self.assertEqual(self.acts()[("ev", "ack")], "done")

    def test_free_text_reply_is_interpreted_once(self):
        h, asked = Hands(), []

        def llm(prompt, system):
            asked.append(prompt)
            return "DECISION: yes\nNOTE: 다음 주 온라인으로"
        h.replies = [{"ts": "2", "user": "U1", "text": "다음 주 온라인으로 잡아주세요"}, {"ts": "3", "user": None, "text": "봇이 쓴 전문"}]
        with gate("slack"):
            f = self.flow(h, llm=llm)
            self.assertEqual(f.advance()["decided"], 2)
            f.advance()
        self.assertEqual(len(asked), 2)                                              # 메일당 한 번. 같은 답글을 다시 묻지 않는다
        self.assertNotIn("봇이 쓴 전문", asked[0])
        self.assertIn("<<<", asked[0])
        self.assertIn("다음 주 온라인으로", [c for c in h.calls if c[0] == "thread"][-1][2])

    def test_unclear_reply_asks_for_an_emoji_once_and_keeps_waiting(self):
        h = Hands()
        h.replies = [{"ts": "2", "user": "U1", "text": "흠 글쎄요"}]
        with gate("slack"):
            f = self.flow(h, llm=lambda p, s: "DECISION: unclear\nNOTE: NONE")
            r = f.advance()
            f.advance()
        self.assertEqual((r["decided"], r["waiting"]), (0, 2))
        self.assertEqual(sum("⭕ / ❌ 로 알려" in c[2] for c in h.calls if c[0] == "thread"), 2)   # 메일당 한 번씩만

    def test_silence_is_reminded_never_declined(self):
        h = Hands()
        with gate("slack"):
            self.flow(h, now="2026-09-21T01:00:00Z").advance()
            self.assertEqual(self.flow(h, now="2026-09-22T01:00:00Z").advance()["reminded"], 0)   # 하루 — 아직
            self.assertEqual(self.flow(h, now="2026-09-23T02:00:00Z").advance()["reminded"], 2)
            for day in ("25", "27", "29", "30"):
                self.flow(h, now=f"2026-09-{day}T03:00:00Z").advance()
        self.assertEqual(sum(c[2].startswith("⏰") for c in h.calls if c[0] == "thread"), 6)        # 메일당 3번에서 멈춘다
        self.assertFalse([c for c in h.calls if c[0] in ("archive", "draft")])                    # 🔴 무응답은 거절이 아니다

    def test_decide_on_a_mail_that_is_not_waiting_is_refused(self):
        with self.assertRaises(ValueError):
            self.flow(Hands()).decide("ko", "yes", by="manual")


if __name__ == "__main__":
    unittest.main()


class Mention(unittest.TestCase):
    def test_name_needs_an_honorific_and_email_does_not(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", encoding="utf-8") as f:
            f.write('- { names: [홍길동, 길동], email: gd@example.com, slack_id: "U123" }\n- { names: [앤, reviewer-three], email: a@example.com, slack_id: "" }\n')
            f.flush()
            with mock.patch.object(flow, "PEOPLE", Path(f.name)):
                self.assertEqual(flow.mention("홍길동 심사역님 연사 참여 요청"), "<@U123>")
                self.assertEqual(flow.mention("cc gd@example.com"), "<@U123>")
                self.assertEqual(flow.mention("앤님께 전달 부탁드립니다"), "*앤*")               # ID 가 없으면 이름만
                self.assertEqual(flow.mention("channel partner 홍길동전 상영"), "")             # 호칭 없는 부분일치는 안 잡는다

    def test_repo_roster_parses(self):
        self.assertIsInstance(flow.mention("아무 글"), str)


class Report(unittest.TestCase):
    def test_report_switch(self):
        h = Hands()
        real = flow.constraints.get
        for value, want, n in (("off", "꺼짐", 0), ("on", "게시함", 1)):
            with mock.patch.object(flow.constraints, "get", lambda k, v=value: v if k == "mail_observe.report_slack" else real(k)):
                self.assertEqual(flow.post_report("요약", io=h), want)
            self.assertEqual(len(h.calls), n)
        self.assertEqual(h.calls[0][:2], ("post", "test"))                # 보고는 test 채널로만

    def test_report_failure_does_not_raise(self):
        h = Hands()
        h.slack_post = mock.Mock(side_effect=RuntimeError("not_in_channel"))
        self.assertTrue(flow.post_report("요약", io=h).startswith("실패("))
