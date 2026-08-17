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
        "payload": {"pastJobTitles": ["Software Engineer", "Product Manager",
                                      "Data Scientist", "Designer"],
                    "locations": ["South Korea"]},
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


def payload(axis: str, start_page: int) -> dict:
    a = AXES[axis]
    return {**COMMON, **a["payload"], "startPage": start_page}
