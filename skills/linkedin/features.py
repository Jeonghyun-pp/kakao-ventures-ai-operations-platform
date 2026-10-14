"""카드 한 장의 특징 — 사람이 아니라 **특징**을 배운다 [사용자 결정 2026-10-14].

코드로 뽑는다(claude 아님): 같은 사람이면 매주 같은 값이 나와야 주별 비교가 된다.
특징은 고정 목록이다 — 여기 하나를 더하면 learn 의 표에 한 줄이 생긴다. 구간 경계는 근거가 있는 것만(주석).
행선지: 사람 특징 → criteria.md · 시간 특징(신선도) → card 정렬·설정 · 서버로 걸 수 있는 특징 → axes.
"""
from __future__ import annotations

from datetime import date

from . import profile


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


def extract(item: dict | None, card: dict) -> dict[str, str]:
    """item = 산 날 원본(없으면 None) · card = {axis, tier, posted_day, captured_day, explore, resurfaced}."""
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
