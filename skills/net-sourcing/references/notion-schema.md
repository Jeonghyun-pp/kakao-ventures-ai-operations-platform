# Notion 스키마 & 필드 매핑

**소싱 착지 = 그물망 소싱 DB(`00000000-0000-0000-0000-d609b10d8459`)** · 판단 승격 = 극초기 DB(`EXAMPLE_ID`, 추후).

> ⚠️ 이 문서의 옛 ID(36c0ac54 / ec585ca6)는 **폐기됐다.** 정본은 `config.yaml`이다.
> 스키마의 **진실은 라이브 DB**이지 이 문서가 아니다 — 코드는 `GET /databases`로 읽어 교집합만 쓴다.
>
> ## [2026-07-13] 인물 1행 전환으로 추가된 칸
> | 필드 | 타입 | 의미 |
> |---|---|---|
> | `Post Count` | number | 본문에 쌓인 글 수. **누적**(갱신 시 덮어쓰지 않는다) |
> | `Sourcing Layers` | multi_select | 걸린 층 **전부**(합집합). `Sourcing Method`(select)는 대표 층 하나 |
> | `Tier` | select | **`Catch \| Trend \| Wait`** (A/B/C에서 교체, 2026-07-13) |
>
> 스레드 행의 **페이지 본문**에 그 사람의 글이 시간순 블록으로 쌓인다(프로퍼티가 아니다).
> 새 select/multi_select 값을 쓰기 전에 `tools/notion_migrate.py --apply` 를 먼저 돌릴 것.

---

# 1. 그물망 소싱 DB (착지 대상 · land)

DB: `collection://00000000-0000-0000-0000-a2148bbb401c` ("그물망 스타트업 소싱")

REST(`GET /databases`)는 text 컬럼을 `rich_text`로, 데이터소스 스키마는 `text`로 준다 —
`_prop_value`가 둘 다 허용. 착지는 **스키마에 존재+타입 일치**하는 필드만 넣는다.

## Candidate → DB 매핑 (`core/notion.py._build_landing_properties`)
| DB 필드 | 타입 | 소스 | 비고 |
|---|---|---|---|
| `서비스명` | title | `name` | 앱=앱명, 소셜=핸들/이름(raw) |
| `Channel` | select | `CHANNEL_LABEL[source]` | Threads/LinkedIn/App Store |
| `Post URL` | url | `url` | dedup 키(소셜) |
| `Raw Text` | rich_text | `raw_text` | 2000자 컷 |
| `Captured At` | date | run_id 날짜 | 착지 시각 |
| `Status` | select | `config.notion.land_status` | 기본 `New` |
| `Run ID` | rich_text | 실행 타임스탬프 | 회차 묶음 |
| `Sourcing Method`·`Search Mode`·`Search Type` | select | `signals.*` | keyword_search/CHART 등 |
| `Matched Keyword`·`Korea Signal` | multi_select | `signals.*` | 리스트 |
| `Match Reason`·`API Query` | rich_text | `signals.*` | 발굴 근거·쿼리 |
| **소셜** `Author Display Name`·`Author Username`·`Author Headline` | rich_text | `signals.*`/`founder` | |
| `Author Verified`·`Is Reply`·`Is Quote Post` | checkbox | `signals.*` | |
| `Likes`·`Comments`·`Shares` | number | `signals.*` | |
| `Content Type`·`Media Type` | select | `signals.*` | text/IMAGE 등 |
| `Posted At` | date | `released_at` | 소셜 게시일 |
| **앱스토어** `App ID` | rich_text | `app_id` | dedup 키(앱) |
| `Chart Rank` | number | `signals.rank` | |
| `Genre` | multi_select | `signals.genre` | 옵션명 정확일치(엔터테인먼트/교육/사진비디오/생산성) |
| `Developer` | rich_text | `founder` | |
| `Release Date` | date | `released_at` | 최초 출시일 |
| `Entry Type` | select | `signals.entry_type` | chart_new 등 |
| `Artwork` | url | `signals.artwork` | 아이콘 |
| `타깃`·`Notes`·`Quality` | checkbox/text/select | — | **판단 에이전트가 채움(착지 시 비움)** |

dedup: `App ID`(앱) / `Post URL`(소셜)이 DB에 이미 있으면 재착지 skip.

---

# 2. 극초기 스타트업 DB (승격 대상 · 추후)

DB: `collection://00000000-0000-0000-0000-e3dc926767d1` (⛏️ 극초기 스타트업 DB)

## 판단 카드 → DB 필드 매핑 (sync)

| DB 필드 | 타입 | net-sourcing 소스 |
|---|---|---|
| `법인(서비스)` | title | `candidate.name` (판단 후 법인명 정규화) |
| `한 줄 설명` | text | `judgment.one_liner` |
| `Tier` | select `Catch\|Trend\|Wait` | `judgment.tier_suggestion` |
| `영역` | multi-select | `judgment.area` (아래 허용값에서) |
| `논의 포인트` | text | `pros`/`cons` + 링크 |
| `링크` | url/text | `candidate.url` |
| `발굴` | select | `candidate.source` = `앱스토어`/`스레드`/`링크드인` |
| `발굴자` | user[] | `config.discoverer_user_id` |
| `Status` | select | `config.notion.unreviewed_status` ("" 또는 "AI수집-미검토") [D3] |
| `DB 확인` | checkbox | 중복대조 완료 시 true |
| `논의?` | checkbox | false (회의 전) |
| `다운로드수`·`평점` | text/float | signals에서 (있으면) |

## `발굴` select 허용값 (일부)
앱스토어 · 플레이스토어 · 디스콰이엇 · 프로덕트헌트 · 링크드인 · 스레드 · 인스타그램 ·
GitHub · Travis Bot · 앤틀러 · 긱뉴스 · 해커뉴스 · 클로드코드 …

## `Status` 옵션 (전체 흐름)
0. 미팅 X / 1. 미팅여부 결정전 / 2. 미팅요청 / 3. 미팅회신 대기중 / 4. 미팅예정 /
5. 미팅 후 미결정 / 6. (소프트 포함) 드랍완료 / 7. 투자완료 / 8. 스타트업 DB로 포팅 /
메일 작성(앤·마이크·Reviewer C) / 초안 메일 작성 완료(AI)
→ net-sourcing은 **미검토 상태로만** 적재. 이후 상태는 사람·회의·기존 콜드메일 자동화가 관리.

## Tier 정의
- **Catch**: 꼭 만나자
- **Wait**: 지금은 잘 모르겠지만 일단 보자 / 투자 타이밍 잡기
- **Trend**: 투자는 어려워도 시장 볼만 & 서비스 성장 기대

## `영역` 허용값 (자주 쓰는 것)
지식정보·교육·엔터테인먼트·라이프스타일·헬스케어·생산성·개발자 도구·AI·SaaS·B2B·소셜·
커뮤니티·콘텐츠제작·글쓰기·AI 에이전트·음성 AI·영상 AI·이미지·검색·리서치·도서 … (신규 최소화, 대표 1개)
