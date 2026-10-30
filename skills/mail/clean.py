"""모델에 넣기 전에 본문을 다듬는다. 순수 함수뿐 — 망도 DB 도 안 닿는다.

## 왜 필요한가 (실측 2026-09-21)

    웹플로우   옛 파서는 `문의 내용` 의 **첫 줄만** 잡았다 — 실제 886~3,904자 중 6~37자. 모델은 인사말 한 줄로 판정했다
    인용문     결정적 문장이 인용 원문 속에 있거나, 인용문이 앞자리를 차지한다
    잔재       HTML 을 글자로 바꾸며 `<o:p>`·`&nbsp;` 가 남는다
"""
from __future__ import annotations

import html
import re

from kvlib import connections

from . import route

# 웹플로우 알림의 꼬리. 여기부터는 문의 내용이 아니다.
_WF_FOOTER = "If you believe this is a spam submission"
_WF_CONTENT = re.compile(r"문의 내용\s*:\s*(.*)", re.S)
_EMAIL = r"[\w.+-]+@[\w-]+\.[\w.-]+"

# 여기부터는 상대가 **새로 쓴 글이 아니다.** 첫 표식에서 자른다.
# ponytail: 흔한 표식 다섯 가지만. 놓친 인용문은 그냥 본문으로 들어간다(모델이 읽을 뿐 해는 없다).
#           표식이 늘면 여기 한 줄씩 추가한다.
_QUOTE = re.compile(
    r"^(?:"
    r"On .{5,120}wrote:\s*$"                         # Gmail·Apple (영문)
    r"|\d{4}년 \d{1,2}월 \d{1,2}일.{0,80}작성:\s*$"    # Gmail (국문)
    r"|-{3,}\s*(?:Original Message|원본 메시지|Forwarded message|전달된 메일)\s*-{3,}\s*$"
    r"|(?:From|보낸 사람)\s*:.{0,200}\n(?:Sent|Date|보낸 날짜|날짜)\s*:"   # Outlook 헤더 블록
    r"|>.*\n>"                                        # '>' 인용 두 줄 이상
    r")",
    re.M,
)


def text(body: str) -> str:
    """태그·엔티티 잔재와 보이지 않는 글자를 걷고 빈 줄을 정리한다. 내용은 안 지운다."""
    s = html.unescape(body or "")
    s = re.sub(r"</?[A-Za-z][^<>\n]{0,200}>", " ", s)       # <o:p> 같은 남은 태그
    s = s.replace("\xa0", " ")
    s = re.sub(r"[​‌‍﻿]", "", s)
    s = re.sub(r"[ \t]{2,}", " ", s)                         # 태그를 걷은 자리의 공백 뭉치
    s = re.sub(r"[ \t]+\n", "\n", s)
    return route._tidy(s)                                    # 빈 줄·불릿 정리는 있는 걸 쓴다


def split_quoted(s: str) -> tuple[str, str]:
    """(새로 쓴 글, 인용·전달된 글). 표식이 없으면 전부 새 글이다."""
    m = _QUOTE.search(s or "")
    if not m or not s[:m.start()].strip():
        # 표식이 맨 앞이면(자기 메일을 그대로 재전송) 전부를 본문으로 본다 — 그게 읽을 전부다
        return (s or "").strip(), ""
    return s[:m.start()].strip(), s[m.start():].strip()


def webflow(body: str) -> dict:
    """웹플로우 폼 본문 → 문의 유형·문의자 주소·제목·내용(끝까지).

    🔴 발신은 `no-reply-forms@webflow.com` 이다. 답장하면 거기로 가서 반송된다(2026-07-10 실제 550 반송, 문의자는 답을 못 받았다).
       그래서 **본문에서 문의자 주소를 찾아** 문의자가 보낸 메일처럼 본다. 못 찾으면 초안을 안 만든다(flow 의 no-reply 수신자 검사).
       폴백으로 본문 전체에서 찾되 webflow·설정된 조직 주소는 뺀다 — 그게 반송의 원인이다.
    """
    body = body or ""

    def field(label: str) -> str:
        m = re.search(rf"{label}\s*:\s*(.+)", body)
        return m.group(1).strip() if m else ""

    own = {str(a).lower() for a in connections.get("organization.own_addresses")}
    domain = str(connections.get("organization.internal_domain")).lower().lstrip("@")
    m = re.search(_EMAIL, field("이메일"))
    reply_to = m.group(0) if m else next((c for c in re.findall(_EMAIL, body)
                                          if "webflow.com" not in c.lower() and c.lower() not in own
                                          and c.rsplit("@", 1)[-1].lower() != domain), "")
    c = _WF_CONTENT.search(body)
    return {"kind": field("문의 유형"), "reply_to": reply_to, "title": field("문의 제목"),
            "content": text(c.group(1).split(_WF_FOOTER)[0]) if c else ""}
