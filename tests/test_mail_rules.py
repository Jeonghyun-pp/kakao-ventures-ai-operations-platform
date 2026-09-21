"""rules.py 의 현재 동작 — 합성 입력."""
from __future__ import annotations

import unittest

from kvlib import constraints
from skills.mail import rules


def msg(**kw) -> dict:
    base = {"id": "m1", "thread_id": "t1", "subject": "", "sender": "someone@example.com",
            "body": "", "recipient_count": 1, "list_unsubscribe": False}
    base.update(kw)
    return base


class RuleOrder(unittest.TestCase):
    """판정 순서가 곧 우선순위다. 앞에서 확정되면 뒤를 안 본다."""

    def test_nothing_matches_returns_none(self):
        self.assertIsNone(rules.classify(msg(subject="투자 검토 요청드립니다", body="안녕하세요")))

    def test_bplan_intake_by_subject(self):
        hit = rules.classify(msg(subject="[카카오벤처스]주식회사 가나다 로부터 접수된 사업계획서"))
        self.assertEqual(hit["category"], rules.BPLAN_INTAKE)

    def test_bplan_beats_ad_header(self):
        hit = rules.classify(msg(subject="[카카오벤처스]가나다 로부터 접수된 사업계획서",
                                 list_unsubscribe=True, sender="noreply@example.com"))
        self.assertEqual(hit["category"], rules.BPLAN_INTAKE)

    def test_bplan_reply_is_also_protected_known_defect(self):
        """🔴 알려진 결함: 창업자의 답장("Re: …접수된 사업계획서")도 접수 메일로 잡힌다.
        실측 2026-09-11~14. 비플랜 쪽 정규식은 앵커가 있어 답장을 거른다 — 이쪽은 없다."""
        hit = rules.classify(msg(subject="Re: [카카오벤처스]가나다 로부터 접수된 사업계획서",
                                 sender="founder@example.com"))
        self.assertEqual(hit["category"], rules.BPLAN_INTAKE)

    def test_webflow_by_sender(self):
        hit = rules.classify(msg(sender="no-reply-forms@webflow.com"))
        self.assertEqual(hit["category"], rules.WEBFLOW)

    def test_system_notice_exact_address_only(self):
        self.assertEqual(rules.classify(msg(sender="tips_admin@kban.or.kr"))["category"],
                         rules.SYSTEM_NOTICE)
        self.assertIsNone(rules.classify(msg(sender="manager@kban.or.kr")))

    def test_impersonation_keyword_in_body(self):
        hit = rules.classify(msg(subject="Question", body="Is this from kakaotalk.vc real?"))
        self.assertEqual(hit["category"], rules.IMPERSONATION)

    def test_impersonation_beats_ad_header(self):
        hit = rules.classify(msg(body="피싱 주의 안내", list_unsubscribe=True))
        self.assertEqual(hit["category"], rules.IMPERSONATION)

    def test_impersonation_substring_catches_vendor_pitch_known_defect(self):
        """🔴 알려진 결함: 단순 부분일치라 '피싱'이 든 보안 솔루션 영업도 사칭 문의가 된다."""
        hit = rules.classify(msg(subject="피싱 메일 차단 솔루션 도입 제안", body="데모를 제안드립니다"))
        self.assertEqual(hit["category"], rules.IMPERSONATION)

    def test_mass_blast_threshold_from_constraints(self):
        cap = constraints.get("gmail.mass_blast_recipients")
        self.assertEqual(rules.classify(msg(recipient_count=cap))["category"], rules.MASS_BLAST)
        self.assertIsNone(rules.classify(msg(recipient_count=cap - 1)))

    def test_internal_beats_ad_header(self):
        hit = rules.classify(msg(sender="someone@example.vc", list_unsubscribe=True))
        self.assertEqual(hit["category"], rules.INTERNAL)

    def test_ad_by_unsubscribe_header(self):
        hit = rules.classify(msg(list_unsubscribe=True))
        self.assertEqual((hit["category"], hit["why"]), (rules.AD_NOISE, "List-Unsubscribe 헤더"))

    def test_ad_header_alone_decides_known_policy_gap(self):
        """🔴 정책 미확정: 헤더 하나로 LLM 없이 광고가 확정된다. 담당자 명의의 협력 제안이
        이렇게 아카이브된 실례가 있다(2026-09-09). 정책이 정해지면 이 기대값을 바꾼다."""
        hit = rules.classify(msg(subject="[기관] 포트폴리오사 채용 지원 관련 협력 제안",
                                 body="처음 인사드립니다. 방문 미팅을 제안드립니다.",
                                 list_unsubscribe=True))
        self.assertEqual(hit["category"], rules.AD_NOISE)

    def test_ad_by_sender_token_noreply_known_policy_gap(self):
        """🔴 'noreply' 토큰은 광고가 아닌 시스템 알림도 잡는다 — 우리 소유 파일의 열람 권한
        요청 알림이 광고로 아카이브됐다(2026-09-17)."""
        hit = rules.classify(msg(sender="drive-shares-noreply@google.com", subject="공유 요청"))
        self.assertEqual(hit["category"], rules.AD_NOISE)

    def test_ad_by_subject_token(self):
        hit = rules.classify(msg(subject="(광고) 10월 교육과정 안내"))
        self.assertEqual(hit["category"], rules.AD_NOISE)


class NamedPartner(unittest.TestCase):
    """호칭이 붙어야 지명이다. 부분일치로 되돌리지 마라(2026-08-16 오탐 4건)."""

    def test_address_in_body(self):
        self.assertEqual(rules.find_named_partner(msg(body="cc reviewer-one@example.vc")),
                         "reviewer-one@example.vc")

    def test_korean_name_with_honorific(self):
        self.assertEqual(rules.find_named_partner(msg(body="리뷰어일님께 전달 부탁드립니다")),
                         "reviewer-one@example.vc")

    def test_at_mention(self):
        self.assertEqual(rules.find_named_partner(msg(subject="@reviewer-three 확인 부탁")),
                         "reviewer-three@example.vc")

    def test_substring_inside_word_does_not_match(self):
        for text in ("Clannect 서비스 소개", "channel partner program", "제로넥스 제안", "jaded investors"):
            self.assertEqual(rules.find_named_partner(msg(subject=text)), "", text)

    def test_real_name_is_not_in_roster_known_defect(self):
        """🔴 알려진 결함: 명단이 닉네임 4명뿐이라 실명·직함 지목은 전부 놓친다.
        2026-08-17~09-21 전 기간 발동 0회."""
        self.assertEqual(rules.find_named_partner(msg(subject="홍길동 심사역님 연사 참여 요청")), "")


WEBFLOW_BODY = (
    "문의 유형 : 투자 문의\n"
    "이메일 : founder@example.com\n"
    "문의 제목 : 투자 검토 요청\n"
    "문의 내용 : 안녕하세요.\n"
    "저희는 B2B SaaS 를 만드는 팀입니다.\n"
    "\n"
    "시드 라운드를 준비하고 있어 검토를 부탁드립니다.\n"
)


class Webflow(unittest.TestCase):
    def test_reply_to_and_kind(self):
        f = rules.parse_webflow(WEBFLOW_BODY)
        self.assertEqual((f["kind"], f["reply_to"], f["title"]),
                         ("투자 문의", "founder@example.com", "투자 검토 요청"))

    def test_content_is_first_line_only_known_defect(self):
        """🔴 알려진 결함: `문의 내용` 의 첫 줄만 잡힌다. 실측 3건 — 실제 886~3,904자 중
        6~37자만 잡혔고, LLM 은 인사말 한 줄로 판정했다. 고치면 이 기대값을 바꾼다."""
        self.assertEqual(rules.parse_webflow(WEBFLOW_BODY)["content"], "안녕하세요.")

    def test_fallback_skips_webflow_and_kakao_addresses(self):
        body = "문의 유형 : 기타 문의\nno-reply-forms@webflow.com\ndeals@example.vc\nreal@example.org\n"
        self.assertEqual(rules.parse_webflow(body)["reply_to"], "real@example.org")

    def test_as_message_rewrites_sender_to_inquirer(self):
        alt = rules.webflow_as_message(msg(sender="no-reply-forms@webflow.com", body=WEBFLOW_BODY,
                                           list_unsubscribe=True, recipient_count=3))
        self.assertEqual(alt["sender"], "founder@example.com")
        self.assertEqual(alt["subject"], "투자 검토 요청")
        self.assertEqual((alt["recipient_count"], alt["list_unsubscribe"]), (1, False))
        self.assertEqual(alt["webflow_kind"], "투자 문의")

    def test_as_message_none_when_no_address(self):
        self.assertIsNone(rules.webflow_as_message(msg(body="문의 유형 : 기타 문의\n주소 없음\n")))

    def test_korean_ratio(self):
        self.assertEqual(rules.korean_ratio(""), 0.0)
        self.assertEqual(rules.korean_ratio("가나다abc"), 0.5)


if __name__ == "__main__":
    unittest.main()
