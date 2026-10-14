"""비플랜 판정 — 입력 조립·다섯 줄 파싱·저장·정답 동기화. 모델·덱·노션은 가짜."""
from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from kvlib import Fetched, judgments
from skills.bplan import judge, run

ALLOWED = ["칼드랍", "Red", "Orange", "Green", "?"]
ROW = {"_page_id": "p1", "접수키": "a@x.com|2026-09-20T15:00:00", "기업명": "새회사", "대표명": "김대표", "이메일": "a@x.com",
       "사업계획서": "https://drive.google.com/open?id=ABCDEFGHIJKLMNOP", "to do": "Newly Added", "DB 업데이트 시간": "2026-09-20T06:00:00.000Z",
       "서비스정의": "B2B SaaS", "문제정의": "문제", "솔루션": "솔루션", "비즈니스모델": "구독", "경쟁사": "없음", "팀이력": "팀", "경쟁력": "특허", "추가내용": ""}
GOOD = "SCORE: Orange\nREASON: 이 팀은 특허 때문에 볼 만합니다\nSUMMARY: B2B SaaS\nTEAM: 김대표\nRISK: 경쟁사 미조사"


def con():
    c = sqlite3.connect(":memory:"); c.executescript(judgments.SCHEMA); return c


class Parse(unittest.TestCase):
    def test_reads_five_lines_and_finds_score_by_value(self):
        p = judge.parse("```\nSCORE: `Orange`\nREASON: r\nSUMMARY: s\nTEAM: t\nRISK: k\n```", ALLOWED)
        self.assertEqual((p["ok"], p["verdict"], p["summary"], p["risk"]), (1, "Orange", "s", "k"))

    def test_score_outside_vocabulary_or_missing_lines_fail(self):
        self.assertEqual(judge.parse("SCORE: Yellow\nREASON: r\nSUMMARY: s", ALLOWED)["ok"], 0)
        self.assertEqual(judge.parse("SCORE: Red\nSUMMARY: s", ALLOWED)["ok"], 0)

    def test_input_carries_deck_or_the_reason_it_is_missing(self):
        t = judge.build_input(ROW, Fetched.measured("덱 본문 " * 50, name="d.pdf", chars=250), ALLOWED, ["접수 2026-09-20"])
        self.assertIn("덱(d.pdf · 250자)", t)
        self.assertIn("[경쟁사]\n없음", t)
        self.assertNotIn("비플랜 1차 스크리너", t)                                             # 🔴 기준은 입력에 안 넣는다 — 부를 때 붙인다
        t2 = judge.build_input(ROW, Fetched.unknown("drive:404 공유"), ALLOWED, [])
        self.assertIn("덱: 못 읽었다 — drive:404", t2)
        t3 = judge.build_input(ROW, Fetched.measured("", why="텍스트 없음(이미지 덱, 3자)"), ALLOWED, [])
        self.assertIn("덱: 텍스트 없음", t3)
        t4 = judge.build_input(ROW, Fetched.measured("--- 1쪽 ---\n본문", name="d.pdf", chars=12, transcribed=True), ALLOWED, [])
        self.assertIn("이미지 덱을 옮겨 적음", t4)


class Transcribe(unittest.TestCase):
    PAGES = "--- 1쪽 ---\n" + "월매출 1.3억 · 고객사 현대홈쇼핑\n" * 20

    def test_page_marked_transcript_is_accepted(self):
        f = judge.transcribe(Path("deck.pdf"), call=lambda p, prompt, **kw: self.PAGES)
        self.assertTrue(f.is_measured and f.require().startswith("--- 1쪽 ---"))

    def test_refusal_or_summary_never_becomes_deck_text(self):
        # 🔴 거절·요약 한 줄이 덱 텍스트로 들어가면 그게 Red 의 근거가 된다
        for out in ("죄송하지만 이 파일을 읽을 수 없습니다.", "--- 1쪽 ---\n짧음", "이 덱은 안전관리 SaaS 를 소개한다. " * 20):
            self.assertFalse(judge.transcribe(Path("deck.pdf"), call=lambda p, prompt, **kw: out).is_measured, out[:20])

    def test_call_failure_is_unknown_not_raise(self):
        def boom(p, prompt, **kw):
            raise RuntimeError("timeout")
        self.assertIn("timeout", judge.transcribe(Path("deck.pdf"), call=boom).reason)


class Judge(unittest.TestCase):
    def setUp(self):
        self.c = con()
        self.enterContext(mock.patch.object(run.notion, "allowed_options", lambda db, prop: ALLOWED))
        self.enterContext(mock.patch.object(run.notion, "writable_id", lambda k: k))

    def test_judges_saves_input_and_card_and_links_page(self):
        seen = []
        r = run.judge_rows(self.c, [ROW], [ROW], llm=lambda crit, data: seen.append((crit, data)) or GOOD, deck=lambda u: Fetched.measured("", why="텍스트 없음"))
        self.assertEqual((r["judged"], r["invalid"], r["stopped"]), (1, 0, None))
        self.assertTrue(seen[0][0].startswith("# 비플랜 1차 스크리닝 기준"))                   # 기준은 호출 때 앞에
        row = self.c.execute("SELECT key, verdict, reason, page_id, input_text, extra FROM judgments").fetchone()
        self.assertEqual((row[0], row[1], row[2], row[3]), ("a@x.com|2026-09-20T15:00:00", "Orange", "이 팀은 특허 때문에 볼 만합니다", "p1"))
        self.assertEqual(json.loads(row[5])["summary"], "B2B SaaS")                              # 카드 문안은 extra 열에
        self.assertIn("덱: 텍스트 없음", row[4])

    def test_bad_output_is_saved_as_not_ok_and_llm_failure_stops(self):
        run.judge_rows(self.c, [ROW], [ROW], llm=lambda c, d: "SCORE: Yellow", deck=lambda u: Fetched.measured(""))
        self.assertEqual(self.c.execute("SELECT ok, error FROM judgments").fetchone()[0], 0)

        def boom(c, d):
            raise RuntimeError("차단선")
        r = run.judge_rows(self.c, [{**ROW, "_page_id": "p2", "접수키": "k2"}], [ROW], llm=boom, deck=lambda u: Fetched.measured(""))
        self.assertEqual(r["stopped"], "RuntimeError: 차단선")

    def test_new_vs_past_selection(self):
        rows = [ROW, {**ROW, "_page_id": "p2", "접수키": "k2", "to do": "To Drop"}, {**ROW, "_page_id": "p3", "접수키": "k3", "to do": "Review Done"}]
        judged = []
        with mock.patch.object(run, "judge_rows", lambda con, pool, all_rows, **kw: judged.append([r["_page_id"] for r in pool]) or {"judged": len(pool), "invalid": 0, "stopped": None}):
            run.judge_new(self.c, rows)
            run.judge_new(self.c, rows, past=5)
        self.assertEqual(judged, [["p1"], ["p2"]])                                               # 새것 = Newly Added · 옛것 = 결정 난 행만

    def test_label_uses_partner_todo_and_buckets(self):
        run.judge_rows(self.c, [ROW], [ROW], llm=lambda c, d: GOOD, deck=lambda u: Fetched.measured(""))   # Orange → keep
        self.assertEqual(run.label(self.c, [{**ROW, "to do": "미팅요청"}]), 0)                     # keep = keep → 갈림 아님(정답은 적힌다)
        self.assertEqual(self.c.execute("SELECT verdict FROM judgments WHERE run='human'").fetchone()[0], "keep")
        self.assertEqual(run.label(self.c, [{**ROW, "to do": "To Drop"}]), 1)                    # 심사역은 드랍 → 갈림
        self.assertEqual(self.c.execute("SELECT verdict FROM judgments WHERE run='human'").fetchone()[0], "drop")
        self.assertEqual(run.label(self.c, [{**ROW, "to do": "Review Done"}]), 0)                # 미결은 건드리지 않는다(human 은 남음)
        self.assertEqual(run.bucket("칼드랍"), "drop"); self.assertEqual(run.bucket("?"), "?")


if __name__ == "__main__":
    unittest.main()
