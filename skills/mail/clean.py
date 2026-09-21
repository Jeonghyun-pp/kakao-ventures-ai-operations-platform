"""모델에 넣기 전에 본문을 다듬는다. 순수 함수뿐 — 망도 DB 도 안 닿는다.

## 왜 필요한가 (실측 2026-09-21)

    웹플로우   `rules.parse_webflow` 는 `문의 내용` 의 **첫 줄만** 잡는다.
              실제 886~3,904자 중 6~37자. 모델은 인사말 한 줄로 판정했다
    인용문     결정적 문장이 인용 원문 속에 있거나, 인용문이 앞자리를 차지한다
    잔재       HTML 을 글자로 바꾸며 `<o:p>`·`&nbsp;` 가 남는다

`rules.parse_webflow` 는 **안 고친다** — 현행 plan 이 쓰고 있고 테스트가 그 동작을 고정해 뒀다.
옛 흐름을 걷어낼 때 이쪽으로 바꾼다.
"""
from __future__ import annotations

import html
import re

from . import route, rules

# 웹플로우 알림의 꼬리. 여기부터는 문의 내용이 아니다.
_WF_FOOTER = "If you believe this is a spam submission"
_WF_CONTENT = re.compile(r"문의 내용\s*:\s*(.*)", re.S)

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
    """`rules.parse_webflow` 와 같되 `content` 가 **끝까지**다."""
    form = rules.parse_webflow(body or "")
    m = _WF_CONTENT.search(body or "")
    if m:
        form["content"] = text(m.group(1).split(_WF_FOOTER)[0])
    return form


def clip(s: str, cap: int) -> tuple[str, int]:
    """상한을 넘으면 **머리와 꼬리**를 남긴다. (글, 잘라낸 글자 수).

    요청은 대개 끝에 있다("…검토 부탁드립니다"). 앞에서만 자르면 그걸 버린다.
    """
    if len(s) <= cap:
        return s, 0
    head = cap * 2 // 3
    tail = cap - head
    cut = len(s) - cap
    return f"{s[:head]}\n[…중략 {cut}자…]\n{s[-tail:]}", cut
