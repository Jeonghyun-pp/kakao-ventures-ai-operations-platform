# Threads 어댑터 세부 — 3층 구조 (2026-07-09)

키워드 검색은 알고리즘 기반·비전수라 recall 천장이 있다(Meta 공식 문서). 극초기 창업자는
시드 단어를 잘 안 쓰고, 스샷만 올리거나 남의 글에 리플라이로 등장 → 키워드가 구조적으로 놓침.
그래서 **단어를 안 써도 잡히는** 두 발굴층을 더한 하이브리드. 층 on/off = `config.channels.threads.layers`.

| 층 | 액터(config.actors) | 방식 | 시드(keywords.yaml) |
|---|---|---|---|
| ① `keyword` | **igview-owner/threads-search-scraper** | 키워드 검색(sort=recent) | `threads.keyword` |
| ①-b `watchlist` | **meta-threads (`mode:user`)** | 이미 찾은 빌더의 새 글 추적 | `threads.watch_accounts` |
| ② `following_graph` | igview(팔로잉) + **meta(글)** | 시드계정 팔로잉 1홉 → 최근 글 | `threads.following_seed_accounts` |
| ③ `replies` | futurizerush/threads-replies | 앵커 포스트 댓글 수집 | `threads.reply_anchor_posts` + 자동 수확 |

## 🔴 2026-07-13 실측 — 액터 선택의 근거 (재조사 금지)

**① burbn의 `from`은 쓸 수 없다.**
```
400: Field input.searchQuery must NOT have fewer than 1 characters
```
`from`은 검색 결과를 좁히는 필터일 뿐, **"그 사람의 모든 글"을 못 준다.** 즉 워치리스트 층과
팔로잉-글 층은 그동안 **구조적으로 불가능**했다(스키마에 `from`이 보여서 된다고 믿었다).
→ **meta-threads `mode:user`**가 정확히 그 일을 한다. 한국 계정 2명 → 글 14건, **런 1회 $0.02**.

**② 한국어 *검색*이 되는 액터는 burbn과 igview-owner 둘뿐이다.**
배치 입력(키워드 배열)을 받는 액터는 **전부 한국어 0건**이었다:
`futurizerush/threads-search-scraper` · `automation-lab/threads-scraper` · `meta-threads(mode:search)`.
→ 키워드 층은 **키워드당 런 1회**가 불가피하다. `OR` 문법을 찾을 이유도 없다.

**③ igview-owner는 burbn의 완전한 상위호환 — 키워드 층을 여기로 옮겼다.**
| | burbn | igview-owner |
|---|---|---|
| 최소 메모리 | **4GB 강제** | **1GB 가능** |
| 런 시작 (GB당 과금) | **$0.16** | **$0.005** |
| 아이템 | $0.005 | $0.005 |
| 중복률(같은 글 반복 반환) | 2.33배 | **1.56배** |
| 출력 키 | — | **8/8 동일** (파서 변경 0) |
→ 키워드 층 $8.19 → **$4.93**. burbn은 롤백용으로 config.apify.actors 에 남겨둔다.

## ⚠️ 2026-07-12 정정 — "②는 sessionId 때문에 원천 봉쇄"는 **거짓이었다**
그 한 문장 때문에 발굴층 하나가 통째로 접혀 있었다. 실제로는 **팀이 고른 액터 하나만** 로그인을
요구했다. 라이브 스키마 GET으로 확정한 사실:
```
igview-owner~threads-user-following-scraper   required=['username'] · {max_following}  ← 로그인 불필요
futurizerush~threads-user-following-…-api     sessionId 필수                            ← 이것만 봉쇄
futurizerush~threads-replies-scraper          required=['post_urls'] · **객체 배열** [{url}] · max_replies 10~50
```
③이 안 돌던 진짜 이유도 봉쇄가 아니라 **payload 형식**이었다(문자열 배열을 보내고 있었다).

**`watchlist`는 ②의 대체재가 아니다.** 진입 조건이 "키워드 층에 이미 잡힘"이라 `watchlist ⊆ keyword`
이고, 뚫으라던 recall 천장을 그대로 물려받는다(신규 발굴 원리적 0). 그건 **발굴이 아니라 추적**이다.
②의 존재 근거는 **순증 인원**이며, 어댑터가 매 실행 그 숫자를 찍는다.

### 앵커는 자동으로 찬다
사람이 매주 앵커 URL을 손으로 찾는 건 지속가능하지 않다 → keyword 층 결과에서 **댓글 8개 이상 +
의도 신호(뭐 만드세요/자랑/사이드프로젝트…) + 이벤트 미끼 아님**을 통과한 글을 앵커 풀에 자동 등록한다.
⚠️ **댓글 수만으로 고르면 안 된다** — 실측 1위가 "팔로워 100명 됐당"(82댓글)이었다. 그 댓글엔
창업자가 없다. 게이트 적용 후 실측: 9개 기각, 5개 채택(@innovationo=바이브코딩 커뮤니티,
@mj.won.dev=창업/사이드프로젝트 모임 — 요구사항이 말한 바로 그 "인디허브 앵커").

## 공식 API (mode: official) — keyword 층 소스 대안
- `GET https://graph.threads.net/v1.0/keyword_search`
- 파라미터: `q`(필수) · `search_mode=KEYWORD`(임의 문구 "출시했습니다" 그대로) 또는 `TAG` ·
  `search_type=RECENT|TOP` · `since`/`until`(증분 수집) · `media_type` · `limit≤100` · `author_username`
- 스코프: `threads_basic` + **`threads_keyword_search`**. 레이트리밋 2,200쿼리/24h.
- ⚠️ **미승인 시 "내 계정 글만" 조용히 검색됨** — 이게 팀이 막힌 진짜 원인.

### App Review 통과 체크리스트
1. Meta **Business Verification** 완료
2. **테스트 유저 계정으로** 키워드 검색 use-case를 처음~끝까지 보여주는 **스크린캐스트 재촬영**
   (거부 1순위 사유가 이 부분 — 권한이 실제 화면에서 쓰이는지 리뷰어가 확인)
3. 데이터 사용 목적을 개인정보 처리방침에 명시 (`KV그물망 개인정보처리방침` 활용)

## Apify 폴백 (mode: apify) — 승인 전, 로그인 불필요·무인
- 현행 `igview-owner/threads-search-scraper`. 입력 = `searchQuery`·`sort`·`after`·`maxPosts`(burbn과 동일).
  ⚠️ 대안으로 적혀 있던 `automation-lab/threads-scraper`·`futurizerush/*`는 **한국어 검색 0건**(실측).
- 대안 벤더: EnsembleData(로그인 불필요, 무료 50/day~$1.4k/mo).
- ⚠️ 비공식 스크래핑은 Meta ToS상 회색. 일부 actor는 로그인 필요할 수 있으니 actor별 확인.

## 죽은 길
`junhoyeo/threads-api`(2023 Meta C&D 아카이브, 애초에 글/키워드 검색 없음).
Bright Data Threads(=URL 기반 enrichment 전용, 키워드 발굴 불가).

## 주의
바이브코딩 바이럴 슬롭은 KV 프로필과 거리 멂 → adapters가 아니라 **core/judge**가 컷.
