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


def match_keys(item: dict) -> list[str]:
    """이 사람을 가리키는 **모든** 키. 하나라도 겹치면 같은 사람이다.

    🔴 키가 하나면 안 된다. 실측(2026-08-17): 우리가 수집한 사람은 URN 키
    (`urn:ACxxxxxxxxx`)를 갖는데, 노션 옛 행에서 뽑을 수 있는 건 URL 키(`in/vanity`)뿐이다.
    **두 키 공간이 절대 안 만난다** — URN 에서 vanity 를 유도할 방법이 없다.
    그래서 595명을 넣었더니 509명(86%)이 이미 있던 사람이었다.

    옛 코드가 이걸 알고 `match_keys` 로 여러 키를 달았는데 우리가 하나만 달았다.
    사람마다 URN·URL 둘 다 달아두면 어느 쪽으로 와도 잡힌다.
    """
    out = []
    urn = canonical_urn(str(item.get("id") or "")) or str(item.get("id") or "")
    if urn:
        out.append(urn)
    for u in (item.get("linkedinUrl"), item.get("publicIdentifier")):
        if not u:
            continue
        raw = str(u)
        k = normalize_url(raw if "linkedin.com" in raw else f"linkedin.com/in/{raw}")
        if k and k not in out:
            out.append(k)
    return out


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


# ================================================================ 개월 계산
#
# 🔴 우리 판정 기준이 이 숫자 위에 서 있다 (실측: 창업 3~24개월 1.9배 · 재직 60개월+ Catch 0).
#    계산을 대충 하면 기준이 통째로 어긋난다.

FOUNDER_ROLE_TERMS = (
    "founder", "co-founder", "cofounder", "ceo", "cto",
    # 🔴 풀어 쓴 형태를 빠뜨리면 안 된다. `"ceo" in "chief executive officer"` 는 False 다.
    #    실측에서 Chief Executive Officer 가 창업 직함으로 안 잡혀, 그 사람의 재직기간을
    #    옆에 있던 파트타임 자문이 다시 가렸다(9명). Example Person F 사고와 같은 계열이다.
    "chief executive", "chief technology",
    "대표", "창업자", "공동창업자", "창업",
)

_MON = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _today_m() -> int:
    from datetime import date
    t = date.today()
    return t.year * 12 + t.month


def is_founder_role(pos: dict) -> bool:
    t = str(pos.get("position") or pos.get("title") or "").lower()
    return any(term in t for term in FOUNDER_ROLE_TERMS)


def _pos_months(pos: dict) -> int | None:
    """포지션 하나의 재직 개월. **스크래퍼 모드마다 키가 다르다.**

        Short  tenureAtCompany = {"numYears": 2, "numMonths": 4}
        Full   startDate = {"month": "Jan", "year": 2026}  ·  duration = "1 yr 3 mos"

    🔴 Full 로 바꾼 뒤 tenureAtCompany 가 사라졌는데 코드가 그것만 읽고 있었다
       → Tenure Months 가 0/24 로 비었고 재직 기반 판정이 통째로 죽었다.
       **액터가 주는 키 이름을 추측하지 마라** — 이 스킬의 단골 실패다.
    """
    t = pos.get("tenureAtCompany")
    if isinstance(t, dict) and (t.get("numYears") is not None or t.get("numMonths") is not None):
        return int(t.get("numYears") or 0) * 12 + int(t.get("numMonths") or 0)

    sd = pos.get("startDate")          # duration 문자열보다 신뢰도가 높다
    if isinstance(sd, dict) and sd.get("year"):
        m = (_today_m()
             - int(sd["year"]) * 12
             - _MON.get(str(sd.get("month", "")).strip()[:3].lower(), 1))
        if m >= 0:
            return m

    import re
    dur = str(pos.get("duration") or "")
    yrs, mos = re.search(r"(\d+)\s*yr", dur), re.search(r"(\d+)\s*mo", dur)
    if yrs or mos:
        return int(yrs.group(1) if yrs else 0) * 12 + int(mos.group(1) if mos else 0)
    return None


def tenure_months(item: dict) -> int | None:
    """현 포지션 중 **가장 짧은** 재직 개월. 없으면 None.

    왜 min 인가: 동시 포지션을 가진 사람(97명 중 12명)은 [0]=삼성 10년, [1]=Founder 3개월
    처럼 온다. 알고 싶은 건 "**새로 시작한 일**이 얼마나 됐나"라 짧은 쪽이다.
    [0]이나 max 를 보면 대기업 10년으로 읽혀 창업 신호가 정반대로 뒤집힌다.

    🔴🔴 **창업 직함이 하나라도 있으면 그중에서만 고른다.** 그냥 min 을 쓰면 파트타임
       자문·멘토가 "가장 짧은 일"로 잡혀 창업 시점을 덮어쓴다.
       실측 피해: 행정안전부 자문(5개월)이 AIM Intelligence 창업(27개월)을 가렸다.
       271명 재계산에서 **21명(7.7%)** 이 "코드상 12개월 미만"인데 실제 창업은 24개월 이상이었다.
       창업 직함이 **없는** 사람은 전체 min 이 맞다(대기업 재직 중 창업 준비자 등).
    """
    ps = _raw_positions(item)
    founders = [x for x in ps if is_founder_role(x)]
    vals = [m for m in (_pos_months(x) for x in (founders or ps)) if m is not None]
    return min(vals) if vals else None


def founding_months(item: dict) -> int | None:
    """**창업자로 산 기간** — 현 창업 포지션 중 가장 **긴** 것. 없으면 None.

    tenure_months 와 짝이다. 둘을 같이 봐야 갈린다:
        tenure 2 · founding 2   → 갓 시작한 창업자
        tenure 2 · founding 84  → 7년차가 법인을 하나 더 세운 것
    """
    vals = [m for m in (_pos_months(x) for x in _raw_positions(item) if is_founder_role(x))
            if m is not None]
    return max(vals) if vals else None


def gap_months(item: dict) -> int | None:
    """마지막 직장 종료 후 공백 개월.

    세 상태가 **전부 다르다** — 섞으면 안 된다:
        0     진행 중인 포지션이 있다 → 공백 없음 (확인된 사실)
        N     마지막 경력이 N개월 전에 끝났고 그 뒤가 비었다
        None  경력에 날짜가 없어 **판정 불가** (미확인이지 "공백 없음"이 아니다)
    """
    exp = [e for e in (item.get("experience") or []) if isinstance(e, dict)]
    if any(is_present(e) for e in exp):
        return 0
    ends = [int(e["endDate"]["year"]) * 12
            + _MON.get(str(e["endDate"].get("month", "")).strip()[:3].lower(), 1)
            for e in exp
            if isinstance(e.get("endDate"), dict) and e["endDate"].get("year")]
    return (_today_m() - max(ends)) if ends else None


def _raw_positions(item: dict) -> list[dict]:
    """개월 계산용 원본 포지션(dict 그대로). positions() 는 표시용으로 평평하게 만든다."""
    out = []
    for key in ("currentPosition", "currentPositions", "positions"):
        out += [p for p in (item.get(key) or []) if isinstance(p, dict)]
    out += [e for e in (item.get("experience") or []) if isinstance(e, dict) and is_present(e)]
    return out
