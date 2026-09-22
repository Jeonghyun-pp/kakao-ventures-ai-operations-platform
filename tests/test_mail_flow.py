"""flow.py — 가짜 손(io)으로. 슬랙도 메일함도 안 닿는다. 임시 DB 파일을 쓴다(체크포인터가 연결을 따로 연다)."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skills.mail import flow, observe
from tests.test_mail_observe import LLM, Reader, mail


class Hands:
    def __init__(self, reactions=()):
        self.calls, self.reactions, self.replies = [], list(reactions), []
        self.send_fails, self.drafts_exist, self.after, self.legacy = False, True, 0, set()

    def slack_post(self, ch, text, thread_ts=None):
        self.calls.append(("thread" if thread_ts else "post", ch, text)); return "ts-1"

    def slack_reactions(self, ch, ts):
        return self.reactions

    def slack_replies(self, ch, ts):
        return self.replies

    def draft_reply(self, mid, to, body, subject=None):
        self.calls.append(("draft", mid, to, body[:12])); return f"draft-{mid}"

    def send_draft(self, did):
        self.calls.append(("send", did))
        if self.send_fails:
            raise RuntimeError("timeout")
        return "sent-1"

    def draft_exists(self, did):
        return self.drafts_exist

    def thread_after(self, tid, mid):
        return self.after

    def legacy_acted(self, mid):
        return mid in self.legacy

    def archive(self, mid):
        self.calls.append(("archive", mid)); return True


def gate(value, **over):
    """문과, 필요하면 다른 설정값도 바꿔 끼운다: gate("on", send_branches=["INBOUND_KO"])."""
    real = flow.constraints.get
    table = {"mail_observe.outbound": value, **{f"mail_observe.{k}": v for k, v in over.items()}}
    return mock.patch.object(flow.constraints, "get", lambda k: table[k] if k in table else real(k))


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
        self.assertEqual(self.acts(), {("ev", "post"): "dry", ("tr", "post"): "dry", ("tr", "draft"): "dry", ("ev", "archive"): "dry",
                                       ("ko", "send"): "dry"})                       # 인바운드는 묻지 않고 폼 안내를 '보냈을 것'

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

    def test_unknown_kind_is_dry_unless_the_gate_is_fully_open(self):
        """🔴 문은 허용 목록이다. 앞으로 생길 종류(예: label)를 깜빡해도 'slack' 단계에서는 안 나간다."""
        f, did = self.flow(Hands()), []
        with gate("slack"):
            self.assertEqual(f._out("ev", "label", {}, lambda: did.append(1)), "dry")
        with gate("on"):
            self.assertEqual(f._out("ev", "label", {}, lambda: did.append(1)), "done")
        self.assertEqual(did, [1])

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


class SendCase(unittest.TestCase):
    """폼 안내 자동 회신. 검사마다 하나씩."""

    ON = dict(send_branches=["INBOUND_KO", "INBOUND_EN"])

    def setUp(self):
        d = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.path = str(Path(d) / "mail.sqlite")
        self.con = observe.connect(self.path)
        self.addCleanup(self.con.close)
        msgs = [mail("a", sender="f@example.com", subject="투자 문의", ms=10),
                mail("a2", sender="f@example.com", subject="투자 문의 (자료 추가)", ms=20),         # 같은 사람이 대화를 나눠 또 보냄
                mail("fu", sender="g@example.com", subject="재문의 FOLLOW", ms=30),
                mail("t1", sender="h@example.com", subject="투자 문의 H", ms=40),
                mail("t2", t="t1", sender="deals@example.vc", subject="Re: 투자 문의 H", ms=50),   # 우리가 이미 답했다
                mail("nr", sender="no-reply@example.com", subject="투자 문의 NR", ms=60)]
        llm = lambda p, s: "CATEGORY: INBOUND_KO\nREASON: r\nFOLLOWUP: %s" % ("yes" if "FOLLOW" in p.split("제목:")[1][:40] else "no")
        observe.run_once(self.con, Reader(msgs), llm, days=2, max_judge=50, no_judge=False)

    def flow(self, hands):
        f = flow.Flow(self.con, self.path, hands, now=lambda: "2026-09-21T01:00:00Z")
        self.addCleanup(f.close)
        return f

    def sends(self):
        return {r[0]: (r[1], json.loads(r[2])) for r in self.con.execute("SELECT message_id, status, detail FROM actions WHERE kind='send'")}

    def test_eligibility_is_decided_from_the_db(self):
        h = Hands()
        r = self.flow(h).act_all()                                                 # 문은 닫혀 있다(리포 기본값)
        s = self.sends()
        self.assertEqual(h.calls, [])                                                # 🔴 아무 손도 안 불렀다
        self.assertEqual(s["a"][0], "dry")                                           # 보냈을 것
        self.assertIn("같은 수신자", s["a2"][1]["why"])
        self.assertIn("후속", s["fu"][1]["why"])
        self.assertIn("대화가 이미 진행", s["t1"][1]["why"])
        self.assertIn("수신자", s["nr"][1]["why"])
        self.assertEqual((r["would_send"], r["skipped"], r["sent"]), (1, 4, 0))

    def test_open_gate_but_empty_branch_list_sends_nothing(self):
        """🔴 허용 목록의 기본은 빈 목록이다. 문만 열어서는 안 나간다."""
        h = Hands()
        with gate("on"):
            self.flow(h).act_all()
        self.assertEqual(h.calls, [])
        self.assertIn("안 켜져", self.sends()["a"][1]["blocked"])

    def test_slack_level_never_sends(self):
        h = Hands()
        with gate("slack", **self.ON):
            self.flow(h).act_all()
        self.assertEqual([c for c in h.calls if c[0] in ("draft", "send")], [])

    def test_open_gate_and_allowed_branch_sends_once(self):
        h = Hands()
        with gate("on", **self.ON):
            f = self.flow(h)
            self.assertEqual(f.act_all()["sent"], 1)
            f.act_all()
        self.assertEqual([c[:2] for c in h.calls], [("draft", "a"), ("send", "draft-a")])   # 초안 하나, 발송 하나. 두 번째 실행은 아무것도 안 한다
        self.assertEqual(self.sends()["a"][0], "done")

    def test_never_auto_send_branch_is_skipped(self):
        real = flow.constraints.get
        with mock.patch.object(flow.constraints, "get", lambda k: ["INBOUND_KO"] if k == "gmail.never_auto_send" else real(k)):
            self.flow(Hands()).act_all()
        self.assertIn("영원히 수동", self.sends()["a"][1]["why"])

    def test_send_time_gates_block_without_sending(self):
        for setup, word in ((lambda h: h.legacy.add("a"), "현행 시스템"), (lambda h: setattr(h, "after", 1), "대화가 진행")):
            h = Hands()
            setup(h)
            f = self.flow(h)
            self.con.execute("DELETE FROM actions")
            with gate("on", **self.ON):
                f.act_all()
            self.assertEqual(h.calls, [], word)
            self.assertIn(word, self.sends()["a"][1]["blocked"])                     # dry 로 남아 다음 실행에 다시 본다

    def test_daily_cap(self):
        h = Hands()
        with gate("on", send_per_day=0, **self.ON):
            self.flow(h).act_all()
        self.assertEqual(h.calls, [])
        self.assertIn("상한", self.sends()["a"][1]["blocked"])

    def test_failed_send_keeps_the_draft_and_never_makes_a_second_one(self):
        h = Hands()
        h.send_fails = True
        with gate("on", **self.ON):
            self.flow(h).act_all()
            self.assertEqual(self.sends()["a"][0], "failed")
            h.send_fails = False
            self.flow(h).act_all()
        self.assertEqual([c[0] for c in h.calls], ["draft", "send", "send"])         # 🔴 초안은 한 번만 만들었다
        self.assertEqual(self.sends()["a"][0], "done")

    def test_vanished_draft_means_it_already_went_out(self):
        h = Hands()
        h.send_fails = True
        with gate("on", **self.ON):
            self.flow(h).act_all()
            h.drafts_exist = False                                                   # 응답은 못 받았지만 실제로는 나갔다
            self.flow(h).act_all()
        self.assertEqual([c[0] for c in h.calls], ["draft", "send"])                 # 다시 보내지 않았다
        self.assertEqual(self.sends()["a"][0], "done")


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


class ActCase(unittest.TestCase):
    """기다림 없는 행동 — 아카이브·포워드. 옛 apply 가 하던 일을 새 시스템이 '했을 것'으로 남긴다."""

    def setUp(self):
        d = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.path = str(Path(d) / "mail.sqlite")
        self.con = observe.connect(self.path)
        self.addCleanup(self.con.close)
        msgs = [mail("pt", sender="p@example.com", subject="제휴 제안", ms=10),
                mail("ad", sender="ad@example.com", subject="광고 안내", ms=20),
                mail("uk", sender="u@example.com", subject="알 수 없음", ms=30)]
        cats = {"제휴": "PARTNERSHIP", "광고": "AD_NOISE"}
        llm = lambda p, s: "CATEGORY: %s\nREASON: 요약\nFOLLOWUP: no" % next((c for k, c in cats.items() if k in p.split("제목:")[1][:20]), "UNKNOWN")
        observe.run_once(self.con, Reader(msgs), llm, days=2, max_judge=50, no_judge=False)

    def flow(self, hands):
        f = flow.Flow(self.con, self.path, hands, now=lambda: "2026-09-22T01:00:00Z")
        self.addCleanup(f.close)
        return f

    def acts(self):
        return {(r[0], r[1]): r[2] for r in self.con.execute("SELECT message_id, kind, status FROM actions")}

    def test_closed_gate_records_only(self):
        h = Hands()
        n = self.flow(h).act_all()
        self.assertEqual(h.calls, [])
        self.assertEqual(self.acts(), {("ad", "archive"): "dry", ("pt", "forward"): "dry", ("pt", "draft"): "dry", ("uk", "forward"): "dry"})
        self.assertEqual((n["archive"], n["forward"]), (1, 2))

    def test_slack_level_posts_forward_to_the_configured_channel_only(self):
        """포워드는 슬랙 글이라 'slack' 단계에서 올라간다. 거절 초안·아카이브는 여전히 기록만."""
        h = Hands()
        with gate("slack"):
            self.flow(h).act_all()
        posts = [c for c in h.calls if c[0] == "post"]
        self.assertEqual([c[1] for c in posts], ["test", "test"])                       # forward_channel 기본값
        self.assertTrue(any("제휴 제안" in c[2] for c in posts))
        self.assertTrue(any(c[0] == "thread" for c in h.calls))                         # 전문은 스레드에
        self.assertEqual((self.acts()[("pt", "draft")], self.acts()[("ad", "archive")]), ("dry", "dry"))

    def test_open_gate_acts_once(self):
        h = Hands()
        with gate("on"):
            f = self.flow(h)
            f.act_all()
            self.assertIn(("archive", "ad"), h.calls)
            self.assertEqual([c[:3] for c in h.calls if c[0] == "draft"], [("draft", "pt", "p@example.com")])   # 제휴 거절 초안
            k = len(h.calls)
            n = f.act_all()
        self.assertEqual((len(h.calls), n["archive"], n["forward"]), (k, 0, 0))         # 두 번째 실행은 아무것도 안 한다

