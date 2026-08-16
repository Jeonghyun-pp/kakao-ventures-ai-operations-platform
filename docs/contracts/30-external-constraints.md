# [0-a] 외부 제약 대장

작성 2026-08-16 · **네트워크 호출 0** · 디스크에 이미 있는 응답 원본만 읽었다.
근거 데이터: `~/kv-data/net-sourcing/raw/_actor/` 136본(161MB, 전수) ·
`~/kv-data/net-sourcing/snapshots/` 3,617본(전수) · `~/kv-data/net-sourcing/raw/*.json` 52본 ·
`~/kv-data/net-sourcing/fixtures/` 11본 · `~/kv-data/ai-consumer-pulse/raw/**` 4,222본.
기계 산출물: `actor_schema.json` · `deep.json` · `snapshots.json` · `other.json` (같은 디렉토리).

문서 안의 "옛 수치"는 리포 주석/SKILL.md에 적힌 값이고, "전수"는 이번에 다시 센 값이다.
**둘이 다르면 그 사실을 항목 안에 명시했다.**

---

## 0. 대상 인벤토리 (읽은/전체)

| 유형 | 경로 | 읽은/전체 | 파싱 실패 | 비고 |
|---|---|---|---|---|
| Apify 액터 원본 | `net-sourcing/raw/_actor/*.json` | **136 / 136** | 0 | 161MB · 7 slug |
| 앱스토어·디스콰이엇 스냅샷 | `net-sourcing/snapshots/*.json` | **3,617 / 3,617** | 0 | 0바이트 0개 · 빈 배열 21개 |
| 파싱 후 raw | `net-sourcing/raw/*.json` | **52 / 52** | 0 | 우리 Candidate 스키마(외부 아님) |
| fixtures | `net-sourcing/fixtures/*` | **11 / 11** | 0 | 액터 응답 표본 |
| ACP 수집물 | `ai-consumer-pulse/raw/**` | **4,222 / 4,222** | 0 | .json 4,211 · .jsonl 7 · .txt 4 |
| 코드(제약 유도) | `kv-core/kv_core/*` · `net-sourcing/adapters/*` · `mail-harness/triage/core/gmail.py` | 전량 | — | 경로 정정: `kv_core` → `kv-core/kv_core` |

---

## 1. Apify 액터 — slug별 스키마 (전수)

**공통**: 136본 전부 `{"slug","payload","items"}` 래퍼다. 래퍼는 우리가 씌운 것이고
(`adapters/apify._archive`), `items`가 액터 원본 dataset item 배열이다.
액터 자체의 최상위는 `run-sync-get-dataset-items` 규약상 **list 또는 `{items:[]}` 둘 다 올 수 있고**,
`run_actor`가 `data if isinstance(data,list) else data.get("items",[])`로 흡수한다.

| slug | 파일 | MB | 아이템 총/최소~최대 | 키 유니온 |
|---|---|---|---|---|
| `harvestapi-linkedin-profile-search` | 83 | 92.0 | 1,859 · 1~25 | 56 |
| `apify-threads-profile-api-scraper` | 14 | 69.6 | 639 · 2~50 | 30 |
| `igview-owner-threads-search-scraper` | 21 | 4.3 | 781 · **0**~54 | 40 |
| `igview-owner-threads-user-following-scraper` | 10 | 0.3 | 240 · **0**~30 | 14 |
| `futurizerush-meta-threads-scraper` | 5 | 1.2 | 261 · 4~90 | 69 |
| `harvestapi-linkedin-profile-scraper` | 2 | 0.4 | 6 · 1~5 | 50 |
| `futurizerush-threads-replies-scraper` | 1 | 0.3 | 140 | 45 |

### 1.1 파서가 폴백을 둬야 하는 자리 (등장률이 100%가 아닌 키)

**`harvestapi-linkedin-profile-*` (사람 1,167명 · 행 1,865)**

| 키 | 등장률 | 파서 함의 |
|---|---|---|
| `id` (URN) | **100%** | 유일하게 믿을 수 있는 사람 식별자 |
| `linkedinUrl` | 100% | 단 형태가 3종 — §2 참조 |
| `publicIdentifier` | **99.5% (1,856/1,865)** | 🔴 100%가 아니다 — §2 |
| `headline` | 99.2% (1,158/1,167) | 폴백 필요 |
| `about` | **73.3%** (855/1,167) | 27%는 빈다 |
| `currentPosition[]` | 94.0% 비어있지 않음 (70명 공백) | §3 |
| `experience[].startDate/endDate` | 8,844/8,853 (99.9%) | 9건 결손 |
| `experience[].companyId` | 80.6% · `companyLogo` 80.4% · `companyUniversalName` 53.1% | 회사 조회에 못 기댄다 |
| `experience[].employmentType` | **비어있음 42.3% (3,749/8,853)** | 옛 수치 40%(227/561) — **전수 재확인, 유지** |
| `occupation` · `position` · `summary`(최상위) | **0% / 0% / 0.7%** | linkedin.py:462 "실측 0/97" — 전수 재확인 |
| `_meta` (search 액터만) | 1,859/1,865 | `profile-scraper`엔 없고 `originalQuery`가 대신 온다 |
| `profileIdInSearch` | search=True 1,859 / scraper=False 6 | 두 액터를 가르는 마커 |

**두 harvestapi 액터의 스키마 차이**(파서가 분기해야 하는 지점):
`search`에만 `_meta`·`profileIdInSearch`·`composeOptionType`, `scraper`에만 `originalQuery`.
나머지 필드는 동형이다.

**`apify-threads-profile-api-scraper` (639 아이템)** — snake_case다(다른 threads 액터와 다르다).
`latestPosts`만 camelCase다: 키 존재 632/639, **실제로 글이 든 것은 606/639 (94.8%)** (§4).

**`igview-owner-*` 두 액터**: 파일 단위 0건이 존재한다(min=0). 0건은 액터 파손·차단 신호로
`run_actor`가 `⚠️ HEALTH` 를 찍는다.

---

## 2. 🔴 링크드인 URN 형식의 변이 — 전수

`id` · `publicIdentifier` · `linkedinUrl` 세 필드를 1,865행 전수로 훑었다.

**`id`** — 변이 **1종뿐**. `AC?AA` + 34자 = **39자 고정**, 1,865/1,865.
접두 분포: `ACoAA` 1,856 · `ACwAA` **9**.
→ `base.canonical_linkedin_urn`의 정규식 `AC[A-Za-z0-9]AA[A-Za-z0-9_\-]{20,}` 이 **전부 흡수한다.**
정규화 키 `AC`+9자도 두 접두를 같은 키로 접는다(설계 의도대로 작동).

**`publicIdentifier`** — 3종.
- `vanity_lower` (ASCII 소문자+하이픈) **1,734 (93.0%)**
- `other` = **한글 vanity** `example-profile…` **122 (6.5%)** — 퍼센트 인코딩은 **0건**, 전부 raw 비ASCII
- **`<missing>` 9 (0.5%)**

**`linkedinUrl`** — 3종, 호스트는 전건 `www.linkedin.com` (kr./ 서브도메인 변형 0건).
- `/in/<ascii-vanity>` 1,734 · `/in/<한글-vanity>` 122 · **`/in/ACwAA…` (URN 형태) 9**

### 🔴 대조 결과 — 코드의 주장 하나가 전수에서 깨진다

```
제약        harvestapi Full 모드가 publicIdentifier를 **항상 주지는 않는다**.
증거        전수 1,865행 중 9행이 publicIdentifier 없음. 그 9행은 정확히 id 접두가 ACwAA 인
            행들이고, linkedinUrl 도 vanity 가 아니라 /in/ACwAABxUV6AB…(URN) 형태로 왔다.
            전부 2026-07-14T1220~1223 의 harvestapi-linkedin-profile-search 응답이다.
우리 대응    base._normalize_linkedin_url 이 /in/ 슬러그를 canonical_linkedin_urn 으로 접으므로
            "li:in/urn:AC…" 키는 만들어진다. ext_id(=id) 키와도 겹쳐 중복은 잡힌다.
어기면      base.py:104-107 의 주석이 **틀렸다** — "지금은 Full 모드가 publicIdentifier를
            100% 준다(실측 287/287)므로 URN 형태 URL이 애초에 안 생긴다"고 적혀 있는데,
            전수로 보면 **이미 9건 생겼다**. 그 주석이 "다시 생기기 시작하면 URN을 노션에
            저장해서 대조하는 수밖에 없다"고 예고한 상태가 **이미 도래해 있다.**
            vanity 키와 URN 키는 여전히 서로 다른 li: 키라, ext_id 가 없는 경로로 들어온
            같은 사람은 지금도 두 행이 된다.
```

```
제약        publicIdentifier/URL 슬러그에 **한글이 raw 로 온다**(퍼센트 인코딩 아님).
증거        전수 122/1,865 (6.5%) — 예: https://www.linkedin.com/in/example-profile…
우리 대응    _normalize_linkedin_url 은 .lower() + 정규식 추출만 하므로 한글이 그대로 보존된다.
어기면      URL 을 quote/unquote 하는 코드를 중간에 끼우면 같은 사람이 두 키가 된다.
            비교 전에 인코딩을 통일하지 마라 — 원본이 이미 통일돼 있다.
```

```
제약        harvestapi 검색 결과의 _meta.pagination.totalPages 는 **100에서 잘린다**.
증거        전수 1,859건 중 350건이 totalPages=100이고, **100을 넘는 값은 하나도 없다**.
            같은 응답의 totalElements 는 1,246 같은 값이 나온다(=페이지로는 못 닿는다).
우리 대응    linkedin.py:40 이 이 사실을 상수로 박아 두고 축을 쪼개 파티션을 늘린다.
어기면      "다음 페이지가 있으니 더 받자"로 돌면 영원히 끝나지 않거나, 반대로
            totalElements 를 믿고 커버리지를 계산하면 실제보다 훨씬 낙관적인 숫자가 나온다.
```

---

## 3. 🔴 `currentPosition` vs `experience[endDate=="Present"]` — 전수 재측정

**옛 수치 (SKILL.md:263 · linkedin.py:1180)**: 271명 중 **117명(43%)**.
**전수 (1,865행 → 사람 1,167명 · id 기준 중복제거)**:

| 지표 | 전수 | 옛 수치 |
|---|---|---|
| `experience`에 Present인데 `currentPosition`이 그 회사를 누락 | **396 / 1,167 = 33.9%** | 117/271 = 43% |
| `currentPosition`이 아예 빈 사람 | 70 / 1,167 = 6.0% | — |
| 회사명 집합이 어긋난 사람(누락 or 여분) | 395 / 1,167 = 33.8% | 108/271 = 40% |
| Present 경력을 하나라도 가진 사람 | 1,088 / 1,167 = 93.2% | — |

```
제약        harvestapi 의 currentPosition[] 은 **진행 중인 경력을 통째로 누락한다.**
증거        전수 1,167명 중 **396명(33.9%)** 이 experience 에 endDate.text=="Present" 인 회사를
            갖는데 currentPosition 에는 그 회사가 없다. (옛 표본 271명 43% → 전수 33.9%,
            **약 9%p 낮다** — 방향과 심각도는 동일하고 인구가 4.3배로 늘며 완화됐다.)
            차이의 원인: 옛 271명은 '5축 발굴'로 모인 좁은 표본이고, 전수 1,167명에는
            프로필 형태가 더 다양한 사람이 섞인다. **어느 쪽이든 3명 중 1명 이상이다.**
우리 대응    adapters/linkedin._positions() — currentPosition[] ∪ experience[endDate=="Present"]
어기면      33.9%의 '현재 상태'를 못 본다. 창업 직함이 experience 쪽에만 있는 사람은
            Tier 판정에서 통째로 사라진다(Example Person F 사례: currentPosition 이 파트타임
            자문위원 하나뿐이었다).
```

```
제약        endDate 는 **문자열이 아니라 dict** 다. Present 판정은 endDate.text == "Present".
증거        전수 experience 14,913개 중 dict 14,902 · None 11. 문자열은 **0건**.
            dict 모양 3종: {month,text,year} 10,770 · {text} 3,531 · {text,year} 601.
            text=="Present" 는 3,531건 — {text}만 있는 모양과 정확히 일치한다(진행 중이면
            종료 월/년이 아예 없다).
우리 대응    adapters/linkedin.py:1226 `_is_present` — endDate.text 로 판정
어기면      endDate == "Present" 문자열 비교로 짜면 **전건 False** 가 되고,
            §3의 33.9% 보정이 통째로 무효가 된다. 조용히 0이 되는 종류의 버그다.
```

---

## 4. 🔴 `latestPosts` 정렬 보장 — 전수 재측정

대상: `apify-threads-profile-api-scraper` 14본 전수, 아이템 639, 글 2,980편.

| 지표 | 전수 | 옛 수치(SKILL.md:413 · core/followup.py:10) |
|---|---|---|
| `latestPosts` **키 존재** | 632 / 639 (98.9%) | "632/639명 보유" ✅ 일치 |
| `latestPosts` **비어있지 않음** | **606 / 639 (94.8%)** | (문서에 없는 구분) |
| taken_at 2개 이상 = 정렬 판정 가능 | 590 | (n=639명/2,980편) |
| **완전 내림차순(최신순)** | **294 / 590 = 49.8%** | **49.8%** ✅ 일치 |
| `latestPosts[0]` 이 최신이 **아닌** 경우 | **243 / 590 = 41.2%** | 243/606 = 40.1% (분모만 다름) |
| 4편 창이 덮는 기간 중앙값 | **7.05일** | 7.1일 ✅ |
| 그 창이 7일 이하 | **49.7%** | 49.7% ✅ |
| 글 총수 | 2,980 | 2,980 ✅ |

```
제약        Apify threads 액터군은 latestPosts 를 **최신순으로 배송하지 않는다.**
증거        전수 590명 중 내림차순은 294명 = **49.8%** (2026-08-16 재측정 — 문서의 49.8%와
            소수점까지 동일). latestPosts[0] 을 최신으로 쓰면 **243명(41.2%)에서 틀린다.**
            분자 243은 문서와 같고 분모만 606(보유자) → 590(판정 가능자)으로 정정된다.
우리 대응    core/followup.newest_post — taken_at 최대값으로 고른다. 이 함수가 존재하는 유일한 이유.
어기면      '새 글 감지'가 절반 가까이 오판한다. 오래된 글을 최신으로 기록하면
            last_post_ts 가 뒤로 가고, 그 사람은 이후 진짜 새 글에서도 발화하지 않는다.
```

```
제약        latestPosts 는 **키가 있어도 빈 배열일 수 있다** — 두 상태를 구분해야 한다.
증거        전수 639 아이템: 키 존재 632(98.9%, 문서의 632/639 와 일치) 중
            **비어있지 않은 것은 606(94.8%)** · 빈 배열/None 26 · 키 자체 부재 7.
            즉 문서의 "632명 보유"는 키 기준이고, 실제로 글이 있는 건 606명이다.
우리 대응    newest_post 가 None 을 돌려주고 is_new_post 가 False 를 낸다(기준선 미수립).
어기면      키 존재만 검사하면 26명에서 빈 배열을 글로 취급한다.
            `if "latestPosts" in it` 이 아니라 값으로 봐야 한다.
```

---

## 5. 앱스토어 스냅샷 — 전수

3,617본 전부 파싱 성공. **최상위는 3,617본 전부 `list`** 이고 **원소는 dict 가 아니라 app_id 문자열**이다.
(= 스냅샷은 "본 것" 집합이지 데이터가 아니다. 필드 유니온이라는 개념이 없다.)

- 값 타입: `str` 607,786건 (100%)
- 형태: 10자리 숫자 410,014 · 9자리 숫자 197,576 · 그 외 196
  (그 외 196 = `disquiet_products_*` 2본의 제품 슬러그 `wdyl_`·`withbuddy` — 같은 디렉토리에
  같은 규약으로 저장되지만 **ID 공간이 다르다**)
- 배열 길이 분포: 200이 2,361본 · 100이 637본 · 199가 71본 (= SSR 200위 / RSS 100위 천장)

### 5.1 파일명 규약 — 전수에서 유도된 것

| 패턴 | 개수 | 생산자 |
|---|---|---|
| `appstore_board_<genre>_<chart>_<date>.json` | **1,778** | `tools/board.py` (appstore-rank 보드, $0 크론) |
| `appstore_<genre>_<chart>_<date>.json` | **1,804** | `adapters/_util.save_snapshot(prefix="appstore", key="<genre>_<chart>")` |
| `appstore_all_new_<date>.json` | 24 | 위와 같은 경로, genre=None → key="all_new" |
| `appstore_<genre>_<date>.json` | **8** | 🔴 **레거시 고아** — 아래 |
| `appstore_rss_new_<date>.json` | 1 | 1회성 |
| `disquiet_products_<date>.json` | 2 | disquiet 어댑터 |

차트 축: `top-free` 1,490 · `top-paid` 1,483 · `top-grossing` 604 · `new` 30.
장르 축: board 계열 **42종**(전체 36 + 25 카테고리 + 게임 세부장르 7001~7019 **16종**),
plain 계열 **26종 + all + rss**(게임 세부장르 없음).

```
제약        같은 디렉토리에 **두 생산자**가 서로 다른 접두어로 같은 형식을 쓴다.
증거        appstore_board_* 1,778본(보드 크론) vs appstore_* 1,804본(소싱 파이프라인).
            board 쪽만 게임 세부장르 7001~7019 16종을 커버한다(42 vs 26).
우리 대응    config.channels.appstore.baseline_bridge = ["appstore_board"] →
            _util.load_seen_ids(prefix, key, also_prefixes) 가 보드 스냅샷을 기준선으로 빌려 쓴다.
            자체 히스토리가 없어도 신규진입 판정이 즉시 켜진다.
어기면      브리지를 빼면 소싱이 첫날 전량을 '기준선'으로 삼켜 emit 0 이 된다
            (최신출시 피드 110개가 노션에 한 번도 안 올라간 사고가 정확히 이 형태였다).
```

```
제약        appstore_<genre>_<date>.json (차트 축 없는 8본) 은 **어떤 glob 에도 안 잡힌다.**
증거        전수 8본 — 6007·6008·6016·6017 × 2026-07-07·07-08. 차트 키가 도입되기 전 규약.
            load_seen_ids 는 f"{prefix}_{key}_*.json" 으로 key="6014_top-free" 를 찾으므로
            key 가 "6007" 뿐인 이 파일들은 영원히 매칭되지 않는다.
우리 대응    없다(무해한 고아). 다만 디스크에 남아 "커버리지가 있다"는 착시를 준다.
어기면      날짜만 세서 커버리지를 논하면 07-07/07-08 을 정상일로 오판한다.
```

### 5.2 날짜 축 커버리지 — 결손일

관측 구간 2026-07-07 ~ 2026-08-15, 실재 **32일 / 달력 40일**. **결손 8일**:

`07-16(목)` · `07-22(수)` · `07-23(목)` · `07-24(금)` · `07-25(토)` · `07-29(수)` · `08-01(토)` · `08-02(일)`

주말 결손이 아니다(목·금·수가 섞여 있다). 안정 운영 구간은 **08-03 이후**로,
그 뒤로는 board 84 + plain 76 = **매일 160본**이 정확히 반복된다.
예외: `07-30`·`08-08`·`08-09` 는 board 84본만 있고 plain 76본이 없다(소싱 파이프라인만 안 돌았다).

```
제약        신규진입 판정은 diff 라 **안 본 날은 영영 못 본다.**
증거        결손 8일 + plain 단독 결손 3일. adapters/_util.py:19-26 이 07-19·07-20 이틀 전멸을
            기록한다 — 맥이 깨어나는 중 launchd 가 먼저 발화해 DNS 가 안 올라온 상태로
            76개 요청 전부 "Failed to resolve 'itunes.apple.com'". 낮에 재시도하면 0.4초에 200.
우리 대응    _util.get_with_retry — **연결 계층 오류에만** 15→30→60s(총 105초) 백오프.
            404·429·5xx 는 재시도 안 한다(상대 서버의 답이지 망 문제가 아니다).
            + 회로 차단기: 한 번 끝까지 기다려 보고 안 되면 나머지는 즉시 실패.
            (없을 때 76요청 × 105초 = 2시간+ 로 크론이 붙잡혔다.)
어기면      백오프가 짧으면 결손일이 늘고, 회로 차단기가 없으면 크론이 몇 시간 잠긴다.
            누적 seen-set 방식이라 하루 걸러도 0건이 되진 않는다(lookback 방식은 그랬다).
```

### 5.3 0바이트 · 파싱 실패 · 빈 배열

| 분류 | 개수 | 판정 |
|---|---|---|
| 0바이트 | **0** | — |
| 파싱 실패 | **0** | — |
| 빈 배열 `[]` | **21** | **전부 정상(진공)** |

빈 배열 21본은 **전부 `appstore_board_7006_top-paid_*`** (= 카지노 유료 차트)이고,
날짜는 2026-07-08 부터 08-15 까지 board 가 돈 **모든 날**이다.

```
제약        한국 앱스토어에 **유료 카지노 게임 차트는 원래 비어 있다.** 빈 배열은 애플의 정답이다.
증거        전수 빈 배열 21본이 전부 board_7006_top-paid, board 가 돈 날 전부. 다른 차트는 0건.
우리 대응    kv_core.workers._board_files_today — **파싱 성공 여부**로 진공과 결손을 가른다.
            빈 배열도 "잘 형성된 응답"으로 온전하게 세고(n+=1), 0바이트·절단만 무효로 친다.
            "전부 빈 배열이면?"은 총 행 수(rows==0)와 비어있지 않은 비율(_MIN_NONEMPTY_RATIO=0.5)이 막는다.
어기면      "비어있지 않은 배열만 온전"으로 세면 유효 83/84 가 되어
            "부분 결손 · 해당 차트는 영구 손실 가능" 알림이 **매일** 발동한다(실제로 그랬다).
            반대로 존재만 세면 0바이트 84개도 통과한다(실증).
```

```
제약        판정 기준일은 **로컬(KST)** 이다. board.py 가 그 기준으로 파일명을 쓴다.
증거        workers.py:55-56 — 스케줄러의 period 는 UTC 라 00~09시 KST 에 하루 어긋난다.
우리 대응    _board_files_today 가 date.today()(로컬)로 glob 한다.
어기면      UTC 로 세면 **매일 아침 오탐**이 난다.
```

```
제약        board 정상 산출은 84본이다(하한 80). appstore-rank 보드는 84차트를 돈다.
증거        08-03 이후 매일 정확히 84본. 실측(2026-08-10) 84차트 중 83개가 비어있지 않고 15,456행.
우리 대응    workers.run_appstore_snapshot — n<80 이면 실패 처리(다음 wake 재시도),
            80≤n<84 면 alerts.observe("partial_output"), n==84 면 빈 차트를 로그에만 남긴다.
어기면      board.py 는 84차트 중 0개를 저장해도 **exit 0** 이다 — rc 를 믿으면 전멸을 못 본다.
```

---

## 6. 그 밖의 raw — 형태

### 6.1 `net-sourcing/raw/*.json` (52본) — **외부 응답이 아니다**

전부 최상위 `list`이고 원소는 **우리 `Candidate` 스키마**(source·name·url·one_liner·founder·
released_at·raw_text·app_id·ext_id·linkedin_url·homepage·adapter·signals)다.
`adapters/apify.py` 의 `_ARCHIVE` 주석이 이 사실을 명시한다 — *"data/raw/\*.json 은 파싱이 끝난
Candidate 다. 어댑터가 필드명을 잘못 읽으면 그 raw 엔 이미 값이 없다."*

채널별(2026-08-16 재실측): appstore 27본(빈 2) · threads 3본(빈 1) ·
linkedin_harvestapi 6본(빈 1) · linkedin_listly 3본(**전부 빈**) · disquiet 1본 · velog 1본.

🔴 **[2026-08-16 정정] 이전 판의 `listly 4본 · newchan 4본 · threads 빈 5` 는 수집 결과가 아니었다.**
`listly`·`newchan` 은 `pipeline._REGISTRY` 에 **없는 이름**이다 — `_synthetic_apify_gate.py` 의
픽스처 이름이다. 그 테스트가 `KV_DATA` 스크래치를 우회해(`project/data` 심볼릭 링크)
프로덕션 `raw/` 에 직접 썼다. `threads_<D>` 4본도 같은 출처다: 네 날짜(08-13·14·15·16)
모두 세 파일의 mtime 이 **같은 초**로, 수집 스케줄이 아니라 스위트 실행 시각이다.
12본을 `_quarantine/2026-08-16-test-leak/` 로 격리하고 테스트를 고쳤다.

```
제약        2바이트("[]") 파일이 7본이다 — linkedin_listly 3/3 · appstore 2/27 ·
            linkedin_harvestapi 1/6 · threads 1/3.
증거        전수 재실측(2026-08-16). 이전 판 16본 중 12본은 테스트 누출이라 제외했다.
우리 대응    `linkedin_listly` 의 `[]` 는 **고장이 아니라 정상**이다 — 사람이 xlsx/csv 를
            드롭 폴더에 넣어야 도는 반자동 어댑터이고, 지금은 config 에서 꺼져 있다
            (`no_fetch=True` 로 선언돼 있어 계측 0 이 degraded 로 안 잡힌다).
어기면      ① 파일 존재만으로 채널 생존을 판정하면 죽은 층이 살아 있는 척한다
              (appstore.py 가 "받긴 받았는데 emit 0" 이라 부르는 패턴).
            ② 🔴 그 역도 성립한다 — **파일이 있다고 그 채널이 돌았다고 읽으면 안 된다.**
              이 항목의 이전 판이 정확히 그 오독이었다. 이름이 레지스트리에 있는지부터 본다.
```

`Candidate` 필드 중 `ext_id`·`linkedin_url`·`homepage`·`adapter` 는 appstore raw 에서
91.4%만 존재한다(초기 6본에는 그 필드가 아예 없다) — **스키마가 시간에 따라 넓어졌다.**

### 6.2 `fixtures/` (11본)

전부 최상위 `list` = 액터 dataset item 배열 그대로(래퍼 없음). 아이템 0~25.
`live_*` 접두 6본은 실호출 표본, 나머지는 액터별 고정 표본.
`live_automation-lab_threads-scraper`·`live_futurizerush_threads-search-scraper`·`live_meta_kr`
는 **0 아이템** — 그 액터가 그 시점에 0건을 돌려준 사실 자체를 픽스처로 굳혀 둔 것이다.

### 6.3 `ai-consumer-pulse/raw/**` (4,222본)

- `.json` 4,211본: **전부 최상위 `dict` = 문서 1건**. 배열이 아니다.
  필드 유니온(4,205 문서 기준, `_actor` 제외):

  | 100% | `key` `source_id` `org` `tier` `track` `adapter` `title` `url` `published` `fetched_at` `content` |
  |---|---|
  | 부분 | `kind` 28.4% · `quote_verbatim` 19.6% · `batch`/`tags` 4.4% · `duration_s` 2.5% · `author`/`is_reply`/`is_retweet` 1.8% · `points`/`num_comments`/`hn_url` 1.3% · `desc_missing` 0.9% · `actual_author`/`is_repost` 0.7% · `repo`/`release` 0.1% |

  🔴 **빈 값이 있는 필수 필드**: `url` 빈 값 **282건(6.7%)** · `published` 빈 값 **938건(22.3%)** ·
  `content` 빈 값 2건. 키는 항상 있지만 값이 없다 — **존재 검사로는 못 잡는다.**
- 주차별: W29 41소스/743본 · W30 91소스/1,413본 · W31 80소스/729본 · W32 102소스/1,320본
- `_actor/` 6본: `apidojo-tweet-scraper` 2 · `harvestapi-linkedin-profile-posts` 4.
  net-sourcing 과 **같은 `{slug,payload,items}` 래퍼** = 같은 Apify 계정·같은 아카이브 규약.
- `backfill-2026H1/*.jsonl` 7본(146줄): `collected_via,date,org,source,speaker_type,text,title,url`
  이 공통, a16z 만 `date_sitemap`/`date_source`/`body` 추가, yc_youtube 만 `priority`/`title_keywords`.
- `baselines/*.txt` 4본: 벤치마크 보고서 원문(스키마 없음).

```
제약        ACP 문서의 published 는 **22.3% 가 빈 문자열**이다(키는 100% 존재).
증거        전수 4,205 문서 중 938건. url 도 282건(6.7%) 빈다.
우리 대응    (분석 단계가 날짜 없는 문서를 어떻게 다루는지는 이 조사 범위 밖 — 미확인)
어기면      `if "published" in doc` 로 검사하면 전건 통과한다. 값으로 봐야 한다.
            날짜 없는 22%를 "이번 주"로 취급하면 빈도 변화 신호가 오염된다.
```

---

## 7. 코드에서 유도한 외부 제약 — 대장

각 항목은 **"남의 시스템이 이렇게 생겨서 우리가 이렇게 한다"** 이고, 증거는 주석의 날짜·숫자다.

### 노션 (`kv-core/kv_core/notion.py`)

```
제약        노션 rich_text = 2,000 **UTF-16 코드유닛** (파이썬 len 아님)
증거        495명 중 7명 착지 실패, 이모지 3개 → 2000자가 2003유닛 (2026-07-27)
            에러 본문: "content.length should be ≤ 2000, instead was 2003"
우리 대응    core/notion.clip16 — 이진탐색으로 유닛 기준 절단. rich_text() 도 청크마다 clip16.
어기면      그 행이 통째로 400. 로그의 '✗ 착지 실패' 한 줄이 유일한 신호.
            이모지 쓰는 사람이 정확히 우리가 찾는 인구다(게임·디자인·인디 개발자).
```

```
제약        노션 공식 rate limit = **평균 3 req/s**
증거        2026-07-11 에 415행을 무지연 순차 POST 했고 retry/backoff 코드가 리포 전체에 0줄
우리 대응    notion.RATE_SLEEP = 0.34 (성공해도 잔다) · MAX_RETRY = 4 · 429 는 Retry-After 우선
어기면      429 가 나면 그 행은 print 만 되고 사라지는데, seen-store 는 그와 무관하게
            '봤음'으로 확정돼 **다시는 안 잡힌다**(조용한 영구 유실).
```

```
제약        노션 children 배열 1회 최대 **100개**
증거        notion.MAX_CHILDREN = 100 (API 문서 한도)
우리 대응    호출부가 100개씩 쪼갠다
어기면      400
```

```
제약        노션은 **알 수 없는 select 옵션 등을 조용히 버리면서 200을 준다.**
증거        notion.write docstring — "200을 받았다고 값이 들어간 것은 아니다"
우리 대응    착지 후 되읽어 확인(불변 규칙 05). allowed_options() 로 **라이브 스키마**에서
            select 허용값을 읽는다 — 문서 상수를 안 믿는다.
어기면      07-19 에 신설한 판정 옵션이 코드 상수에 없어서 마이그레이션이 옵션을 되돌리려 했다
            (2026-07-27, dry-run 에서 잡음). **옵션 목록의 정본은 항상 라이브 스키마다.**
```

```
제약        integration 토큰은 **공유된 리소스만** 접근한다.
증거        notion.token() docstring — 403 이면 토큰이 아니라 공유 설정을 먼저 의심하라
우리 대응    fetch_schema 실패 메시지에 "integration 을 대상 DB에 연결(공유)했는지 확인하라"를 박아 둠
어기면      403 을 인증 문제로 오진하고 토큰을 재발급하며 시간을 쓴다
```

```
제약        절전 해제 직후 DNS 가 안 올라와 있다 — 노션 쓰기가 gaierror 로 죽는다.
증거        07-18 크론: 수집 10,112건 성공 → 착지 직전 쓰기가 죽어 **229건 유실**.
            당시 백오프 1/2/4s = 7초뿐. 수집 경로는 같은 날 105초로 강화됐는데
            **쓰기 경로만 안 고쳐졌다** (2026-07-21 에 발견).
우리 대응    notion.NET_BACKOFF = 15.0 → 15/30/60s = 총 105초. 429/5xx(1s 기반)와 **분리**.
어기면      두 경로의 정책이 갈라지면 파이프라인 뒷단에서 똑같이 죽는다.
```

```
제약        requests 의 망 계열 예외는 ConnectionError 하나가 아니다.
증거        ChunkedEncodingError 는 ConnectionError 의 **서브클래스가 아니다** — 헤더는 왔는데
            본문 도중 끊긴 경우로, 노션 query 응답(100행 × 수십 속성 = 수백 KB)에서 가장 흔하다.
            2026-07-13 에 ReadTimeout 한 번으로 904행 착지가 통째로 죽어 Timeout·ConnectionError
            를 넓혔는데, 모양이 같고 클래스만 다른 사고가 그대로 남아 있었다 (2026-08-13 W12 B-F6).
우리 대응    notion._NET_ERRORS = (Timeout, ConnectionError, ChunkedEncodingError, ContentDecodingError)
어기면      RequestException 을 통째로 잡으면 MissingSchema·InvalidURL 같은 **프로그래밍 오류**까지
            105초 기다렸다 재시도한다(고쳐야 할 것을 숨긴다). 망 계열만 재시도하라.
```

### Apify (`adapters/apify.py`)

```
제약        Apify actor-start 는 **메모리 GB당 1회** 과금하고, 액터가 선언한 minMemoryMbytes
            미만이면 **400** 이 난다.
증거        실측 2026-07-13 (GET /v2/acts/{slug}/builds/default):
              burbn~threads-search-scraper         min=4096 max=8192  start $0.16/런
              futurizerush~meta-threads-scraper    min=4096 max=4096  start $0.02/런
              futurizerush~threads-replies-scraper min=512  max=1024  start $0.005/런
              harvestapi~linkedin-profile-search   min=128  max=512   start 미과금(page당)
우리 대응    config.apify.actors[slug].memory_mb. 400+"memor" 응답이면 memory 를 벗고 1회 재시도.
            폴백 시엔 _FALLBACK_MEM=8192 로 **비싼 쪽을 가정**해 장부를 보정한다.
어기면      400 이 "액터가 죽었다"로 오진되고 그 채널이 통째로 0건이 된다.
            폴백 후 원래 값으로 계상하면 과소계상 — 4GB 요청 거부 → 기본 8GB 실행 = start 2배.
```

```
제약        Apify 는 **런이 시작되면 과금한다.** 응답을 못 받아도(타임아웃·429·5xx) 돈은 나갔다.
증거        대역 프로브 실측: 10회 연속 ReadTimeout → 장부 $0.00, budget_tripped() False,
            화면엔 "누적 추정 $0.00". 실제로는 최대 $4.00 이 나갈 수 있다 (2026-08-13 W12 A-F5).
            tools/recheck.py:197 이 배치 실패를 except: continue 로 넘겨 조용히 반복된다.
우리 대응    apify._start_cost 를 **POST 직전 선반영**(try/finally 가 아니라 선반영 — 예외가 나도
            그 줄은 이미 실행됐다). 아이템 단가만 응답 뒤에 더한다.
어기면      "돈만 나가고 결과가 0" 인 경우를 회당 차단선이 **원리적으로 못 본다.**
```

```
제약        Apify 는 모르는 쿼리 파라미터를 **200 과 함께 조용히 무시한다.**
증거        apify.usage_usd docstring — "maxTotalChargeUsd 파라미터 이름이 틀리면 200 을 주고
            조용히 무시한다. 가드가 걸린 줄 알았는데 청구서는 그대로다."
우리 대응    세 겹: ①액터측 하드캡 maxTotalChargeUsd(쿼리 파라미터) ②클라이언트 _SPENT 누적
            ③GET /v2/users/me/limits 실측 대조. ③이 없으면 ①②는 **장식이다.**
            그리고 실행 옵션은 **쿼리 파라미터**로 간다 — payload(액터 입력)에 넣으면 무시된다.
어기면      2026-07-10: burbn 6런이 $5.94 를 태워 무료 한도 $5 를 넘겼고,
            그때부터 스레드·링크드인이 전부 403 으로 죽었다(원인 불명으로 보였다).
```

```
제약        Apify 월 한도는 **플랫폼이 정하고 우리가 못 넘긴다** — STARTER 실한도 $29.
증거        config 의 $45 는 사문. 링크드인 회당 $1.6(always 3축) ~ $5.2(전량).
            게이트가 pipeline.collect 한 곳에만 있었는데 run_actor 호출부는 **23곳/13파일**
            이었다 — 그중 게이트를 지나는 건 1곳뿐 (2026-08-13 W12 A-F3).
우리 대응    가드를 **호출부가 아니라 자원 앞에** 뒀다 — apify._monthly_block 을 run_actor 안에서
            POST 전에 부른다. 천장 = 우리 설정 ∩ 플랫폼 한도 중 **낮은 쪽**(kv_core.gate).
            매 호출 API 를 치지 않고 한 번 읽어 로컬 증분 추적, _REPROBE_USD=1.0 마다 재조회.
            조회 실패는 **fail-open + loud**(사실을 표면화하고 통과).
어기면      "게이트 부르는 걸 잊는다"가 다시 표현 가능해진다. 천장을 넘겨 돌리면 Apify 가
            중간에 끊어 '돈은 쓰고 결과는 반쪽'이 된다 — 2026-07-10 의 형태.
```

```
제약        액터가 조용히 깨진다(Meta doc_id 주간 로테이션·차단·쿼터).
증거        apify.py 헤더 주석. 실제로 igview-owner 두 액터는 저장된 원본에서 0건 파일이 존재한다.
우리 대응    ① 0건 반환 시 "⚠️ HEALTH: 액터 '{slug}' 0건 반환 — 파손/차단 의심" 출력
            ② _FETCH_ATT/_FETCH_FAIL 을 run_actor **안에서** 센다(chokepoint) —
              시도는 POST 전, 실패는 finally. 예외·4xx·정상 어느 경로로 나가든 세어진다.
어기면      2026-07-19 앱스토어 사고(76요청 전멸 → "✅ 0건" + 종료코드 0)가
            돈이 나가는 두 채널에서 재현된다. threads(외부요청 7곳)·linkedin(4곳)이
            _note_fetch 를 **한 번도** 안 불러서 실제로 그 상태였다.
```

```
제약        액터 원본을 안 남기면 파싱 버그를 **돈을 다시 써야** 고친다.
증거        07-13 LinkedIn Full 모드 필드명 사고가 정확히 이것이었다.
            data/raw/*.json 은 이미 파싱된 Candidate 라 잘못 읽은 필드는 값이 없다.
우리 대응    apify._archive — {slug,payload,items} 를 data/raw/_actor/{slug}_{stamp}.json 에 무조건 저장.
            실패해도 수집을 막지 않는다(보험이 본체를 죽이면 안 된다).
어기면      파싱 오류 수정이 유료가 된다. **이 조사가 네트워크 0으로 가능한 이유가 이 장치다.**
```

### 앱스토어 / iTunes (`adapters/appstore.py`)

```
제약        iTunes RSS `newapplications` 는 **genre 파라미터를 무시**하고 **110건 하드캡**이다.
증거        실측 — 전체=게임=금융 결과 동일. 스냅샷 실측: 07-08→07-11 106개 중 102개 교체,
            07-11→07-14 새 app_id 0개(4일째 정지). 영구 폐기인지 일시 정지인지는 **모른다**
            (옛 주석의 "애플이 폐기했다·되살릴 방법 없다"는 과했다 — 2026-07-14 정정).
우리 대응    chart=="new" 면 targets=[None] 로 전체 피드 1회만 부른다.
어기면      25개 장르 × 같은 응답을 25번 받는다(비용 아니라 시간 낭비 + 중복 판정 오염).
```

```
제약        SSR 무료·유료는 200위, RSS 매출은 100위 · 최신출시는 110건.
증거        스냅샷 배열 길이 분포 전수 — 200이 2,361본, 100이 637본, 199가 71본.
우리 대응    n = min(cfg.top_n, 200). RSS URL 은 limit=200 을 요청하지만 애플이 잘라서 준다.
어기면      "200을 요청했는데 100이 왔다"를 결손으로 오판한다.
```

```
제약        종합 top-200(genre=36)에는 극초기 앱이 **구조적으로 못 든다.**
증거        신규진입 29건 중 24건이 홈플러스·배민·라인의 시즌성 재진입 (실측)
우리 대응    25개 카테고리 × 4차트로 쪼갠다. genre=36 단독 사용 금지.
어기면      대기업 재진입만 잡힌다.
```

```
제약        애플 원문 장르명("사진 및 비디오")과 노션 옵션명("사진비디오")이 다르다.
증거        appstore.GENRE_NAMES 25개 매핑
우리 대응    코드에 매핑표를 둔다
어기면      기존 옵션과 별개로 중복 옵션이 생겨 multi_select DB 가 오염된다.
```

```
제약        한국팀 여부를 **정규식으로는 어느 방향으로도 못 맞춘다.**
증거        넓게: 424개 중 225개가 한국으로 찍히는데 절반이 베트남·중국·터키 오탐.
            좁게: 실제 한국팀 123개 중 100개를 놓친다.
우리 대응    kr_signal 을 지우고 seller_url(개발사 도메인)을 그대로 노출한다.
            .cn/.vn/.kr 은 사람이 0.5초에 판단한다.
어기면      어느 임계를 잡아도 절반이 틀린다. **다시 넣지 마라.**
```

```
제약        출시일 lookup 이 실패하면 '나이 미상' 앱이 노션에 쏟아진다.
증거        평시 미상 비율은 **0% (실측 4,971/4,971)**.
우리 대응    new_only=False 모드에서는 released_at 없는 건을 **버린다**(검증 못 한 것은 안 내보낸다).
            미상 비율 >5% 면 "🔴 HEALTH ... lookup 장애/레이트리밋 의심" 출력.
어기면      L0(judge)는 미상을 통과시키므로, 입력이 차트 전체가 되는 순간 수천 개가 쏟아진다.
```

```
제약        스냅샷을 collect 시점에 확정하면 land 실패한 날의 앱이 영영 안 올라온다.
증거        _util.save_snapshot docstring — 노션 403 등으로 land 실패 시 조용한 영구 유실
우리 대응    2단계 커밋: save_snapshot 은 `.json.pending` 으로만 쓰고,
            land 성공 후 pipeline 이 commit_snapshots() 로 확정.
            load_seen_ids 는 `*.json` 만 glob 하므로 pending 은 기준선에 안 잡힌다.
어기면      유실 대신 재수집이 안전한 방향인데, 반대가 된다.
```

```
제약        lookback 방식 기준선은 조용히 무너진다.
증거        명목 14일 lookback 이 가드 없이 실제로는 2일로 동작했다 (2026-07-11 실측).
우리 대응    load_seen_ids = **누적 seen-set**(오늘 이전 모든 스냅샷 합집합).
            하루 걸러도 0건이 되지 않고 쌓일수록 강해진다.
어기면      하루라도 거르면 baseline 이 흔들려 신규진입 판정이 요동친다.
```

```
제약        차트 신규진입을 **필터로 쓰면** 관측 시작 이전부터 차트에 있던 젊은 앱이 영구 소실된다.
증거        실측 319개 (2026-07-14)
우리 대응    new_entry_only: false — 신규진입은 이제 **필터가 아니라 라벨**이다.
            entry_type = chart_new / chart_existing / new_release
어기면      라벨을 안 나누고 전부 "신규진입"으로 박으면 **노션이 거짓말을 한다.**
```

### Gmail (`kv-core/kv_core/gmail.py` · `mail-harness/triage/core/gmail.py`)

```
제약        gmail.modify 스코프는 **messages.send 를 허용한다** — API 레벨 차단이 아니다.
증거        W0-W5 리뷰 H6 정정 — 이전 주석은 "이중 방어"라고 잘못 주장했다.
우리 대응    발송을 막는 것은 **"코드에 send 호출이 없다"는 사실 하나**뿐이다.
            tests/_synthetic_slice.py ④ 의 **AST 검사**가 매 커밋 send 호출 0건을 확인한다.
어기면      이 모듈에 send 헬퍼를 추가하면 그 테스트가 깨진다 — 그게 의도다.
            (발송 승격은 W10 승인 브릿지 경유로만)
```

```
제약        Gmail messages().list 의 maxResults 로는 다 못 읽는다 — 페이지네이션이 필수다.
증거        적대리뷰 MED-3 — 예전엔 maxResults=200 한 번만 불렀다.
            21일치가 200건을 넘는 건 hello@ 에서 흔하다.
우리 대응    kv_core.gmail.sent_messages — maxResults=500 + nextPageToken 루프,
            max_messages=2000 상한. 상한에 걸리면 **잘렸다는 사실을 반환**한다(truncated).
            목록 조회 실패도 ([], True) = '잘림'으로 본다(측정 무효).
어기면      잘린 건이 unmatched 로 빠져 **n 이 줄고 무수정률 게이트가 안 열린다.**
            그리고 리포트가 그걸 "아직 안 보냈나 보다"로 **틀리게 설명했다.**
```

```
제약        Gmail 본문은 MIME 트리 어디에 있을지 모르고, charset 이 utf-8 이 아닐 수 있다.
증거        적대리뷰 HIGH-3/MED-2 — 실패 시 "" 를 돌려줬는데 빈 문자열 둘의 difflib 유사도가
            **1.0** 이라 '무수정'이 공짜로 적립됐다. charset 을 안 읽어 euc-kr 한국어가
            통째로 깨져 '거짓 수정'이 됐다.
            MED-1 — mimeType 검사 없이 data 만 있으면 본문으로 삼아
            **인라인 이미지의 바이너리**까지 본문이 됐다(프로브로 재현).
우리 대응    _decode_part 는 실패 시 **None**(빈 문자열 아님). charset 헤더 → utf-8 → cp949 순.
            _plain_body 는 ①text/plain ②text/html 태그제거 ③그 외 **본문 없음**(body_ok=False).
어기면      측정이 조용히 오염된다. 빈 문자열을 흘리지 마라.
```

```
제약        no-reply 발신 메일에 reply 초안을 만들면 반송된다.
증거        Webflow 메일 — reply 를 쓰면 수신자가 no-reply@webflow.com 으로 잡힌다 (2026-07-10 실발생)
우리 대응    triage/core/gmail.draft_forward 경로. draft_reply/draft_forward 둘 다
            **to 를 명시적으로 인자로 받고 비면 ValueError** — 절대 헤더에서 추측하지 않는다.
어기면      반송. 그리고 조용히 반송되면 '보냈다'로 집계된다.
```

```
제약        Gmail 초안 에디터가 긴 text/plain 라인을 중간에서 wrap 하고 문단 빈 줄을 접는다.
증거        bplan-automation 에서 검증된 사실
우리 대응    _to_gmail_html — 수동 작성 시의 <div> 블록 구조를 흉내낸 text/html 파트를 함께 넣는다
            (MIMEMultipart("alternative")).
어기면      심사역이 보는 초안의 줄바꿈이 깨지고, 고쳐 보내면 '무수정률'이 떨어진다.
```

```
제약        비플랜 접수 메일은 hello@ 가 hello@ 에게 보내는 구조라 SENT+INBOX 를 동시에 단다.
증거        triage/core/gmail.inbox_messages docstring
우리 대응    여기서 거르지 않고 rules.py 의 BPLAN_INTAKE 규칙이 **명시적으로 잡아 보호**한다.
어기면      "발신이 우리 자신이면 제외" 를 일반 규칙으로 적용하면 접수 메일이 통째로 사라진다.
```

```
제약        google/token.json 을 스킬별로 복사하면 재인증을 N번 해야 한다.
증거        통합 전 token.json 이 4벌이었는데 refresh_token·client_id 가 전부 동일했다 —
            물리적 복사본일 뿐이었다.
우리 대응    secrets.credential_path("google/token.json") — $KV_CONFIG 단일 참조
어기면      한 곳만 갱신되고 나머지 3벌이 만료된 채 조용히 실패한다.
```

### Slack (`notify.py` · `slackbot.py`)

```
제약        Slack **webhook 은 ts 를 돌려주지 않는다** — 반응(이모지) 조회가 불가능하다.
증거        notify.py 헤더 [W8 정정]
우리 대응    경로를 둘로 나눈다: 알림=webhook(notify.py) · 승인=bot 토큰
            chat.postMessage→ts + conversations.history 로 reactions 읽기
            (`channels:history` 스코프로 충분 — 실측 확인).
어기면      "왜 안 왔지"의 탐색 지점을 못 찾는다. 다이제스트·경보는 notify.py, 승인 카드는 slackbot.py.
```

```
제약        Slack 200 은 **"요청을 받았다"** 이지 채널에 떴다는 뜻이 아니다.
증거        notify.py 헤더 (불변 규칙 05)
우리 대응    중요한 알림은 표시 여부를 따로 확인할 경로가 필요하다(미구현 — 사실만 기록)
어기면      발송 성공을 전달 성공으로 착각한다.
```

```
제약        Slack API 실패에는 영구와 일시가 섞여 있다.
증거        slackbot.post — invalid_auth·account_inactive·channel_not_found·not_in_channel·
            is_archived 는 영구, 나머지(타임아웃·비JSON 429)는 일시적일 수 있다.
            ok:true 인데 ts/channel 이 결손인 경우도 있다 [W9 B-F10].
우리 대응    영구/일시를 로그에 구분해 남긴다(🔴 vs ⚠️). 반환 계약은 None 유지.
            ok+결손도 raise 하지 않고 None.
어기면      영구 실패를 매일 재시도하거나, 일시 실패에 사람을 부른다.
```

```
제약        조회 실패(None)와 반응 0개(빈 리스트)는 **다른 사건**이다.
증거        slackbot.message_reactions docstring (규칙 05).
            [W9 B-F11] post 는 loud 인데 여기만 silent 였다 — 수거가 조용히 멈춘 상태가
            "반응 없음"과 같은 로그를 남겼다(불변규칙 06 비대칭).
우리 대응    실패는 None + loud, 반응 없음은 [].
어기면      승인 수거가 죽은 걸 "아무도 승인 안 했네"로 읽는다.
```

```
제약        KV_NO_SEND 킬 스위치는 **실패가 아니라 억제**다.
증거        [W9 Phase0 0-4] ① True(성공) 반환 → 호출부(alerts.push)가 "발송됨"으로 기록해
            dedup 마커를 태우고 **그날 알림이 영구 유실**됐다(B-H1 실증).
            ② truthy 판정 → KV_NO_SEND=0·false·no 가 전부 "켜짐"이었다(직관 정반대).
            [W9 Phase0 0-6] slackbot 은 None(실패)을 돌려줘 _announce 가 전건 실패로 집계하고
            slackbot-down 경보를 매일 띄웠다(A-M10).
우리 대응    notify.slack → False. slackbot.post → SUPPRESSED 센티널(bool False 지만 None 아님).
            _SUPPRESS_VALUES = {"1","true","yes","on"} 명시 값만.
            orrery 가 is_suppressed() 를 읽어 "🚫 억제됨"을 표시한다.
어기면      스위치가 켜진 줄 모르고 "알림이 안 온다"를 정상으로 오해한다 — 이 스위치의 최대 위험.
```

### claude CLI (`kv-core/kv_core/session.py`)

```
제약        claude 구독 세션에는 **사용량 API 가 없다.**
증거        session.py 헤더 [W6]
우리 대응    호출 수를 프록시로 잰다. 이 모듈이 유일한 통로라 여기가 chokepoint metering:
            spawn 전 gate.check_calls("claude"), spawn 시도 후(성공/실패/timeout 공통) gate.record_call.
            임계는 config/resources.yaml 선언(하드코딩 0).
어기면      게이트에 막힌 호출은 spawn 자체가 없으므로 카운트되지 않는다 — 이 비대칭이 의도다.
```

```
제약        `claude -p` 는 **rc 0 + 빈 출력**으로 인증 만료를 표현한다.
증거        [W9 B-F1] ok=(rc==0) 이었는데, 이 파일 스스로 "rc 0 + 빈 출력 = 인증 만료"라고
            정의한다 — 즉 **인증 만료가 ok:true 로 기록돼** auth-suspect 알림이
            자기 목적 시나리오에서 영원히 미발동했다(실증).
우리 대응    성공 판정을 rc 가 아니라 실제 출력으로. 빈 출력 → record_call(ok=False, err="empty_output")
            + SessionError.
어기면      exit0 ≠ 성공. 계측 버전의 같은 오류.
```

```
제약        claude 출력은 **soft** 다 — 스키마를 프롬프트에 줘도 강제가 아니다.
증거        session.py 헤더 — 프로즈가 섞이거나 코드펜스로 감싸일 수 있다.
우리 대응    session 은 stdout 원문(str)을 그대로 반환하고 **파싱하지 않는다.**
            검증은 validate.py(WAP 의 Audit) 몫.
어기면      관심사가 섞이고, 파싱 실패가 호출 실패로 오인된다.
```

```
제약        전역 ~/.claude 설정의 MCP 서버·allowlist 가 세션에 상속된다.
증거        리뷰 H5
우리 대응    --strict-mcp-config + --mcp-config 미지정 = MCP 0개.
            + --disallowedTools Bash,WebFetch,WebSearch 항상. + _assert_safe_tools 로 구조적 거부.
            prompt 는 argv 가 아니라 **stdin** 으로 (데이터가 "-" 로 시작해도 플래그로 안 읽힌다, W5-med).
어기면      "도구 0개"가 선언에 그치고 구조가 되지 않는다.
```

```
제약        세션이 권한 프롬프트에 걸리면 무한정 멈춘다.
증거        session.py 헤더
우리 대응    _DEFAULT_TIMEOUT = 180초. timeout 도 **쿼터를 태웠으므로 카운트**한다.
            FileNotFoundError(spawn 자체가 없음)는 카운트하지 않는다.
어기면      조용히 매달린다. timeout 이 시끄럽게 죽인다.
```

---

## 8. 대장 항목 수

| 절 | 항목 |
|---|---|
| §2 링크드인 URN·URL | 3 |
| §3 currentPosition / endDate | 2 |
| §4 latestPosts | 2 |
| §5 앱스토어 스냅샷 | 5 |
| §6 그 밖의 raw | 2 |
| §7 노션 | 6 |
| §7 Apify | 5 |
| §7 앱스토어/iTunes | 7 |
| §7 Gmail | 6 |
| §7 Slack | 5 |
| §7 claude CLI | 5 |
| **합계** | **48** |

---

## 9. 못 읽은 파일

**없다.** 대상 전량(136 + 3,617 + 52 + 11 + 4,222 = 8,038본)이 파싱 성공했다.
0바이트 0개 · JSONDecodeError 0개.

읽지 **않은** 것(범위 밖):
- `~/kv-data/ai-consumer-pulse/` 의 raw 이외 디렉토리(analysis·digests·state·themes·trends 등) — 외부 응답 원본이 아니라 우리 산출물
- `~/kv-data/net-sourcing/` 의 state·seen-store 계열 — [0-a] 대상이 아님
- `.venv` 내부

---

## 10. 문서의 옛 수치 vs 전수 — 차이 요약

| 항목 | 문서 | 전수(2026-08-16) | 판정 |
|---|---|---|---|
| latestPosts 최신순 비율 | 49.8% | **49.8%** (294/590) | ✅ 그대로 |
| latestPosts[0] 오판 | 243/606 = 40.1% | **243/590 = 41.2%** | 분자 동일, 분모 정정 |
| latestPosts 4편 창 중앙값 | 7.1일 / 7일 이하 49.7% | **7.05일 / 49.7%** | ✅ 그대로 |
| latestPosts 보유 | 632/639 (98.9%) | 키 **632** ✅ / 실제 글 있음 **606** | 정의 차이(문서엔 구분 없음) |
| currentPosition 누락 | 117/271 = 43% | **396/1,167 = 33.9%** | 🔴 9%p 낮음(인구 4.3배) |
| 회사명 바뀐 사람 | 108/271 = 40% | **395/1,167 = 33.8%** | 위와 같은 방향 |
| employmentType 표기없음 | 227/561 = 40% | **3,749/8,853 = 42.3%** | ✅ 일치 |
| publicIdentifier 보유 | 287/287 = 100% | **1,856/1,865 = 99.5%** | 🔴 **깨졌다** (§2) |
| occupation/position 키 | 0/97 | **0/1,167** | ✅ 그대로 |
| _meta totalPages 천장 | 100 고정 | **최대 100, 초과 0건** | ✅ 그대로 |
| URN 형태 | AC?AA + 9자 + 꼬리 | **39자 고정 1종, 접두 ACoAA/ACwAA** | ✅ 정규식이 전부 흡수 |

---

## 추가 [2026-08-16] — base 주간소싱 DB 는 **대조원**이지 착지 대상이 아니다

```
제약        `notion.db_id`(base 주간소싱 DB, 1,470행)는 아무도 판정하지 않는 축적본이다.
증거        `Tier` 판정완료 **0/1470** · `프로필 점수` 0/1470 · `AI Reason` 0/1470 ·
            `Last Seen` 0/1470. 반면 채널 DB 와 크게 겹친다 —
            핸들 교집합 894 · 제목 1,379 · 링크드인 `/in/` 키 260.
            `judge_cli._targets` 는 `include_base=False` 가 기본이라 판정 루프가 이 DB 를
            아예 안 읽는다(결정 10-b: 채널 DB 가 정본, base 는 원본 잔존).
우리 대응    ① 읽기는 한다 — `notion.landing_dbs()` 가 dedup ①단에서 base 를 포함해 읽는다.
              빠뜨리면 base 에 이미 있는 사람을 다시 착지시켜 중복 행이 생긴다.
            ② 🔴 **쓰기는 막는다** — `notion.db_for(cfg, source)` 가 매핑 없는 채널에 대해
              base 로 조용히 폴백하던 것을 `RuntimeError` 로 바꿨다(2026-08-16).
              `db_for(cfg, "")` 는 "base 를 명시적으로 달라"는 관용구라 그대로 둔다
              (`pipeline.land` 이 '채널 분리 착지 중인가' 판별에 쓴다).
어기면      어댑터 `source` 는 9종인데 `channel_db_ids` 매핑은 3종(앱스토어·스레드·링크드인)
            뿐이다. 나머지 6종(해커뉴스·디스콰이엇·프로덕트헌트·네이버·X·velog)은 전부 지금
            config 에서 꺼져 있지만, **켜는 순간 아무 말 없이 base 로 착지한다.**
            그 DB 에는 그 채널용 칸이 없어 값 대부분이 버려지고, 남는 건 오염된 대조원이다.
            그러면 dedup 의 기준 자체가 틀어져 **중복 판정이 조용히 늘어난다.**
예외        `tools/backfill_{artwork,axis,seed}.py` · `mark_builders.py` 4본은 지금도
            base 에 직접 쓴다(`cfg["notion"]["db_id"]`). 레거시 축적본 정비용 수동 도구라
            의도된 예외다 — **자동 경로에는 base 쓰기가 없다**는 것이 이 제약의 내용이다.
```

## 추가 [2026-08-16] — 잘린 텍스트는 잘렸다고 말한다

```
제약        노션 rich_text 는 2,000 UTF-16 코드유닛이 한계다. 잘라 넣으면 값 자체로는
            "원래 그 길이"와 "잘렸다"가 구별되지 않는다.
증거        `ch_링크드인.Raw Text` 가 **정확히 2000 유닛인 행 33개**(≥1900 은 46개).
            `dedup_bplan3`: 추가내용 10 · 경쟁사 6 · 경쟁력 5 · 팀이력 5 · 비즈니스모델 3 · 솔루션 2.
우리 대응    `core/notion._clip16(text, marker=...)` — 실제로 잘렸을 때만 끝에 `…⟪잘림⟫` 을
            붙인다. 마커 길이만큼 예산을 줄여 자르므로 결과는 언제나 한계 이하다.
            net-sourcing 의 **모든 rich_text 쓰기가 지나는 목**인 `_prop_value` 에 걸었다.
            `title` 에는 안 붙인다 — 제목은 사람이 검색·대조하는 키 성격이다
            (`cross_db_overlap.md` 의 제목 교집합이 그 대조를 쓴다).
어기면      판정은 `Raw Text` 를 그 사람의 이력 **전체**로 읽는다. 잘린 걸 완전한 걸로 읽으면
            조용히 오판하고, 그 오판은 로그에 아무 흔적도 남기지 않는다.
            그리고 마커를 붙이면서 한계 계산을 틀리면 2026-07-27 사고가 재현된다 —
            2,000 **자**가 이모지 때문에 2,003 **유닛**이 되어 400, 링크드인 495건 중 7건이
            통째로 착지 실패했다. 그래서 검증에 '마커를 붙여도 한계 이하'가 들어 있다
            (`_synthetic_notion_transport` T5, 10검사).
```
