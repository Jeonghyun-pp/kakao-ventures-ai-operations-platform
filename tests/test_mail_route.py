"""route.py 의 현재 동작 — 합성 입력."""
from __future__ import annotations

import unittest

from skills.mail import route, rules


def msg(**kw) -> dict:
    base = {"id": "m1", "thread_id": "t1", "subject": "제목", "sender": "a@example.com",
            "sender_raw": "A <a@example.com>", "body": "본문"}
    base.update(kw)
    return base


def decide(cat: str, why: str = "이유", **kw) -> dict:
    return route.decide(msg(**kw), {"category": cat, "why": why})


class Decide(unittest.TestCase):
    def test_protect(self):
        for cat in (rules.BPLAN_INTAKE, rules.INTERNAL):
            self.assertEqual(decide(cat)["action"], "protect", cat)

    def test_silent_archive_branches(self):
        """🔴 회신 없이 아카이브되는 갈래."""
        for cat in (rules.SYSTEM_NOTICE, rules.AD_NOISE, rules.MASS_BLAST, rules.VENDOR_PITCH,
                    rules.FOREIGN_COLD, rules.CLOSED_FOLLOWUP):
            act = decide(cat)
            self.assertEqual(act["action"], "archive", cat)
            self.assertNotIn("body", act, cat)          # 초안을 만들지 않는다

    def test_inbound_ko_reply_template(self):
        act = decide(rules.INBOUND_KO)
        self.assertEqual((act["action"], act["to"]), ("reply", "a@example.com"))
        self.assertEqual(act["body"], route.tpl("inbound_form_ko", google_form=route.GOOGLE_FORM))

    def test_webflow_inbound_uses_homepage_template(self):
        """[사용자 결정 2026-10-15] 홈페이지 문의자는 자동 회신을 안 받았다 — 노션 「홈페이지 문의 › 투자 문의」 문안."""
        act = decide(rules.INBOUND_KO, webflow_kind="투자 문의")
        self.assertEqual(act["body"], route.tpl("webflow_invest_ko", google_form=route.GOOGLE_FORM))
        self.assertNotIn("자동 회신", act["body"])
        self.assertIn("자동 회신", decide(rules.INBOUND_KO)["body"])               # 메일로 직접 온 것은 그대로

    def test_shared_without_reply(self):
        """[사용자 결정 2026-10-08] 출자·채용·폼 오류·사칭은 슬랙 공유. 회신은 안 만든다."""
        for cat in (rules.LP_CUSTODY, rules.RECRUIT, rules.FORM_ERROR, rules.IMPERSONATION):
            act = decide(cat)
            self.assertEqual(act["action"], "forward", cat)
            self.assertNotIn("body", act, cat)
            self.assertNotIn("unknown", act, cat)

    def test_legacy_inbound_en_is_left_alone(self):
        """옛 어휘(언어 기준) 판정은 외국인인지 모른다 — 폼 안내도 아카이브도 하지 않는다."""
        self.assertEqual(decide(rules.INBOUND_EN)["action"], "protect")
        self.assertNotIn(rules.INBOUND_EN, rules.JUDGE_CATEGORIES)

    def test_trusted_ref_goes_to_slack_not_reply(self):
        act = decide(rules.TRUSTED_REF)
        self.assertEqual((act["action"], act["kind"], act["ch"]), ("slack", "trusted_ref", route.POST_CH))

    def test_event_goes_to_slack(self):
        act = decide(rules.EVENT_SPEAKER)
        self.assertEqual((act["action"], act["kind"]), ("slack", "event"))

    def test_post_channel_is_test(self):
        """🔴 게시 채널 키가 아직 test 다. --no-slack 을 빼도 실채널로 가지 않는다."""
        self.assertEqual(route.POST_CH, "test")

    def test_partnership_declines_after_days(self):
        """[사용자 결정 2026-10-13] 국내 제휴 제안 → 7일 뒤 거절. 영원히 수동이라 초안까지다."""
        act = decide(rules.PARTNERSHIP)
        self.assertEqual((act["action"], act["to"], act["after_days"], act["pick_decline"]), ("reply", "a@example.com", 7, True))
        self.assertIn(rules.PARTNERSHIP, route.constraints.get("gmail.never_auto_send"))

    def test_decline_body_follows_notion_templates(self):
        """노션 「Hello 메일 템플릿」 — 학회는 학회명을 호칭에, 탐방·기타는 각자의 문안."""
        pick = lambda out: route.decline_body(msg(), lambda p, s: out)
        self.assertTrue(pick("KIND: 학회\nORG: BICS × ProSeed").startswith("안녕하세요 BICS × ProSeed 담당자님, 카카오벤처스입니다."))
        self.assertIn("산학협력이 어려울", pick("KIND: 학회\nORG: BICS"))
        self.assertTrue(pick("KIND: 학회\nORG: NONE").startswith("안녕하세요 담당자님, 카카오벤처스입니다."))
        self.assertTrue(pick("KIND: 학회\nORG: " + "가" * 31).startswith("안녕하세요 담당자님,"))         # 길면 자르지 않고 뺀다
        self.assertIn("기업 탐방은 어려울", pick("KIND: 탐방\nORG: 무엇"))
        self.assertNotIn("무엇", pick("KIND: 탐방\nORG: 무엇"))
        self.assertEqual(pick("KIND: 기타\nORG: NONE"), route.tpl("partnership_decline", org=""))

    def test_decline_body_none_when_unsure(self):
        self.assertIsNone(route.decline_body(msg(), lambda p, s: "KIND: 모르겠다"))
        self.assertIsNone(route.decline_body(msg(), lambda p, s: 1 / 0))

    def test_unknown_forwards_to_shared_inbox(self):
        act = decide(rules.UNKNOWN)
        self.assertEqual((act["action"], act["unknown"]), ("forward", True))

    def test_unlisted_category_falls_through_to_unknown_forward(self):
        self.assertTrue(decide("NOT_A_CATEGORY")["unknown"])


class BodyChunks(unittest.TestCase):
    def test_empty_body_gives_nothing(self):
        self.assertEqual(route.body_chunks(msg(body="")), [])

    def test_bullet_lines_are_merged(self):
        self.assertIn("• 일시", route._tidy("-\n\n   일시: 11월"))

    def test_long_body_is_split(self):
        parts = route.body_chunks(msg(body=("가나다라마바사\n" * 1000)))
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= route._CHUNK + 40 for p in parts))


if __name__ == "__main__":
    unittest.main()
