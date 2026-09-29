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
from datetime import date

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


# 🔴 부업으로 보는 고용형태는 여기까지다. **넓히지 마라** —
#    `Contract` 를 넣으면 `Deputy Site Manager(LNG) · Contract` 같은 명백한 본업을 무직으로 만든다.
_SIDE_EMPLOYMENT = {"Part-time", "Internship", "Volunteer", "Seasonal"}

# 한국 거주 판별용. 🔴 이걸로 "한국팀"을 판정하지 않는다 — 그건 정규식으로 못 맞힌다.
#    거주지 문자열에 이게 있으면 KR-거주 신호를 다는 것뿐이다.
KR_LOCATION_TERMS = (
    "korea", "seoul", "한국", "서울", "대한민국",
    "busan", "부산", "daejeon", "대전", "incheon", "인천", "daegu", "대구",
    "gwangju", "광주", "ulsan", "울산", "sejong", "세종",
    "gyeonggi", "경기", "pangyo", "판교", "seongnam", "성남", "gangnam", "강남",
)
KR_SCHOOLS = ("seoul national", "kaist", "korea advanced institute", "postech",
              "yonsei", "korea university", "서울대", "카이스트", "포스텍", "연세", "고려대")


def is_side_job(pos: dict) -> bool:
    return str(pos.get("employmentType") or "") in _SIDE_EMPLOYMENT


def fulltime_gap_months(item: dict) -> int | None:
    """**본업**(부업 제외) 기준 공백 개월. `gap_months` 의 짝이다.

    🔴 왜 따로 재나: `gap_months` 는 Present 가 **하나라도** 있으면 0 이다. 그런데 퇴사하고
       자문·EIR 만 파트타임으로 걸어둔 사람이 있다 — 실질은 본업이 없는데 0 으로 찍힌다.
       EIR(Entrepreneur in Residence)은 **말 그대로 창업 준비 중**이라, 이 채널에서
       가장 놓치면 안 되는 인구다.

    두 칸을 **같이** 봐야 뜻이 산다:
        공백 0 / 본업공백 6  → 파트타임 하나 걸고 6개월째 본업 없음  ← 신호
        공백 0 / 본업공백 0  → 정상 재직
    """
    exp = [e for e in (item.get("experience") or []) if isinstance(e, dict)]
    if any(is_present(e) and not is_side_job(e) for e in exp):
        return 0
    ends = [int(e["endDate"]["year"]) * 12
            + _MON.get(str(e["endDate"].get("month", "")).strip()[:3].lower(), 1)
            for e in exp
            if not is_present(e) and not is_side_job(e)
            and isinstance(e.get("endDate"), dict) and e["endDate"].get("year")]
    return (_today_m() - max(ends)) if ends else None


def nationality_input(item: dict) -> tuple[str, str, str, str]:
    """국적 판정(judge.nationality)에 넣을 (이름, 거주지, 학력, 경력·헤드라인). 원본 아이템에서."""
    s = summarize(item)
    loc = item.get("location") or item.get("locationName") or ""
    if isinstance(loc, dict):
        loc = loc.get("linkedinText") or ""
    schools = " / ".join(str(e.get("schoolName") or e.get("school") or e.get("title") or "")
                         for e in (item.get("education") or []) if isinstance(e, dict))
    work = " · ".join(x for x in [s["headline"]] + [f"{p['title']}@{p['company']}" for p in s["positions"][:4]] if x)
    return s["name"], str(loc), schools, work


def korea_signals(item: dict) -> list[str]:
    """노션 `Korea Signal` multi_select 값. 거주·학교만 판별한다."""
    out = []
    loc = str(item.get("location") or item.get("locationName") or "").lower()
    if any(t in loc for t in KR_LOCATION_TERMS):
        out.append("KR-거주")
    schools = " ".join(
        str(e.get("schoolName") or e.get("school") or e.get("title") or "")
        for e in (item.get("education") or []) if isinstance(e, dict)).lower()
    if any(t in schools for t in KR_SCHOOLS):
        out.append("KR-학교")
    return out


def company_url(item: dict) -> str:
    """현재 회사의 링크드인 URL. 노션 `회사` formula 가 이걸 파싱한다.

    판단할 때 **회사 자체를 조회할 유일한 열쇠**다 — payload 에 회사의 나이·투자단계가 없다.
    창업 포지션을 우선한다.
    """
    for e in _raw_positions(item):
        u = e.get("companyLinkedinUrl")
        if u:
            return str(u)
    return ""


def _raw_positions(item: dict) -> list[dict]:
    """개월 계산용 원본 포지션(dict 그대로). positions() 는 표시용으로 평평하게 만든다."""
    out = []
    for key in ("currentPosition", "currentPositions", "positions"):
        out += [p for p in (item.get(key) or []) if isinstance(p, dict)]
    out += [e for e in (item.get("experience") or []) if isinstance(e, dict) and is_present(e)]
    return out


# ================================================================ 임시 소싱 조건 (2026-08-19)
#
# 🔴 아래 셋은 **서버에서 못 거는 조건**이다. 액터 입력 파라미터 41개를 전수 조회했는데
#    (`GET /v2/acts/harvestapi~linkedin-profile-search/builds/default`, 무과금)
#    **학력 연도 필터도, "현 직장 없음" 필터도 없다.**
#    → 사고 나서 거른다 = **걸러진 사람 값도 이미 나간 돈이다.**
#      그래서 거른 수를 반드시 세서 남긴다(조용한 삭제 없음). `axes.reject` 가 그 자리다.

# 학위 문자열에서 학부를 가려낸다. **제외를 먼저 본다** — "Master of Business Administration - MBA"
# 처럼 학사 약어(`BA`)를 품은 상위 학위가 있어서, 포함부터 보면 석사가 학부로 샌다.
_NOT_UNDERGRAD = re.compile(
    r"(master|mba|m\.?s\.?c?\b|m\.?a\.?\b|ph\.?d|doctor|석사|박사|"
    r"high school|고등학교|associate|전문학사)", re.I)
_UNDERGRAD = re.compile(
    r"(bachelor|학사|학부|\bb\.?s\.?c?\b|\bb\.?a\.?\b|\bb\.?e\.?\b|\bb\.?tech\b|\bbba\b)", re.I)


def undergrad_start_year(item: dict) -> int | None:
    """학부 시작연도. **학위가 학부라고 적혀 있고 시작연도가 있는 것만** 센다.

    🔴 `None` 은 "학부가 없다"가 아니라 **판정 불가**다. 실측(아카이브 675명):
       교육 레코드 1,541개 중 `startDate` 가 null 인 것이 28.1% · 학위를 아예 안 적은 것이 138개.
       버릴지 남길지는 **부르는 쪽이** 정한다 — 여기서 0 이나 9999 로 뭉개지 않는다.
    """
    years = []
    for e in item.get("education") or []:
        if not isinstance(e, dict):
            continue
        deg = str(e.get("degree") or "")
        if not deg or _NOT_UNDERGRAD.search(deg) or not _UNDERGRAD.search(deg):
            continue
        sd = e.get("startDate")
        y = sd.get("year") if isinstance(sd, dict) else None
        if y:
            years.append(int(y))
    return min(years) if years else None


# 나이 추정 (2026-09-29 · 사용자 결정 "나이가 많다"). 생년은 링크드인에 없다 — 연도에서 역산한다.
UNDERGRAD_AGE = 19      # 학부 입학
FIRST_JOB_AGE = 23      # 첫 경력 시작 (학부 연도가 없을 때만)


def estimated_age(item: dict) -> int | None:
    """추정 나이. 학부 입학 연도가 있으면 그걸로, 없으면 가장 이른 경력 시작 연도로.

    🔴 `None` 은 "젊다"가 아니라 **판정 불가**다 — 버릴지는 부르는 쪽이 정한다.
    ⚠️ 첫 경력은 **나이를 낮게** 잡는 쪽으로 틀린다(재학 중 인턴 · 액터가 경력을 잘라 준 경우).
       즉 이 값으로 자르면 나이 많은 사람이 새는 쪽이지, 젊은 사람을 버리는 쪽이 아니다.
    """
    y = date.today().year
    u = undergrad_start_year(item)
    if u:
        return y - u + UNDERGRAD_AGE
    starts = [int(e["startDate"]["year"]) for e in (item.get("experience") or [])
              if isinstance(e, dict) and isinstance(e.get("startDate"), dict) and e["startDate"].get("year")]
    return y - min(starts) + FIRST_JOB_AGE if starts else None


def _norm_school(name: str) -> str:
    """`Korea University / 고려대학교` 처럼 병기된 이름은 앞부분만 본다."""
    n = str(name or "").split("/")[0].strip().lower()
    return re.sub(r"\s+", " ", n)


# ================================================================ KAIST 재학 판별 (2026-09-15, Reviewer C 요청)
#
# 🔴 education의 endDate에 "Present"는 **0건**이다 (아카이브 2,923건 실측).
#    experience에서는 흔하지만 education에서는 안 쓰인다.
#    재학 판별은 endDate.year >= 현재연도 **만** 쓴다.

_KAIST_NAMES = {"kaist", "korea advanced institute of science and technology"}
_PHD_RE = re.compile(r"(ph\.?d|doctor|박사)", re.I)


def kaist_currently_enrolled(item: dict) -> str:
    """KAIST 학석사 재학 여부. 통과하면 빈 문자열, 아니면 이유."""
    current_year = date.today().year

    found_kaist = False
    all_phd = True
    has_graduated = False

    for e in item.get("education") or []:
        if not isinstance(e, dict):
            continue
        if _norm_school(e.get("schoolName") or "") not in _KAIST_NAMES:
            continue
        found_kaist = True
        if _PHD_RE.search(str(e.get("degree") or "")):
            continue
        all_phd = False
        end = e.get("endDate")
        if isinstance(end, dict) and end.get("year"):
            if int(end["year"]) >= current_year:
                return ""
            has_graduated = True
            continue
        # endDate 빈 dict / None → startDate 로 추정
        sd = e.get("startDate")
        if isinstance(sd, dict) and sd.get("year") and int(sd["year"]) >= current_year - 6:
            return ""  # 6년 이내 입학 → 재학 추정

    if not found_kaist:
        return "KAIST-아님"
    if all_phd:
        return "KAIST-박사"
    if has_graduated:
        return "KAIST-졸업"
    return "KAIST-재학미확인"
