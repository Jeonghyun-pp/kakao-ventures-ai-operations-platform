"""수집 축 — 무엇을 사올지의 선언.

## 서버에서 거는 것 / claude 가 보는 것

    돈이 나가는 조건은 서버 필터로, 돈이 안 드는 판단은 claude 가.

사후 필터링은 **이미 돈을 쓴 뒤**다. 100명 사서 10명만 쓸 거면 90명 값은 날아간다.
그래서 메타데이터로 거를 수 있는 건 전부 서버에서 건다.

## 🔴 이 채널의 유일한 큰 레버

    yearsAtCurrentCompanyIds: ["1"]   현 직장 재직 1년 미만
    companyHeadcount: ["A","B"]       Self-Employed · 1~10명

이 둘을 켜기 전에는 재직 중앙값 18개월에 60%가 "이미 오래 Founder 인 사람"이었고
**구글코리아 사장까지 걸렸다.** 켜니 중앙값 6개월이 됐다.
*같은 돈으로 사는 사람이 통째로 달라진다.*

## 🔴 이 채널의 목적은 "알려지기 전에 찾는 것"이다

이미 트랙션이 있고 알려진 팀은 다른 경로로도 우리에게 온다. 그러니 배분을 **이른 쪽으로** 기울인다.

다만 필터만 당기면 안 된다 — 실측으로 창업 0~3개월(1.34배)이 3~6개월(1.91배)보다 **낮다.**
사람이 별로여서가 아니라 **보여줄 게 아직 없어서** 판정이 Wait/Cut 으로 몰리는 것이다.
그래서 `criteria.md` 도 같이 바꿨다 — "정보가 없는 것은 감점이 아니다".

## 🔴 [2026-08-17 실측] 페이지를 24 → 4.5 로 줄였다

첫 전 축 실행에서 **650명을 사서 566명이 신규**였다. 이 페이스면 풀(gap 제외 약 2,700명)이
**5주면 마른다.** 목표는 주 70~80명이었다.

축마다 신규율이 완전히 다르다:

    커서 축    다음 페이지를 산다     → 사는 족족 신규 (25명/페이지)
    rewind 축  매번 같은 사람을 산다   → 신규는 "그 사이 새로 생긴 사람"뿐

`flow-new-founder` 가 극단적이다. 모수 237명인데 첫 실행에 224명을 봤다. 다음엔
$2.00 을 써도 신규는 10~20명이다 — 명당 $0.10~0.20 으로 커서 축($0.008)의 20배다.
그래도 못 버리는 이유는 **빠름**이다. 이번 주 창업한 사람을 이번 주에 잡는 게 목적이니까.

→ **커서 축은 페이지를 줄이고, rewind 축은 주기를 늘렸다**(전량이어야 "놓치는 게 0"이
   성립하므로 페이지를 못 줄인다).

## 페이지 배분은 실측 성적 + 위 목적으로 정했다

노션 ch_링크드인 1,151행의 축별 Catch 비율(기저 17.9%):

    posted-early 33.3% · early-kr 29.6% · flow 19.3% · stealth-us 17.9%
    stealth-kr 16.3% · gap-practitioner 7.3%

`stealth-kr` 은 Catch 가 낮지만 **Cut 이 6%로 전 축 최저**다 — 사람이 별로여서가 아니라
회사명이 "Stealth"라 **판단할 정보가 없어서**다. 그래서 줄이지 않고 늘렸다.

`gap-practitioner` 는 Catch 로 재면 안 된다. "아직 창업 안 한 사람"을 찾는 축이라
Catch 가 낮은 게 당연하고, Trend 가 49%다. 3~6개월 뒤 창업했는지로만 평가할 수 있다.
"""
from __future__ import annotations

from . import judge, profile

SLUG = "harvestapi~linkedin-profile-search"

# 페이지당 $0.10(런당 고정) + 25명 × $0.004 = $0.20
USD_PER_PAGE = 0.20
PER_PAGE = 25

# 전 축 공통. 리크루터를 안 빼면 자기 리크루팅 회사의 "대표"로 자리를 다 먹는다.
COMMON = {
    "profileScraperMode": "Full",     # 학력이 필요하다. Short 는 education 을 0/97 로 안 준다
    "maxItems": 2500,                 # 쿼리당 절대 천장 (startPage 100 × 25)
    "takePages": 1,                   # 🔴 액터가 무시한다. 2를 보내도 1페이지만 온다 → 페이지당 1런
    "excludeCurrentJobTitles": ["recruiter", "talent acquisition", "headhunter",
                                "sourcer", "HR manager"],
}

_TITLES = ["Founder", "Co-Founder", "Cofounder", "CEO", "CTO", "대표", "창업자", "공동창업자"]
_EARLY = {"yearsAtCurrentCompanyIds": ["1"], "companyHeadcount": ["A", "B"]}

# 🔴 [사용자 결정 2026-09-01 · 시니어(Reviewer B) 기준] 사후에 텍스트로 거르던 컷을
#    **서버로 옮긴다.** 사고 나서 버리면 그 값은 이미 나간 돈이다.
#    실측으로 ✅ 손해가 0인 것만 옮겼다 — 리크루팅·컨설팅은 ✅ 11명 중 0명을 자른다.
#    ⚠️ 분류는 링크드인이 정한다. 창업자가 Consulting 으로 잡히면 **사기도 전에 사라진다** —
#       사후 컷은 되살릴 수 있지만 서버 컷은 데이터에 안 남는다. 프로브로 확인할 것.
# 🔴 **문자열이다.** 스키마상 items.type = "string" 인데 enum 값이 숫자처럼 생겼다.
#    정수로 보내면 액터가 http400 invalid-input 을 준다 (2026-09-01 실측, $0.20 날림).
_EXCL_FN = ["6", "12", "25"]        # Consulting · Human Resources · Sales
_EXCL_SENIOR = ["220", "300"]       # Director · Vice President
_EXCL = {"excludeFunctionIds": _EXCL_FN, "excludeSeniorityLevelIds": _EXCL_SENIOR}

# 경력단절 축이 쓰는 과거 직함. 🔴 임원 직함으로 걸면 "이미 CEO 를 해본 사람"만 나온다
#    (유료 프로브 실측: 임원 4/25 전원 휴지기 임원 · 실무 10/25 중 9/12 가 실무자).
_PRACTITIONER = ["Software Engineer", "Product Manager", "Data Scientist", "Designer"]
_KAIST = ["KAIST", "Korea Advanced Institute of Science and Technology"]   # 🔴 약칭·정식명 둘 다 — 학교 필터는 이름으로 엔티티를 맞힌다

# 창업자를 분류로 잡는다. 직함 문자열이 놓치는 사람 —
# "Building something new" 처럼 쓴 사람 — 을 여기서 줍는다.
_FOUNDER_CLASS = {"seniorityLevelIds": ["320", "310"]}   # Owner/Partner · CXO

AXES: dict[str, dict] = {
    # 실측 33.3% — 최고. "지금 움직이는 사람"
    "posted-early": {
        "every_days": 7, "pages": 1, "rewind": False,
        "payload": {"currentJobTitles": _TITLES, "locations": ["South Korea"],
                    "recentlyPostedOnLinkedIn": True, **_EARLY},
    },
    # 실측 29.6% — 이미 극초기인 사람 전체 (모수 1,348명)
    "early-kr": {
        "every_days": 7, "pages": 1, "rewind": False,
        "payload": {"currentJobTitles": _TITLES, "locations": ["South Korea"], **_EARLY},
    },
    # 실측 16.3% 지만 Cut 이 6%로 최저 — 정보가 없을 뿐이다.
    # 🔴 재직·규모 필터를 **일부러 안 건다**. 스텔스 2년차도 팔로업 대상이라
    #    재직기간으로 자르면 그 사람들이 사라진다.
    #    그리고 Stealth 는 직함이 아니라 **회사명**에 박힌다(실측: 직함 0명 / 회사명 2명).
    "stealth-kr": {
        "every_days": 7, "pages": 1, "rewind": False,
        "payload": {"searchQuery": "Stealth Startup", "locations": ["South Korea"]},
    },
    # 신규 유입 감지기. rewind = 매번 전량(237명) 재구매 —
    # 액터에 "이미 산 사람 빼고" 하는 파라미터가 **없어서** 어쩔 수 없다. 사고 나서 장부로 거른다.
    "flow-new-founder": {
        "every_days": 30, "pages": 10, "rewind": True,
        "payload": {"currentJobTitles": _TITLES, "locations": ["South Korea"],
                    "recentlyChangedJobs": True, **_EARLY},
    },
    # 🔴 앞 축들과 논리가 정반대다 — **현 직함이 없는** 사람을 찾는다.
    #    currentJobTitles·yearsAtCurrentCompanyIds 를 걸면 "현 직장이 있어야 성립"하는
    #    조건이라 공백자를 구조적으로 배제한다(실측: 271명 중 공백자 0명).
    #    과거 직함을 임원으로 걸면 공백자 4/25가 전원 휴지기 임원인데,
    #    실무 직함으로 걸면 10/25 이고 9/12가 실무자였다(유료 프로브 2회).
    "gap-practitioner": {
        "every_days": 14, "pages": 1, "rewind": False,
        "payload": {"pastJobTitles": _PRACTITIONER, "locations": ["South Korea"]},
    },
    # 해외에 있는 한국 연고자. schools 서버 필터는 실제로 작동한다(25명 중 24명이 서·연·고·카·포).
    # 한국 거주 축에는 학교를 안 건다 — 효과가 1.27배로 약한데 모수만 크게 준다.
    "stealth-us-krschool": {
        "every_days": 30, "pages": 6, "rewind": True,
        "payload": {"searchQuery": "Stealth Startup", "locations": ["United States"],
                    "schools": ["Seoul National University", "KAIST", "POSTECH",
                                "Yonsei University", "Korea University"]},
    },
}

# ================================================ 2026-09-01 · 시니어(Reviewer B) 기준
#
#     스텔스  OR  최근 6개월 내 경력단절  OR  현 직장 재직 6개월 이내
#     그리고 창업자는 **직함과 분류 둘 다로** 잡는다.
#
# 🔴 **OR 은 서버가 못 건다.** 액터는 조건을 AND 로만 묶는다(스키마 41개 실조회).
#    그래서 갈래마다 축을 따로 두고 사서 합친다 — 축을 나누는 것이 곧 OR 이다.
#
# 🔴 **"6개월 이내"도 서버에 없다.** yearsAtCurrentCompanyIds 의 최소 단위가
#    `1 = 1년 미만`이다. 1년치를 사서 사후에 절반을 버린다 — 돈은 1년치가 나간다.
#
# 🔴 **학력은 안 건다**(사용자 결정 2026-09-01). elite-kr·elite-us-kr 은 은퇴한다.
#    학력이 좋은 신호라는 근거를 끝내 못 찾았다 — 1티어 35명의 Catch 34% 가
#    학교 덕인지 스텔스 조건 덕인지 갈리지 않았고, 갈릴 만큼의 표본도 없었다.
_AXES_2026_09 = {
    # 축 A — 스텔스. 🔴 재직·규모를 일부러 안 건다(스텔스 2년차도 팔로업 대상).
    "s-stealth": {
        "every_days": 7, "pages": 2, "rewind": False,
        "payload": {"searchQuery": "Stealth Startup", "locations": ["South Korea"],
                    **_EXCL},
    },
    # 축 B — 경력단절.
    # 🔴 여기엔 **아무 현직 조건도 안 건다.** 직군·직급·리크루터 제외까지 뺐다 —
    #    전부 현 직장 기준이라 걸면 공백자가 통째로 사라진다(실측: 271명 중 0명).
    #    대신 사고 나서 claude 가 거른다.
    "s-gap": {
        "every_days": 14, "pages": 1, "rewind": False,
        "keep_exclude_titles": False,
        "payload": {"pastJobTitles": _PRACTITIONER, "locations": ["South Korea"]},
    },
    # 축 C1 — 최근 입사 · 직함으로.
    "s-recent-title": {
        "every_days": 7, "pages": 3, "rewind": False,
        "payload": {"currentJobTitles": _TITLES, "locations": ["South Korea"],
                    **_EARLY, **_EXCL},
    },
    # 축 C2 — 최근 입사 · 분류로.
    # 🔴 C1 과 한 축에 못 합친다. 직함과 분류를 한 쿼리에 넣으면 AND 라 **더 좁아진다.**
    #    OR 로 만들려면 따로 사서 합쳐야 한다.
    "s-recent-class": {
        "every_days": 7, "pages": 3, "rewind": False,
        "payload": {"locations": ["South Korea"],
                    **_EARLY, **_FOUNDER_CLASS, **_EXCL},
    },
    # 페이지 수 [사용자 결정 2026-10-15 "많이 사도 된다"] — 주 11페이지 ≈ $2.2 → 월 ≈ $9.5 (+ recheck ≈ $6.3, 상한 $29 안).
    #    기준: 주 착지 ≥ 카드 소모(주 25장). 10-12 실측 1페이지 ≈ 7명 착지 — 전엔 축마다 1페이지라 넣는 양 < 쓰는 양이었다.
    # 축 D — 연쇄 창업자 [사용자 결정 2026-10-15]. ✅ 공통점 1순위(연쇄창업 ✅ 12/25 = 48% · 기저 35%)를
    #    판정이 아니라 **살 때** 잡는다 — 과거 직함도 창업 직함이면 서버가 걸러 준다.
    # 🔴 재직 2년 이하(`1`·`2`)를 건다. 안 걸면 6년 된 회사 대표가 섞인다(10-15 시험: 재직 17~78개월, 40세+ 7/14).
    #    걸고 다시 사면 17~29개월 · 40세+ 3/9 · 모수 2,588 → 841.
    #    _EARLY(1년 미만)는 안 쓴다 — ✅ 연쇄창업자의 재직은 대부분 4~18개월이다.
    "s-serial": {
        "every_days": 7, "pages": 3, "rewind": False,
        "payload": {"currentJobTitles": _TITLES, "pastJobTitles": _TITLES, "locations": ["South Korea"],
                    "companyHeadcount": ["A", "B"], "yearsAtCurrentCompanyIds": ["1", "2"], **_EXCL},
    },
}
AXES.update(_AXES_2026_09)

# 🔴 [사용자 결정 2026-09-29] 09-01 시니어 기준(s-*)이 대신하는 옛 축은 주차한다 — 정기 수집이 안 집는다.
#    재고 기준선(100명)에 막혀 08-17 뒤로 안 돌았을 뿐 꺼진 적이 없었다. 기준선을 없애자 24페이지 중
#    20페이지($4.00)가 이 축들로 잡혔다(flow-new-founder 10p·stealth-us-krschool 6p 는 매번 전량 재구매).
for _a in ("posted-early", "early-kr", "stealth-kr", "flow-new-founder", "gap-practitioner", "stealth-us-krschool"):
    AXES[_a]["parked"] = "09-01 s-* 축이 대신한다(시니어 기준). 되살리려면 s-* 와 무엇이 다른지부터 보여라"


# ================================================================ KAIST 재학 창업자 (2026-09-15, Reviewer C 요청)
#
# 🔴 서버에 "재학중" 필터가 없다. schools 로 KAIST 전원을 사서
#    education.endDate.year >= 현재연도 인 사람만 남긴다.
#    졸업생 값도 이미 나간 돈이다 — reject 가 이유별로 세서 남긴다.
#
# 🔴 education 의 endDate 에 "Present" 는 **0건**이다(아카이브 2,923건 실측).
#    experience 의 is_present() 를 그대로 쓰면 재학생 0명이 나온다.

AXES["kaist-founder"] = {
    "every_days": 7, "pages": 10, "rewind": True, "manual": True,
    "post": "kaist-current",
    "payload": {
        "currentJobTitles": _TITLES,
        "schools": _KAIST,
        **_EARLY,       # 재직 1년 미만 + 10인 이하 → 모수를 줄인다
    },
}

# 직함이 아니라 분류(Owner/CXO)로 잡는 축. "Building something new" 처럼
# Founder 를 안 쓴 사람을 여기서 줍는다.
AXES["kaist-founder-class"] = {
    "every_days": 7, "pages": 10, "rewind": True, "manual": True,
    "post": "kaist-current",
    "payload": {
        "schools": _KAIST,
        **_EARLY, **_FOUNDER_CLASS,
    },
}

# 스텔스 + KAIST. _EARLY 를 안 건다 — 스텔스 2년차도 대상이다(stealth-kr 과 동일 논리).
AXES["kaist-stealth"] = {
    "every_days": 7, "pages": 10, "rewind": True, "manual": True,
    "post": "kaist-current",
    "payload": {
        "searchQuery": "Stealth Startup",
        "schools": _KAIST,
    },
}


def nationality_exempt(axis: str) -> bool:
    """외국인을 빼지 않는 축. [Reviewer C 요청 2026-09-15 · 사용자 재확인 2026-09-28] KAIST 유학생도 대상이다."""
    return AXES.get(axis, {}).get("post") == "kaist-current"


def reject(axis: str, item: dict) -> str:
    """사후 조건에 걸리면 **이유**를, 통과하면 빈 문자열을 준다.

    🔴 이유를 돌려주는 이유: 거른 사람을 세서 남기기 위해서다. bool 로 만들면
       "몇 명이 왜 빠졌는지"가 사라지고, 그건 조용한 삭제다.
    """
    # 🔴 kaist-current 는 외국인 필터를 안 건다 — KAIST 유학생도 대상이다.
    if nationality_exempt(axis):
        return profile.kaist_currently_enrolled(item)
    loc = str(item.get("location") or item.get("locationName") or "").lower()
    if loc and not profile.korea_signals(item):
        return f"외국인({loc})"
    return ""



def payload(axis: str, start_page: int) -> dict:
    a = AXES[axis]
    common = dict(COMMON)
    # 🔴 과거직함 축은 리크루터 제외를 뺀다 — 현 직함 기준 필터라 공백자를 통째로 뺄 수 있고,
    #    그러면 "공백형 0명"이 나와 **축이 고장난 것으로 오진**한다.
    if not a.get("keep_exclude_titles", True):
        common.pop("excludeCurrentJobTitles", None)
    return {**common, **a["payload"], "startPage": start_page}


# ================================================ 카드 필터 (노션 행에 대고 판정)
#
# 🔴 위 수집 축과 **같은 기준을 노션 행에 대본 것**이다. 완전히 같을 수는 없다 —
#    노션에 없는 칸이 셋 있다:
#      직군(function) · 직급(seniority) · 회사 규모
#    그래서 축 C2(분류로 창업자 잡기)는 **여기서 적용 불가**하고, 직군·직급 제외는
#    텍스트로 근사한다. 새 축으로 사온 사람부터는 이 격차가 줄어든다.
#
# 🔴 실측(2026-09-01, 아직 안 나간 1,190명):
#      축 A 141 · 축 B 55 · 축 C1 168 → 합집합 347명 (69일치)
#      나갔던 42명 중 30명 통과 · 그중 ✅ 7명 = 23%
#    ⚠️ 적중률이 기저(26%)보다 낮다. 다만 이 필터의 실질인 직군·직급을 텍스트로만
#       근사했고 C2 는 통째로 빠져 있어, **지금 데이터로는 제대로 평가되지 않는다.**

_CARD_FOUNDER = ("founder", "co-founder", "cofounder", "ceo", "cto",
                 "대표", "창업자", "공동창업자")
_CARD_RECRUIT = ("recruit", "헤드헌", "talent acquisition", "sourcer", "hr manager")
_CARD_EXCL_FN = ("consult", "컨설팅", "자문", "freelance", "프리랜서", "에이전시",
                 "agency", "인사담당", "채용대행", "sales rep", "영업대행")
_CARD_EXCL_SENIOR = ("director", "vice president", "전무", "상무", "본부장")
# 🔴 [사용자 결정 2026-09-21] 값을 조였다. 실측(재고 1,177명 기준, 329명에서 시작):
#      MAX_TENURE 60→36: -7 · STEALTH_MAX_MONTHS 신설(12): -38
#      GAP_MONTHS 6→3 + `본업 공백` 체크에도 같은 상한: -37
#      RECENT_MONTHS 6→3: -35 (Catch 8명 포함)
#    → 200명대 초반. `본업 공백` 체크가 개월 수와 무관하게 통과시키던 우회를 막았다 —
#      체크는 있는데 `공백 개월` 이 비어 있으면 여전히 통과한다(모르는 건 감점이 아니다).
# 🔴 [사용자 결정 2026-10-15] RECENT_MONTHS 3→24 · STEALTH_MAX_MONTHS 12→24. 위 값은 Catch 수(AI 판정)로 정했는데
#    정답(✅)으로 다시 재니 재직 기간은 ✅ 를 가르지 못했다 — 반응 63장(✅ 22)에 컷별로 적용:
#      3·12개월 ✅ 9/22 살림 · 통과군 ✅율 33%  |  12 → 15/22 · 34%  |  18 → 21/22 · 36%  |  24 → 21/22 · 35%
#    3개월 컷은 거르는 힘 없이 ✅ 13명을 버렸다. 24 = s-serial 축의 서버 조건(재직 2년 이하)과 같은 선.
#    Reviewer B·Reviewer C 확인은 사후 공유로 [같은 결정].
RECENT_MONTHS = 24       # 창업자 직함 + 현 직장 재직 24개월 이내
GAP_MONTHS = 3           # 최근 3개월 내 경력단절 — 체크박스에도 같은 상한
MAX_TENURE = 36          # 현 직장 재직 3년 초과 제외 (60 이었을 때 실측 Catch 0/105)
STEALTH_MAX_MONTHS = 24  # 스텔스라도 재직 24개월 초과면 제외 (3년차 스텔스는 극초기가 아니다)
MAX_AGE = 39             # [사용자 결정 2026-09-29] 추정 40세 이상 제외. 추정 불가는 통과(모르는 건 감점이 아니다)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def legacy_reason(live: dict) -> str:
    """사람 자체가 대상이 아닌 이유(국적·나이). collect 가 **착지 전에** 이걸로 Tier 판정을 건너뛰고 Legacy 로 보낸다.

    card 에서는 이것·비창업·card_reject 전부가 Legacy 사유다 — 노션 값은 착지 후 안 바뀌기 때문이다(run.card 주석).
    여기를 따로 두는 이유는 collect 가 판정 호출을 아끼는 자리라서다.
    """
    # 🔴 [사용자 결정 2026-09-28] 외국인 — 국적 기준(거주지 아님). 태그는 judge.nationality 가 단다.
    #    KAIST 축은 태그 자체를 안 달아 여기서 통과한다. 사람이 노션에서 태그를 지우면 다시 올라간다.
    if judge.FOREIGN in (live.get("Korea Signal") or []):
        return "외국인"
    age = _f(live.get("추정 나이"))
    if age is not None and age > MAX_AGE:
        return f"추정 {MAX_AGE + 1}세 이상"
    return ""


def card_reject(live: dict) -> str:
    """카드로 올릴 수 없으면 **이유**를, 올릴 수 있으면 빈 문자열을 준다.

    🔴 bool 이 아니라 이유를 준다 — 몇 명이 왜 빠졌는지가 화면에 남아야 한다.
       조용히 빠지면 재고가 왜 줄었는지 나중에 못 밝힌다.
    """
    head = str(live.get("Author Headline") or "").lower()
    blob = " ".join(str(live.get(k) or "") for k in
                    ("Author Headline", "Raw Text", "회사", "서비스명", "One Liner")).lower()
    tenure = _f(live.get("Tenure Months"))

    lr = legacy_reason(live)
    if lr:
        return lr
    if tenure is not None and tenure > MAX_TENURE:
        return f"재직 {MAX_TENURE}개월 초과"
    if any(w in blob for w in _CARD_RECRUIT):
        return "리크루팅"
    if any(w in blob for w in _CARD_EXCL_FN):
        return "컨설팅·인사·영업"
    if any(w in head for w in _CARD_EXCL_SENIOR):
        return "Director·VP"

    company = str(live.get("회사") or "").lower()
    if any(w in company for w in ("stealth", "steath")):
        if tenure is not None and tenure > STEALTH_MAX_MONTHS:
            return f"스텔스 재직 {STEALTH_MAX_MONTHS}개월 초과"
        return ""                                    # 축 A
    gap = _f(live.get("공백 개월"))
    if gap is not None and 0 < gap <= GAP_MONTHS:
        return ""                                    # 축 B
    if live.get("본업 공백") and (gap is None or gap <= GAP_MONTHS):
        return ""                                    # 축 B — 체크만 있고 개월이 빈 경우
    if any(w in head for w in _CARD_FOUNDER) and tenure is not None and tenure <= RECENT_MONTHS:
        return ""                                    # 축 C1
    return "세 갈래 어디에도 안 걸림"
