"""결정적 분류 규칙 — LLM보다 먼저 돈다.

원칙: **확신할 수 있는 것만 규칙이 잡는다.** 애매하면 None을 반환해 LLM(또는 사람)에게 넘긴다.
규칙이 무리해서 분류하면 오분류가 조용히 외부로 나간다.
"""
from __future__ import annotations

import re

# 카테고리 상수
BPLAN_INTAKE = "BPLAN_INTAKE"          # 비플랜 접수 메일 — 절대 건드리지 않음
WEBFLOW = "WEBFLOW"                    # 홈페이지 문의 (하위 유형은 webflow.kind)
AD_NOISE = "AD_NOISE"                  # 광고·뉴스레터·시스템 알림
MASS_BLAST = "MASS_BLAST"              # 대량 수신자 스팸
IMPERSONATION = "IMPERSONATION"        # 사칭·피싱 확인 문의 (이번 범위 밖 — 차단만)
INBOUND_KO = "INBOUND_KO"
INBOUND_EN = "INBOUND_EN"
TRUSTED_REF = "TRUSTED_REF"
EVENT_SPEAKER = "EVENT_SPEAKER"
PARTNERSHIP = "PARTNERSHIP"
LP_CUSTODY = "LP_CUSTODY"
RECRUIT = "RECRUIT"
NAMED_PARTNER = "NAMED_PARTNER"
UNKNOWN = "UNKNOWN"

LLM_CATEGORIES = [
    INBOUND_KO, INBOUND_EN, TRUSTED_REF, EVENT_SPEAKER,
    PARTNERSHIP, LP_CUSTODY, RECRUIT, IMPERSONATION, UNKNOWN,
]

# 비플랜 접수 메일 제목. zerobot이 구글폼 접수 때 보내는 고정 양식이다.
_BPLAN_SUBJECT = re.compile(r"\[카카오벤처스\].*로부터\s*접수된\s*사업계획서")

_WEBFLOW_SENDERS = ("no-reply-forms@webflow.com", "forms@webflow.com")

# 사칭 확인 문의 — 실측(2026-07-12~13, 피해자 3명). 자동 회신 대상에서 빼는 것이 목적.
_IMPERSONATION_HINTS = (
    "kakaotalk.vc", "legit check", "verification of", "verify whether",
    "사칭", "피싱", "is this email legitimate", "interview invitation",
)


def korean_ratio(text: str) -> float:
    ko = len(re.findall(r"[가-힣]", text or ""))
    en = len(re.findall(r"[A-Za-z]", text or ""))
    return ko / (ko + en) if (ko + en) else 0.0


def parse_webflow(body: str) -> dict:
    """Webflow 폼 메일 본문 파싱.

    실제 형식 (2026-07 확인):
        문의 유형: 투자 문의
        이메일: someone@example.com
        문의 제목: ...
        문의 내용: ...

    이메일을 못 찾으면 reply_to=None을 반환한다. 호출부는 그 경우 초안을 만들면 안 된다
    (수신자를 추측하면 no-reply@webflow.com으로 나가 반송된다).
    """
    def grab(label: str) -> str:
        m = re.search(rf"{label}\s*:\s*(.+)", body or "")
        return m.group(1).strip() if m else ""

    kind = grab("문의 유형")
    email = grab("이메일")
    # 라벨 뒤에 붙어 나오는 경우가 있어(줄바꿈 없이) 주소만 다시 추출
    addr = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", email)
    # 본문 어디든 있는 주소를 폴백으로 쓰되, webflow 자체 주소는 배제한다.
    if not addr:
        cands = [
            a for a in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", body or "")
            if "webflow.com" not in a.lower() and "kakao" not in a.lower()
        ]
        addr = re.match(r".*", cands[0]) if cands else None
        reply_to = cands[0] if cands else None
    else:
        reply_to = addr.group(0)

    return {
        "kind": kind,                     # "투자 문의" | "섭외 문의" | "멘토링 문의" | ...
        "reply_to": reply_to,
        "title": grab("문의 제목"),
        "content": grab("문의 내용"),
    }


def classify(msg: dict, cfg: dict) -> dict | None:
    """규칙으로 분류되면 {category, reason, ...}, 아니면 None(→ LLM)."""
    sender = msg.get("sender", "")
    subject = msg.get("subject", "")
    body = msg.get("body", "")
    blob = f"{subject}\n{body}".lower()

    # ① 비플랜 접수 메일 — 최우선. 무조건 보호.
    #    bplan-automation의 sync가 이 메일을 인박스에서 제목으로 찾는다. 우리가 아카이빙하거나
    #    라벨을 옮기면 sync가 "메일 못 찾음"으로 전체 정지하고 다음 실행에도 계속 막힌다.
    if _BPLAN_SUBJECT.search(subject):
        return {"category": BPLAN_INTAKE, "reason": "비플랜 접수 양식 제목"}

    # ② Webflow 홈페이지 문의 — 발신 주소로 100% 확정된다.
    if any(s in sender for s in _WEBFLOW_SENDERS):
        wf = parse_webflow(body)
        return {"category": WEBFLOW, "reason": f"webflow 폼 ({wf['kind'] or '유형미상'})", "webflow": wf}

    # ③ 사칭·피싱 확인 문의 — 자동 회신 대상에서 빼기 위해 규칙으로 먼저 잡는다.
    if any(h in blob for h in _IMPERSONATION_HINTS):
        return {"category": IMPERSONATION, "reason": "사칭/피싱 확인 문의 정황"}

    # ④ 대량 발송 — 수신자 수로 판정. 실측 KORION 건은 수신자 140+.
    if msg.get("recipient_count", 0) >= cfg["noise"]["mass_blast_recipients"]:
        return {"category": MASS_BLAST,
                "reason": f"수신자 {msg['recipient_count']}명 대량 발송"}

    # ⑤ 광고·뉴스레터·시스템 알림
    noise = cfg["noise"]
    if msg.get("list_unsubscribe"):
        return {"category": AD_NOISE, "reason": "List-Unsubscribe 헤더"}
    for tok in noise["sender_tokens"]:
        if tok.lower() in sender:
            return {"category": AD_NOISE, "reason": f"발신처 '{tok}'"}
    for tok in noise["subject_tokens"]:
        if tok.lower() in subject.lower():
            return {"category": AD_NOISE, "reason": f"제목 '{tok}'"}

    # 나머지는 LLM(없으면 UNKNOWN)에게. 규칙이 무리하지 않는다.
    return None


# 사람을 **부르는** 표지. 이름 뒤에 이게 붙어야 지명으로 본다.
# 한국어 비즈니스 메일에서 사람을 부르는 실제 형태다(`리뷰어일 심사역님께` · `ReviewerOne님`).
_NAME_MARKER = r"(?:님|씨|께|\s*심사역|\s*파트너|\s*대표|\s*앞)"


def find_named_partner(msg: dict, cfg: dict) -> str | None:
    """제목/본문에 특정 심사역이 **지명**됐는지. 지명됐으면 그 사람 메일 주소.

    ## 🔴 [2026-08-16 · 결정 ⑩] 부분일치를 버렸다

    예전엔 `if name.lower() in blob` 한 줄이었다. 토큰이 **더 긴 단어 안에** 들어가면
    엉뚱한 동료 메일함으로 메일이 나간다:

        Cl«reviewer-three»ct · ch«reviewer-three»l              → reviewer-three@      ← 실측으로 걸린 건
        «제로»넥스(실제 회사명) · «제로»베이스   → zero@
        j«ade»d · «앤»드류                    → reviewer-one@ / reviewer-three@

    실측(plan 12본 · 고유 메일 156건 · **제목만**): 지명 매칭 1건 → **전부 오탐**
    (`[카카오벤처스]Clannect 로부터 접수된 사업계획서`), 정당한 지명 0건.
    ⚠️ 본문 1,500자는 plan 에 저장되지 않아 못 쟀다 — 매칭 표면의 대부분이 거기라 **하한**이다.

    당시 안 터진 건 두 겹이 막고 있어서다 — 카테고리 게이트(EVENT_SPEAKER·PARTNERSHIP·
    UNKNOWN 만 오버라이드)와 HARD-GATE #1(`apply` 미가동). **컷오버하면 실행된다.**

    ### 왜 엄격한 쪽으로 갔나 — 비용이 비대칭이다

        오탐(잘못 포워드)  동료 메일함에 무관한 메일 → 반복되면 신뢰를 잃는다
        미탐(지명 놓침)    일반 라우팅을 타고 사람이 큐에서 본다 → **유실이 아니다**

    ### 두 경로

    ① **주소 직접 언급** — 본문에 `reviewer-one@example.vc` 이 있으면 모호함이 0이다.
    ② **이름 + 호칭/멘션** — 이름이 단어 경계에 서 있고 뒤에 `님/씨/께/심사역/파트너/
       대표/앞` 이 붙거나, 앞에 `@` 가 붙을 때만.

    ②의 **왼쪽 경계**가 `Clannect`·`제로넥스` 를 막고, **오른쪽 호칭 요구**가
    `제로 웨이스트`·`zero trust` 같은 띄어쓴 일반어를 막는다. 단어 경계만으로는 후자를
    못 막는데, `제로`·`zero` 가 흔한 단어라 그게 8토큰 중 4개다.

    ⚠️ 알고 포기한 것: `ReviewerOne, 안녕하세요` 같은 영어식 직접 호칭은 놓친다. 위 비대칭상
       놓치는 쪽이 싸다 — 그 메일은 일반 라우팅으로 사람에게 간다.
    """
    blob = f"{msg.get('subject','')}\n{msg.get('body','')[:1500]}".lower()
    partners = (cfg.get("partners") or {})

    # ① 주소가 직접 적혀 있으면 그 자체가 지명이다(오탐 여지 없음)
    for addr in partners.values():
        if str(addr).lower() in blob:
            return addr

    # ② 이름 + 호칭/멘션
    for name, addr in partners.items():
        n = str(name).lower()
        if not n:
            continue
        # 왼쪽 경계 — 한글 토큰은 앞이 한글이 아니어야, 영문 토큰은 앞이 영숫자가 아니어야.
        left = r"(?<![가-힣])" if re.search(r"[가-힣]", n) else r"(?<![a-z0-9])"
        esc = re.escape(n)
        pat = rf"(?:@{esc}(?![a-z0-9]))|(?:{left}{esc}{_NAME_MARKER})"
        if re.search(pat, blob):
            return addr
    return None
