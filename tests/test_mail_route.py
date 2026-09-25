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
        """🔴 회신 없이 아카이브되는 갈래. 정책 미확정 항목이 섞여 있다(FORM_ERROR·LP_CUSTODY·RECRUIT)."""
        for cat in (rules.SYSTEM_NOTICE, rules.AD_NOISE, rules.MASS_BLAST, rules.VENDOR_PITCH,
                    rules.LP_CUSTODY, rules.RECRUIT, rules.FORM_ERROR, rules.IMPERSONATION):
            act = decide(cat)
            self.assertEqual(act["action"], "archive", cat)
            self.assertNotIn("body", act, cat)          # 초안을 만들지 않는다

    def test_inbound_ko_reply_template(self):
        act = decide(rules.INBOUND_KO)
        self.assertEqual((act["action"], act["to"]), ("reply", "a@example.com"))
        self.assertEqual(act["body"], route.tpl("inbound_form_ko", google_form=route.GOOGLE_FORM))

    def test_inbound_en_reply_template(self):
        act = decide(rules.INBOUND_EN)
        self.assertEqual((act["action"], act["to"]), ("reply", "a@example.com"))
        self.assertEqual(act["body"], route.tpl("inbound_form_en", google_form=route.GOOGLE_FORM))

    def test_trusted_ref_goes_to_slack_not_reply(self):
        act = decide(rules.TRUSTED_REF)
        self.assertEqual((act["action"], act["kind"], act["ch"]), ("slack", "trusted_ref", route.POST_CH))

    def test_event_goes_to_slack(self):
        act = decide(rules.EVENT_SPEAKER)
        self.assertEqual((act["action"], act["kind"]), ("slack", "event"))

    def test_post_channel_is_test(self):
        """🔴 게시 채널 키가 아직 test 다. --no-slack 을 빼도 실채널로 가지 않는다."""
        self.assertEqual(route.POST_CH, "test")

    def test_partnership_forward_plus_decline(self):
        act = decide(rules.PARTNERSHIP)
        self.assertEqual((act["action"], act["to"]), ("forward", route.SLACK_BPLAN))
        self.assertEqual(act["also_reply"]["to"], "a@example.com")
        self.assertEqual(act["also_reply"]["body"], route.tpl("partnership_decline"))

    def test_unknown_forwards_to_shared_inbox(self):
        act = decide(rules.UNKNOWN)
        self.assertEqual((act["action"], act["to"], act["unknown"]), ("forward", route.SLACK_BPLAN, True))

    def test_unlisted_category_falls_through_to_unknown_forward(self):
        self.assertTrue(decide("NOT_A_CATEGORY")["unknown"])

    def test_webflow_without_address_escalates(self):
        act = decide(rules.WEBFLOW, body="문의 유형 : 기타 문의\n주소 없음\n")
        self.assertEqual(act["action"], "escalate")


class NamedOverride(unittest.TestCase):
    def test_overrides_only_three_categories(self):
        m = msg(body="리뷰어일님께 전달 부탁드립니다")
        for cat in (rules.EVENT_SPEAKER, rules.PARTNERSHIP, rules.UNKNOWN):
            act = route.apply_named_partner(m, cat, route.decide(m, {"category": cat, "why": ""}))
            self.assertEqual(act["action"], "slack", cat)
            self.assertIn("ping", act, cat)
        for cat in (rules.RECRUIT, rules.IMPERSONATION, rules.INBOUND_KO):
            base = route.decide(m, {"category": cat, "why": ""})
            self.assertEqual(route.apply_named_partner(m, cat, base), base, cat)

    def test_no_slack_id_pings_by_name(self):
        m = msg(body="리뷰어일님께")
        act = route.apply_named_partner(m, rules.UNKNOWN, route.decide(m, {"category": rules.UNKNOWN, "why": ""}))
        self.assertFalse(act["ping"].startswith("<@"))
        self.assertIn("슬랙 ID 미등록", act["note"])


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
