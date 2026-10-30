"""비플랜 블라인드 라벨링 — 고르기·카드 문안·반응 해석·수집·보고. 슬랙은 가짜."""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from kvlib import Fetched, judgments
from skills.bplan import blind


def sub(i: int, ts: str = "2026-03-01T10:00:00", **props) -> dict:
    p = {"기업명": f"회사{i}", "서비스정의": "B2B SaaS", "팀이력": "전 카카오 PM", "사업계획서": f"https://drive.google.com/open?id=D{i:014d}",
         "문제정의": "문제", "솔루션": "솔루션", "비즈니스모델": "구독", "경쟁사": "A사", "경쟁력": "도메인", "추가내용": "", **props}
    return {"key": f"f{i}@x.com|{ts}", "ts": ts, "email": f"f{i}@x.com", "props": p}


def con():
    c = sqlite3.connect(":memory:"); c.executescript(judgments.SCHEMA); return c


class Pick(unittest.TestCase):
    def test_order_is_fixed_and_skips_sent_and_old(self):
        subs = [sub(i) for i in range(50)] + [sub(99, ts="2025-12-31T23:59:59")]
        a = blind.pick(subs, set(), 10)
        self.assertEqual([s["key"] for s in a], [s["key"] for s in blind.pick(list(reversed(subs)), set(), 10)])   # 입력 순서와 무관
        self.assertNotIn("f99@x.com|2025-12-31T23:59:59", [s["key"] for s in blind.pick(subs, set(), 51)])          # since 이전은 안 고른다
        b = blind.pick(subs, {s["key"] for s in a}, 10)
        self.assertFalse({s["key"] for s in a} & {s["key"] for s in b})

    def test_new_submission_does_not_reshuffle_existing_order(self):
        subs = [sub(i) for i in range(30)]
        before = [s["key"] for s in blind.pick(subs, set(), 30)]
        after = [s["key"] for s in blind.pick(subs + [sub(77)], set(), 31) if s["key"] != sub(77)["key"]]
        self.assertEqual(before, after)


class Card(unittest.TestCase):
    def test_card_has_raw_form_and_no_model_judgment(self):
        t = blind.card_text(sub(1, 팀이력="서울대 박사과정\n멀티모달"))
        self.assertIn("팀: 서울대 박사과정 멀티모달", t)                                  # 줄바꿈은 한 줄로
        self.assertIn("덱 열기", t)
        for word in ("Orange", "Red", "칼드랍", "SCORE", "볼 만합니다"):                    # 🔴 모델 판정이 새면 라벨이 끌려간다
            self.assertNotIn(word, t)
        self.assertIn("(빈 칸)", blind.form_text(sub(2)))                               # 빈 칸도 빈 칸이라고 보인다


class Decide(unittest.TestCase):
    def test_reaction_vocabulary(self):
        self.assertEqual(blind.decide({"o"}), "yes")
        self.assertEqual(blind.decide({"x"}), "no")
        self.assertEqual(blind.decide({"thinking_face"}), "maybe")
        self.assertEqual(blind.decide({"o", "x"}), "maybe")                            # 헷갈린 것
        self.assertIsNone(blind.decide({"eyes"}))


class Flow(unittest.TestCase):
    def setUp(self):
        self.c = con()
        self.posts = []

        def post(ch, text, thread_ts=None, **kw):
            assert ch == "test"                                                        # 🔴 review_test 말고는 안 간다
            self.posts.append((text, thread_ts)); return Fetched.measured(f"ts{len(self.posts)}")
        self.p = mock.patch.object(blind.slack, "post", post); self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_send_posts_card_then_form_in_thread_and_never_resends(self):
        g = blind.gate(self.c)
        r = blind.send(g, [sub(i) for i in range(5)], n=3)                              # constraints 의 문 = slack
        self.assertEqual(r, {"done": 3})
        self.assertEqual(len(self.posts), 6)
        self.assertEqual(self.posts[1][1], "ts1")                                      # 폼은 카드 스레드에
        blind.send(g, [sub(i) for i in range(5)], n=3)
        self.assertEqual(len(self.posts), 10)                                           # 남은 2장만 더 (2 카드 + 2 폼)

    def test_off_gate_posts_nothing(self):
        g = blind.gate(self.c, dry_run=True)
        self.assertEqual(blind.send(g, [sub(1)], n=1), {"dry": 1})
        self.assertEqual(self.posts, [])

    def test_collect_reads_reaction_and_human_reason_only(self):
        g = blind.gate(self.c)
        g.put("k1", "label_card", "done", {"result": "ts1"})
        g.put("k2", "label_card", "done", {"result": "ts2"})
        react = {"ts1": [{"name": "o", "users": ["U1"]}], "ts2": []}
        rep = {"ts1": [{"text": "*2. 문제* ...", "bot_id": "B1"}, {"text": "팀이 강함 — 넥슨 PD", "user": "U1"}]}
        got = blind.collect(g, reactions=lambda ch, ts: Fetched.measured(react[ts]),
                            replies=lambda ch, ts: Fetched.measured(rep.get(ts, [])))
        self.assertEqual(got, {"yes": 1, "waiting": 1})
        self.assertEqual(self.c.execute("SELECT verdict, reason FROM blind_labels").fetchall(), [("yes", "팀이 강함 — 넥슨 PD")])

    def test_report_flags_notion_drops_that_were_labeled_worth_a_look(self):
        g = blind.gate(self.c)
        self.c.execute("INSERT INTO blind_labels VALUES('k1','yes','팀 강','[]','ts1','t','t')")
        self.c.execute("INSERT INTO blind_labels VALUES('k2','no','','[]','ts2','t','t')")
        t = blind.report(self.c, {"k1": "To Drop", "k2": "To Drop"})
        self.assertIn("볼만 비율 50.0%", t)
        self.assertIn("To Drop 인데 볼만·애매로 단 건 1", t)


if __name__ == "__main__":
    unittest.main()
