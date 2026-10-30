"""확실한 것만 규칙으로 잡는다. 나머지는 claude 에게 넘긴다.

## 🔴 규칙은 확신이 있을 때만 손을 댄다

애매한 걸 규칙으로 억지로 확정하면 **조용히 틀린다.** 규칙이 못 잡으면 `None` 을 돌려주고
claude 가 본다. claude 도 애매하면 UNKNOWN 이고, 그건 사람에게 간다 — 슬랙 메시지 하나 값이다.
오분류 한 건은 그보다 훨씬 비싸다.

## 판정 순서가 곧 우선순위다

앞에서 확정되면 뒤를 안 본다. 순서를 바꾸면 동작이 바뀐다.

    ① BPLAN_INTAKE   접수 메일. 무조건 1번 — 뒤 규칙이 이걸 광고로 잡으면 비플랜이 멈춘다
    ② WEBFLOW        발신 주소로 확정된다
    ③ IMPERSONATION  사칭. 광고 필터보다 앞이다 — 사칭 메일이 뉴스레터 형식일 수 있다
    ④ MASS_BLAST     수신자 수
    ⑤ INTERNAL       사내 발신. 🔴 광고 검사보다 **앞**이다 — 사내 공지가 메일링 툴로
                     나가면서 구독해지 헤더가 붙어 광고로 잡혔다
    ⑥ AD_NOISE       구독해지 헤더 · 발신/제목 토큰
"""
from __future__ import annotations

import re

from kvlib import connections, constraints

# 규칙이 확정하는 것
BPLAN_INTAKE = "BPLAN_INTAKE"
WEBFLOW = "WEBFLOW"
IMPERSONATION = "IMPERSONATION"
MASS_BLAST = "MASS_BLAST"
INTERNAL = "INTERNAL"
SYSTEM_NOTICE = "SYSTEM_NOTICE"
AD_NOISE = "AD_NOISE"

# claude 가 판정하는 것 — JUDGE_CATEGORIES 밖의 값은 파서가 버린다
INBOUND_KO = "INBOUND_KO"
# 🔴 [사용자 결정 2026-10-08] 옛 어휘다 — 모델에게 더는 안 준다. 언어가 아니라 사람으로 가른다:
#    외국인 콜드 제안은 FOREIGN_COLD(아카이브), 한국인은 언어와 무관하게 INBOUND_KO.
#    이름은 DB 에 남은 옛 판정을 읽으려고만 둔다(route 는 손대지 않는다).
INBOUND_EN = "INBOUND_EN"
FOREIGN_COLD = "FOREIGN_COLD"
CLOSED_FOLLOWUP = "CLOSED_FOLLOWUP"
TRUSTED_REF = "TRUSTED_REF"
EVENT_SPEAKER = "EVENT_SPEAKER"
PARTNERSHIP = "PARTNERSHIP"
VENDOR_PITCH = "VENDOR_PITCH"
LP_CUSTODY = "LP_CUSTODY"
RECRUIT = "RECRUIT"
FORM_ERROR = "FORM_ERROR"
UNKNOWN = "UNKNOWN"

# 🔴 AD_NOISE 가 여기에도 있다 [사용자 결정 2026-08-18].
#    규칙은 발신·제목 토큰으로만 광고를 잡는데, 그 명단에 없는 홍보 메일은 못 잡는다.
#    특히 영문 홍보는 콜드 투자 제안과 문면이 비슷해 투자 문의로 새기 쉬웠다.
#    claude 가 내용을 읽고 홍보라고 하면 아카이빙한다 — 언어와 무관하게.
JUDGE_CATEGORIES = (INBOUND_KO, FOREIGN_COLD, TRUSTED_REF, EVENT_SPEAKER,
                    PARTNERSHIP, VENDOR_PITCH, LP_CUSTODY, RECRUIT, IMPERSONATION,
                    AD_NOISE, FORM_ERROR, CLOSED_FOLLOWUP, SYSTEM_NOTICE, UNKNOWN)
# SYSTEM_NOTICE 는 규칙(발신 주소 정확 일치)이 먼저 잡고, 모델도 붙일 수 있다 [2026-09-22] — 기계 알림에 넣을 칸이 없어
# 하루 60통이 UNKNOWN → 포워드로 새던 것. 현행 criteria.md 는 이 이름을 안 쓰므로 현행 판정은 안 바뀐다.


# ---------------------------------------------------------------- 규칙 재료

_BPLAN_SUBJECT = re.compile(rf"\[{re.escape(connections.get('intake.notification_company_name'))}\].*로부터\s*접수된\s*사업계획서")

_WEBFLOW_SENDERS = ("no-reply-forms@webflow.com", "forms@webflow.com")

# 사람이 아니라 시스템이 보내는 안내. 논의할 게 없고 회신할 곳도 없다.
# 🔴 도메인이 아니라 **주소를 정확히** 적는다. `kban.or.kr` 로 막으면 같은 기관의
#    사람이 보낸 메일까지 걸린다 — 좁게 막고, 필요하면 한 줄씩 늘린다.
#    [사용자 결정 2026-09-02] 전에는 UNKNOWN 으로 빠져 #Squad-비플랜 으로 갔다.
#    실물: "운영사 회원가입 인증키 EXAMPLE_CODE — 소속 직원이 입력할 수 있도록 공유해 주세요"
_NOTICE_SENDERS = ("tips_admin@kban.or.kr",)

# 사칭 확인 문의 — 회신은 절대 안 붙인다.
#
# 🔴 [사용자 결정 2026-08-18] 행동이 escalate → archive 로 바뀌면서 **오탐 비용이 커졌다.**
#    전에는 오탐이 나도 인박스에 남아 사람 눈에 띄었다. 이제는 조용히 사라진다.
#    그래서 넓은 힌트 둘을 뺐다 — 근거가 없고, 이제 대가가 실재한다:
#        "interview invitation"  정상 면접 안내가 통째로 아카이빙된다
#        "verification of"       "verification of documents/account" 등에 걸린다
#    남긴 것들은 우리 이름을 사칭하는 맥락에서만 나오는 표현이다.
_IMPERSONATION_HINTS = (
    "kakaotalk.vc", "legit check", "verify whether",
    "사칭", "피싱", "is this email legitimate",
)

# 🔴 사내 발신은 광고 규칙에서 뺀다 [사용자 결정 2026-08-18].
#    월간 컴 안내·세미나 공지가 메일링 툴로 나가면서 List-Unsubscribe 가 붙어
#    광고로 잡혔다. 정현님이 읽어야 하는 메일이다.
#    (접수 메일은 이미 BPLAN_INTAKE 가 먼저 잡으므로 영향 없다.)
#
#    🔴 규칙에서 빼기만 하고 None 을 돌려주면 claude 로 넘어가 UNKNOWN → **#Squad-비플랜
#       포워드**가 된다. 읽으시라고 뺀 메일을 남의 채널로 보내는 셈이다(08-18 실측).
#       그래서 명시적으로 protect 한다 — 인박스에 그대로 두고 아무것도 안 한다.
_INTERNAL_DOMAIN = connections.get("organization.internal_domain").lower()

_AD_SENDER_TOKENS = (
    "innoforest.co.kr", "markncompany.co.kr", "iva.or.kr", "mail.instagram.com",
    "email.openai.com", "interactivebrokers.com", "substack.com",
    "updates.notion.so", "mail.notion.so", "person-c6f3b1bbe31e@example.com",
    "mailer-daemon", "person-58d31a3f85bd@example.com", "noreply", "no-reply", "donotreply",
    # 🔴 우리 청구서·영수증 [사용자 결정 2026-08-18]. 전엔 UNKNOWN 이 되어
    #    #Squad-비플랜으로 포워드됐다 — 남의 채널에 우리 결제 내역이 갔다.
    #    apify 는 hello@ 만 있고 실제 발신은 billing@ 이라 안 걸렸다.
    "billing@", "invoice+", "invoice@", "receipt@", "statements@",
)
_AD_SUBJECT_TOKENS = ("(광고)", "수신거부", "뉴스레터", "무료 세미나", "참가자 모집", "홍보요청")

# ---------------------------------------------------------------- 보조

# ---------------------------------------------------------------- 규칙 판정

def classify(msg: dict) -> dict | None:
    """규칙으로 확정되면 {category, why}. 아니면 None → claude 몫."""
    subject = msg.get("subject") or ""
    sender = msg.get("sender") or ""
    body = msg.get("body") or ""

    # ① 비플랜 접수 — 무조건 첫 번째
    if _BPLAN_SUBJECT.search(subject):
        return {"category": BPLAN_INTAKE, "why": "비플랜 접수 메일 제목 형식"}

    # ② 웹플로우 — 발신 주소로 확정
    if any(s in sender for s in _WEBFLOW_SENDERS):
        return {"category": WEBFLOW, "why": "웹플로우 홈페이지 문의 폼"}

    # ③ 시스템 안내 — 발신 주소로 확정
    if any(a in sender for a in _NOTICE_SENDERS):
        return {"category": SYSTEM_NOTICE, "why": "시스템 자동 안내 발신"}

    # ④ 사칭 — 광고 필터보다 앞이다
    blob = f"{subject}\n{body}".lower()
    for hint in _IMPERSONATION_HINTS:
        if hint in blob:
            return {"category": IMPERSONATION, "why": f"사칭·검증 문의 신호 '{hint}'"}

    # ④ 대량 발송
    cap = constraints.get("gmail.mass_blast_recipients")
    if msg.get("recipient_count", 0) >= cap:
        return {"category": MASS_BLAST, "why": f"수신자 {msg['recipient_count']}명 (기준 {cap})"}

    # ⑤ 광고·뉴스레터 — 🔴 사내 발신은 먼저 뺀다
    if _INTERNAL_DOMAIN in sender:
        return {"category": INTERNAL, "why": "사내 발신 — 사람이 직접 읽는다"}
    if msg.get("list_unsubscribe"):
        return {"category": AD_NOISE, "why": "List-Unsubscribe 헤더"}
    for tok in _AD_SENDER_TOKENS:
        if tok in sender:
            return {"category": AD_NOISE, "why": f"발신 토큰 '{tok}'"}
    low_subject = subject.lower()
    for tok in _AD_SUBJECT_TOKENS:
        if tok in low_subject:
            return {"category": AD_NOISE, "why": f"제목 토큰 '{tok}'"}

    return None
