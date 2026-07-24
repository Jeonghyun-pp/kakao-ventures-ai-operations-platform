"""카테고리 → 액션 결정. 여기가 4개 태스크의 실제 처리 규칙이다.

액션 타입:
  protect  — 아무것도 하지 않는다 (비플랜 접수 메일)
  archive  — INBOX 라벨만 제거 (L0, 자동)
  reply    — 원본 스레드에 답장 초안 (L1, 사람이 발송)
  forward  — 포워드 초안 (L1, 사람이 발송)
  escalate — 인박스에 남기고 리포트에만 올림 (L2, 사람이 직접 판단)

reply/forward는 **초안까지만** 만든다. 발송 경로는 core/gmail.py에 존재하지 않는다.
"""
from __future__ import annotations

from pathlib import Path

from . import rules

TPL_DIR = Path(__file__).resolve().parent.parent / "templates"


def tpl(name: str, **kw) -> str:
    return (TPL_DIR / name).read_text(encoding="utf-8").format(**kw)


def _fwd(cfg: dict, note: str) -> str:
    return tpl("fwd_intro.txt", assignee=cfg["assignee"]["name"], note=note)


def _webflow(msg: dict, hit: dict, cfg: dict) -> dict:
    """홈페이지 문의 (태스크 2).

    <HARD-GATE> 파싱된 문의자 주소가 없으면 초안을 만들지 않는다. 예외 없음.
    reply를 쓰거나 주소를 추측하면 no-reply@webflow.com으로 나가 반송된다
    (2026-07-10 실제 발생: 550 Address not found. 문의자는 답을 못 받았다).
    """
    wf = hit["webflow"]
    gf = cfg["routing"]["google_form"]
    to = wf.get("reply_to")
    kind = (wf.get("kind") or "").strip()
    content = f"{wf.get('title','')} {wf.get('content','')}"
    is_ko = rules.korean_ratio(content) >= 0.15

    if not to:
        return {"action": "escalate",
                "note": "Webflow 문의자 주소를 본문에서 찾지 못했다. 수동 확인 필요 "
                        "(주소를 추측해 reply하면 no-reply@webflow.com으로 반송된다)."}

    if "투자" in kind:
        body = tpl("webflow_invest_ko.txt", google_form=gf) if is_ko \
            else tpl("webflow_invest_en.txt", google_form=gf)
        return {"action": "forward", "to": to, "body": body,
                "note": f"홈페이지 투자 문의 → 구글폼 안내 ({'국문' if is_ko else '영문'})"}

    if "멘토링" in kind:
        job = any(k in content for k in ("취업", "커리어", "진로", "career", "job"))
        body = tpl("webflow_mentoring_job.txt") if job \
            else tpl("webflow_mentoring_invest.txt", google_form=gf)
        return {"action": "forward", "to": to, "body": body,
                "note": f"홈페이지 멘토링 문의 ({'취업' if job else '투자'})"}

    if "TIPS" in kind.upper() or "팁스" in kind:
        return {"action": "forward", "to": to, "body": tpl("webflow_tips.txt", google_form=gf),
                "note": "홈페이지 TIPS 문의"}

    if "섭외" in kind:
        # 섭외 건은 대응하지 않고 슬랙으로 넘긴다 (에디 지시).
        return {"action": "forward", "to": cfg["routing"]["slack_bplan"],
                "body": _fwd(cfg, f"홈페이지로 섭외 문의가 들어와 포워드 드립니다.\n"
                                  f"(문의자: {to})"),
                "note": "홈페이지 섭외 문의 → #Squad-비플랜 공유"}

    return {"action": "escalate",
            "note": f"홈페이지 문의인데 유형이 '{kind or '미상'}'이라 템플릿을 못 고름. 수동 확인."}


def decide(msg: dict, hit: dict, cfg: dict) -> dict:
    """hit = {category, reason, ...} → 액션 dict."""
    cat = hit["category"]
    r = cfg["routing"]

    # ── 태스크 0: 비플랜 보호 ──
    if cat == rules.BPLAN_INTAKE:
        return {"action": "protect",
                "note": "비플랜 접수 메일 — bplan-automation sync 소관. 건드리지 않는다."}

    # ── 태스크 1: 광고성 아카이빙 ──
    if cat in (rules.AD_NOISE, rules.MASS_BLAST):
        return {"action": "archive", "note": "회신 없이 아카이빙"}

    # 영문 콜드 인바운드: auto-reply가 이미 나갔으므로 회신하지 않고 아카이빙한다.
    # (가이드라인: "영문 메일에는 보내지 않습니다" / 해외 인바운드는 포워드 없이 아카이빙)
    if cat == rules.INBOUND_EN:
        return {"action": "archive",
                "note": "영문 인바운드 — auto-reply 기발송. 회신 없이 아카이빙."}

    # ── 태스크 2: Webflow ──
    if cat == rules.WEBFLOW:
        return _webflow(msg, hit, cfg)

    # ── 태스크 4: 인바운드 투자 검토 요청 ──
    if cat == rules.INBOUND_KO:
        return {"action": "reply", "to": msg["sender"],
                "body": tpl("inbound_form_ko.txt", google_form=r["google_form"]),
                "note": "국문 인바운드 → 구글폼 접수 요청"}

    if cat == rules.TRUSTED_REF:
        return {"action": "reply", "to": msg["sender"],
                "body": tpl("trusted_direct_ko.txt"),
                "note": "신뢰 경로 인바운드 → 폼 없이 바로 접수 안내. "
                        "⚠️ 정말 신뢰 경로가 맞는지 발송 전 확인할 것."}

    # ── 태스크 3: 협력·섭외·채용·출자 ──
    if cat == rules.EVENT_SPEAKER:
        return {"action": "forward", "to": r["slack_bplan"],
                "body": _fwd(cfg, "행사/섭외 관련 메일이 와서 포워드 드립니다."),
                "note": "행사·섭외 → #Squad-비플랜 공유 (대응하지 않음)"}

    if cat == rules.PARTNERSHIP:
        # 슬랙에 먼저 공유하고, 패스 확인되면 거절 회신을 보낸다(가이드라인).
        # 두 초안을 다 만들어 두되, 회신 초안은 슬랙 확인 후 발송하도록 표시한다.
        return {"action": "forward", "to": r["slack_bplan"],
                "body": _fwd(cfg, "협력 제안 메일이 와서 포워드 드립니다."),
                "note": "협력 제안 → #Squad-비플랜 공유",
                "also_reply": {"to": msg["sender"], "body": tpl("partnership_decline.txt"),
                               "note": "거절 회신 초안 — 슬랙에서 패스 확인 후 발송할 것"}}

    if cat == rules.LP_CUSTODY:
        # 관리팀(esther) 직행은 검증 안 된 가정이었다 — 가이드라인의 공식 폴백(슬랙)으로 보낸다.
        # 관리팀 직행이 맞다고 확인되면 to를 r["admin_team"]으로 되돌릴 것 (config.yaml 주석 참고).
        return {"action": "forward", "to": r["slack_bplan"],
                "body": _fwd(cfg, "출자/조합 수탁 제안 관련 메일이 와서 포워드 드립니다."),
                "note": "출자·수탁 → #Squad-비플랜 공유 (담당 라우팅 확인 전)"}

    if cat == rules.RECRUIT:
        # <의도적으로 초안을 만들지 않는다>
        # Notion 채용 템플릿은 전부 "현재 채용을 진행하고 있지 않습니다"인데, Gmail 라벨에
        # '0. Recruit/2026 H2 Intern'이 살아 있다 = 지금 채용 중이다. 템플릿을 자동 생성하면
        # 채용 중인데 "안 뽑는다"는 메일이 회사 이름으로 나간다. 채용 상태는 시점마다 바뀌므로
        # 코드가 알 수 없다. 대신: 사람이 현재 채용 상태를 확인하고 템플릿을 고른다.
        return {"action": "escalate",
                "note": "채용 문의 — 현재 채용 진행 상태를 사람이 확인 후 회신할 것. "
                        "(템플릿의 '채용 안 함' 문구가 지금 사실과 다를 수 있음)"}

    if cat == rules.IMPERSONATION:
        return {"action": "escalate",
                "note": "⚠️ 카카오벤처스 사칭 관련 문의. 자동 회신 금지. PR팀/담당자 확인 필요."}

    # ── 판단 불가 → 가이드라인의 폴백: #Squad-비플랜에 공유하고 사람이 판단 ──
    return {"action": "forward", "to": r["slack_bplan"],
            "body": _fwd(cfg, "분류가 어려운 메일이 와서 포워드 드립니다. 확인 부탁드립니다."),
            "note": f"판단 불가 → #Squad-비플랜 공유 ({hit.get('reason','')})",
            "unknown": True}
