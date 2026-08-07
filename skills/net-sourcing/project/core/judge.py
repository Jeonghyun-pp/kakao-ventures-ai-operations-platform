"""판단(트리아지) — 현재는 **L0 하드컷만** 동작. AI 판단(L1/L2)은 보류(사용자 결정 2026-07-11).

방침: **최대한 많이 수집하고, 의미 없는 홍보성 글만 걷어낸다.** 정예 선별은 사람이 노션에서.
따라서 L0는 "정교한 분류기"가 아니라 **명백한 홍보/뉴스 제거기**다.

  L0 하드컷 (결정론적, AI 없음, 공짜):
    · 앱스토어 — 초기성(최초 출시 2년 초과) + 대기업/기관 개발사 denylist
    · 스레드   — 홍보성 컷: 타사 뉴스 전달 / 뉴스 큐레이션 피드 / 이벤트·공구 미끼 / 제3자 상품 홍보 / 스팸
    · 링크드인 — 없음 (검색 자체가 창업 타이틀로 서버측 필터됨)
  L1/L2 (AI): 미구현 — _ai_judge는 패스스루(전량 surface). 기준은 references/judge-criteria.md.

⚠️ 실측으로 확립된 원칙 (2026-07-11, 스레드 99명 전수 라벨 + 12개 검증렌즈):
  1. **recall 최우선.** L0가 걸러줘서 아끼는 비용은 주 $0.11에 불과하다. 반면 잘못 자른
     창업자는 영구 소실된다. 조금이라도 애매하면 **살려서 사람에게 넘긴다.**
  2. **소프트웨어/창업 신호가 있으면 홍보 룰을 면제한다.** 진짜 인디 개발자의 런칭 글이
     이벤트·홍보 어휘를 함께 쓰는 경우가 많다(실측: @yuni_studio_official 유아게임 출시).
  3. **본인 제품 링크(apps.apple.com 등)가 있으면 무조건 살린다.** URL은 매칭 전에 제거한다 —
     안 그러면 apps.apple.com의 'apple'이 "외부 대기업 언급"으로 오폭한다(실측: @zenonplane).
  4. 아래는 실측으로 **기각된 룰**이다. 다시 넣지 말 것:
     · 제품 링크 없으면 컷 → 진짜 빌더의 절반은 링크를 안 붙인다(TRUE 2명 사망)
     · 좋아요/댓글 수 기반 컷 → likes는 대부분 None, 댓글 1위는 "팔로워 100명 됐당"
     · 한글 비율 컷 → 영어로 쓰는 한국인 인디해커 전멸
     · 빅테크 이름 denylist → "구글 플레이스토어에 첫 앱 출시했습니다"가 컷된다
     · 실물 커머스 어휘 일괄 컷 → 제조·D2C 창업자가 죽는다(@hello_sunnyberry = 자체공장 운영)

실측 성능(2026-07-10 스레드 raw, 고유작성자 99명): 11컷 / 88통과.
⚠️ 예전 이 자리에 **"진성 창업자 오탐 0"**이라고 적혀 있었다 — **거짓이다**(2026-07-12 재현으로 반증).
   _THIRD 룰이 이모지 📍 하나·해시태그 '맛집' 하나로 발화하는데 1인칭 면제가 없어서, 본인 제품을
   만든 제조/D2C 창업자가 '제3자 상품 홍보'로 죽고 있었다. 그 문장을 근거로 이 룰들을 신뢰하지 말 것.
   (지금은 _THIRD에 _FIRST 면제를 걸었다. 나머지 룰의 오탐률은 **여전히 미측정**이다.)
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

CRITERIA_PATH = Path(__file__).resolve().parent.parent.parent / "references" / "judge-criteria.md"

MAX_AGE_DAYS = 180                            # 6개월. config.judge.appstore.max_age_days로 덮어씀


def run(cfg: dict, candidates: list[dict]) -> list[dict]:
    judged = []
    for c in candidates:
        cut_reason = _hardcut(cfg, c)          # L0 (채널별)
        if cut_reason:
            judged.append(_card(c, verdict="cut", reason=cut_reason))
            continue
        judged.append(_ai_judge(cfg, c))       # L1 + L2 (현재 패스스루)
    return judged


def hardcut(cfg: dict, candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    """land 경로의 노이즈 제거. (통과, 컷) 튜플 반환.

    2단: ① 결정론적 규칙(공짜, 명백한 것만) → ② LLM 사실분류(규칙 통과분만, 키 있을 때).
    ②는 "좋은 팀인가"를 판단하지 않는다 — "홍보글인가", "대기업인가"라는 사실 질문만 던진다.
    키가 없으면 ①만으로 동작한다. 컷된 것도 사유를 달아 반환한다(조용한 삭제 없음).
    """
    kept, cut = [], []
    for c in candidates:
        reason = _hardcut(cfg, c)
        if reason:
            cut.append({**c, "_cut_reason": reason})
        else:
            kept.append(c)

    kept, llm_cut = _llm_pass(cfg, kept)       # ② LLM 사실분류
    return kept, cut + llm_cut


def _llm_pass(cfg: dict, cands: list[dict]) -> tuple[list[dict], list[dict]]:
    """규칙을 통과한 후보에 LLM 사실분류를 적용. 확신이 낮으면 자르지 않는다(recall 우선)."""
    from core import llm
    # [중간점검 E2] 기본값 True→False — 선언(LLM 끔)과 코드 기본값이 반대면 키 오타 한 번에 조용히 부활한다.
    if not cfg.get("judge", {}).get("llm", {}).get("enabled", False) or not llm.available():
        return cands, []

    kept, cut = [], []

    # 스레드만 AI 판별한다 — "본인이 만든 걸 알리는 글인가, 남의 소식/광고인가".
    # ⚠️ 앱스토어는 **AI 판별을 하지 않는다**(사용자 결정 2026-07-11): 그 채널은 나열이 목적이고,
    #    발굴 가치 판단은 사람이 노션에서 한다. 6개월 컷이 초기성을 이미 보장한다.
    threads = [c for c in cands if c.get("source") == "스레드"]
    verdicts = llm.classify_promo(cfg, threads) if threads else {}

    # 링크드인 — **컷하지 않는다. tier(A/B/C)만 붙여 랭킹한다.**
    # 정규식 스코어는 무력했고(최상위 밴드 21.7% ≈ base 20.6%), LLM은 정밀도 11/11이다.
    # 그래도 하드컷은 안 한다: 61/97을 자르면 사람이 주 25분 덜 읽는 대가로 FOUNDER_LATE 23명 +
    # UNSURE 2명 + EARLY 1명이 영구 소실된다. 전원 착지시키고 tier로 정렬한다.
    lis = [c for c in cands if c.get("source") == "링크드인"]
    tiers = llm.classify_founders(cfg, lis) if lis else {}

    ti = li = 0
    for c in cands:
        if c.get("source") == "스레드":
            v = verdicts.get(ti)
            ti += 1
            if v and v["verdict"] == "promo":
                cut.append({**c, "_cut_reason": f"홍보성(AI): {v['reason'][:60]}"})
                continue
        elif c.get("source") == "링크드인":
            t = tiers.get(li)
            li += 1
            if t:
                sig = c.setdefault("signals", {})
                sig["tier"] = t["tier"]                       # A/B/C — 노션 정렬 키
                sig["company_does"] = t["company_does"]       # 회사가 뭘 하는가(5초 요약)
                sig["ai_reason"] = t["reason"]
                if t.get("injection"):
                    sig["injection_attempt"] = True           # 프롬프트 주입 시도 — 실데이터에 존재
                    print(f"🔒 프롬프트 주입 시도 감지: {c.get('name')}")
        kept.append(c)
    return kept, cut


# ============================ L0 하드컷 (결정론적) ============================
def _hardcut(cfg: dict, c: dict) -> str:
    """배제 사유 문자열 반환(배제) 또는 "" (통과). 채널별 분기."""
    src = c.get("source", "")
    if src == "앱스토어":
        return _hardcut_appstore(cfg, c)
    if src == "스레드":
        return _hardcut_threads(cfg, c)
    return ""                                  # 링크드인 = L0 없음(검색이 이미 타이틀 필터)


def _hardcut_appstore(cfg: dict, c: dict) -> str:
    """앱스토어 L0 — 초기성이 사실상 전부다.

    ① 최초 출시일이 max_age_days 초과 → 컷. **6개월(180일)** — 사용자 결정 2026-07-11.
       출시일 미상이면 통과(recall 우선).
       2년 컷일 때 이미 461컷 중 459를 이 룰 하나가 담당했다. 6개월로 좁히면 대기업 앱은
       거의 자동으로 사라진다(기아 앱 2024-09, 나라사랑포털 2026-01 전부 여기서 걸림).
    ② 개발사명 denylist — 보조. ①이 못 잡은 신생 대기업 앱만 남는다.

    ⚠️ 여기서 "발굴 가치"를 판단하지 않는다. 슬롭인지 좋은 팀인지, 한국팀인지는 사람이 노션에서
    출시일·개발사 도메인·순위를 보고 정한다. 정규식으로 그걸 하려던 시도는 전부 실패했다.
    """
    conf = cfg.get("judge", {}).get("appstore", {})

    max_days = conf.get("max_age_days", MAX_AGE_DAYS)
    rel = (c.get("released_at") or "")[:10]
    if rel:
        cutoff = (date.today() - timedelta(days=max_days)).isoformat()
        if rel < cutoff:
            return f"출시 {max_days}일 초과({rel})"

    dev = (c.get("founder") or "").lower()
    if dev:
        for token in conf.get("bigcorp_denylist", []):
            if token.lower() in dev:
                return f"대기업/기관 개발사({c.get('founder')})"

    return ""


# ── 스레드 홍보 컷 (실측 튜닝, 오탐 0) ──
_URL = re.compile(r"https?://\S+")

# 본인 제품 링크 = 홍보가 아니라 런칭. 있으면 무조건 통과.
_OWN_LINK = re.compile(
    r"(apps\.apple\.com|play\.google\.com|testflight|github\.com|notion\.site|vercel\.app"
    r"|웨이팅리스트|사전예약)", re.I)

# 소프트웨어/창업 신호 = 홍보 룰 면제(recall 우선). 인디 개발자의 런칭 글은
# 이벤트·홍보 어휘를 함께 쓰는 일이 잦다.
_SW = re.compile(
    r"(앱|어플|애플리케이션|게임|서비스|플랫폼|웹사이트|사이트|프로그램|솔루션|SaaS|API"
    r"|개발|코딩|프로그래밍|사이드프로젝트|토이프로젝트|MVP|베타|스타트업|창업|법인|출시|런칭|배포)", re.I)

# 1인칭 제작 표지 — 있으면 "남의 소식 전달"이 아니다.
_FIRST = re.compile(
    r"(제가|저는|저희|우리(가|는| 팀)|내가|나는|직접 만든|직접 개발|만들었|만든|개발했|개발 중"
    r"|1인 개발|혼자 만든)")

# 외부 대기업/AI 서비스 주어 — 뉴스 전달 계정의 표지.
_EXT = re.compile(
    r"(OpenAI|오픈ai|ChatGPT|GPT-?\d|Gemini|제미나이|구글|google|애플|apple|테슬라|tesla|메타\b"
    r"|nvidia|엔비디아|anthropic|마이크로소프트|아마존|네이버|카카오|삼성|현대차|쿠팡|배민|토스)", re.I)
_NEWS_VERB = re.compile(
    r"(출시했습니다|공개했습니다|발표했습니다|선보였습니다|도입했습니다|런칭했습니다|공식 출시)")
_NEWS_FEED = re.compile(
    r"(오늘의 (AI )?(이슈|뉴스)|이슈 브리핑|뉴스레터|헤드라인|출처:|관련 기사)")

_BAIT = re.compile(
    r"(맞팔|선팔|리포스트 하기|이벤트 참여|참여 방법|응모|추첨|증정|무료 나눔|공동구매|할인코드|쿠폰|체험단)")
_THIRD = re.compile(r"(^공유\)|📍|맛집|다녀왔|먹어봤)")
_SPAM = re.compile(r"(드랍쉬핑|부업|월 ?\d+ ?(천|백)만원|수익 ?인증|리딩방|자동 수익)")


def _hardcut_threads(cfg: dict, c: dict) -> str:
    """스레드 L0 — **의미 없는 홍보성 글만** 제거. 애매하면 살린다.

    순서가 중요하다: 면제(본인 링크 → SW 신호)를 먼저 보고, 그 다음에 홍보 룰을 적용한다.
    """
    if not cfg.get("judge", {}).get("threads", {}).get("promo_cut", True):
        return ""

    text = c.get("raw_text") or c.get("one_liner") or ""
    if not text:
        return ""
    body = _URL.sub(" ", text)                 # URL 제거 후 매칭 — 본인 앱 링크의 'apple' 오폭 방지

    if _OWN_LINK.search(text):                 # ① 본인 제품 링크 = 런칭. 무조건 통과
        return ""

    # ② 타사 소식 전달 (외부 기업 주어 + 뉴스 동사, 1인칭 제작 표지 없음)
    if _EXT.search(body) and _NEWS_VERB.search(body) and not _FIRST.search(body):
        return "홍보성: 타사 뉴스 전달"
    # ③ 뉴스 큐레이션 피드 (AI 이슈 브리핑 등)
    if _NEWS_FEED.search(body) and not _FIRST.search(body):
        return "홍보성: 뉴스 큐레이션 계정"

    if _SW.search(body):                       # ④ SW/창업 신호 있으면 이하 홍보 룰 면제
        return ""

    if _BAIT.search(body):
        return "홍보성: 이벤트/공구 미끼"
    # ★ _THIRD는 이모지 📍 하나, 해시태그 안의 '맛집' 부분문자열 하나로도 발화한다(워드바운더리 없음).
    #   본인이 "만들었습니다"라고 쓴 제조·D2C 창업자가 '제3자 상품 홍보'로 죽었다 — 실측 확인.
    #   → **1인칭 제작 표지가 있으면 면제한다.** 남의 물건을 홍보하는 사람은 "제가 만들었"다고 안 쓴다.
    if _THIRD.search(body) and not _FIRST.search(body):
        return "홍보성: 제3자 상품/매장 홍보"
    if _SPAM.search(body):
        return "홍보성: 스팸/수익인증"
    return ""


def _ai_judge(cfg: dict, c: dict) -> dict:
    """L1+L2 — **보류**(사용자 결정 2026-07-11: "판단 에이전트는 나중에").

    현재는 패스스루: L0를 통과한 후보는 전량 surface → 노션에 롱리스트로 적재하고 사람이 선별.
    AI 판단을 켤 때 이 줄을 Anthropic API 호출로 교체한다(기준: references/judge-criteria.md).
    """
    return _card(c, verdict="surface")


def _card(c: dict, *, verdict: str, reason: str = "", tier: str = "",
          confidence: str = "", pros=None, cons=None) -> dict:
    """판단 카드 — 사람 리뷰용 구조화 결과."""
    return {
        "verdict": verdict,                    # surface | borderline | cut
        "cut_reason": reason,
        "confidence": confidence,
        "tier_suggestion": tier,
        "one_liner": c.get("one_liner", ""),
        "area": [],
        "pros": pros or [],
        "cons": cons or [],
        "candidate": c,
        "judged_at": date.today().isoformat(),
    }
