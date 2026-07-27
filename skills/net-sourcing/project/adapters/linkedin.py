"""LinkedIn 어댑터 — HarvestAPI 쿠키리스(로그인 불필요, 밴리스크 0). 2026-07-11 전면 재설계.

목표: 극초기 창업자를 **먼저**(초기·미접촉·실시간) 찾는다. 판별/tier는 **별도 에이전트**(스코프 밖).
수집만 한다. 노이즈는 자르지 않고 넘긴다 — 유일한 하드컷은 리크루터 소스 제외뿐.

■ 엔진 2개
  1) profile-search — 발굴 본체. **축(쿼리)을 쪼개 천장을 뚫는다.**
     액터 절대 한계 = **쿼리당 2,500명**(startPage max 100 × 25/page). 83,231명짜리 검색에서도
     totalPages가 100으로 고정되는 걸 실측했다. 그런데 **축을 쪼개면 파티션마다 천장이 새로 생기고**,
     축 간 중복은 97명 중 3명뿐(거의 서로소)이라 축을 늘리면 recall이 거의 선형 증가한다.
     → 직함별로 10축. 각 축은 **페이지 커서**로 매주 다음 구간을 읽어 백필 없이 누적한다.
  2) post-search — 새 글을 창업 신호로.
     · 발굴: 키워드로 새 글 → **작성자 URL을 모아 profile-scraper로 위치를 따로 조회** → 한국만.
             (게시글 검색엔 지역 파라미터가 없고 출력 author에도 location이 없다 — 별도 조회 필수)
     · 모니터링: **극초기 축에서 나온 사람만** 감시. 그들이 최근에 쓴 글 = "지금 움직이는 사람".

■ seen-store = **90일 후 재확인**(영구 제외 아님)
  우리가 찾는 건 **변화**다. 회사명 `Stealth` → `주식회사 XX`, 직함 `PM` → `Founder`.
  그게 링크드인에서 얻을 수 있는 **가장 이른 신호**인데, 한 번 본 사람을 영영 안 보면 놓친다.
  → {urn: {날짜, 지문}}. 재확인 시 지문이 **바뀐 사람만** 후보로 올리고 dedup을 우회시킨다.

■ ⚠️ 이 파일에서 세 번 반복된 버그: **액터가 주는 키 이름을 추측하지 마라.**
  headline(0/97) · takePages(전원 1페이지) · 게시글 파싱(키 4개 전부 오답) — 전부 같은 원인이다.
  **보낸 값이 아니라 받은 값을 검증하라.** 페이지 루프에 pageNumber assert가 들어간 이유다.

안정 ID = URN(Candidate.ext_id).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import apify
from .base import Adapter, Candidate, _normalize

PROFILE_ACTOR = "harvestapi~linkedin-profile-search"
SCRAPER_ACTOR = "harvestapi~linkedin-profile-scraper"   # 개별 프로필 상세(위치·학력) 조회

# 액터 절대 상한: startPage max=100 × 25명/page = **쿼리당 2,500명**.
# 실측: `_meta.totalPages`가 83,231명짜리 검색에서도 100으로 고정된다. 이 이상은 못 받는다.
# → 그래서 축(쿼리)을 쪼갠다. 쿼리마다 2,500 천장이 새로 생긴다.
MAX_PAGE = 100
POST_ACTOR = "harvestapi~linkedin-post-search"

PROJ = Path(__file__).resolve().parent.parent

# --- 소프트 스코어 (하드 게이트 아님 — **약한 사전정렬**용) ---
#
# ⚠️ 이 점수를 믿지 마라. 97명 실측으로 랭킹 정보량이 거의 0임이 확인됐다:
#     최상위 밴드 정밀도 21.7%  ≈  base rate 20.6%   (최하위 밴드가 30.4%로 오히려 높았다)
#   진짜 판별은 LLM(judge)이 한다 — 홀드아웃 정밀도 11/11, FOUNDER_EARLY recall 95%.
#
# 지운 룰 (전부 실측 근거. 다시 넣지 말 것):
#   strong_title(+3) → **94/97(97%) 발화 = 상수.** 검색이 이미 서버측에서 창업 타이틀로
#                      필터하므로 전원이 받는다. 모두에게 같은 점수를 주면 순위가 안 바뀐다.
#   no_company(+2)   → 0/97. currentPositions[].companyName이 97/97 채워져 영원히 발화 불가.
#   kr_school_global → 0/97. Short 모드는 education을 아예 안 준다(이건 진짜 액터 한계).
#   student_only(-2) → 0/97.
#   recent_post(+1)  → post_search 엔진을 껐다(구조적 부적합).
W_RECENT_JOB = 3       # 90일 내 이직 = 창업 신호. 실측 발화 25/97 — 유일하게 작동하던 축
W_STEALTH = 2          # **회사명**에 stealth/newco. 발화 2/97이지만 둘 다 진성 창업자(정밀도 100%)
W_KR_LOCATION = 1      # 한국 거주. 발화 76/97
W_FRESH_TENURE = 3     # 현 직장 재직 12개월 미만 = "이 일을 시작한 지 얼마 안 됐다"
                       # 실측: 액터 페이로드 114/114 포지션에 tenureAtCompany가 들어오는데
                       # 어댑터가 한 번도 읽지 않았다. 우리가 수집하는 사람의 재직 **중앙값이
                       # 34개월**(t-영문스톡 축은 6.3년)이다 — 90%가 극초기가 아닌 '스톡'이다.
                       # 12개월 미만은 99명 중 24명뿐. **극초기를 직접 재는 유일한 필드**라
                       # 점수와 신호에 반드시 남긴다. (컷은 판단 에이전트가 한다)
FRESH_TENURE_MONTHS = 12

STRONG_TITLE_TERMS = ("founder", "co-founder", "cofounder", "ceo", "building",
                      "creator of", "창업", "대표", "공동창업")
# ★ [2026-07-27] 창업 **역할** 판정용 — STRONG_TITLE_TERMS와 목적이 다르니 섞지 마라.
#   STRONG_TITLE_TERMS는 "창업스러운 말"이라 `building`·`creator of` 같은 자기소개 표현이 들어 있다
#   (헤드라인 스캔용, 94/97 발화 = 사실상 상수). 아래는 **"이 포지션이 창업 포지션인가"**를 가르는
#   것이라 회사 직책명으로만 구성한다. 여기에 `building`을 넣으면 "Building the future @ 대기업"이
#   창업 포지션으로 잡혀 tenure 계산이 통째로 오염된다.
FOUNDER_ROLE_TERMS = ("founder", "co-founder", "cofounder", "ceo", "cto",
                      # 🔴 [2026-07-27] **풀어 쓴 형태를 빠뜨리면 안 된다.** `"ceo" in "chief executive officer"`는
                      #   False다 — 실측에서 `Chief Executive Officer`가 창업 직함으로 안 잡혀,
                      #   그 사람의 tenure를 옆에 있던 파트타임 자문이 다시 가리고 있었다(9명).
                      #   Example Person F 사고와 **같은 계열의 잔여 구멍**이었다.
                      "chief executive", "chief technology",
                      "대표", "창업자", "공동창업자", "창업")
# stealth는 headline이 아니라 **회사명**에 박힌다 — 실측: headline 0/97 발화,
# companyName에 "Stealth", "Newco - still in stealth mode"가 실재.
STEALTH_TERMS = ("stealth", "newco", "스텔스")
# ⚠️ 서울만 넣으면 지방 창업자를 놓친다. 실측: 부산 1명·대전 3명이 kr_location=False였고,
# 그중 하나가 companyName "Newco - still in stealth mode"인 최고 신호였다.
KR_LOCATION_TERMS = (
    "korea", "seoul", "한국", "서울", "대한민국",
    "busan", "부산", "daejeon", "대전", "incheon", "인천", "daegu", "대구",
    "gwangju", "광주", "ulsan", "울산", "sejong", "세종",
    "gyeonggi", "경기", "pangyo", "판교", "seongnam", "성남", "gangnam", "강남",
)


class LinkedInAdapter(Adapter):
    source = "링크드인"

    def collect(self) -> list[Candidate]:
        ch = self.cfg["channels"].get("linkedin", {})
        engines = ch.get("engines", ["profile_search"])

        # post-search 먼저 — 작성자 이름을 프로필 교차가점에 쓴다.
        post_cands: list[Candidate] = []
        post_authors: set[str] = set()
        if "post_search" in engines:
            post_cands = self._collect_posts(ch)
            post_authors = {_normalize(c.founder or c.name) for c in post_cands}

        profile_cands: list[Candidate] = []
        if "profile_search" in engines:
            profile_cands = self._collect_profiles(ch, post_authors)

        return profile_cands + post_cands

    # ---------------- people-search (발굴 본체) ----------------
    def _collect_profiles(self, ch: dict, post_authors: set[str]) -> list[Candidate]:
        pconf = ch.get("profile_search", {})
        specs = self._profile_specs()
        if not specs:
            return []

        exclude = pconf.get("exclude_titles", [])
        base = {
            "profileScraperMode": pconf.get("scraper_mode", "Short"),
            "maxItems": pconf.get("max_items", 100),
        }
        kr_schools = {_normalize(s) for s in pconf.get("kr_schools", [])}
        default_pages = pconf.get("pages", 0)      # 0 = 결과 소진까지 (액터 상한 100페이지 = 2,500명)
        batch_pages = pconf.get("batch_pages", 5)  # 한 런에 몇 페이지를 요청할지. 무시되면 자동 강등

        # ★ 축별 실행 주기 — **스톡 축과 플로우 축을 나눈다.**
        #   링크드인 사람 검색은 매주 거의 같은 사람을 돌려준다(사람이 갑자기 늘지 않는다).
        #   그러니 "이미 Founder인 사람들"(t-founder·t-ceo…)은 **1회 백필이면 끝**이고,
        #   신규 유입은 `recentlyChangedJobs` 축 하나가 전부 잡는다 — 새 창업자는 직함을
        #   Founder로 바꾸는 순간 거기 걸리기 때문이다.
        #   → 매주 900런을 돌리는 대신, 매주는 플로우 축만(699명), 스톡 축은 백필 1회 + 분기 갱신.
        state = self._axis_state(committed_only=True)   # 판단은 **확정본**으로만
        specs = [s for s in specs if self._axis_due(s, state)]
        if not specs:
            print("· linkedin: 이번 실행에 해당하는 축 없음 (전부 백필 완료·주기 미도래)")
            return []

        # URN 단위 병합(여러 쿼리 UNION). 같은 URN이 여러 스펙에서 나오면 신호 누적.
        merged: dict[str, dict] = {}
        for spec in specs:
            recent_pass = bool(spec.get("recentlyChangedJobs"))
            label = spec.get("label", "")
            # ★ **페이지 커서** — 이 축을 지난번에 어디까지 읽었는지 이어서 읽는다.
            #   링크드인 검색은 매주 같은 사람을 같은 순서로 돌려준다. 매번 1페이지부터 소진까지
            #   돌리면 2주차엔 똑같은 2,500명이 나와 seen-store가 전부 걸러낸다 — 헛수고다.
            #   커서를 기억하면 매주 **다음 구간**을 읽어 DB가 자연히 누적된다.
            #     1주차 1~10p → 2주차 11~20p → … → 100p 도달 시 소진, refresh 주기에 되감기
            pages = int(spec.get("pages", default_pages))     # 이번 실행에 읽을 페이지 수
            # 🔴 우리 쪽 노브는 **액터 payload로 새어 나가면 안 된다.** 액터가 모르는 키를 받으면
            #   무시하거나(운 좋으면) 400으로 죽는다. `rewind`를 빠뜨려서 그렇게 죽은 적이 있다.
            spec_payload = {k: v for k, v in spec.items()
                            if k not in ("label", "pages", "cadence", "refresh", "rewind")}
            start = self._axis_cursor(spec, state)
            end = start + pages - 1 if pages else MAX_PAGE    # pages=0 → 소진까지(100p 상한)
            end = min(end, MAX_PAGE)
            got, total, page, exhausted = 0, None, start, False
            failed = False                                    # 액터 호출 자체가 실패했나(403/쿼터/네트워크)
            batch = max(1, int(batch_pages))                  # 적응형: 배치가 무시되면 1로 강등
            while page <= end:
                take = min(batch, end - page + 1)
                payload = dict(base, **spec_payload)
                # 🔴 [2026-07-27] **과거직함 축(pastJobTitles)에는 리크루터 제외를 걸지 않는다.**
                #   이 제외는 "현 직함이 Founder인 사람"을 찾을 때 리크루터가 2,500 천장을 먹는 걸
                #   막으려고 넣은 것이다. 그런데 공백형 축은 **현 직함이 없는 사람**을 찾는다 —
                #   현 직함 기준 필터를 걸면 그들이 통째로 빠질 수 있고, 그러면 "공백형 0명"이 나와
                #   **축이 고장난 것으로 오진**한다(프로브에는 이 필터가 없었으므로 조건도 달라진다).
                #   ⚠️ 실측으로 확인한 게 아니라 **위험을 회피한 것**이다. 나중에 재봐도 좋다.
                if exclude and "excludeCurrentJobTitles" not in payload \
                        and not spec.get("pastJobTitles"):
                    payload["excludeCurrentJobTitles"] = exclude
                payload["startPage"] = page
                payload["takePages"] = take
                try:
                    items = apify.run_actor(PROFILE_ACTOR, payload)
                except Exception as e:                   # 한 페이지 실패가 축 전체를 막지 않음
                    print(f"⚠️  linkedin '{label}' p{page} 실패: {e}")
                    failed = True
                    break
                if not items:
                    exhausted = True                     # 결과 소진 — 다음엔 refresh 주기에 되감는다
                    break
                # ★ **받은 페이지를 검증한다.** takePages를 액터가 조용히 무시하는 버그가 실재했다:
                #   4런 전부 takePages=2를 보냈는데 반환 97/97이 pageNumber==1 이었다.
                #   이 검증이 없어서 "우리는 1페이지만 사고 있다"를 몇 주 동안 몰랐다.
                got_pages = {(p.get("_meta") or {}).get("pagination", {}).get("pageNumber")
                             for p in items}
                want = set(range(page, page + take))
                if take > 1 and got_pages != want:
                    # 배치가 무시됐다 → 페이지당 1런으로 강등하고 같은 페이지부터 재시도.
                    print(f"⚠️  linkedin '{label}': takePages={take} 요청에 {sorted(got_pages)} 수신 "
                          f"— 액터가 무시한다. 페이지당 1런으로 전환.")
                    batch = 1
                    continue
                if take == 1 and got_pages != {page}:
                    print(f"⚠️  linkedin '{label}' p{page}: {sorted(got_pages)} 수신 "
                          f"— 페이지 파라미터가 아예 안 먹힌다. 이 축 중단.")
                    break

                got += len(items)
                total = (items[0].get("_meta") or {}).get("pagination", {}).get("totalElements")
                for p in items:
                    urn = _urn(p)
                    if not urn:
                        continue
                    rec = merged.setdefault(urn, {"raw": p, "recent_job": False, "labels": []})
                    rec["raw"] = p
                    rec["recent_job"] = rec["recent_job"] or recent_pass
                    # 어느 검색축에서 나왔는지 보존 — label을 버리면 "해외 거주 한국인"인지
                    # "방금 이직한 사람"인지 구분이 안 되고 사람이 선별할 근거가 사라진다.
                    if label and label not in rec["labels"]:
                        rec["labels"].append(label)
                page += take
            if page > MAX_PAGE:
                exhausted = True                         # 액터 상한(100p = 2,500명) 도달
            # 커서 저장 — 다음 실행은 여기서 이어 읽는다.
            # 🔴 **실패한 실행은 상태를 쓰지 않는다.** 2026-07-12에 이 한 줄이 없어서:
            #    Apify 403(쿼터 초과)으로 10축 전부 0건이었는데 last_run=오늘이 박혔고,
            #    cadence=weekly라 **결제를 풀어도 7/19까지 링크드인이 안 도는** 상태가 됐다.
            #    수집 사고 하루가 팔로업 주기 일주일을 통째로 태운다.
            # ★ 이 축이 무엇을 데려왔는지 **축 단위로 남긴다.** 공백형 축은 산출이 0이어도
            #   에러가 안 나므로(정상 응답에 공백자가 없을 뿐) 로그가 없으면 조용히 죽는다.
            if got and spec.get("pastJobTitles"):
                gaps = sum(1 for u, r in merged.items()
                           if label in r.get("labels", []) and (_gap_months(r["raw"]) or 0) >= 3)
                print(f"· linkedin '{label}': {got}명 중 **공백 3개월+ {gaps}명**")
                if gaps == 0:
                    print(f"  🔴 HEALTH: 공백형 축인데 공백자가 0명이다 — "
                          f"excludeCurrentJobTitles가 붙었는지, 액터 동작이 바뀌었는지 확인하라.")
            if failed and got == 0:
                print(f"· linkedin '{label}': 액터 실패 — 축 상태 미기록(다음 실행에 재시도)")
            else:
                self._save_axis(label, page, exhausted)
            span = f"p{start}~{page - 1}" if page > start else "0페이지"
            print(f"· linkedin '{label}': {got}건 · {span}"
                  + (" · 소진(다음 refresh에 되감음)" if exhausted else " → 다음은 p%d" % page)
                  + (f" · 검색 총원 {total:,}" if total else ""))

        # (커서는 축별 루프 안에서 이미 저장됨 — _save_axis)

        # ★ seen-store — **영구 제외가 아니라 N개월 후 재수집**(사용자 결정: 3개월).
        #   우리가 찾는 건 "변화"다: 6개월 전엔 회사명이 `Stealth`였는데 지금은 실명이 생겼다면
        #   **그게 가장 이른 신호**다. 한 번 본 사람을 영영 안 보면 그 순간을 놓친다.
        #   → URN별로 마지막으로 본 날짜를 기록하고, 재수집 주기가 지나면 다시 후보로 올린다.
        seen_path = pconf.get("seen_store")
        seen = self._load_seen(seen_path)                    # {urn: {"d": 날짜, "f": 지문}}
        recheck_days = int(pconf.get("recheck_days", 90))
        from datetime import date, timedelta
        cutoff = (date.today() - timedelta(days=recheck_days)).isoformat()

        # ★ [2026-07-17 팔로업] 창(90일) **안**의 재조우도 지문은 비교한다 — 아이템은 seen 체크
        #   전에 이미 샀으므로($) 비교는 공짜다. 예전엔 창 안이면 비교 없이 버려서, rewind 축이
        #   매주 다시 사 오는 "이직/창업이 방금 일어난 사람"을 90일간 못 봤다(회의의 '3개월 뒤
        #   재추적 누락' 딜레마의 실체). 롤백 = config `compare_within_window: false` 한 줄.
        compare_within = bool(pconf.get("compare_within_window", False))
        fresh_urns, recheck, unchanged, changes = [], 0, 0, {}
        within_changed = within_same = within_nofp = rebaselined = 0
        for u, rec in merged.items():
            st = seen.get(_seen_key(u))
            if st is None:
                fresh_urns.append(u)                         # 처음 보는 사람
                continue
            if st.get("d", "") > cutoff:
                if not compare_within:
                    continue                                 # (구동작) 최근에 본 사람 → 무조건 건너뜀
                old_fp = st.get("f", "")
                if not _fp_comparable(old_fp):
                    # 지문이 없거나 **구버전**이면 비교 불가 — 이를 '변화'로 치면 배포 첫 런에 대량 오탐.
                    # 90일 경과 시 기존 경로가 지문을 자연 갱신한다.
                    within_nofp += 1
                    continue
                if old_fp == _fingerprint(rec["raw"]):
                    within_same += 1
                    continue                                 # 창 내 무변화 → pending 제외(90일 주기 보존)
                fresh_urns.append(u)                         # 창 내 **변화** — 즉시 흘려보낸다
                recheck += 1
                within_changed += 1
                changes[u] = old_fp
                continue
            # 재확인 주기 도래 — **바뀌었을 때만** 후보로 올린다.
            # 안 바뀐 사람까지 매번 다시 착지시키면 노션이 중복으로 찬다.
            old_fp, new_fp = st.get("f", ""), _fingerprint(rec["raw"])
            comparable = _fp_comparable(old_fp)
            if comparable and old_fp == new_fp:
                unchanged += 1
                continue
            fresh_urns.append(u)
            if comparable:
                recheck += 1
                changes[u] = old_fp                          # 뭐가 바뀌었는지 보여주기 위해 보관
            else:
                rebaselined += 1     # 구버전 지문 — 재관찰은 하되 '변화'로 **표기하지 않는다**
        dropped = len(merged) - len(fresh_urns) - unchanged
        if not merged:
            # 🔴 정직한 로그. 검색이 0건이면 seen-store는 **판정을 한 적이 없다.**
            #    예전엔 이 경우에도 "기존 0 제외 · 변화 감지 0건"이라 찍혀서 정상처럼 보였다(반복버그 ④).
            print(f"🔴 linkedin: 검색 결과 0건 — seen-store 판정 미수행 "
                  f"(seen에 {len(seen)}명이 있는데 이번에 한 명도 못 받았다 = 액터 실패 의심)")
        else:
            print(f"· linkedin seen-store: 기존 {dropped} 제외 · "
                  f"{recheck_days}일 경과 중 무변화 {unchanged} 제외 · **변화 감지 {recheck}건 포함**"
                  + (f" · 구버전 지문 재기준선 {rebaselined}건(변화 아님)" if rebaselined else ""))
            if compare_within:
                print(f"  · 창 내 비교(팔로업): 변화 {within_changed} · 무변화 {within_same}"
                      f" · 지문없음(구형) {within_nofp}")

        out: list[Candidate] = []
        for urn in fresh_urns:
            rec = merged[urn]
            c = self._profile_to_candidate(urn, rec, kr_schools, post_authors)
            if urn in changes:
                # ★ 변화 감지 — 이미 노션에 있는 사람이므로 **dedup을 통과시켜야** 한다.
                #   (dedup.filter_new는 LinkedIn URL이 주간 DB에 있으면 무조건 버린다)
                #   "Stealth → 실제 회사명"처럼 프로필이 바뀌는 순간이 가장 이른 신호다.
                c.signals["profile_changed"] = True
                c.signals["changed_from"] = changes[urn][:120]
                # ★ 사람이 읽을 요약 — 노션 `변화 요약` 칸으로 간다(지문 원문이 아니라).
                c.signals["change_summary"] = _change_summary(
                    changes[urn], _fingerprint(rec["raw"]))
                c.signals["match_reason"] = (
                    f"[변화 감지] {changes[urn][:60]} → {_fingerprint(rec['raw'])[:60]}")
            out.append(c)
        if seen_path:
            # [2단계 커밋] collect 시점엔 pending만 기록. land 성공 후 commit_seen()이 병합.
            # collect 시점 커밋은 land 실패 시 이 URN들이 '이미 본 것'이 되어 영구 유실됐다.
            # 지문도 함께 넘긴다 — 다음 재확인 때 "뭐가 바뀌었나"를 비교하려면 필요하다.
            # 키는 **정규화 URN**으로 쓴다 — 모드가 바뀌어도 같은 사람이 같은 칸에 들어간다.
            pend = {_seen_key(u): _fingerprint(merged[u]["raw"]) for u in fresh_urns}
            (PROJ / (seen_path + ".pending")).parent.mkdir(parents=True, exist_ok=True)
            (PROJ / (seen_path + ".pending")).write_text(
                json.dumps(pend, ensure_ascii=False), encoding="utf-8")
        # ★ "외국인 빼고 **한국인 먼저**" (요구 ver3) — 지금까지 score를 계산만 하고 **아무도 안 읽었다**.
        #   하드컷은 하지 않는다(수집에서 자르지 않는 게 이 스킬의 원칙이고, 위치가 빈 프로필도 있다).
        #   대신 **정렬**한다: 착지 순서가 곧 사람이 노션에서 위에서부터 읽는 순서다.
        #   kr_location/kr_school/stealth/fresh_tenure가 높은 사람이 위로 온다.
        out.sort(key=lambda c: c.signals.get("score", 0), reverse=True)
        kr = sum(1 for c in out if c.signals.get("korea_signal"))
        st = sum(1 for c in out if c.signals.get("stealth"))
        print(f"✅ linkedin profile_search: {len(out)}건(신규 URN) · 쿼리 {len(specs)}개 "
              f"· 한국 신호 {kr}명 · Stealth {st}명 (score 내림차순 정렬 — 위에서부터 보면 된다)")
        return out

    # ---------------- 축별 실행 주기 (스톡/플로우 분리) ----------------
    AXIS_STATE = PROJ / "data" / "linkedin_axis_state.json"

    @classmethod
    def _axis_state(cls, committed_only: bool = False) -> dict:
        """축 상태 로드.

        🔴 [2026-07-13] **읽기 목적을 분리해야 한다.** 예전엔 항상 pending을 먼저 읽었는데,
           그러면 2단계 커밋이 **통째로 무의미해진다**:
             착지 실패 → pending은 남아 있음 → 다음 실행이 pending을 읽음 → 커서가 이미
             전진한 것으로 판단 → **돈 주고 산 그 페이지들을 영영 다시 안 산다.**
           (커밋되지 않은 커서는 "없던 일"이어야 한다. 재수집이 유실보다 항상 낫다.)

        - `committed_only=True`  → **판단용**(이 축을 돌아야 하나 / 몇 페이지부터). 확정본만 본다.
        - `committed_only=False` → **누적용**(_save_axis가 축별로 pending에 덧쓴다). pending 우선.
        """
        paths = ([cls.AXIS_STATE] if committed_only
                 else [cls.AXIS_STATE.with_suffix(".json.pending"), cls.AXIS_STATE])
        for p in paths:
            if p.exists():
                try:
                    return json.loads(p.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    return {}
        return {}

    @staticmethod
    def _axis_due(spec: dict, state: dict) -> bool:
        """이 축을 이번 실행에 돌려야 하나.

        세 개의 노브가 있다 — 헷갈리지 말 것:
          `cadence` = **최소 실행 간격.** 파이프라인이 매일 돌아도 이 축은 N일에 한 번만 돈다.
          `rewind`  = **매 실행 p1부터 다시 읽는다** (플로우 축). 커서를 쓰지 않는다.
          `refresh` = **소진 후** 되감기 주기. 커서 축에만 의미가 있다.

        🔴 [2026-07-14] **`refresh: weekly`가 영영 안 걸리는 버그가 있었다.**
           `recent-job-kr`은 "매주 되감아 새 사람을 잡는다"는 의도로 만든 축인데, 되감기는
           `exhausted`일 때만 발동한다. 모수 699명(28p)을 주 1페이지씩 읽으면 소진까지 **28주** —
           즉 되감기가 **6.5개월에 한 번** 일어났다. 이번 주에 직함을 Founder로 바꾼 사람이
           이미 읽고 지나간 페이지에 들어가면 **반년 동안 안 보인다.**
           "실시간 신호"가 존재 이유인 축이 구조적으로 실시간을 못 잡고 있었다.
           → `rewind: true`를 도입했다. 이 축은 **소진 개념 자체가 없다.**
        """
        from datetime import date, timedelta

        from core import cadence as cad          # 주기 어휘 = core/cadence.py 가 **정본**
        label = spec.get("label", "")
        st = state.get(label) or {}
        if not st:
            return True                                   # 한 번도 안 돈 축
        last = st.get("last_run", "")
        try:
            last_d = date.fromisoformat(last)
        except ValueError:
            return True

        # ① 최소 실행 간격 — rewind 축도 이건 지킨다(매일 돌면 매일 과금된다)
        if not cad.interval_elapsed(spec.get("cadence"), last,
                                    where=f"linkedin:{label}"):
            return False                                  # 아직 간격이 안 찼다

        # ② rewind 축 = 항상 돈다. exhausted/refresh를 **보지 않는다.**
        #    (안 그러면 빈 페이지 한 번에 exhausted=True가 박히고, refresh 기본값 quarterly에
        #     걸려 **플로우 축이 90일간 죽는다** — 고치려던 버그가 그대로 재현된다.)
        if spec.get("rewind"):
            return True

        if not st.get("exhausted"):
            return True                                   # 읽을 페이지가 남았다 → 이어서 읽는다

        # ③ 소진됨 — 되감기 주기가 왔나 (새 유입이 있으니 처음부터 다시 훑는다)
        return cad.interval_elapsed(spec.get("refresh"), last, default="quarterly",
                                    where=f"linkedin:{label}.refresh")

    def _axis_cursor(self, spec: dict, state: dict) -> int:
        """이 축을 몇 페이지부터 읽을 것인가.

        `rewind: true` → **항상 1페이지.** 매 실행 같은 구간을 다시 읽는다.
          낭비처럼 보이지만 아니다: 이미 본 사람은 seen-store가 거르므로 **착지는 신규 유입만**이다.
          모수가 작은 플로우 축(예: 237명 = 10p)에서만 쓴다. 큰 축에 쓰면 커서가 무의미해진다.
        """
        st = state.get(spec.get("label", "")) or {}
        if spec.get("rewind"):
            return 1
        if st.get("exhausted"):
            return 1                                      # 재탐색 — 처음부터
        return max(1, int(st.get("next_page", 1)))

    def _save_axis(self, label: str, next_page: int, exhausted: bool) -> None:
        """[1단계] 페이지 커서를 **.pending 으로만** 기록. 확정은 land 성공 후 commit_seen().

        커서를 collect 시점에 확정하면, land가 실패한 날 **돈 주고 산 페이지가 그냥 소비된다**
        (커서만 전진하고 사람은 노션에 없다). 소진 후 refresh 되감기까지 그 페이지는 안 돌아온다.
        """
        from datetime import date
        state = self._axis_state()
        state[label] = {"last_run": date.today().isoformat(),
                        "next_page": next_page, "exhausted": exhausted}
        self.AXIS_STATE.parent.mkdir(parents=True, exist_ok=True)
        pend = self.AXIS_STATE.with_suffix(".json.pending")
        pend.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _profile_specs(self) -> list[dict]:
        kw = self.keywords.get("linkedin", {})
        if isinstance(kw, dict):
            return kw.get("profile_search", []) or []
        return []

    def _profile_to_candidate(self, urn: str, rec: dict, kr_schools: set[str],
                              post_authors: set[str]) -> Candidate:
        p = rec["raw"]
        name = _text(_first(p, "name", "fullName")) or _join_name(p)
        # ⚠️ 액터(harvestapi/linkedin-profile-search Short)는 headline/occupation/position 키를
        # 주지 않는다 — 실측 0/97. 실제로는 summary(89/97)와 currentPositions[](97/97)에 들어온다.
        # 구 키를 먼저 보되 없으면 실 페이로드로 폴백. 이 폴백이 없으면 headline이 전부 빈다.
        headline = (_text(_first(p, "headline", "occupation", "position", "summary"))
                    or _position_title(p))
        location = _text(_first(p, "location", "locationName", "geoLocationName"))
        current_co = _current_company(p)
        schools = _schools(p)

        # ★ 소개 전문(About) — Full 응답에 이미 딸려 오는데 버리고 있었다(실측 162자 보유).
        #   headline 한 줄만으론 judge가 극초기 창업자를 못 가른다. one_liner는 headline 유지,
        #   raw_text·signals에만 실어 판단 입력을 넓힌다(비용 0 — 이미 값 치르고 받은 필드).
        #   실측 키는 `about`(Full). Short 모드에선 summary가 headline 폴백으로 이미 쓰이므로
        #   중복이면 버린다.
        about = _text(_first(p, "about", "summary", "bio", "biography")).replace("\n", " ").strip()
        if about == headline:
            about = ""

        h = headline.lower()
        loc = location.lower()
        # 직함은 **전 포지션**을 스캔한다. currentPositions[0]만 보면 "대기업 재직 중 창업"
        # ([0]=삼성, [1]=Founder)을 놓친다 — 97명 중 12명이 동시 포지션 보유.
        titles = _position_title(p).lower()
        is_kr = any(t in loc for t in KR_LOCATION_TERMS)
        has_strong = any(t in h or t in titles for t in STRONG_TITLE_TERMS)
        # stealth는 headline이 아니라 **회사명**에 있다(실측). "Newco - still in stealth mode"
        is_stealth = any(t in current_co.lower() or t in h for t in STEALTH_TERMS)
        has_kr_school = bool(kr_schools & {_normalize(s) for s in schools})

        score = 0
        reasons: list[str] = []

        def add(cond, w, tag):
            nonlocal score
            if cond:
                score += w
                reasons.append(f"{tag}{'+' if w >= 0 else ''}{w}")

        tenure = _tenure_months(p)
        is_fresh = tenure is not None and tenure < FRESH_TENURE_MONTHS

        add(rec["recent_job"], W_RECENT_JOB, "recent_job")
        add(is_stealth, W_STEALTH, "stealth")
        add(is_kr, W_KR_LOCATION, "kr_location")
        add(is_fresh, W_FRESH_TENURE, "fresh_tenure")

        # ★ 점수에는 안 넣지만 **신호로는 반드시 남긴다.** 지우면 안 되는 이유:
        #   kr_school — `schools` 서버측 필터가 실제로 작동했는지 **검증할 유일한 수단**이다.
        #     kr-tie-global 축 결과의 다수가 비한국인이라 필터 무시가 의심되는데,
        #     Full 모드로 바꾼 이유가 바로 education을 받아서 이걸 확인하려는 것이다.
        #     계산하고 버리면 Full로 바꾼 의미가 없어진다.
        #   strong_title — 94/97(97%) 발화라 점수로는 무의미하지만(상수), 판별 에이전트가
        #     "직함은 정보량이 없다"를 확인하는 데 쓴다.
        #   also_posted — 이 사람이 게시글 엔진에도 잡혔다 = 최근에 뭔가 말했다(엔진 교차).
        also_posted = bool(post_authors) and _normalize(name) in post_authors

        prof_url = _profile_url(p, urn)
        return Candidate(
            source=self.source,
            name=name,
            url=prof_url,
            founder=name,
            one_liner=headline,
            raw_text=" · ".join(x for x in (headline, current_co, location, about) if x),
            ext_id=urn,
            linkedin_url=prof_url,               # [C4] go-forward dedup 키
            adapter="linkedin.harvestapi",       # [C9] provenance
            homepage=current_co if current_co.startswith("http") else "",
            signals={
                "engine": "profile_search",
                "channel": "LinkedIn",
                "label": rec.get("labels") or [],     # 어느 검색축에서 나왔나(복수 가능)
                "match_reason": " · ".join(rec.get("labels") or []),
                "headline": headline,
                "about": about,                       # ★ 소개 전문 — judge 입력(Full 응답에서 살림)
                "location": location,
                "current_company": current_co,
                # ★ 재직기간 — 판단 에이전트의 1순위 입력. "극초기"를 재는 유일한 하드 팩트다.
                #   전체 중앙값 34개월 / 12개월 미만은 10%뿐 → 이 값 없이는 스톡과 신규를 못 가른다.
                "tenure_months": tenure,
                "fresh_tenure": is_fresh,
                # ★ [2026-07-27] tenure의 짝. "지금 창업이 몇 개월"(tenure) vs "창업자로 산 기간".
                #   둘이 크게 벌어지면 연쇄창업자다. 컷은 judge가 한다(어댑터는 판단 금지).
                "founder_since_months": _founder_since_months(p),
                "present_roles": len(_positions(p)),
                # ★ 시제 구분 — "과거에 스텔스였다"(=이미 공개했다)와 "지금 스텔스다"는 정반대 신호다.
                "past_stealth": _past_stealth(p),
                # ★ 회사 단계의 유일한 직접 증거. payload에 회사 나이·투자단계가 없어서
                #   지금은 tenure로 대리 추정 중인데, 여기엔 답이 문장으로 있을 때가 있다.
                "featured": _featured_titles(p),
                # ★ 경력 전문 — 노션 **페이지 본문**으로 간다(2,000자를 넘는 사람이 절반이다).
                "career": _career_timeline(p),
                # ★ 공백기(사수 요구사항) — 0=공백없음 · N=N개월 공백 · 빈칸=판정불가.
                "gap_months": _gap_months(p),
                # ★ 부업(시간제·인턴)만 걸어둔 사람의 실질 공백. 0과 뜻이 다르다.
                "fulltime_gap_months": _fulltime_gap_months(p),
                "company_url": _current_company_url(p),
                "schools": schools,
                "kr_school": has_kr_school,           # ★ schools 서버측 필터 작동 검증용
                "strong_title": has_strong,           # 참고(94/97 발화 = 상수)
                "also_posted": also_posted,           # 게시글 엔진에도 잡혔나(엔진 교차)
                "score": score,
                "score_reasons": reasons,
                # ★ 아래 둘은 **계산해놓고 노션에 안 싣던 값**이다(반복버그 ②: 계산하고 버림).
                #   요구사항이 "외국인 빼고 한국인 먼저" · "Stealth 창업자 리스트 3~4개월 팔로업"인데,
                #   정작 노션에서 한국인으로 필터하거나 Stealth만 뽑을 방법이 없었다.
                "korea_signal": [t for t, on in (("KR-거주", is_kr), ("KR-학교", has_kr_school)) if on],
                "stealth": is_stealth,
            },
        )

    # ================= post-search: 새 글을 창업 신호로 =================
    # 두 경로다. 성격이 완전히 다르니 섞지 말 것.
    #   ① 발굴(discovery)  — 키워드로 새 글을 찾고, **작성자 위치로 한국을 걸러낸다.**
    #      게시글 검색 API에는 지역 파라미터가 없지만(24개 전수 확인), 결과에 작성자 프로필이
    #      딸려 오므로 받아온 뒤에 거르면 된다. 영어 쿼리를 넣는 이유가 여기 있다 —
    #      야심 있는 한국 창업자는 창업 공지를 영어로 쓴다.
    #   ② 모니터링(monitoring) — **누적 DB의 창업자들이 최근에 쓴 글.** 발굴이 아니라 타이밍이다.
    #      "레이더에 있는 수천 명 중 누가 지금 움직이는가." DB가 커질수록 강해진다.
    WATCHLIST = PROJ / "data" / "linkedin_watchlist.json"

    def _collect_posts(self, ch: dict) -> list[Candidate]:
        pconf = ch.get("post_search", {})
        out: list[Candidate] = []
        if pconf.get("discovery", {}).get("enabled", True):
            out += self._posts_discovery(pconf.get("discovery", {}))
        if pconf.get("monitoring", {}).get("enabled", True):
            out += self._posts_monitoring(pconf.get("monitoring", {}))
        return out

    def _posts_discovery(self, conf: dict) -> list[Candidate]:
        kw = self.keywords.get("linkedin", {})
        queries = (kw.get("post_search") or []) if isinstance(kw, dict) else []
        if not queries:
            return []
        payload = {
            "searchQueries": queries,
            "postedLimit": conf.get("posted_limit", "week"),
            "sortBy": "date",
            "maxPosts": conf.get("max_posts", 200),
            "scrapePages": conf.get("pages", 5),
            # ★ 작성자 프로필까지 받아야 위치로 거를 수 있다. 이게 이 경로의 전제다.
            "profileScraperMode": "main",
        }
        try:
            items = apify.run_actor(POST_ACTOR, payload)
        except Exception as e:
            print(f"⚠️  linkedin post 발굴 실패: {e}")
            return []
        if not items:
            print("⚠️  linkedin post 발굴: 0건 — 지난 실행에서도 한국어 쿼리가 전부 0이었다. "
                  "액터 고장인지 LinkedIn 한국어 색인 부재인지 프로브로 규명 필요.")
            return []
        # ★ 작성자 위치를 **별도 조회**해서 한국만 남긴다 (사용자 결정: 경로 a).
        #   게시글 검색에는 지역 파라미터가 없고, 출력 author 객체에도 location이 없다.
        #   → 작성자 URL을 모아 profile-scraper로 위치를 따로 긁는다. 확실히 작동한다.
        #     (profile-scraper: urls/publicIdentifiers 입력 → location·education 출력. 스키마 확인함)
        locs = self._author_locations(items)
        kept, dropped, unknown = [], 0, 0
        for p in items:
            key = ((p.get("author") or {}).get("publicIdentifier") or "").lower()
            loc = locs.get(key)
            if loc is None:
                unknown += 1               # 위치 조회 실패 → **살린다**(recall 우선)
            elif not any(t in loc.lower() for t in KR_LOCATION_TERMS):
                dropped += 1
                continue
            kept.append(self._post_to_candidate(p))
        print(f"✅ linkedin post 발굴: {len(kept)}건 "
              f"(수집 {len(items)} · 비한국 {dropped} 제외 · 위치불명 {unknown} 통과)")
        return kept

    def _author_locations(self, posts: list[dict]) -> dict[str, str]:
        """게시글 작성자들의 **위치를 profile-scraper로 조회**한다. {publicIdentifier: location}.

        조회 실패는 빈 dict — 호출부가 "위치 불명 → 통과"로 처리한다(잘못 버리면 영구 소실).
        """
        ids = []
        for p in posts:
            pid = (p.get("author") or {}).get("publicIdentifier")
            if pid and pid not in ids:
                ids.append(pid)
        if not ids:
            return {}
        out: dict[str, str] = {}
        for i in range(0, len(ids), 100):          # 100명씩 배치
            try:
                rows = apify.run_actor(SCRAPER_ACTOR, {
                    "publicIdentifiers": ids[i:i + 100],
                    "profileScraperMode": "Profile details no email ($4 per 1k)",
                })
            except Exception as e:
                print(f"⚠️  작성자 위치 조회 실패: {e} — 이 배치는 전량 통과")
                continue
            for r in rows:
                pid = (r.get("publicIdentifier") or "").lower()
                loc = r.get("location")
                if isinstance(loc, dict):          # {linkedinText: "Seoul, South Korea", ...}
                    loc = loc.get("linkedinText") or loc.get("text") or ""
                if pid:
                    out[pid] = str(loc or "")
        print(f"· 작성자 위치 조회: {len(out)}/{len(ids)}명")
        return out

    def _posts_monitoring(self, conf: dict) -> list[Candidate]:
        """누적 DB의 창업자들이 최근 쓴 글. 커서로 매 실행 다른 사람을 본다."""
        watch = self._load_watchlist()
        if not watch:
            print("· linkedin post 모니터링: 워치리스트 비어있음 (프로필이 착지되면 자동으로 찬다)")
            return []
        batch = int(conf.get("batch", 100))
        state = self._axis_state()
        cur = int((state.get("_watch") or {}).get("next_page", 0))
        if cur >= len(watch):
            cur = 0                                          # 한 바퀴 돌았다 → 처음부터
        chunk = watch[cur:cur + batch]
        payload = {
            "authorUrls": chunk,
            "postedLimit": conf.get("posted_limit", "week"),
            "sortBy": "date",
            "maxPosts": conf.get("max_posts", 20),
            "profileScraperMode": "short",
        }
        try:
            items = apify.run_actor(POST_ACTOR, payload)
        except Exception as e:
            print(f"⚠️  linkedin post 모니터링 실패: {e}")
            return []
        self._save_axis("_watch", cur + batch, False)        # 커서 전진
        print(f"✅ linkedin post 모니터링: {len(items)}건 "
              f"(워치리스트 {len(watch)}명 중 {cur}~{cur + len(chunk)}번째)")
        return [self._post_to_candidate(p) for p in items]

    def _load_watchlist(self) -> list[str]:
        if self.WATCHLIST.exists():
            try:
                return json.loads(self.WATCHLIST.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return []
        return []

    def _post_to_candidate(self, p: dict) -> Candidate:
        """게시글 → Candidate.

        ⚠️ 필드명은 **액터 README의 실제 출력 예시에서 확인**한 것이다. 추측하지 말 것 —
        구 코드는 postUrl/postedDate/position/likesCount를 찾고 있었고 **전부 존재하지 않는 키**였다.
        (`headline` 버그와 같은 부류. postedAt은 문자열이 아니라 **dict**다.)

        실제 스키마:
          linkedinUrl(게시글 URL) · content(본문) · postedAt{date, timestamp, postedAgoText}
          author{name, linkedinUrl(**vanity URL**), publicIdentifier, info(=headline), website}
          engagement{likes, comments, shares}
        """
        author = p.get("author") or {}
        name = _text(author.get("name"))
        posted = p.get("postedAt") or {}
        eng = p.get("engagement") or {}
        return Candidate(
            source=self.source,
            name=name,
            url=p.get("linkedinUrl", ""),                 # 게시글 URL
            founder=name,
            released_at=(posted.get("date") or "") if isinstance(posted, dict) else str(posted),
            raw_text=p.get("content", ""),
            # ★ 게시글의 author.linkedinUrl은 **vanity URL**(/in/nickbennett05)이다.
            #   프로필 검색은 URN URL(/in/ACwAAA...)을 준다 — **둘은 서로 매칭되지 않는다.**
            #   즉 게시글 후보와 프로필 후보를 URL로 dedup할 수 없다. 이름 퍼지 매칭에 의존한다.
            linkedin_url=author.get("linkedinUrl", ""),
            homepage=author.get("website") or "",
            adapter="linkedin.harvestapi",
            signals={
                "engine": "post_search",
                "channel": "LinkedIn",
                "headline": author.get("info") or "",     # 작성자 headline (position/headline 아님)
                "author_username": author.get("publicIdentifier") or "",
                "likes": eng.get("likes"),
                "comments": eng.get("comments"),
                "shares": eng.get("shares"),
                "posted_ago": posted.get("postedAgoText") if isinstance(posted, dict) else "",
                "sourcing_method": "keyword_search",
                "search_mode": "KEYWORD",
                "search_type": "RECENT",
            },
        )

    # ---------------- seen-store (주간 URN diff) ----------------
    def _load_seen(self, rel: str | None) -> dict[str, dict]:
        """{urn: {"d": 마지막 본 날짜, "f": 프로필 지문}}.

        구 형식(URN 리스트)도 읽는다 — 날짜/지문이 비면 '아주 오래 전, 지문 없음'으로 취급돼
        재확인 대상이 되고, 지문이 없으면 무조건 변화로 본다(안전한 방향: 놓치느니 한 번 더 본다).
        """
        if not rel:
            return {}
        path = PROJ / rel
        if not path.exists():
            return {}
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if isinstance(d, list):                  # 구 형식(리스트) 호환
            return {u: {"d": "", "f": ""} for u in d}
        if not isinstance(d, dict):
            return {}
        # 구 형식(문자열 날짜)도 호환
        raw = {u: (v if isinstance(v, dict) else {"d": str(v), "f": ""}) for u, v in d.items()}
        return _fold_seen(raw)


def commit_seen(cfg: dict) -> None:
    """[2단계 커밋] land 성공 후 호출 — pending URN을 seen-store에 병합하고 pending 삭제.

    pending이 없으면 no-op. collect만 하고 land를 안 한 경우 pending이 남아
    다음 collect에서 같은 후보가 다시 신규로 잡힌다(의도된 동작 — 유실 방지).
    """
    # [2단계 커밋 ①] 페이지 커서 확정 — collect 때 .pending 으로만 써 뒀다.
    axis_final = LinkedInAdapter.AXIS_STATE
    axis_pend = axis_final.with_suffix(".json.pending")
    if axis_pend.exists():
        axis_pend.replace(axis_final)
        print("· linkedin 페이지 커서 확정")

    # [2단계 커밋 ②] seen-store
    rel = (cfg.get("channels", {}).get("linkedin", {})
           .get("profile_search", {}).get("seen_store"))
    if not rel:
        return
    seen_path = PROJ / rel
    pend_path = PROJ / (rel + ".pending")
    if not pend_path.exists():
        return
    def _load(p):
        try:
            return set(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return set()
    from datetime import date
    # pending = {urn: 지문}. seen-store = {urn: {"d": 날짜, "f": 지문}}.
    # 오늘 본 URN의 날짜와 지문을 갱신한다 — 이래야 recheck_days 뒤에 **변화를 비교**할 수 있다.
    try:
        pending = json.loads(pend_path.read_text(encoding="utf-8"))
    except Exception:
        pending = {}
    if isinstance(pending, list):                       # 구 형식(URN 리스트) 호환
        pending = {u: "" for u in pending}
    try:
        committed = json.loads(seen_path.read_text(encoding="utf-8")) if seen_path.exists() else {}
    except Exception:
        committed = {}
    if isinstance(committed, list):                     # 구 형식 마이그레이션
        committed = {u: {"d": "", "f": ""} for u in committed}
    # ★ [2026-07-27] 병합 전에 **정규화해서 접는다.** 안 하면 같은 사람이 `ACoAA…`/`ACwAA…`
    #   두 칸을 차지하고, 재확인 주기·지문 비교가 사람 단위로 안 돌아간다(실측 85쌍).
    committed = _fold_seen(committed)
    today = date.today().isoformat()
    for u, fp in pending.items():
        committed[_seen_key(u)] = {"d": today, "f": fp}
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    seen_path.write_text(json.dumps(committed, ensure_ascii=False, sort_keys=True),
                         encoding="utf-8")
    pend_path.unlink()

    # ★ 워치리스트 갱신 — **고밀도 축에서 나온 사람만** 감시 대상에 추가한다.
    #   post_search 모니터링이 이 명단의 최근 글을 본다("누가 지금 움직이는가").
    #   ⚠️ 전원을 넣으면 2,000명 ÷ 배치 = 한 바퀴에 수십 주가 걸려 실시간성이 사라진다.
    #      명성 자석 축(t-founder 재직 중앙값 6.2년)에서 나온 사람을 감시해봐야 소득이 없다.
    watch_axes = set((cfg.get("channels", {}).get("linkedin", {}).get("post_search", {})
                      .get("monitoring", {}).get("watch_axes")) or [])
    wl_path = PROJ / "data" / "linkedin_watchlist.json"
    try:
        watch = json.loads(wl_path.read_text(encoding="utf-8")) if wl_path.exists() else []
    except json.JSONDecodeError:
        watch = []
    have, added = set(watch), 0
    for f in sorted((PROJ / "data" / "raw").glob("linkedin_harvestapi_*.json"))[-1:]:
        for c in json.loads(f.read_text(encoding="utf-8")):
            s = c.get("signals") or {}
            if s.get("engine") != "profile_search":
                continue
            if watch_axes and not (watch_axes & set(s.get("label") or [])):
                continue                       # 고밀도 축에서 나온 사람이 아니다 → 감시 안 함
            u = c.get("linkedin_url") or ""
            if u and u not in have:
                watch.append(u)
                have.add(u)
                added += 1
    wl_path.write_text(json.dumps(watch, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"· linkedin 워치리스트: {len(watch)}명 (+{added}) "
          f"— 감시 축 {sorted(watch_axes) or '전체'}")


# ---------------- 방어적 필드 추출(액터 출력 스키마 편차 흡수) ----------------
def _first(d: dict, *keys):
    for k in keys:
        v = d.get(k)
        if v:
            return v
    return None


def _text(v) -> str:
    """액터가 문자열 대신 객체/리스트로 주는 필드(HarvestAPI location 등)를 문자열로 코어싱.
    이 코어싱이 없으면 headline/location 이 dict일 때 .lower()에서 크래시난다."""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        for k in ("linkedinText", "text", "name", "fullName", "title", "displayName", "value"):
            val = v.get(k)
            if isinstance(val, str) and val:
                return val
        for val in v.values():                     # 폴백: 첫 비어있지 않은 문자열 값
            if isinstance(val, str) and val:
                return val
        return ""
    if isinstance(v, list):
        return _text(v[0]) if v else ""
    return str(v) if v is not None else ""


def _join_name(p: dict) -> str:
    return " ".join(x for x in (p.get("firstName"), p.get("lastName")) if x).strip()


def _seen_key(urn: str) -> str:
    """seen-store의 사람 키. 링크드인 URN은 **모드마다 달라지므로 정규화**한다.

    🔴 [2026-07-27 실측] `data/linkedin_seen.json` 344 엔트리 = `ACoAA` 247 + `ACwAA` 97인데,
       정규화하면 **85쌍이 같은 사람**이었다(실제 ~259명). 접지 않으면:
         · 같은 사람이 두 칸을 차지해 recheck 주기가 사람 단위로 안 돌고
         · 노션에도 두 번 착지한다(실제로 84행이 그렇게 생겼다).
       비용 손해는 없다 — seen-store는 돈을 낸 **뒤** 거르는 장치다.
    """
    from adapters.base import canonical_linkedin_urn
    return canonical_linkedin_urn(urn) or urn


def _fold_seen(d: dict) -> dict:
    """seen-store를 정규화 키로 접는다. 충돌하면 **날짜가 최신인 쪽**을 남기고,
    지문이 있는 쪽을 우선한다(지문이 없으면 비교 불가 처리되어 재기준선을 잡게 된다)."""
    out: dict[str, dict] = {}
    for u, v in d.items():
        k = _seen_key(u)
        cur = out.get(k)
        if cur is None:
            out[k] = v
            continue
        if (v.get("d") or "") > (cur.get("d") or "") or (v.get("f") and not cur.get("f")):
            out[k] = v
    return out


def _featured_titles(p: dict) -> str:
    """`featured`(대표 콘텐츠)의 제목들을 이어붙인다. **이미 산 필드인데 버리고 있었다.**

    🔴 [2026-07-27] 왜 중요한가 — payload 어디에도 **회사의 나이·투자단계가 없다**(키 50개 전수).
       그래서 "이 사람 회사가 이미 자리를 잡았나"를 tenure로 **대리 추정**하고 있는데,
       `featured`에는 그 답이 문장으로 들어 있는 경우가 있다(실측 287명 중 10명, 3.5%):
         「셀론, AI '기억 레이어'로 승부…프라이머·더벤처스서 시드 투자 유치」
         「피직스에이아이, 서울대기술지주 시드 투자 유치 - 로봇신문」
         「매쉬업벤처스, AI 컨설팅사 '오르빗코리아'에 프리시드 투자」

    ⚠️ **정규식으로 '투자받음' 플래그를 만들지 않는다.** 본인이 올린 하이라이트라 회수율이 낮아
       (투자받은 사람이 전부 올리지는 않는다) 빈 값이 "투자 안 받음"으로 읽히는 순간 거짓이 된다.
       → **텍스트만 싣고 판단은 judge가 한다.** 있으면 사실, 없으면 **미확인**이지 부정이 아니다.
    """
    f = p.get("featured")
    if not isinstance(f, dict):
        return ""
    out: list[str] = []
    for sl in (f.get("slides") or []):
        if not isinstance(sl, dict):
            continue
        t = str(sl.get("title") or "").strip()
        sub = str(sl.get("subtitle") or "").strip()
        one = " · ".join(x for x in (t, sub) if x)
        if one and one not in out:
            out.append(one)
    for k in ("title", "subtitle"):
        v = str(f.get(k) or "").strip()
        if v and v not in out:
            out.append(v)
    return " / ".join(out)[:1800]          # 노션 rich_text 한 블록 상한(2000) 안쪽


def _career_timeline(p: dict) -> str:
    """경력 + 학력을 **원본 그대로** 한 덩어리 텍스트로. 판단 에이전트에게 넘길 본문이다.

    🔴 [2026-07-27] 왜 필요한가 — 지금까지 우리는 경력으로 **숫자만 계산하고 원본을 버렸다**
       (tenure·창업개월). 그런데 판단에 필요한 건 숫자가 아니라 **이야기**다:

         Advisory Committee @ 행정안전부 | Mar 2026~Present | Part-time
         Cofounder & CTO @ AIM Intelligence | May 2024~Present | Full-time
           The most frontier AI safety startup you will see
         Founder @ Stealth Startup | Feb 2024~Aug 2024
           - #12 appstore/playstore app - 55 million+ views

       이 열 줄이면 tenure 숫자 하나로 씨름할 이유가 없다. Example Person F 사고도 여기선 안 난다 —
       **`Part-time`이 눈에 보이기 때문**이다.

    ⚠️ **설명을 자르지 않는다**(사용자 결정 2026-07-27). 요약형(중앙 808자)이 아니라 전문
       (중앙 1,808자·최대 12,570자)을 넘긴다. 여기서 자르면 그게 '조용한 삭제'다.
       → 2,000자를 넘는 사람이 136/287이라 **노션 칸에는 안 들어간다. 페이지 본문에 쌓는다.**
    """
    lines: list[str] = []
    for e in (p.get("experience") or []):
        if not isinstance(e, dict):
            continue
        sd = (e.get("startDate") or {}).get("text") or "?"
        ed = (e.get("endDate") or {}).get("text") or "?"
        bits = [f"{e.get('position') or '?'} @ {e.get('companyName') or '?'}", f"{sd}~{ed}"]
        # ★ 고용형태 — Example Person F을 가른 바로 그 정보. 시간제 자문이 창업을 가리는 걸 사람이 즉시 본다.
        for k in ("employmentType", "workplaceType", "location"):
            if e.get(k):
                bits.append(str(e[k]))
        lines.append(" | ".join(bits))
        desc = " ".join(str(e.get("description") or "").split())
        if desc:
            lines.append("  " + desc)
    for ed_ in (p.get("education") or []):
        if not isinstance(ed_, dict):
            continue
        tail = " ".join(x for x in (ed_.get("degree"), ed_.get("fieldOfStudy")) if x)
        lines.append(f"[학력] {ed_.get('schoolName') or '?'}" + (f" | {tail}" if tail else ""))
        d = " ".join(str(ed_.get("description") or "").split())
        if d:
            lines.append("  " + d)
    return "\n".join(lines)


def _current_company_url(p: dict) -> str:
    """현재 회사의 LinkedIn URL. **판단 에이전트가 회사를 조회할 유일한 열쇠**다(실측 287/287 보유).

    payload에 회사의 나이·투자단계가 없으므로, 회사 자체를 보려면 이 URL이 있어야 한다.
    창업 포지션을 우선한다(`_positions`가 이미 창업 직함을 앞으로 정렬해 둔다).
    """
    for e in _positions(p):
        u = e.get("companyLinkedinUrl")
        if u:
            return str(u)
    return ""


# 🔴 **부업으로 보는 고용형태는 여기까지다(S1).** 넓히지 마라 —
#   `Contract`를 넣었다가는 `Deputy Site Manager(LNG) · Contract`처럼 **명백한 본업**을 무직으로
#   찍는다(실측: S1 2명 → S2 6명인데 늘어난 4명 중 3명이 진짜 근무자였다).
#   `Freelance`·`Self-employed`도 본업이다(프리랜서로 먹고살고, self-employed는 곧 창업이다).
#   ⚠️ employmentType **표기없음이 40%(227/561)**다. 모르면 **본업으로 취급**한다 —
#      반대로 하면 거짓 공백이 대량 생긴다.
_SIDE_EMPLOYMENT = {"Part-time", "Internship", "Volunteer", "Seasonal"}


def _is_side_job(pos: dict) -> bool:
    return str(pos.get("employmentType") or "") in _SIDE_EMPLOYMENT


def _fulltime_gap_months(p: dict) -> int | None:
    """**본업**(부업 제외) 기준 공백 개월. `_gap_months`의 짝이다.

    왜 따로 재나 (2026-07-27 실측): `공백 개월`은 Present가 **하나라도** 있으면 0이다.
    그런데 퇴사하고 자문·EIR만 파트타임으로 걸어둔 사람이 있다 — 실질은 본업이 없는데 0으로 찍힌다.
      · `jaeminbae`  EIR(Entrepreneur in Residence, Part-time) · 본업 종료 후 **6개월**
      · `2miho`      CEO Staff(Part-time)                      · 본업 종료 후 **7개월**
    EIR은 **말 그대로 창업 준비 중**이라는 뜻이라, 이 축에서 가장 놓치면 안 되는 인구다.

    두 칸을 **같이** 봐야 뜻이 산다. 하나로 합치면 어느 쪽도 못 믿는다:
      공백 0 / 본업공백 6  → "파트타임 하나 걸고 6개월째 본업 없음" ← 신호
      공백 0 / 본업공백 0  → 정상 재직
    """
    from datetime import date
    exp = [e for e in (p.get("experience") or []) if isinstance(e, dict)]
    if any(_is_present(e) and not _is_side_job(e) for e in exp):
        return 0
    today = date.today().year * 12 + date.today().month
    ends = []
    for e in exp:
        if _is_present(e) or _is_side_job(e):
            continue                       # 부업의 종료일은 본업 공백의 기준이 아니다
        ed = e.get("endDate")
        if isinstance(ed, dict) and ed.get("year"):
            ends.append(int(ed["year"]) * 12 +
                        _MON.get(str(ed.get("month", "")).strip()[:3].lower(), 1))
    return (today - max(ends)) if ends else None


def _gap_months(p: dict) -> int | None:
    """**마지막 직장 종료 후 공백 개월.** 회의 요구사항(2026-07-24, 사수) 반영.

    값의 뜻을 헷갈리면 안 된다 — 세 상태가 **전부 다르다**:
      · `0`   = 진행 중인 포지션이 있다 → **공백 없음**(확인된 사실)
      · `N`   = 마지막 경력이 N개월 전에 끝났고 그 뒤가 비었다
      · 빈칸  = 경력에 날짜가 없어 **판정 불가**(미확인이지 "공백 없음"이 아니다)

    🔴 [2026-07-27 실측] **현재 5축으로는 이 값이 거의 항상 0이다.** 271명 중 공백자 0명.
       축이 전부 `currentJobTitles`/`yearsAtCurrentCompanyIds`(=현 직장이 있어야 성립) 또는
       `searchQuery:"Stealth Startup"`(=그 문자열이 현 회사명에 있어야 매칭) 기반이라
       **공백자는 검색 결과에 들어오지도 않는다.** 계산이 아니라 **수집**의 문제다.
       → 값을 채우려면 `pastJobTitles`/`pastCompanies` 축을 따로 태워야 한다(프로브: 4/25 = 16%).

    ⚠️ 사람들은 공백을 실제로 프로필에 남긴다(경력 2개 이상 260명 중 232명, 89%). 지표 자체는 성립한다.
    """
    from datetime import date
    if any(_is_present(e) for e in (p.get("experience") or []) if isinstance(e, dict)):
        return 0
    today = date.today().year * 12 + date.today().month
    ends = []
    for e in (p.get("experience") or []):
        if not isinstance(e, dict):
            continue
        ed = e.get("endDate")
        if isinstance(ed, dict) and ed.get("year"):
            ends.append(int(ed["year"]) * 12 +
                        _MON.get(str(ed.get("month", "")).strip()[:3].lower(), 1))
    return (today - max(ends)) if ends else None


def _change_summary(old_fp: str, new_fp: str) -> str:
    """지문 두 개를 **사람이 읽는 문장**으로. `재검토 ✅`만 켜고 이유를 안 남기면 소용없다.

    🔴 [2026-07-27] 수집 중 감지한 변화 8건이 노션에서 `변화 요약` **전부 빈칸**이었다.
       사람은 "이 사람이 왜 재검토에 올라왔지?"를 알 수 없고, 결국 프로필을 다시 열어봐야 한다 —
       그러면 자동 감지의 의미가 없다. (`recheck-linkedin` 레인은 쓰고 있었는데 `land` 경로가 빠져 있었다)

    지문 형식은 `v{n}|회사|직함`이다. **바뀐 항목만** 적는다:
        회사: Stealth → 주식회사 드래프타입
        직함: Product Manager → Founder
    """
    def parts(fp: str) -> tuple[str, str]:
        seg = (fp or "").split("|")
        return (seg[1] if len(seg) > 1 else "", seg[2] if len(seg) > 2 else "")
    oc, ot = parts(old_fp)
    nc, nt = parts(new_fp)
    out = []
    if oc != nc:
        out.append(f"회사: {oc or '(없음)'} → {nc or '(없음)'}")
    if ot != nt:
        out.append(f"직함: {ot or '(없음)'} → {nt or '(없음)'}")
    return " / ".join(out)


def _fingerprint(p: dict) -> str:
    """프로필 지문 — **"이 사람이 뭔가 했나"를 나타내는 필드만** 넣는다.

    회사명과 직함이 핵심이다:
      · 회사명 `Stealth` → `주식회사 드래프타입`  = **법인을 세우고 공개했다**
      · 직함 `Product Manager` → `Founder`      = **창업했다**
    소개글(summary)은 자주 바뀌므로(문구 다듬기) 넣지 않는다 — 넣으면 무의미한 변화가 쏟아진다.

    🔴 **버전 접두사가 붙는다(`v2|`).** 지문의 재료(`_positions`)가 바뀌면 같은 사람의 지문도
       바뀌므로, 버전이 없으면 **배포 첫 실행에 저장된 344명이 전부 "변화 감지"로 오탐**한다
       (전원 재착지 + 재검토 체크 = 사람이 볼 큐가 쓰레기가 된다).
       구버전 지문은 `_fp_comparable()`이 **비교 불가**로 판정해 조용히 재기준선을 잡는다.
       → 지문 재료를 바꾸면 **반드시 이 버전도 올려라.**
    """
    co = _current_company(p)
    titles = _position_title(p)
    return f"{_FP_VERSION}|{co}|{titles}"


_FP_VERSION = "v2"          # v2 = _positions가 experience의 Present를 포함(2026-07-27)


def _fp_comparable(fp: str) -> bool:
    """저장된 지문을 **지금 지문과 비교해도 되는가**. 구버전/빈값이면 False."""
    return bool(fp) and fp.startswith(_FP_VERSION + "|")


def _post_author_is_kr(p: dict) -> bool:
    """게시글의 **작성자가 한국인가.**

    ⚠️ **이 필터가 작동한다는 보장이 없다.** 게시글 검색 API에는 지역 파라미터가 아예 없고
    (24개 파라미터 전수 확인 — location/geo/country 매칭 0회), 액터 README의 출력 예시를 보면
    **author 객체에 location 필드가 없다**:
        author{universalName, publicIdentifier, type, name, linkedinUrl, info, website, avatar}
    `profileScraperMode: "main"`($0.002/명, "프로필 상세를 긁는다")이면 위치가 딸려올 **가능성**이
    있으나 라이브 검증 전엔 모른다.

    → 위치를 못 읽으면 **살린다**(recall 우선). 즉 main 모드가 위치를 안 주면 이 필터는
      **전량 통과 = 무필터**가 되고, 게시글 발굴은 "전 세계 창업 게시글"을 긁게 된다.
      그 경우의 대안은 두 가지다:
        (a) 작성자 URL을 모아 profile-scraper로 위치를 따로 조회 (추가 호출)
        (b) 한국어 쿼리에만 의존 (= 언어 필터. 영어 쓰는 한국 창업자를 놓친다)
      **어느 쪽을 택할지는 main 모드 실측 후 결정한다.**
    """
    a = p.get("author") or {}
    loc = ""
    for k in ("location", "locationName", "geoLocationName", "geo"):
        v = a.get(k) if isinstance(a, dict) else None
        if isinstance(v, dict):
            v = v.get("name") or v.get("text") or ""
        if v:
            loc = str(v)
            break
    if not loc:
        return True                        # 위치 필드 자체가 없다 → 살린다(무필터)
    return any(t in loc.lower() for t in KR_LOCATION_TERMS)


def _urn(p: dict) -> str:
    return (_first(p, "id", "linkedinUrn", "urn", "profileId", "publicIdentifier") or "").strip()


def _profile_url(p: dict, urn: str) -> str:
    u = _first(p, "linkedinUrl", "url", "profileUrl", "publicProfileUrl")
    if u:
        return u
    pub = p.get("publicIdentifier")
    return f"https://www.linkedin.com/in/{pub}" if pub else urn


def _positions(p: dict) -> list[dict]:
    """**현재** 포지션 목록 = `currentPosition[]` ∪ `experience[]` 중 **endDate=Present**.

    🔴 스크래퍼 모드마다 키가 다르다 (실측 2026-07-13):
         Short : currentPositions[]  (복수)
         Full  : currentPosition[]   (단수)  ← 복수형은 **아예 없다**

    🔴🔴 [2026-07-27 실측] **`currentPosition`은 현재 진행 중인 경력을 통째로 누락한다.**
       271명 표본에서 **117명(43%)** 이 `experience`에 `endDate=Present`인데 `currentPosition`에
       없는 포지션을 갖고 있었다. 실제 피해:

         Example Person F — currentPosition = [행정안전부 자문위원(파트타임, 5개월)] **하나뿐**.
                     `Cofounder & CTO @ AIM Intelligence (2024-05~Present, 27개월)`은
                     experience에만 있다. 그 결과 tenure가 **4개월**로 착지해
                     "극초기 창업자"로 분류됐고(실제로는 창업 2년 3개월차·Gitex 그랑프리 수상),
                     회사명은 "행정안전부"만 남아 `t-stealth`로 오분류됐다.

       옛 주석은 `experience` 폴백을 **"과거 경력까지 포함하니 위험"**이라며 최후 수단으로 뒀는데,
       진단은 맞고 처방이 과했다 — **`endDate=Present`로 거르면 과거 경력은 안 들어온다.**
       그 한 줄이 없어서 43%의 현재 상태를 못 보고 있었다.

    ⚠️ 순서: **창업 직함을 앞에 둔다.** `_current_company`·`_position_title`이 이 순서대로 이어붙이므로
       "AIM Intelligence · 행정안전부"가 되어 judge가 첫 조각에서 정체를 본다(반대면 정부부처가 먼저다).
    """
    out: list[dict] = []
    seen: set[tuple] = set()
    for key in ("currentPosition", "currentPositions", "positions"):
        for x in p.get(key) or []:
            if isinstance(x, dict):
                k = (str(x.get("companyName") or ""), str(x.get("position") or x.get("title") or ""))
                if k not in seen:
                    seen.add(k)
                    out.append(x)
    for x in p.get("experience") or []:                # ★ Present만 흡수 (과거 경력은 안 들어온다)
        if isinstance(x, dict) and _is_present(x):
            k = (str(x.get("companyName") or ""), str(x.get("position") or x.get("title") or ""))
            if k not in seen:
                seen.add(k)
                out.append(x)
    if not out:
        # 🔴 [2026-07-27] 최후 폴백은 **"진행 중인지 알 수 없을 때"만** 쓴다.
        #   경력에 날짜가 제대로 있는데 Present가 하나도 없다 = **정말로 현 직장이 없는 사람**이다
        #   (공백형). 그런 사람에게 과거 경력을 '현재'로 돌려주면 **가짜 tenure**가 생긴다 —
        #   실측: 공백 15개월인 사람에게 `tenure 43개월`이 붙었다. 현 직장이 없는데 재직 43개월은
        #   말이 안 되고, 그게 바로 우리가 오늘 하루 걸려 없앤 종류의 거짓이다.
        #   → 판정 가능하면 **빈 목록**을 준다(tenure·창업개월이 빈칸 = 해당 없음/미확인).
        exp = [x for x in (p.get("experience") or []) if isinstance(x, dict)]
        datable = [x for x in exp if x.get("startDate") or x.get("endDate")]
        out = [] if datable else exp
    out.sort(key=lambda x: 0 if _is_founder_role(x) else 1)   # 안정 정렬 — 창업 직함 우선
    return out


def _is_present(pos: dict) -> bool:
    """이 경력이 **진행 중**인가. `endDate.text == "Present"`가 액터의 표기다(실측)."""
    ed = pos.get("endDate")
    if isinstance(ed, dict):
        return str(ed.get("text") or "").strip().lower() == "present"
    return not ed and bool(pos.get("startDate"))       # endDate 자체가 없으면 진행 중으로 본다


def _is_founder_role(pos: dict) -> bool:
    """이 포지션이 **창업 포지션**인가 (직함 문자열로 판정)."""
    t = str(pos.get("position") or pos.get("title") or "").lower()
    return any(term in t for term in FOUNDER_ROLE_TERMS)


def _position_title(p: dict) -> str:
    """직함 — 동시 포지션이 여럿이면 전부 합친다.

    [0]만 보면 안 된다: 97명 중 12명이 포지션 2~3개를 동시 보유하고, 창업 타이틀이 [1]/[2]에
    있는 경우가 있다(대기업 재직 중 창업 준비자 등). 인덱스 하나로 판정하면 그들을 놓친다.
    """
    # 🔴 키가 모드마다 다르다: Short=title · **Full=position** (실측 2026-07-13).
    #    title만 읽어서 직함이 25/25 전부 비어 있었다 — 그런데 직함은 우리 스코어링의 축이다.
    titles = [str(x.get("position") or x.get("title") or "").strip() for x in _positions(p)]
    return " · ".join(dict.fromkeys(t for t in titles if t))


_MON = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def _pos_months(pos: dict) -> int | None:
    """포지션 하나의 재직 개월수. **스크래퍼 모드마다 키가 다르다** (실측 2026-07-13):

      Short 모드 : currentPositions[].tenureAtCompany = {"numYears": 2, "numMonths": 4}
      Full  모드 : currentPosition[].duration  = "7 mos" / "1 yr 3 mos"
                   currentPosition[].startDate = {"month": "Jan", "year": 2026}

    🔴 Full로 바꾼 뒤 tenureAtCompany가 사라졌는데 코드가 그것만 읽고 있었다 →
       `Tenure Months` 0/24, `fresh_tenure` 가중치 사망, 2년 초과 컷 무력화.
       (이 스킬의 단골 실패: **액터가 주는 키 이름을 추측**한다)
    """
    # ① Short 모드
    t = pos.get("tenureAtCompany")
    if isinstance(t, dict) and (t.get("numYears") is not None or t.get("numMonths") is not None):
        return int(t.get("numYears") or 0) * 12 + int(t.get("numMonths") or 0)

    # ② Full 모드 — startDate 가 가장 정확하다(duration 문자열보다 신뢰도 높음)
    sd = pos.get("startDate")
    if isinstance(sd, dict) and sd.get("year"):
        from datetime import date
        y = int(sd["year"])
        m = _MON.get(str(sd.get("month", "")).strip()[:3].lower(), 1)
        today = date.today()
        months = (today.year - y) * 12 + (today.month - m)
        if months >= 0:
            return months

    # ③ Full 모드 폴백 — "1 yr 3 mos" / "7 mos"
    dur = str(pos.get("duration") or "")
    if dur:
        import re
        yrs = re.search(r"(\d+)\s*yr", dur)
        mos = re.search(r"(\d+)\s*mo", dur)
        if yrs or mos:
            return int(yrs.group(1) if yrs else 0) * 12 + int(mos.group(1) if mos else 0)
    return None


def _tenure_months(p: dict) -> int | None:
    """현 포지션 중 **가장 짧은** 재직기간(개월). 없으면 None.

    왜 min 인가: 동시 포지션을 가진 사람(97명 중 12명)은 [0]=삼성 10년, [1]=Founder 3개월 처럼
    들어온다. 우리가 알고 싶은 건 "이 사람이 **새로 시작한 일**이 얼마나 됐나"이므로 가장 짧은 쪽이다.
    (max/[0]을 보면 대기업 재직 10년으로 읽혀 창업 신호가 완전히 반대로 뒤집힌다)

    🔴🔴 [2026-07-27] **창업 직함이 하나라도 있으면 그중에서만 고른다.** 그냥 min을 쓰면
       파트타임 자문·멘토·고문이 "가장 짧은 일"로 잡혀 창업 시점을 덮어쓴다. 실측 피해가
       Example Person F이다 — 행정안전부 자문(5개월)이 AIM Intelligence 창업(27개월)을 가렸다.
       271명 재계산: **21명(7.7%)** 이 "코드상 12개월 미만"인데 실제 창업직함은 24개월 이상이었다.

       ⚠️ 창업 직함이 **없는** 사람은 옛 동작(전체 min)을 그대로 쓴다 — 그들에겐 "새로 시작한 일"이
          여전히 옳은 정의다(대기업 재직 중 창업 준비자 등).
    """
    pos_all = _positions(p)
    founders = [x for x in pos_all if _is_founder_role(x)]
    pool = founders or pos_all                     # 창업 직함이 있으면 그 안에서만
    vals = [m for m in (_pos_months(x) for x in pool) if m is not None]
    return min(vals) if vals else None


def _founder_since_months(p: dict) -> int | None:
    """**창업자로 산 기간**(개월) — 현 창업 포지션 중 가장 **긴** 것. 없으면 None.

    `_tenure_months`(가장 최근 시작한 창업)와 짝이다. 둘을 같이 실어야 판단이 갈린다:
      · tenure 2 / founder_since 2   → 갓 시작한 창업자
      · tenure 2 / founder_since 84  → 7년차 창업자가 법인을 하나 더 세운 것
    점수에는 안 넣는다(컷은 judge의 일). 추가 구매 비용 0 — 이미 산 필드다.
    """
    vals = [m for m in (_pos_months(x) for x in _positions(p) if _is_founder_role(x))
            if m is not None]
    return max(vals) if vals else None


def _past_stealth(p: dict) -> bool:
    """**끝난** 경력에 stealth가 있나 — "과거에 스텔스였다"와 "지금 스텔스다"는 다르다.

    Example Person F은 `Founder @ Stealth Startup (2024-02~2024-08, 종료)`을 갖고 있어 stealth 축의
    전문검색에 걸려 들어왔다. 시제를 구분하지 않으면 **이미 회사를 공개한 사람**이 계속
    "스텔스 창업자"로 남는다.
    """
    for e in p.get("experience") or []:
        if isinstance(e, dict) and not _is_present(e):
            blob = f"{e.get('companyName') or ''} {e.get('position') or ''}".lower()
            if any(t in blob for t in STEALTH_TERMS):
                return True
    return False


def _current_company(p: dict) -> str:
    """현재 회사명 — **동시 포지션이 여럿이면 전부 합친다** (`_position_title`과 같은 규칙).

    🔴 [2026-07-21] 첫 포지션 하나만 반환하던 버그를 고침. 같은 파일이 직함에 대해선
       "[0]만 보면 안 된다 — 97명 중 12명이 동시 포지션 보유"라고 경고해놓고(`_position_title`),
       **회사명에는 그 교훈이 적용돼 있지 않았다.**

       실측 피해 — Example Person F(AIM Intelligence 공동창업자·CTO, 2024 설립·시리즈A 100억):
       동시 포지션 [0]=행정안전부(자문)만 잡혀 **진짜 회사명이 유실**됐다. 그 결과
       `Raw Text`에 "행정안전부"만 남고 `t-stealth`로 **오분류**됐다(회사 정체가 안 보이니까).

       이 한 필드가 축 전체를 오염시킨다 — line 73 주석대로 **stealth는 headline이 아니라
       회사명에 박히기** 때문이다(`is_stealth`가 `current_co`만 본다). 회사명이 정확해야
       ① stealth 오분류가 줄고 ② 회사명 기반 외부 검증(투자 이력 조회)이 가능해진다.
    """
    for key in ("currentCompany", "company"):
        v = p.get(key)
        if isinstance(v, dict):
            n = v.get("name")
            if n:                             # ⚠️ 빈 dict일 때 ""로 조기 return하면 아래 폴백이 죽는다
                return str(n)
        elif isinstance(v, str) and v:
            return v
    names: list[str] = []
    for pos in _positions(p):                 # 실 페이로드: currentPositions[].companyName
        co = pos.get("companyName") or pos.get("company")
        if isinstance(co, dict):
            co = co.get("name")
        if co:
            names.append(str(co).strip())
    return " · ".join(dict.fromkeys(n for n in names if n))


def _schools(p: dict) -> list[str]:
    out: list[str] = []
    edu = p.get("education") or p.get("schools") or []
    if isinstance(edu, list):
        for e in edu:
            if isinstance(e, dict):
                s = e.get("school") or e.get("schoolName") or e.get("name")
                if isinstance(s, dict):
                    s = s.get("name")
                if s:
                    out.append(s)
            elif isinstance(e, str):
                out.append(e)
    return out
