"""카테고리 → 행동. 행동은 다섯 개뿐이다.

    protect    아무것도 안 한다. 인박스에 그대로 둔다
    archive    인박스 라벨만 뗀다. 삭제가 아니라 되돌릴 수 있다
    reply      답장 초안. 원본 스레드에 붙는다
    forward    포워드 초안. 수신자를 **호출부가 명시**한다 — 절대 헤더에서 추측하지 않는다
    escalate   인박스에 두고 보고서에 🚩 로 올린다. 초안을 안 만든다
    slack      #Squad-비플랜 에 올린다. 메일은 인박스에 남는다 — 반응을 기다리는 중이므로

## 🔴 "회신하지 않는다"와 "인박스에 남긴다"는 별개다

채용·사칭은 **회신을 안 만들되 아카이빙한다**(사용자 결정 2026-08-18).
초안을 만들어두면 사람이 확인 없이 보낼 위험이 생긴다.
    채용   템플릿은 전부 "안 뽑음"인데 채용 상태는 시점마다 바뀐다.
           채용 중인데 안 뽑는다고 **회사 이름으로** 나가는 사고를 막는다
    사칭   회신 절대 금지. 오탐이 조용히 사라지므로 rules.py 의 힌트를 좁혔다

지금 escalate 는 **한 곳뿐이다** — 웹플로우 문의자 주소를 못 찾은 경우.
거기서만은 초안을 안 만든다. 주소를 추측하면 반송되기 때문이다.

## 🔴 웹플로우는 reply 가 아니라 forward 다

발신이 `no-reply-forms@webflow.com` 이라 답장하면 거기로 간다.
2026-07-10 에 550 반송됐고 문의자는 답을 못 받았다. 이 갈래 전체가 forward 인 이유다.
"""
from __future__ import annotations

from pathlib import Path

from kvlib import constraints

from . import rules

_TPL = Path(__file__).resolve().parent / "templates"

ASSIGNEE = "에반"
GOOGLE_FORM = "https://forms.gle/EXAMPLE_FORM_6c44ce733004"
SLACK_BPLAN = "person-43d35afbd5dc@example.com"   # #Squad-비플랜 (메일 포워드용)

# 🔴 슬랙 게시 대상 채널 키. 지금은 test(review-test) 고, 실채널은 사람이 지시할 때만.
POST_CH = "test"

# 🔴 분류가 무엇이든, 무수정률이 얼마든 **영원히** 사람이 보낸다.
NEVER_AUTO_SEND = tuple(constraints.get("gmail.never_auto_send"))


def tpl(name: str, **kw) -> str:
    """템플릿을 읽어 변수만 채운다. 본문은 손대지 않는다 — 회사 이름으로 나가는 글이다."""
    return (_TPL / f"{name}.txt").read_text(encoding="utf-8").format(**kw)


# 맨 위 글에 넣는 미리보기 길이. 전문은 스레드로 따로 간다.
_PREVIEW = 400

# 슬랙 한 메시지 상한은 4,000자다. 여유를 두고 자른다.
_CHUNK = 3500


def _post_text(msg: dict, lead: str, ask: str = "", ping: str = "", summary: str = "") -> str:
    """맨 위에 올릴 글. **서식은 도메인이라 여기서 만든다** — 통로는 글자만 받는다.

    메일을 채널로 포워드하지 않고 우리가 글을 쓴다. 포워드하면 핑을 못 찍고,
    핑이 없으면 알림이 안 가서 올린 의미가 없다. 게다가 `ts` 를 못 받아
    **나중에 반응을 찾을 수 없다.**

    맨 위는 훑어보는 자리라 미리보기만 넣고, **전문은 스레드로 내린다**
    (`body_chunks`). 사람이 하던 방식도 같은 모양이었다 — 메일 첨부가 위에 있고
    한 줄 요약이 스레드에 달렸다. 우리는 위아래만 뒤집었다.
    """
    # 🔴 요약이 있으면 요약만 쓴다. 원문 앞 400자는 대개 인사말이라 훑는 데 쓸모가 없다
    #    (실측 BIXPO: 400자가 전부 행사 소개였고 "30인 초청·9/16 마감" 은 그 뒤에 있었다).
    #    요약이 없으면(claude 실패) 원문 앞머리로 물러난다 — 빈 글을 올리지는 않는다.
    if summary:
        gist = summary
    else:
        b = " ".join((msg.get("body") or "").split())
        gist = b[:_PREVIEW] + ("…" if len(b) > _PREVIEW else "")
    head = f"{ping}\n" if ping else ""
    tail = f"\n\n_{ask}_" if ask else ""
    return (f"{head}*{lead}*\n"
            f"> *제목* {msg.get('subject') or '(없음)'}\n"
            f"> *발신* {msg.get('sender_raw') or msg.get('sender')}\n\n"
            f"{gist}\n\n_전문과 메일함 링크는 이 글의 스레드에 있습니다._{tail}")


def mail_link(msg_id: str) -> str:
    """지메일에서 이 메일을 여는 링크. 아카이빙된 뒤에도 열린다(`#all/`)."""
    return f"https://mail.google.com/mail/u/0/#all/{msg_id}"


_BULLETS = {"-", "*", "•", "·", "‧", "–", "—"}


def _tidy(body: str) -> str:
    """읽을 수 있게 다듬는다. **내용은 안 지운다** — 빈 줄과 꼬리 공백만 정리한다.

    HTML 메일을 텍스트로 바꾸면 목록 하나가 이렇게 나온다:

        -

           일시/장소: 2026. 11. 4.(수) ~ 11. 6.(금)

    빈 줄 셋이 연달아 나오면 사람이 스크롤만 하게 된다. 두 줄로 줄인다.
    """
    lines = [ln.rstrip() for ln in body.strip().splitlines()]

    # ① 불릿만 있는 줄을 다음 내용 줄과 붙인다.
    #    HTML 목록을 텍스트로 바꾸면 "   -" 와 내용이 빈 줄을 사이에 두고 갈라진다.
    merged, i = [], 0
    while i < len(lines):
        if lines[i].strip() in _BULLETS:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                merged.append(f"• {lines[j].strip()}")
                i = j + 1
                continue
        merged.append(lines[i])
        i += 1

    # ② 빈 줄이 연달아 나오면 하나로. 내용은 안 지운다.
    out, blank = [], 0
    for ln in merged:
        if ln:
            out.append(ln)
            blank = 0
        else:
            blank += 1
            if blank < 2:
                out.append("")
    return "\n".join(out).strip()


def body_chunks(msg: dict) -> list[str]:
    """스레드에 붙일 **메일 전문.** 슬랙 상한에 맞춰 나눈다.

    🔴 줄바꿈은 살린다. 맨 위 미리보기는 한 줄로 눌러서 훑기 좋게 하지만,
       전문은 사람이 실제로 읽는 글이라 원문 모양이 그대로여야 한다.
    """
    body = _tidy(msg.get("body") or "")
    if not body:
        return []
    out, buf = [], ""
    for line in body.splitlines(keepends=True):
        while len(line) > _CHUNK:          # 한 줄이 상한보다 길 수도 있다
            out.append(buf + line[:_CHUNK])
            buf, line = "", line[_CHUNK:]
        if len(buf) + len(line) > _CHUNK:
            out.append(buf)
            buf = line
        else:
            buf += line
    if buf.strip():
        out.append(buf)
    n = len(out)
    # 🔴 코드블록(```)으로 감싸지 않는다. 감싸면 슬랙이 링크를 안 살려서
    #    `<http://startup.bixpo.org|startup.bixpo.org>` 같은 날것이 그대로 보인다.
    #    읽으라고 올리는 글이라 읽기 쉬운 쪽을 고른다.
    return [f"*메일 전문*{f' ({i}/{n})' if n > 1 else ''}\n{c.rstrip()}"
            for i, c in enumerate(out, 1)]


def _fwd(note: str) -> str:
    return tpl("fwd_intro", assignee=ASSIGNEE, note=note)


def _webflow(msg: dict) -> dict:
    """웹플로우 갈래. **전부 forward.**"""
    form = rules.parse_webflow(msg.get("body") or "")
    kind = form["kind"]
    to = form["reply_to"]
    blob = f"{form['title']} {form['content']}"

    if not to:
        return {"action": "escalate",
                "note": "웹플로우 문의자 주소를 본문에서 못 찾았다 — 주소를 추측하면 반송된다"}

    ko = rules.korean_ratio(blob) >= constraints.get("gmail.korean_ratio_threshold")

    if "투자" in kind:
        return {"action": "forward", "to": to,
                "body": tpl("webflow_invest_ko" if ko else "webflow_invest_en", google_form=GOOGLE_FORM),
                "note": f"웹플로우 투자 문의 → 문의자({to})"}
    if "멘토링" in kind:
        # 🔴 [사용자 결정 2026-08-18] 회신하지 않고 아카이빙한다.
        #    오피스아워가 '22년 하반기부터 중단된 상태라 안내할 내용이 없다.
        #    대가: 멘토링을 물어본 사람은 아무 답도 못 받는다. 재개되면 되살릴 자리다.
        return {"action": "archive", "note": f"웹플로우 멘토링 문의 — 회신 없이 아카이빙 ({to})"}
    if "TIPS" in kind.upper() or "팁스" in kind:
        return {"action": "forward", "to": to, "body": tpl("webflow_tips", google_form=GOOGLE_FORM),
                "note": f"웹플로우 TIPS 문의 → 문의자({to})"}
    if "섭외" in kind:
        return {"action": "forward", "to": SLACK_BPLAN,
                "body": _fwd(f"웹플로우 섭외 문의입니다 (문의자 {to})"),
                "note": "웹플로우 섭외 → #Squad-비플랜"}

    # 🔴 유형이 우리 갈래 밖이어도(예: '기타 문의') 내용은 대개 분명하다 —
    #    실측: '기타 문의'로 온 Times Higher Education 강연 초청.
    #    사람에게 보내되 **절대 sender 에게 답장하지 않는다**(no-reply 라 반송된다).
    #    그래서 claude 에게 넘기지 않고 여기서 슬랙으로 보낸다 — 어떤 분류가 나오든
    #    문의자에게 답장이 가는 경로를 만들지 않기 위해서다.
    return {"action": "forward", "to": SLACK_BPLAN,
            "body": _fwd(f"홈페이지 문의가 왔는데 유형이 '{kind}' 입니다 (문의자 {to})"),
            "note": f"웹플로우 '{kind}' → #Squad-비플랜"}


def decide(msg: dict, hit: dict) -> dict:
    """카테고리 하나를 행동으로 바꾼다."""
    cat = hit["category"]

    if cat == rules.BPLAN_INTAKE:
        # 🔴 아카이빙하면 비플랜이 이 메일을 제목으로 못 찾아 멈춘다.
        return {"action": "protect", "note": "비플랜이 이 메일을 쓴다 — 건드리지 않는다"}

    if cat == rules.INTERNAL:
        # 사내 공지·안내. 아카이빙도 포워드도 아니다 — 정현님이 읽을 메일이다.
        return {"action": "protect", "note": "사내 발신 — 인박스에 그대로 둔다"}

    if cat == rules.SYSTEM_NOTICE:
        # 🔴 [사용자 결정 2026-09-02] 사람이 논의할 게 없는 자동 안내다.
        #    전에는 UNKNOWN 으로 빠져 #Squad-비플랜 으로 갔다 — 방에 잡음만 늘었다.
        #    대가: 안내에 사람이 챙겨야 할 게 섞여 있으면 놓친다. 발신 주소를 좁게
        #    잡아둔 이유가 그거다.
        return {"action": "archive", "note": "시스템 자동 안내 — 회신 없이 아카이빙"}

    if cat == rules.WEBFLOW:
        return _webflow(msg)

    if cat in (rules.AD_NOISE, rules.MASS_BLAST):
        return {"action": "archive", "note": hit["why"]}

    if cat == rules.INBOUND_EN:
        # 🔴 [사용자 결정 2026-08-18] 아카이브 → 영문 폼 안내로 바꿨다.
        #    옛 규칙의 근거는 "자동 회신이 이미 나갔다"였는데, 그 자동 응답기가 실제로
        #    켜져 있는지 잰 기록이 어디에도 없었다(세 문서가 서로를 인용할 뿐).
        #    초안 한 장이면 그 미확인 전제를 지울 수 있다 — 응답기가 있든 없든 안전하다.
        #    홍보성 영문은 AD_NOISE 로 먼저 걸러지므로 여기 오는 건 진짜 인바운드다.
        return {"action": "reply", "to": msg["sender"],
                "body": tpl("inbound_form_en", google_form=GOOGLE_FORM),
                "note": "영문 콜드 제안 → 영문 구글폼 안내"}

    if cat == rules.INBOUND_KO:
        return {"action": "reply", "to": msg["sender"],
                "body": tpl("inbound_form_ko", google_form=GOOGLE_FORM),
                "note": "한국어 콜드 제안 → 구글폼 안내"}

    if cat == rules.TRUSTED_REF:
        # 🔴 [사용자 결정 2026-08-18] 답장 초안이 아니라 **슬랙에 올려 판단을 받는다.**
        #    폼을 생략하는 특혜 안내라 오판 비용이 크다 — 사람이 한 번 보고 눌러야 한다.
        #      ⭕ → 접수 안내 초안   ❌ → 아카이브   무반응 7일 → 거절 초안 + 아카이브
        lead = "신뢰 경로로 들어온 투자 건입니다."
        ask = "⭕ 접수 안내 · ❌ 거절 · 무반응 7일이면 자동 거절 초안"
        return {"action": "slack", "ch": POST_CH, "kind": "trusted_ref",
                "lead": lead, "ask": ask,
                "text": _post_text(msg, lead, ask),
                "note": "신뢰 경로 → #Squad-비플랜 판단 요청  🔴 영원히 수동"}

    if cat == rules.EVENT_SPEAKER:
        lead = "행사·섭외 문의가 왔습니다."
        ask = "⭕ 참여 검토 · ❌ 거절 · 무반응 7일이면 자동 거절 초안"
        return {"action": "slack", "ch": POST_CH, "kind": "event",
                "lead": lead, "ask": ask,
                "text": _post_text(msg, lead, ask),
                "note": "행사·섭외 → #Squad-비플랜"}

    if cat == rules.VENDOR_PITCH:
        # 🔴 [사용자 결정 2026-09-02] 우리가 고객이 되어달라는 영업은 아카이브다.
        #    실측: 제휴로 잡힌 13건 중 10건이 콜드 세일즈였다 — 헤드헌팅·SaaS·
        #    "30% off for Kakao Ventures's portfolio"·프로모션 안내.
        #    비플랜방에 올려도 아무도 안 움직인다. 거절 초안도 안 만든다 —
        #    안 보낼 글을 만들면 실수로 보낼 위험만 생긴다.
        #    대가: 진짜 볼 만한 영업이 섞여 있으면 사라진다. 그래서 '파는 게 있느냐'로만
        #    가르고, 같이 하자는 제안(PARTNERSHIP)은 그대로 사람에게 간다.
        return {"action": "archive", "note": "영업 제안 — 회신 없이 아카이빙"}

    if cat == rules.PARTNERSHIP:
        # 슬랙 공유가 먼저, 거절 회신은 사람이 확인한 뒤에. 순서를 사람이 지킨다.
        return {"action": "forward", "to": SLACK_BPLAN,
                "body": _fwd("제휴/협력 제안이 와서 포워드 드립니다."),
                "note": "제휴 제안 → #Squad-비플랜 (거절 초안 동봉)  🔴 영원히 수동",
                "also_reply": {"to": msg["sender"], "body": tpl("partnership_decline"),
                               "note": "제휴 거절 회신 초안"}}

    if cat == rules.LP_CUSTODY:
        # 🔴 [사용자 결정 2026-09-02] forward → archive.
        #    금융기관의 출자/수탁 영업이다. 비플랜 방으로 보내도 아무도 안 움직인다 —
        #    출자 결정은 그 방에서 하는 일이 아니다. 회신도 안 만든다.
        #    대가: 진짜 LP 제안이 섞여 있으면 조용히 사라진다. 되살리려면 여기만 고친다.
        return {"action": "archive", "note": "출자·수탁 제안 — 회신 없이 아카이빙"}

    if cat == rules.RECRUIT:
        # 🔴 [사용자 결정 2026-08-18] escalate → archive.
        #    회신은 여전히 안 만든다. 템플릿이 전부 "안 뽑음"인데 채용 상태는 시점마다
        #    바뀌므로, 틀린 답이 회사 이름으로 나가는 것보다 무응답이 낫다.
        return {"action": "archive", "note": "채용 문의 — 회신 없이 아카이빙"}

    if cat == rules.FORM_ERROR:
        # 🔴 [사용자 결정 2026-08-18] 아카이브.
        #    전엔 INBOUND_KO 로 잡혀 **"폼으로 접수하세요"가 다시 나갔다** — 폼이 안 된다고
        #    말한 사람에게. 그 무한루프를 먼저 끊는다.
        #    대가: 그 사람이 보낸 자료는 검토되지 않는다. 노션 지침은 "보내주신 자료로
        #    검토하겠습니다"였다. 되살리려면 여기만 고치면 된다.
        return {"action": "archive", "note": "구글폼 오류 문의 — 회신 없이 아카이빙"}

    if cat == rules.IMPERSONATION:
        # 🔴 [사용자 결정 2026-08-18] escalate → archive. 회신은 여전히 절대 금지다.
        #    오탐이 이제 조용히 사라지므로 rules.py 에서 넓은 힌트 둘을 뺐다.
        return {"action": "archive", "note": "사칭 확인 문의 — 회신 없이 아카이빙"}

    return {"action": "forward", "to": SLACK_BPLAN,
            "body": _fwd("분류가 애매한 메일이 있어 포워드 드립니다."),
            "note": "분류 애매 → #Squad-비플랜", "unknown": True}


# 지명이 덮어쓸 수 있는 카테고리. 채용·사칭은 지명이 있어도 아카이빙으로 남는다.
_OVERRIDABLE = (rules.EVENT_SPEAKER, rules.PARTNERSHIP, rules.UNKNOWN)


def apply_named_partner(msg: dict, cat: str, act: dict) -> dict:
    """심사역이 지명됐으면 **슬랙방에 핑을 찍어** 올린다.

    🔴 [사용자 결정 2026-08-18] 그 사람 메일함으로 보내지 않는다. 슬랙방에 올리고
       핑만 찍는다 — 남의 메일함에 넣는 것보다 되돌리기 쉽고, 다른 심사역도 본다.
    🔴 채용·사칭은 못 덮는다. 이름이 적혀 있어도 회신 없이 아카이빙한다.
    """
    if cat not in _OVERRIDABLE:
        return act
    addr = rules.find_named_partner(msg)
    if not addr:
        return act
    uid = rules.PARTNER_SLACK.get(addr) or ""
    # 🔴 ID 를 모르면 핑 없이 이름만 적는다. 조용히 넘어가지 않고 note 에 남긴다.
    ping = f"<@{uid}>" if uid else f"*{addr}*"
    lead = f"{addr} 심사역님 앞으로 온 메일입니다."
    return {**act, "action": "slack", "ch": POST_CH,
            "kind": act.get("kind") or "named",
            "lead": lead, "ping": ping,
            "text": _post_text(msg, lead, act.get("ask", ""), ping=ping),
            "note": f"심사역 지명 → #Squad-비플랜" + ("" if uid else "  ⚠️ 슬랙 ID 미등록 — 핑 없이 이름만")}


def rebuild_post(msg: dict, act: dict, summary: str) -> str:
    """요약을 받아 맨 위 글을 다시 만든다. 재료(`lead`·`ask`·`ping`)는 `decide` 가 남겨둔다."""
    return _post_text(msg, act.get("lead", ""), act.get("ask", ""),
                      act.get("ping", ""), summary=summary)
