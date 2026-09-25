"""act_all — 판정 뒤 행동 셋(score·card·draft)이 문 뒤에 있다. off 기록만 · slack 카드만 · on 전부."""
from __future__ import annotations

import unittest

from kvlib import Fetched, ledger
from skills.bplan import run
from tests._bplan_fakes import Case
from tests.test_bplan_judge import GOOD, ROW

RED = "SCORE: Red\nREASON: CR-2 선형 매출\nSUMMARY: 유통\nTEAM: 미상\nRISK: 재접촉 조건: 소프트웨어 레이어"


def judged(c, rows, outputs):
    it = iter(outputs)
    run.judge_rows(c.db, rows, rows, llm=lambda crit, data: next(it), deck=lambda u: Fetched.measured(""))


class Act(unittest.TestCase):
    def test_off_records_dry_and_touches_nothing(self):
        with Case(notion=[ROW]) as c:
            judged(c, [ROW], [GOOD])
            n = run.act_all(c.gate, c.notion.rows)
        self.assertEqual(n, {"score": 1, "card": 1})
        self.assertEqual(set(c.acts().values()), {"dry"})
        self.assertEqual((c.notion.updated, c.slack.posts, c.gmail.drafts), ([], [], []))

    def test_slack_level_posts_card_only(self):
        with Case(notion=[ROW], cfg={"bplan.outbound": "slack"}) as c:
            judged(c, [ROW], [GOOD])
            run.act_all(c.gate, c.notion.rows)
        self.assertEqual([p[0] for p in c.slack.posts], ["test", "test"])                        # 헤더 + 카드 1
        self.assertIn("*새회사*  `Orange`", c.slack.posts[1][1])
        self.assertIn("⭕ 미팅요청 · ❌ 드랍 · 🙋 내가 볼게", c.slack.posts[1][1])
        self.assertEqual(c.notion.updated, [])                                                     # 점수는 아직 기록만

    def test_on_scores_and_drafts_drop_in_intake_thread(self):
        red = {**ROW, "_page_id": "p2", "접수키": "k2", "기업명": "드랍회사", "이메일": "b@x.com"}
        with Case(notion=[red], intake=[("m1", "[카카오벤처스]드랍회사 로부터 접수된 사업계획서")], cfg={"bplan.outbound": "on"}) as c:
            ledger.append("bplan", "m1", run.LABELED, company="드랍회사", label="3. Evan")
            judged(c, [red], [RED])
            n = run.act_all(c.gate, c.notion.rows)
        self.assertEqual(n, {"score": 1, "draft": 1})
        self.assertEqual(c.notion.updated[0][1]["점수"], "Red")
        self.assertIn("CR-2 선형 매출", c.notion.updated[0][1]["포워드 이유"])
        self.assertEqual(c.gmail.drafts[0][:2], ("m1", "b@x.com"))                                   # 접수 알림 스레드에 답장
        self.assertTrue(c.gmail.drafts[0][2].startswith("안녕하세요 김대표 대표님"))                # ko 문안, 대표명
        self.assertEqual(c.acts()[("k2", "draft")], "done")

    def test_same_email_within_a_week_is_skipped(self):
        r1 = {**ROW, "_page_id": "p2", "접수키": "k2", "이메일": "b@x.com"}
        r2 = {**ROW, "_page_id": "p3", "접수키": "k3", "이메일": "b@x.com", "기업명": "재접수"}
        with Case(notion=[r1, r2], intake=[("m1", "[카카오벤처스]새회사 로부터 접수된 사업계획서"), ("m2", "[카카오벤처스]재접수 로부터 접수된 사업계획서")],
                  cfg={"bplan.outbound": "on"}) as c:
            judged(c, [r1, r2], [RED, RED])
            run.act_all(c.gate, c.notion.rows)
        self.assertEqual(len(c.gmail.drafts), 1)
        self.assertEqual(c.acts()[("k3", "draft")], "skipped")

    def test_rows_partners_already_moved_are_left_alone(self):
        moved = {**ROW, "to do": "To Drop"}
        with Case(notion=[moved], cfg={"bplan.outbound": "on"}) as c:
            judged(c, [moved], [GOOD])
            self.assertEqual(run.act_all(c.gate, c.notion.rows), {})
        self.assertEqual((c.notion.updated, c.slack.posts), ([], []))

    def test_language_rule(self):
        self.assertEqual(run.drop_lang({"기업명": "Acme", "대표명": "John", "이메일": "person-5d4a06755c8b@example.com", "서비스정의": "AI platform for x " * 5}), "en")
        self.assertEqual(run.drop_lang({"기업명": "Acme", "대표명": "John", "이메일": "person-ce1a7868c38f@example.com"}), "ko")      # 한국 도메인
        self.assertEqual(run.drop_lang({"기업명": "Acme", "대표명": "김대표", "이메일": "person-5d4a06755c8b@example.com"}), "ko")        # 한글 이름


if __name__ == "__main__":
    unittest.main()
