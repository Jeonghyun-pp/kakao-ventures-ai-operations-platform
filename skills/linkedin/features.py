"""카드 한 장의 특징 — 사람이 아니라 **특징**을 배운다 [사용자 결정 2026-10-14].

코드로 뽑는다(claude 아님): 같은 사람이면 매주 같은 값이 나와야 주별 비교가 된다.
특징은 고정 목록이다 — 여기 하나를 더하면 learn 의 표에 한 줄이 생긴다. 구간 경계는 근거가 있는 것만(주석).
행선지: 사람 특징 → criteria.md · 시간 특징(신선도) → card 정렬·설정 · 서버로 걸 수 있는 특징 → axes.
"""
from __future__ import annotations

from datetime import date

from . import profile


# 글에서만 보이는 특징 — claude 가 붙인다(learn.tag). 🔴 어휘는 고정이다: 매주 같은 말로 붙어야 주별로 비교된다.
#    키워드 목록으로 세지 않는 이유 — 다음 표현("acq. by", "1.5M+", "MAU")에서 샌다. 정의를 주고 판단하게 한다.
#    처음 넷은 10/14 ✅ 20 vs 안 고름 41 비교에서 나온 후보(키워드로 거칠게 잰 값), 나머지는 갈리는지 볼 대조군.
TAGS = {
    "트랙션공개": "엑싯·인수·사용자 수·매출·성장률 같은 성과 수치를 스스로 밝혔다 (키워드 실측 ✅ 35% · 안 고름 5%)",
    "연구자형": "박사·연구원·연구실 중심 경력이고 제품·사업 신호는 약하다 (✅ 5% · 안 고름 29%)",
    "연쇄창업": "지금 회사 전에 창업한 회사가 있다",
    "제품공개": "무엇을 만드는지(문제·고객·제품)를 구체적으로 밝혔다 (✅ 55% · 안 고름 51% — 대조군)",
    "빅테크": "글로벌 빅테크(구글·메타·아마존·MS·애플 등) 출신",
    "딥테크": "로봇·하드웨어·바이오·소재·반도체처럼 연구개발이 무거운 분야",
    "재학생": "지금 학생이다(학부·대학원·MBA 재학)",
}


def _bin(v: int | None, edges: tuple[int, ...], unit: str = "개월") -> str:
    """edges=(3, 12, 24) → '0~3' · '4~12' · '13~24' · '25+'. 값이 없으면 '없음'."""
    if v is None:
        return "없음"
    lo = 0
    for e in edges:
        if v <= e:
            return f"{lo}~{e}{unit}"
        lo = e + 1
    return f"{lo}{unit}+"


def extract(item: dict | None, card: dict, tags: list[str] | None = None) -> dict[str, str]:
    """item = 산 날 원본(없으면 None) · card = {axis, tier, posted_day, captured_day, explore, resurfaced} · tags = learn.tag 결과(없으면 None)."""
    out = {
        "축": card.get("axis") or "?",
        "판정 Tier": card.get("tier") or "?",
        "탐색 카드": "예" if card.get("explore") else "아니오",
        "다시 올라옴": "예" if card.get("resurfaced") else "아니오",
    }
    # 시간 특징 — 산 뒤 카드까지 일수. 9/29 분석: 7일 이내 ✅ 31~47% vs 8일+ 5~7% (n=100)
    if card.get("posted_day") and card.get("captured_day"):
        d = (date.fromisoformat(card["posted_day"][:10]) - date.fromisoformat(card["captured_day"][:10])).days
        out["신선도(산 뒤 카드까지)"] = _bin(max(d, 0), (7, 30), "일")
    if tags is not None:
        out.update({f"태그 · {t}": "예" if t in tags else "아니오" for t in TAGS})
    if not item:
        return out
    ps = profile.positions(item)
    companies = " ".join(p["company"] for p in ps if p["company"]).lower()
    about = str(item.get("about") or "")
    age = profile.estimated_age(item)
    out.update({
        # 9/29 분석: 창업 4~12개월 ✅ 29% · 0~3개월 6% — 경계 3·12 는 그 분석과 카드 기준 C1(3개월)에서
        "창업 개월": _bin(profile.founding_months(item), (3, 12, 24)),
        "현 직장 재직": _bin(profile.tenure_months(item), (3, 12, 36)),        # 36 = card_reject MAX_TENURE
        "공백": "있음" if (profile.gap_months(item) or 0) > 0 or profile.fulltime_gap_months(item) else "없음",
        "스텔스": "예" if "stealth" in companies else "아니오",
        "창업 직함": "예" if any(profile.is_founder_role(p) for p in ps) else "아니오",
        # 9/29 분석: 소개글 100자+ 가 ✅ 쪽
        "소개글": "없음" if not about else ("100자+" if len(about) >= 100 else "100자 미만"),
        "추정 나이": "모름" if age is None else ("~29" if age < 30 else "30~34" if age < 35 else "35~39" if age < 40 else "40+"),
        "한국 학교": "예" if any("학교" in s for s in profile.korea_signals(item)) else "아니오",
        "현 직장 수": str(min(len(ps), 3)) + ("+" if len(ps) >= 3 else ""),
    })
    return out
