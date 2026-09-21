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

from kvlib import constraints

# 규칙이 확정하는 것
BPLAN_INTAKE = "BPLAN_INTAKE"
WEBFLOW = "WEBFLOW"
IMPERSONATION = "IMPERSONATION"
MASS_BLAST = "MASS_BLAST"
INTERNAL = "INTERNAL"
SYSTEM_NOTICE = "SYSTEM_NOTICE"
AD_NOISE = "AD_NOISE"

# claude 가 판정하는 것 — 이 9개 밖의 값은 파서가 버린다
INBOUND_KO = "INBOUND_KO"
INBOUND_EN = "INBOUND_EN"
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
#    특히 영문 홍보는 콜드 투자 제안과 문면이 비슷해 INBOUND_EN 으로 새기 쉬웠다.
#    claude 가 내용을 읽고 홍보라고 하면 아카이빙한다 — 언어와 무관하게.
JUDGE_CATEGORIES = (INBOUND_KO, INBOUND_EN, TRUSTED_REF, EVENT_SPEAKER,
                    PARTNERSHIP, VENDOR_PITCH, LP_CUSTODY, RECRUIT, IMPERSONATION,
                    AD_NOISE, FORM_ERROR, UNKNOWN)

# 🔴 옛 코드의 NAMED_PARTNER 상수는 지웠다. 어디서도 대입되지 않는 죽은 상수였다.
#    지명은 카테고리가 아니라 **행동을 덮어쓰는 것**이고, 코드가 실제로 그렇게 동작한다.

# ---------------------------------------------------------------- 규칙 재료

_BPLAN_SUBJECT = re.compile(r"\[카카오벤처스\].*로부터\s*접수된\s*사업계획서")

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
_INTERNAL_DOMAIN = "@example.vc"

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

# 🔴 심사역 슬랙 사용자 ID. **핑을 찍으려면 이게 있어야 한다.**
#    봇에 users:read 스코프가 없어 이메일로 조회할 수 없다(실측 08-18) — 값을 받아 채운다.
#    비어 있으면 핑 없이 이름만 적어 게시한다. 조용히 넘어가지 않고 그 사실을 남긴다.
PARTNER_SLACK: dict[str, str] = {
    "reviewer-one@example.vc": "",
    "reviewer-two@example.vc": "",
    "reviewer-three@example.vc": "",
    "reviewer-a@example.vc": "",
}

# 심사역 명단. 한 사람이 한글·영문 두 키를 갖는다.
PARTNERS = {
    "리뷰어일": "reviewer-one@example.vc", "reviewer-one": "reviewer-one@example.vc",
    "리뷰어이": "reviewer-two@example.vc", "reviewer-two": "reviewer-two@example.vc",
    "앤": "reviewer-three@example.vc", "reviewer-three": "reviewer-three@example.vc",
    "제로": "reviewer-a@example.vc", "zero": "reviewer-a@example.vc",
}

# 🔴 호칭이 붙어야 지명으로 본다.
#
#    2026-08-16 실측 오탐: `Cl«reviewer-three»ct`(회사명) · `ch«reviewer-three»l` · `«제로»넥스` · `j«ade»d`.
#    부분일치로 되돌리지 마라. 놓치는 쪽은 일반 라우팅으로 사람이 보게 되니 유실이 아니지만,
#    오탐은 동료 메일함에 무관한 메일이 반복 도달해 **신뢰를 잃는다.**
#    알고도 놓치는 것: "ReviewerOne, 안녕하세요" 처럼 호칭 없이 쉼표만 오는 경우.
_NAME_MARKER = r"(?:님|씨|께|\s*심사역|\s*파트너|\s*대표|\s*앞)"


# ---------------------------------------------------------------- 보조

def parse_webflow(body: str) -> dict:
    """웹플로우 폼 본문에서 문의 유형·문의자 주소·제목·내용을 뽑는다.

    🔴 발신은 `no-reply-forms@webflow.com` 이다. 답장하면 거기로 가서 반송된다
       (2026-07-10 실제로 550 반송, 문의자는 답을 못 받았다).
       그래서 **본문에서 문의자 주소를 찾아 포워드**한다. 못 찾으면 초안을 안 만든다.
    """
    def field(label: str) -> str:
        m = re.search(rf"{label}\s*:\s*(.+)", body or "")
        return m.group(1).strip() if m else ""

    email_line = field("이메일")
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", email_line)
    reply_to = m.group(0) if m else ""

    if not reply_to:
        # 폴백: 본문 전체에서 찾되 webflow·kakao 주소는 뺀다 (그게 반송의 원인이다)
        for cand in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", body or ""):
            low = cand.lower()
            if "webflow.com" not in low and "kakao" not in low:
                reply_to = cand
                break

    return {
        "kind": field("문의 유형"),
        "reply_to": reply_to,
        "title": field("문의 제목"),
        "content": field("문의 내용"),
    }


def webflow_as_message(msg: dict) -> dict | None:
    """웹플로우 폼을 **문의자가 보낸 메일**처럼 바꾼다. 주소를 못 찾으면 None.

    ## 🔴 왜 폼의 `문의 유형` 칸을 안 쓰는가

    실측 2026-09-02, 폼 9건 전량:
    ```
    투자 문의 4건    전부 진짜 투자 문의였다              ✅ 이 값은 맞았다
    기타 문의 4건    강연 초청 1 · 템프서울 입주 2 · 사칭 확인 1   잡동사니다
    섭외 문의 1건    내용은 사칭 확인이었다                ❌ 틀렸다
    ```
    유형은 **문의자가 드롭다운에서 고르는 값**이라 우리 갈래와 관계가 없다.
    유형으로 가르면 강연 초청(Times Higher Education, "Dear Mr Ki-Example Person K")과
    사칭 확인이 같은 칸에 들어간다.

    그래서 유형 대신 **내용을 읽는다.** 폼을 문의자가 쓴 메일로 바꿔놓으면
    메일과 같은 판정·같은 행동표를 그대로 쓸 수 있다 — 창구가 달라도 결과가 같아진다.
    (전에는 메일로 온 사칭 확인은 아카이브, 폼으로 온 사칭 확인은 비플랜방이었다.)

    🔴 `sender` 를 문의자로 바꾸는 것이 이 함수의 핵심이다. 원래 발신은
       `no-reply-forms@webflow.com` 이고 거기로 답장하면 550 반송된다(2026-07-10 실측).
       바꿔두면 회신 초안이 **구조적으로** 문의자에게만 간다.
    """
    form = parse_webflow(msg.get("body") or "")
    if not form["reply_to"]:
        return None
    return {**msg,
            "sender": form["reply_to"].lower(),
            "sender_raw": form["reply_to"],
            "subject": form["title"] or msg.get("subject", ""),
            "body": f"{form['title']}\n\n{form['content']}",
            # 폼은 우리에게만 온다 — 원래 헤더의 수신자 수는 웹플로우의 것이라 의미가 없다
            "to": [], "recipient_count": 1, "list_unsubscribe": False,
            "webflow_kind": form["kind"]}


def find_named_partner(msg: dict) -> str:
    """심사역이 지명됐나 → 그 사람 주소. 아니면 "".

    두 갈래다.
      ① 주소가 본문에 그대로 있으면 호칭이 필요 없다 (reviewer-one@example.vc)
      ② 이름만 있으면 호칭이 붙어야 한다 (리뷰어일님 / @reviewer-one)
    """
    limit = constraints.get("gmail.named_partner_body_chars")
    blob = f"{msg.get('subject', '')}\n{(msg.get('body') or '')[:limit]}".lower()

    for addr in set(PARTNERS.values()):
        if addr.lower() in blob:
            return addr

    for name, addr in PARTNERS.items():
        esc = re.escape(name.lower())
        # 왼쪽 경계를 막아 단어 속 부분일치를 끊는다
        left = r"(?<![가-힣])" if re.search(r"[가-힣]", name) else r"(?<![a-z0-9])"
        pat = rf"(?:@{esc}(?![a-z0-9]))|(?:{left}{esc}{_NAME_MARKER})"
        if re.search(pat, blob):
            return addr
    return ""


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
