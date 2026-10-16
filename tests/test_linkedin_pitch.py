"""pitch — Reviewer A 문안 틀에 끼울 구절. claude 출력을 원문과 대조해 통과한 칸만 틀에 끼운다(모델 없이 시험)."""
from __future__ import annotations

import itertools
import unittest

from skills.linkedin import pitch

IT = {"headline": "CTO @ Sample AI",
      "about": "I later served as CTO of ExampleSearch, where I helped scale organic search traffic from under 5K to over 500K "
               "monthly clicks, and contributed to the company’s acquisition.",
      "currentPosition": [{"companyName": "Sample AI"}],
      "experience": [{"position": "Co-Founder & CTO", "companyName": "Sample AI", "description": "Real-time AI for global meetings"},
                     {"position": "CTO & Co-Founder", "companyName": "ExampleAnalytics", "description": "B2B analytics"}]}
WEB = ["단계: ARR $200K (출처: https://example.com/a)"]
GOOD = {"company": "Sample AI", "josa": "가",
        "product": {"text": "실시간 AI로 글로벌 미팅의 언어 장벽을 푸는 제품", "quote": "Real-time AI for global meetings"},
        "proof": {"text": "ARR 20만 달러를 만드셨다", "quote": "ARR $200K"},
        "past": {"text": "예시치 CTO로 월 검색 유입을 5천에서 50만으로 키워 인수까지 함께하신",
                 "quote": "scale organic search traffic from under 5K to over 500K monthly clicks, and contributed to the company's acquisition"}}


def slot(text, quote):
    return {"text": text, "quote": quote}


class Check(unittest.TestCase):
    def test_all_good_fills_ahn_frame(self):
        got = pitch.compose(GOOD, IT, "가람", WEB)
        self.assertEqual(got["text"], "링크드인에서 Sample AI 소식을 접하고 연락드립니다. 실시간 AI로 글로벌 미팅의 언어 장벽을 푸는 제품인데, "
                                      "ARR 20만 달러를 만드셨다는 점이 인상 깊었습니다. 예시치 CTO로 월 검색 유입을 5천에서 50만으로 "
                                      "키워 인수까지 함께하신 이력도 눈에 띄었고요.")
        self.assertEqual((got["kept"], got["close"]), (["product", "proof", "past"], "Sample AI가 만들어가는 이야기가 궁금합니다."))
        self.assertEqual(len(got["evidence"]), 3)

    def test_quote_not_in_source_is_dropped(self):
        got = pitch.compose({**GOOD, "proof": slot("ARR 20만 달러를 만드셨다", "ARR $200K from 3 enterprise customers")}, IT, "가람", WEB)
        self.assertNotIn("proof", got["kept"])
        self.assertIn("근거가 원문에 없음", got["dropped"]["proof"])

    def test_number_not_in_quote_is_dropped_with_units_normalized(self):
        """5천=5K, 50만=500K 는 같은 숫자다. 원문에 없는 숫자(1위)는 버린다 — 10/16 미리보기: 대상 → 1위."""
        self.assertEqual(pitch.check("past", GOOD["past"], pitch.source_text(IT)), "")
        bad = slot("예시치 CTO로 월 검색 유입을 5천에서 80만으로 키우신", GOOD["past"]["quote"])
        self.assertIn("근거에 없는 숫자", pitch.check("past", bad, pitch.source_text(IT)))

    def test_letters_glued_to_digits_are_not_numbers(self):
        self.assertEqual(pitch._nums("B2B 분석 · Qwen3 · 42dot"), set())
        self.assertEqual(pitch._nums("$200K · 1.5만 · 100,000명 · 5M+"), {200000.0, 15000.0, 100000.0, 5000000.0})

    def test_wrong_form_is_dropped(self):
        """끝말이 틀리면 틀에 끼웠을 때 문장이 깨진다."""
        src = pitch.source_text(IT) + "\n" + WEB[0]
        for name, text in (("product", "실시간 통역을 합니다"), ("proof", "ARR 20만 달러"), ("past", "예시치 CTO였다")):
            with self.subTest(name=name):
                self.assertIn("꼴이 틀림", pitch.check(name, slot(text, GOOD[name]["quote"]), src))

    def test_praise_and_length_are_dropped(self):
        src = pitch.source_text(IT)
        self.assertEqual(pitch.check("product", slot("혁신적인 회의 제품", "Real-time AI for global meetings"), src), "평가어")
        self.assertIn("자 >", pitch.check("product", slot("가" * 41 + " 제품", "Real-time AI for global meetings"), src))

    def test_stealth_uses_name_opening_and_only_past(self):
        it = {**IT, "currentPosition": [{"companyName": "Stealth Startup"}]}
        got = pitch.compose({**GOOD, "company": "Stealth Startup"}, it, "가람", WEB)
        self.assertTrue(got["text"].startswith("링크드인에서 가람님 프로필을 보고 연락드립니다. 예시치 CTO로"))
        self.assertTrue(got["text"].endswith("이력이 인상 깊었습니다."))
        self.assertEqual((got["kept"], got["close"]), (["past"], ""))

    def test_company_not_in_source_falls_back_to_current_position(self):
        got = pitch.compose({**GOOD, "company": "Saaample"}, IT, "가람", WEB)
        self.assertEqual(got["company"], "Sample AI")

    def test_slack_mentions_and_backticks_are_stripped(self):
        """근거는 코드 블록 밖에 붙는다 — 프로필 원문의 멘션이 #그물망에 핑을 터뜨리면 안 된다."""
        it = {**IT, "about": IT["about"] + " <!channel> ping `x`"}
        got = pitch.compose({**GOOD, "past": slot("예시치 CTO로 인수까지 함께하신 `코드`", "<!channel> ping `x`")}, it, "가람", WEB)
        self.assertNotIn("<!channel>", " ".join(got["evidence"]))
        self.assertNotIn("`", got["text"] + " ".join(got["evidence"]))

    def test_non_string_slot_values_do_not_crash(self):
        got = pitch.compose({**GOOD, "product": {"text": 123, "quote": "Real-time AI for global meetings"},
                             "proof": "문자열", "past": {"text": None}}, IT, "가람", WEB)
        self.assertEqual(got["kept"], [])

    def test_claude_failure_leaves_first_sentence(self):
        got = pitch.compose({}, IT, "가람", [])
        self.assertEqual((got["text"], got["kept"]), ("링크드인에서 Sample AI 소식을 접하고 연락드립니다.", []))


class Frame(unittest.TestCase):
    def test_every_combination_reads_as_sentences(self):
        """틀은 하나 — 빈 칸은 그 조각만 빠진다. 8 조합 모두 '{' 없이, 마침표로 끝나고, 조각이 한 번씩만."""
        parts = {"product": "회의 통역 제품", "proof": "고객 10곳이 쓰고 있다", "past": "이전 회사를 매각하신"}
        for n in range(4):
            for combo in itertools.combinations(parts, n):
                kept = {k: parts[k] for k in combo}
                t = pitch.paragraph("Aco", "가람", kept)
                with self.subTest(combo=combo):
                    self.assertTrue(t.startswith("링크드인에서 Aco 소식을 접하고 연락드립니다."))
                    self.assertTrue(t.endswith("."))
                    for k, v in parts.items():
                        self.assertEqual(t.count(v), 1 if k in kept else 0)
                    self.assertLessEqual(t.count("인상 깊었습니다"), 1)


class Closing(unittest.TestCase):
    def test_josa(self):
        self.assertEqual(pitch.closing("예시치", "이"), "예시치가 만들어가는 이야기가 궁금합니다.")      # 한글은 받침으로 코드가
        self.assertEqual(pitch.closing("당근", "가"), "당근이 만들어가는 이야기가 궁금합니다.")
        self.assertEqual(pitch.closing("Example Product", "이"), "Example Product이 만들어가는 이야기가 궁금합니다.")    # 영문은 claude 값
        self.assertEqual(pitch.closing("Example Product", ""), "")                                          # 모르면 뺀다
        self.assertEqual(pitch.closing("", "가"), "")

    def test_legal_suffix_is_stripped(self):
        it = {**IT, "currentPosition": [{"companyName": "Folding Technologies, Inc."}]}
        self.assertEqual(pitch.compose({}, it, "동욱", [])["company"], "Folding Technologies")


if __name__ == "__main__":
    unittest.main()
