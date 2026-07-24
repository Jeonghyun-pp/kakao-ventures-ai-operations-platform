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


def find_named_partner(msg: dict, cfg: dict) -> str | None:
    """제목/본문에 특정 심사역이 지명됐는지. 지명됐으면 그 사람 메일 주소."""
    blob = f"{msg.get('subject','')}\n{msg.get('body','')[:1500]}".lower()
    for name, addr in (cfg.get("partners") or {}).items():
        if str(name).lower() in blob:
            return addr
    return None
