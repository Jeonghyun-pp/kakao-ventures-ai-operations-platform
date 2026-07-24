# LinkedIn 어댑터 세부 — HarvestAPI 넓은 서치 정책 (2026-07-09 확정)

## 결론
LinkedIn은 **버리지 않고 발굴 채널로 유지**하되, 밴리스크 0인 **HarvestAPI 쿠키리스 2엔진**으로
**넓게 잡고(broad) → 싸게 점수화 → judge가 판단**한다. 계정/쿠키/세션 일절 안 씀(APIFY_TOKEN 재사용).

## 2엔진 (config.channels.linkedin.engines)
| 엔진 | 액터 | 역할 | 과금 |
|---|---|---|---|
| **profile_search** | `harvestapi/linkedin-profile-search` | **발굴 본체**(people-search) | Short: $0.10/page(25명), per-profile 0 / Full: +$0.004/명 |
| **post_search** | `harvestapi/linkedin-post-search` | 런칭 게시물 시그널(보조·교차가점) | ~$5/1k |

넓혀도 저렴: 4쿼리 × 4page(Short) ≈ 주 $1.6. 상위 밴드만 Full로 보강.

## 핵심 원칙 — 캡처에서 자르지 마라
과거 "founder 포함 & seeking 제외" 하드 게이팅은 recall을 죽였음. 전환:
1. **소스 제외는 최소만** — 리크루터/구직자만 액터 `excludeCurrentJobTitles`로 소스에서 컷(싸고 명백).
2. **나머지는 안 자름** → 결정론적 **소프트 스코어**(signals.score)로 랭킹.
3. **judge/사람이 상위 밴드부터** 검토. 컷도 로그(judge-criteria "조용한 삭제 없음").

## ⚠️ 2026-07-12 정정 — **Stealth는 직함이 아니라 회사명에 박힌다**
`t-stealth` 축이 `currentJobTitles: [Stealth, ...]`로 검색하고 있었다. **성립하지 않는 축이다.**
실측(실제 액터 페이로드 100명): `currentPositions[].title`에 stealth = **0명**,
`companyName`에 stealth = 2명 — 그리고 **그 2명의 직함은 Founder/CEO**다.
- Jin-Example Person N — company `"Newco - still in stealth mode"` / title `"Founder, CEO and President"`
- Yongjun Jeong — company `"Stealth"` / title `"Founder"`

→ 해결책은 액터 안에 이미 있었다: **`searchQuery`**(자유 키워드 + LinkedIn 검색 연산자).
라이브 스키마에 실재하는데 코드가 **한 번도 쓴 적이 없다.** 이게 Listly로 "링크드인 검색창에
Stealth Startup 치기"가 하던 일과 정확히 같은 것이다.
(`currentCompanies` 필터는 **회사 URL만** 받아서 회사명 텍스트로 못 건다 — searchQuery가 유일한 수단)

```yaml
- label: t-stealth
  searchQuery: "Stealth Startup"     # ← 코드 변경 0. config만으로 된다
  locations: ["South Korea"]
```

## Listly 경로 = 폐기 (2026-07-12 판정)
사용 흔적 0(드롭 폴더조차 없음) · LinkedIn ToS 위반(심사역 실계정 정지 리스크) · 파싱 스키마
검증 불가. **Listly가 하던 일(자유 키워드 검색)은 위 searchQuery가 no-cookie로 대체한다.**

## 쿼리 팬아웃 (keywords.yaml linkedin.profile_search)
각 스펙 = 액터 payload. 넓게 UNION 후 **URN dedup**. 기본 4스펙:
- `kr-founders-broad` — KR 거주 + 넓은 창업 타이틀
- `kr-founders-recent` — 같은 조건 + `recentlyChangedJobs`(90일 이직) → **+3 가점**
- `kr-tie-global` — 한국 학교 필터 + location 무관 → **해외 한국인 창업자**(recall 확장)
- `kr-lang-titles` — 한국어 타이틀러(창업/대표/공동창업자…)

넓히려면 스펙·타이틀을 자유 추가. `scraper_mode`·`take_pages`·`exclude_titles`·`kr_schools`·
`seen_store`는 config가 주입.

## 소프트 스코어 가중치 (adapters/linkedin.py 상단 상수)
```
+3 recentlyChangedJobs 패스   +3 강한 창업 타이틀   +2 stealth
+2 창업 타이틀 & 현 회사 없음(pre-법인)   +2 한국 학교 & 해외 거주(글로벌 한국인)
+1 한국 거주   +1 post-search 작성자와 이름 일치(엔진 교차)   -2 순수 학생
```

## 안정 ID & 주간 diff
- **dedup 키 = URN**(`Candidate.ext_id`, `ext:{urn}`). **vanity URL 금지**(바뀜).
- **seen-store**(`data/linkedin_seen.json`) = 이번주 URN − 지난주 URN → 신규만 통과.
  같은 프로필 재판정 비용 절감. 기준 바뀌면 파일 삭제로 재스캔.
- DB 교차 dedup(극초기/스타트업 DB)은 core/dedup이 담당 → selection-bias(이미 아는 사람) 제거.

## 알려진 한계 (넓혀도 남음)
- KR 침투율 9~13% → 발굴 수율 얇음(주 0~3명 추정, **미실측**). broad·글로벌연고로 recall 보강.
- 영문 stealth 라벨러 = 이미 노출된 셀렉션 편향. 진짜 알파는 pre-법인 서브셋.
- **액션: HarvestAPI 트라이얼로 7일 간격 2스냅샷 실측**(λ 확정) → 가중치·페이지수 튜닝.

## 죽은 길 / 피할 길 (재시도 금지)
- **쿠키·세션형 전부**(내 계정 귀속 밴 3~7일): Unipile 순수수집·Phantombuster·TexAu·Evaboot·
  Voyager(tomquirk/linkedin-api, 현재 404) 직호출. 아웃리치 자동화군(HeyReach 등)은 "미접촉" 원리 위반.
- **완전 폐쇄**: Proxycurl/Nubela(2025-07 영구폐쇄), LinkedIn 공식 API(발굴 산출 0, 파트너 전용),
  SerpApi(구글 DMCA).

## 법적 구분선
- hiQ v. LinkedIn: CFAA는 공개데이터 자동수집에 미적용(형사 안전).
- Meta v. Bright Data(2024.1): 로그아웃 공개 스크래핑은 ToS 위반 아님(ToS는 로그인 사용자만 구속).
- → **no-cookie 공개수집 = 안전**. HarvestAPI가 여기 해당.
