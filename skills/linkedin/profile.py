"""링크드인 프로필 해석 — 통로가 아니라 도메인 로직.

Apify 가 준 원본 dict 를 우리가 쓸 모양으로 옮긴다. **망에 닿지 않는다** —
그래서 저장해둔 액터 원본 136본으로 **돈 0·망 0** 으로 전량 검증할 수 있다.

## 여기 있는 것은 전부 실사고에서 나왔다

    URN 정규화     같은 사람이 스크래퍼 모드마다 다른 URN 으로 온다.
                  3번째 글자(o/w)와 꼬리만 다르고 앞 12자는 같다.
                  이걸 몰라서 노션 링크드인 345행 중 **84행이 같은 사람의 두 번째 행**이었다

    현재 직장 병합  currentPosition[] 은 **진행 중 경력을 통째로 누락한다.**
                  1,167명 중 396명(33.9%). 창업 직함이 experience 쪽에만 있는 사람은
                  판정에서 통째로 사라진다.
                  (Example Person F: currentPosition 엔 파트타임 자문 하나뿐이고
                   실제 27개월차 Cofounder&CTO 는 experience 에만 있었다 → tenure 4개월로 오분류)

    endDate 는 dict  문자열이 아니다. `endDate == "Present"` 로 짜면 **전건 False** 가 되고
                  위 33.9% 보정이 통째로 무효가 된다. **조용히 0이 되는 종류의 버그다.**
                  실측: experience 14,913개 중 dict 14,902 · None 11 · 문자열 0

    지문 버전 접두   `v2|` 를 안 붙이면 배포 첫 실행에 저장된 344명이 전부
                  "변화 감지" 오탐이 된다
"""
from __future__ import annotations

import re

FP_VERSION = "v2"

# AC?AA + 34자 = 39자 고정. 실측 1,865/1,865. 접두는 ACoAA 1,856 · ACwAA 9.
_URN_RE = re.compile(r"AC[A-Za-z0-9]AA[A-Za-z0-9_\-]{20,}")


def canonical_urn(slug: str) -> str:
    """난독화 URN 에서 사람 고유부만 남긴다. URN 이 아니면 "".

    ⚠️ **대소문자를 죽이기 전에** 판정한다 — 멤버ID 인코딩은 대소문자를 구분하므로
    `.lower()` 뒤에 자르면 서로 다른 사람이 같은 키가 된다.
    """
    s = (slug or "").strip()
    if _URN_RE.fullmatch(s):
        return "urn:" + s[:2] + s[3:12]     # AC + 9자. 3번째 글자(o/w)를 버린다
    return ""


def normalize_url(u: str) -> str:
    """프로필 URL → 정확매칭 키.

    ⚠️ 한계: vanity(`/in/haonpark`)와 URN(`/in/ACwAA…`)은 여전히 다른 키다.
    URN 에서 vanity 를 유도할 방법이 없다. 실측상 publicIdentifier 가 99.5% 라
    0.5%(9/1,865)는 URN 형태 URL 로 온다 — 그 사람은 `ext_id` 키로 잡는다.

    🔴 URL 을 quote/unquote 하지 마라. 한글 vanity 가 **raw 로** 온다(122건, 6.5%).
    인코딩을 통일하면 같은 사람이 두 키가 된다. 원본이 이미 통일돼 있다.
    """
    raw = (u or "").strip()
    if not raw:
        return ""
    m = re.search(r"linkedin\.com/in/([^/?#]+)", raw, re.I)
    if m:
        slug = m.group(1).rstrip("/")
        return "in/" + (canonical_urn(slug) or slug.lower())
    s = re.sub(r"^https?://", "", raw.lower())
    s = re.sub(r"^www\.", "", s)
    return s.split("?", 1)[0].split("#", 1)[0].rstrip("/")


def seen_key(item: dict) -> str:
    """장부에 적을 사람 키. URN 을 정규화한 것이 가장 믿을 만하다(실측 100%)."""
    urn = str(item.get("id") or "")
    return canonical_urn(urn) or urn or normalize_url(str(item.get("linkedinUrl") or ""))


def is_present(exp: dict) -> bool:
    """진행 중인 경력인가.

    🔴 `endDate` 는 **dict** 다. 문자열 비교로 짜면 전건 False 가 된다.
    모양 3종: {month,text,year} · {text} · {text,year}.
    `text=="Present"` 는 정확히 {text}만 있는 모양과 일치한다(진행 중이면 종료 월/년이 없다).
    """
    end = exp.get("endDate")
    if not isinstance(end, dict):
        return False
    return str(end.get("text") or "").strip().lower() == "present"


def positions(item: dict) -> list[dict]:
    """현재 직장 목록 = currentPosition[] ∪ experience[Present].

    🔴 currentPosition 만 믿으면 1,167명 중 396명(33.9%)의 '현재'를 못 본다.
    """
    out: list[dict] = []
    seen: set[str] = set()

    def add(company: str, title: str, src: str) -> None:
        company = (company or "").strip()
        title = (title or "").strip()
        k = f"{company.lower()}|{title.lower()}"
        if not company and not title:
            return
        if k in seen:
            return
        seen.add(k)
        out.append({"company": company, "title": title, "src": src})

    for key in ("currentPosition", "currentPositions", "positions"):
        for p in item.get(key) or []:
            if isinstance(p, dict):
                add(p.get("companyName") or p.get("company") or "",
                    p.get("title") or p.get("position") or "", key)

    for e in item.get("experience") or []:
        if isinstance(e, dict) and is_present(e):
            add(e.get("companyName") or e.get("company") or "",
                e.get("title") or e.get("position") or "", "experience:Present")
    return out


def fingerprint(item: dict) -> str:
    """변화 감지용 지문.

    소개글(summary)은 자주 바뀌므로 **뺀다** — 넣으면 매주 전원이 "변했다"가 된다.
    `v2|` 접두는 지문 규칙이 바뀐 시점을 표시한다. 없으면 규칙을 고친 날
    저장된 전원이 오탐으로 뜬다.
    """
    ps = positions(item)
    companies = "·".join(sorted({p["company"] for p in ps if p["company"]}))
    titles = "·".join(sorted({p["title"] for p in ps if p["title"]}))
    return f"{FP_VERSION}|{companies}|{titles}"


def fp_comparable(old: str, new: str) -> bool:
    """비교해도 되는 지문 쌍인가. 버전이 다르거나 비면 **비교 불가**(변화 아님)."""
    if not old or not new:
        return False
    return old.split("|", 1)[0] == new.split("|", 1)[0] == FP_VERSION


def summarize(item: dict) -> dict:
    """판정에 넣을 최소 정보."""
    ps = positions(item)
    return {
        "key": seen_key(item),
        "name": (item.get("firstName", "") + " " + item.get("lastName", "")).strip()
                or item.get("name") or "",
        "headline": item.get("headline") or "",
        "url": item.get("linkedinUrl") or "",
        "public_id": item.get("publicIdentifier") or "",   # 99.5% — 100% 아니다
        "about": item.get("about") or "",                  # 73.3% — 27%는 빈다
        "positions": ps,
        "fingerprint": fingerprint(item),
    }
