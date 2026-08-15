"""프로필 점수 — 링크드인 후보를 **심사역이 읽을 순서**로 세우는 0~4점 정렬 키.

## 이건 컷이 아니다
점수가 낮다고 버리지 않는다. 수집·착지는 전량 그대로 가고, 이 값은 **슬랙 큐의 정렬**과
노션 정렬에만 쓴다. 이 스킬의 원칙("수집에서 자르지 않는다")을 점수가 우회하면 안 된다.

## 등급(T1~T4)이 아니라 점수인 이유
등급은 경계를 정해야 하는데 그 경계를 정할 근거가 없다(2026-08-15 시점 정답지 n=9).
점수는 경계 없이 정렬만 한다 — 나중에 가중치만 바꾸면 되고 "T1 기준이 뭐냐"를 다시
협상할 필요가 없다.

## 가중치의 출처와 한계 (🔴 지우지 마라)
극초기 스타트업 DB에서 `만나요`가 지정된 **링크드인 9명**과 등록만 된 9명을 대조해 뽑았다.
    스텔스   만나요 4/9 · 등록만 2/9   ← 단일 신호로 가장 잘 맞았다
    설카포연고 만나요 4/9
    2점 이상 = 만나요 6/9 vs 등록만 2/9 (정밀도 75%)
**n=9다. 통계가 아니라 관찰이고, 1~2명으로 뒤집히는 크기다.** 그래서 컷이 아니라 정렬이다.

⚠️ 이전에 `Tier`(Catch/Trend/Wait) 1,149건으로 같은 계산을 하면 스텔스가 **무신호**(리프트
   1.05x)로 나온다. 그 칼럼은 관리되지 않은 라벨이라 근거로 쓰지 않는다(사용자 확인 2026-08-15).
"""
from __future__ import annotations

import re

# ── +2 스텔스 ────────────────────────────────────────────────────────────
# 🔴 **직함이 아니라 회사명**이다. 실측: 직함에 stealth = 97명 중 0명, 회사명엔 실재
#    ("Stealth", "Newco - still in stealth mode"). 이걸 헷갈려서 축 하나가 통째로 죽은 적 있다.
W_STEALTH = 2
STEALTH_TERMS = ("stealth", "newco", "스텔스")

# ── +1 학교 (둘은 배타적 — 합쳐서 최대 +1) ──────────────────────────────
W_SCHOOL = 1
KR_TOP_SCHOOLS = (
    "seoul national", "korea advanced institute", "kaist",
    "pohang university", "postech", "yonsei", "korea university",
    "서울대", "카이스트", "포스텍", "연세대", "고려대",
)
# 🔴 **아이비리그를 따로 두지 않는다.** 정답지에서 아이비 출신 `만나요`는 0명이었고,
#    실제로 만난 사람의 학교는 UMich·GIST였다. 이름값이 아니라 **연구중심대**가 축이다.
#    아래는 정답지가 알려준 것부터 시작한다 — 유입을 보며 넓혀라(넓히는 건 안전하다, 가점이므로).
RESEARCH_SCHOOLS = (
    "university of michigan", "gwangju institute", "gist",
    "georgia institute", "carnegie mellon", "university of illinois",
    "university of california", "stanford", "massachusetts institute",
    "university of washington", "cornell", "columbia university",
    "unist", "dgist", "sogang", "hanyang", "sungkyunkwan",
)

# ── +1 학부 시작연도 ─────────────────────────────────────────────────────
W_FRESH_GRAD = 1
UNDERGRAD_SINCE = 2010
# 🔴 **앞뒤 단어 경계가 필수다.** 경계 없이 `b\.?a\.?`로 쓰면 **MBA가 학부로 잡힌다** —
#    실측 1,160명 중 30명의 학부연도가 바뀌고, 그중 13명이 MBA 입학연도로 +1점을 잘못 받고
#    있었다(MBA는 학부보다 10년쯤 늦어 2010↑ 조건을 그냥 통과한다).
#    `석사`·`PhD`·`High School`·`석박통합과정`·`Candidate`가 안 걸리는지 바꿀 때마다 확인하라.
_UNDERGRAD = re.compile(
    r"bachelor|학사|학부|\bB\.?S\.?\b|\bB\.?A\.?\b|\bB\.?E\.?\b|\bBBA\b|\bBSc\b|undergrad",
    re.I)

MAX_SCORE = W_STEALTH + W_SCHOOL + W_FRESH_GRAD


def _has(text: str, terms) -> bool:
    t = (text or "").lower()
    return any(x in t for x in terms)


def undergrad_year(payload: dict) -> int | None:
    """학부 시작연도(가장 이른 것). 판정 불가면 None.

    ★ **학교 조건과 무관하게** education 전체를 본다. 예전 초안은 "설카포연고의 학부"로
      제한했는데, 그러면 정답지 9명 중 3명만 판정됐다. 이 값은 학교 검증이 아니라
      **나이대 대리지표**라 어느 학교든 상관없다.
    """
    years = []
    for e in payload.get("education") or []:
        if not isinstance(e, dict) or not _UNDERGRAD.search(e.get("degree") or ""):
            continue
        y = (e.get("startDate") or {}).get("year")
        if y:
            years.append(int(y))
    return min(years) if years else None


def current_companies(payload: dict) -> list[str]:
    """현재 포지션의 회사명들. `adapters.linkedin._positions` 가 정본이다.

    지연 import 인 이유: adapters.linkedin 이 core 를 불러서 최상위 import 면 순환이 된다.
    ⚠️ 여기서 자체 파싱을 다시 짜지 마라 — `currentPosition` 만 보면 진행 중인 경력의
       43%를 놓친다(실측 271명 중 117명). 그 로직은 저쪽에 한 벌만 있어야 한다.
    """
    from adapters.linkedin import _positions
    return [str(p.get("companyName") or "") for p in _positions(payload)]


def score(payload: dict) -> tuple[int, list[str]]:
    """(점수, 근거) — 근거는 노션 `점수 근거` 칸으로 가서 사람이 왜 위에 있는지 본다.

    조용한 점수를 만들지 않는다. 점수만 있고 근거가 없으면 "이 사람이 왜 1위지"를
    아무도 재현 못 하고, 그러면 가중치를 고칠 근거도 안 쌓인다.
    """
    total, why = 0, []

    if any(_has(c, STEALTH_TERMS) for c in current_companies(payload)):
        total += W_STEALTH
        why.append("스텔스")

    schools = [str((e or {}).get("schoolName") or "")
               for e in (payload.get("education") or []) if isinstance(e, dict)]
    if any(_has(s, KR_TOP_SCHOOLS) for s in schools):
        total += W_SCHOOL
        why.append("설카포연고")
    elif any(_has(s, RESEARCH_SCHOOLS) for s in schools):
        total += W_SCHOOL
        why.append("연구중심대")

    year = undergrad_year(payload)
    if year is not None and year >= UNDERGRAD_SINCE:
        total += W_FRESH_GRAD
        why.append(f"학부{year}")

    return total, why


# ── 동점 처리 ────────────────────────────────────────────────────────────
# 점수가 같으면 여기서 순서가 갈린다. 근거는 판정 이력 1,149명 실측 리프트:
#   창업 7~24개월 1.80x  ·  창업 0~6개월 1.51x  ·  KR거주+KR학교 1.54x
#   재직 25개월+ 0.40x   ·  창업 61개월+ 0.14x   ← 뒤로 보낸다
# ⚠️ 이 수치는 `Tier` 칼럼 기반이라 **점수 가중치보다 근거가 약하다.** 동점일 때만 쓰는
#    이유가 그거다 — 틀려도 같은 점수대 안에서만 순서가 흔들린다.
def tiebreak(signals: dict) -> tuple:
    """`sorted(key=...)` 에 넣을 튜플. 값이 클수록 위로(reverse=True 와 함께 쓴다)."""
    fs = signals.get("founder_since_months")
    tenure = signals.get("tenure_months")

    if isinstance(fs, (int, float)):
        founder_rank = 3 if 7 <= fs <= 24 else 2 if fs <= 6 else 0 if fs > 60 else 1
    else:
        founder_rank = 1                       # 미확인은 중간 — 없다고 벌하지 않는다

    kr = signals.get("korea_signal") or []
    kr_rank = 1 if len(kr) >= 2 else 0         # KR거주 + KR학교 동시 보유

    stale = -1 if isinstance(tenure, (int, float)) and tenure >= 25 else 0

    return (founder_rank, kr_rank, stale)
