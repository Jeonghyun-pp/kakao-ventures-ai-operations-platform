"""LLM 분류 — 규칙이 못 잡은 메일만 본다.

왜 규칙이 아니라 LLM인가: 콜드 인바운드 투자 제안(motiQ, PETKIUM)과 협력 제안(Airwallex)과
출자 제안(Example Custodian)은 문면이 전부 "안녕하세요, 제안드립니다"로 시작한다. 발신 도메인으로도,
키워드로도 갈리지 않는다. 사람이 읽어야 갈리는 축이라 LLM이 맞다.

설계 원칙 (net-sourcing/core/llm.py 승계):
  · **키가 없거나 API가 실패하면 조용히 UNKNOWN을 반환한다.** 파이프라인이 죽지 않는다.
  · **확신이 낮으면 UNKNOWN.** UNKNOWN은 버려지는 게 아니라 사람에게 에스컬레이션된다.
    애매한 걸 억지로 분류하면 잘못된 초안이 만들어지고, 사람이 그걸 그대로 보낼 위험이 있다.
"""
from __future__ import annotations

import json
import os

from . import rules

DEFAULT_MODEL = "claude-opus-4-8"

SYSTEM = """당신은 카카오벤처스 공용 메일함(deals@example.vc)의 분류기다.
각 메일을 정확히 하나의 카테고리로 분류하라. 판단이 아니라 사실 분류다.

카테고리:
- INBOUND_KO: 창업자가 한국어로 보낸 투자 검토 요청/제안. 구글폼을 거치지 않고 메일로 직접 옴.
- INBOUND_EN: 위와 같으나 영문 메일.
- TRUSTED_REF: 신뢰 경로를 통해 들어온 투자 건. 내부 임직원 포워딩, 증권사·기관의 추천,
  기존에 미팅했던 팀의 후속 연락 등. 콜드메일이 아니라는 근거가 본문에 있어야 한다.
- EVENT_SPEAKER: 행사 초청, 강연·심사위원 섭외, 밋업/피칭 이벤트 참가 요청.
- PARTNERSHIP: 제휴·협력 제안 (솔루션 도입, 포트폴리오사 대상 서비스 제안, 학회 협업, 기업 탐방).
- LP_CUSTODY: 출자 제안, 조합 수탁·설정, 펀드 행정 관련 금융기관 제안.
- RECRUIT: 채용 문의, 인턴/정규직 지원, 커리어 문의.
- IMPERSONATION: 카카오벤처스를 사칭한 메일을 받았다며 진위를 확인하는 문의.
- UNKNOWN: 위 어디에도 확신을 갖고 넣을 수 없는 것.

중요:
- 확신이 낮으면 반드시 UNKNOWN을 써라. 억지로 분류하지 마라. UNKNOWN은 사람에게 전달되므로
  손실이 아니다. 반대로 오분류는 잘못된 회신이 외부로 나가게 만든다.
- 대량 발송 스팸처럼 보이면 UNKNOWN이 아니라 그 성격에 맞는 카테고리를 고르되, 확신이 없으면 UNKNOWN.
- INBOUND_KO/EN 구분은 메일 본문의 언어로 정한다. 발신자 국적이 아니다."""

SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string", "enum": rules.LLM_CATEGORIES},
                    "reason": {"type": "string", "description": "한국어 한 문장 근거"},
                },
                "required": ["id", "category", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def classify(msgs: list[dict], cfg: dict) -> dict[str, dict]:
    """{msg_id: {category, reason}}. 실패하면 전부 UNKNOWN."""
    if not msgs:
        return {}
    fallback = {m["id"]: {"category": rules.UNKNOWN, "reason": "LLM 미사용 — 사람 확인 필요"}
                for m in msgs}
    if not cfg.get("llm", {}).get("enabled", True) or not available():
        return fallback

    payload = [
        {
            "id": m["id"],
            "from": m.get("sender_raw", "")[:200],
            "subject": m.get("subject", "")[:300],
            "body": (m.get("body") or "")[:2500],
        }
        for m in msgs
    ]
    try:
        import anthropic

        resp = anthropic.Anthropic().messages.create(
            model=cfg["llm"].get("model", DEFAULT_MODEL),
            max_tokens=8000,
            system=SYSTEM,
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if resp.stop_reason == "max_tokens":
            return fallback
        data = json.loads("".join(b.text for b in resp.content if b.type == "text"))
        out = dict(fallback)
        for r in data.get("results", []):
            if r["id"] in out:
                out[r["id"]] = {"category": r["category"], "reason": r["reason"]}
        return out
    except Exception as e:                       # 키 없음·네트워크·스키마 불일치 전부 여기로
        for v in fallback.values():
            v["reason"] = f"LLM 실패({type(e).__name__}) — 사람 확인 필요"
        return fallback
