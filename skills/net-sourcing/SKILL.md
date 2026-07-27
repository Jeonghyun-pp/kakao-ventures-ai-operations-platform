---
name: net-sourcing
description: >-
  카카오벤처스 그물망 스타트업 소싱 자동화. 앱스토어·스레드·링크드인에서 신규 초기 스타트업/
  창업자를 수집 → 2단 중복제거(LinkedIn URL 정확 + 이름 퍼지) → 주간 소싱 Notion DB에
  채널 태그로 착지한다(스레드는 **인물 1행** + 글을 페이지 본문에 누적).
  **수집은 3채널, 리텐션(재관찰)은 2채널**(앱스토어는 $0 보드라 리텐션 대상 아님) —
  스레드 `recheck`(bio diff + 새 글) · 링크드인 `recheck-linkedin`(지문 diff + 이탈임박).
  **실행은 수동이다** — 회당 ~$10.5 · 층/축별 주기 적용 시 **월 ~$35**(상한 $45, 하드 차단).
  메인 크론은 꺼져 있고 앱스토어 전용 크론($0)만 돈다.
  **판단(Tier)은 반자동** — 주 1회 Claude Code 세션이 judge-export → judge-apply.
  Triggers on: "그물망 소싱", "그물망 수집", "신규 스타트업 발굴", "앱스토어 랭킹 소싱",
  "스레드/링크드인 창업자 소싱", "그물망 미팅 다이제스트", "극초기 스타트업 수집", "/net-sourcing".
  Do NOT trigger for: 앱스토어 순위 보드만 띄우기(→ appstore-rank), 비플랜 접수 처리(→ bplan-automation).
---

# 그물망 소싱 (net-sourcing)

앱스토어·스레드·링크드인에서 신규 초기 스타트업/창업자를 발굴해 **그물망 소싱 DB**에 착지하는
파이프라인을 래핑한다. `bplan-automation`과 같은 패턴: 코드·`.venv`·자격증명이 `project/`
아래 번들되고 절대경로로 동작하므로 어느 디렉토리에서든 실행된다.

> **상태(2026-07-13)**: 수집 → 2단 dedup → L0 → **인물 접기** → 착지, **전 경로 실데이터 검증 완료.**
> 판단은 **반자동**(주 1회 Claude Code 세션: `judge-export` → 판단 → `judge-apply`).
>
> **검증 완료(2026-07-13, 총 $0.54)**: 스모크로 4개 층 + 링크드인 Stealth 축 전부 실액터 확인.
> 리허설(`tools/rehearse.py`, 무과금)로 파이프라인 전체 + 노션 실착지 확인.
> **미검증으로 남은 것은 '수율'뿐** — 21개 키워드가 실제로 몇 건을 줄지는 유료 풀 실행만 안다.
>
> ⚠️ `tools/probe_actor.py`는 **역할을 다했다**(burbn vs meta 대조). 결과는 위 [비용] 표에 있다.
> 새 액터를 검토할 땐 `tools/smoke.py` 패턴을 따라라 — 최소 물량 + 원본 저장 + 파서 대조.

## 비용 (이게 이 스킬의 최대 리스크다)

**Apify는 `actor-start`를 메모리 GB당 1회 과금한다.** 이게 비용의 정체다 — 데이터 값이 아니라
**런을 여는 값**이다. (07-10에 burbn 6런이 $5.94를 태워 계정이 정지됐다)

### 액터 선택 = 비용 결정 (2026-07-13 실측 확정)

| 액터 | 최소메모리 | 런 시작 | 아이템 | 한국어 | 배치 |
|---|---|---|---|---|---|
| **`igview-owner/threads-search-scraper`** ← **현행 키워드** | 1GB | **$0.005** | $0.005 | ✅ | ✗ |
| `burbn/threads-search-scraper` (롤백용) | **4GB 강제** | **$0.16** | $0.005 | ✅ | ✗ |
| `futurizerush/meta-threads-scraper` ← **현행 user_posts** | 4GB | $0.02 | $0.0025 | **검색✗ / mode:user ✅** | ✅ |
| futurizerush/threads-search · automation-lab/threads | — | — | — | **✗ (0건)** | ✅ |

**핵심 사실 3개 (재조사 금지 — 전부 실측):**
1. **한국어 스레드 *검색*이 되는 액터는 `burbn`과 `igview-owner` 둘뿐이다.** 배치 입력을 받는
   액터는 전부 한국어 0건이었다. → 키워드 층은 키워드당 런 1회가 불가피하다.
2. **`igview-owner`는 burbn의 완전한 상위호환.** 입출력 스키마 **8/8 동일**(파서 변경 0),
   메모리 1GB 가능(start 32배 저렴), 중복률도 낮다(1.56배 vs 2.33배).
3. **burbn의 `from`은 쓸 수 없다.** `searchQuery`가 필수라 "그 사람의 모든 글"을 못 준다
   (실측 400). → 워치리스트·팔로잉 글은 **meta의 `mode:user`**(한국어 OK, usernames 배열).

전 층 1회 = **~$9.8** (burbn이었으면 $18.8)

### 💰 현재 비용 구조 (2026-07-20 기준) — **월 상한 $45**

| | 월 | 비고 |
|---|---|---|
| 수집 · 스레드 | $20.3 | 키워드·리플라이 매주 · **FG 격주** · 워치리스트 폐기 |
| 수집 · 링크드인 | $14.2 | flow·**gap 격주** · stealth-us 월1 · 나머지 매주 (6축, 2026-07-27) |
| 수집 · 앱스토어 | **$0** | 애플 무료 API. Apify 미경유 |
| 리텐션 (2채널) | $1.6 | recheck 7일균일 + LI레인 + 빈-bio TTL |
| **합계** | **~$36** | 상한 $45에 여유 $9 |

**절감의 정체는 "몇 건을 사느냐"가 아니라 "얼마나 자주 사느냐"다.** 건수·필터 레버는 2026-07-16에
소진됐다(키워드 축소는 배타 빌더 5명 손실 + 킬룰 위반, maxPosts 컷은 `sort=recent`인데 배송이
최신순이 아니라 **무작위 꼬리 컷**이다). 층·축별 주기가 주 레버다 → `layer_cadence` / 축 `cadence`.

**🔴 링크드인은 더 깎을 데가 없다 (재제안 방지 — 3원칙, 2026-07-19 실측)**
1. **런당 $0.10 고정비.** `batch_pages: 1` 하드코딩 + 액터가 `takePages`를 무시 → 페이지 1개 = 런 1회.
   **아이템을 줄이는 절감은 무의미하다. 런 수(=빈도)만이 노브다.**
2. **"이미 산 사람 빼고 사기"가 원리적으로 불가능.** payload 키 13개 전수에 **날짜도 제외 목록도 없다.**
   `linkedin_seen.json`은 URN을 받은 **뒤** 거른다 = 돈은 이미 나갔다.
3. **어느 축도 빈 그물이 아니다.** 12축 300명 중 tenure<6이 61명, **최저 축도 5/25**다.
   축 삭제안은 전부 이 표로 기각하라.
⚠️ 단 이 3원칙은 **발굴 액터(`profile-search`) 얘기다.** `post-search`는 start $0 + 아이템 100%라
비용 구조가 정반대고, 거기선 `max_posts`가 노브다.

### 예산 가드는 숫자가 **셋**이다 (축이 다르다 — 합치지 마라)

| | 무엇을 보나 | 언제 | 걸리면 |
|---|---|---|---|
| `preflight_max_usd` $12 | **이번 런 추정** | 실행 전 | 공짜로 중단 |
| `budget_usd` $16 | **이번 런 누적**(추정) | 실행 중 | 이미 쓴 돈은 못 돌려받음 |
| **`monthly_budget_usd` $45** | **이번 달 실제 청구액** | 실행 전 | 공짜로 중단 · `--force-budget`로 사람만 우회 |

🔴 월 가드가 왜 필요한가: **결정 #6(수동 실행)이 지금까지 사실상 월 예산 가드였다.** 사람이 매번
"돌릴까"를 판단하는 게 월 총액을 누르는 유일한 장치였고, **크론은 그 사람을 없앤다.**
기준은 Apify **실제 청구액**(`monthlyUsageUsd`)이라 추정 오차(액터 오버슛 +15% 등)에 안 속는다.

### 돈 쓰기 전에 도는 것들 (전부 무과금)
- **`tools/estimate_cost.py`** — 실행 전 총액 추정. `collect`가 **자동으로 먼저 부르고**,
  `config.apify.preflight_max_usd`를 넘으면 **수집을 시작조차 안 한다** (→ CRITICAL #7)
  (중간에 터지면 앞 채널이 예산을 다 쓰고 뒤 채널이 0건이 된다 — 그건 가드가 아니라 사고다).
- **`tools/rehearse.py`** — 저장된 **실제 액터 출력**(`data/fixtures/live_*.json`)으로 파이프라인
  전체를 리허설. 앱스토어는 무료 API라 진짜로 수집. 노션 페이로드를 라이브 스키마와 대조해
  **조용한 드롭**을 잡는다. `--land` 를 붙이면 대표 행을 진짜 써보고 아카이브한다.
  **상태 파일(커서·seen-store)은 스냅샷 후 원복한다.**
- **`tools/smoke.py`** — 미검증 경로를 최소 물량으로 1회씩(~$0.6). 액터 원본을 저장하므로,
  파싱이 틀리면 **그 뒤로는 $0으로 고치고 재현**할 수 있다.
- 실행 후 `💰 실제 과금 $X / 추정 $Y` 를 반드시 본다 — 실제≫추정이면 가드가 장식이라는 뜻이다.

<HARD-GATE>
`land`/`run` 실행 전 아래 전제가 확인되기 전까지 착지를 시도하지 않는다. 예외 없음.
1. Notion 통합('plan')이 **주간 소싱 DB(`00000000-0000-0000-0000-d609b10d8459`)에 공유(연결)**돼 있다 — 없으면 GET 404.
2. `.env`에 **NOTION_TOKEN**(dedup 대조·착지 쓰기) + **APIFY_TOKEN**(스레드·링크드인 수집)이 있다 — 없으면 해당 채널 0건/대조 생략.
없으면 사용자에게 알리고 멈춘다.
</HARD-GATE>

---

## Non-Negotiable Rules
모든 착지에 적용. 위반 = 재작업.

<CRITICAL>
**#1 — 착지 대상 = 그물망 소싱 DB(`EXAMPLE_ID`)**: `config.notion.db_id`·`weekly_db_id`가 이 DB를 가리켜야 한다.
dedup **대조**용 DB는 별개다 — 극초기 DB(`EXAMPLE_ID`, 1,272행)·Bplan 3.0(`EXAMPLE_ID`, 420행).
⚠️ 옛 문서의 `EXAMPLE_ID`/`EXAMPLE_ID`는 **UUID 오기로 404였다**(2026-07-12 확정). 정본은 `config.yaml`뿐이다.
섞으면 스키마 불일치로 매핑이 통째로 누락된다. 대신: 착지=`db_id`, 대조=`earlystage/startup_db_id`.

**#2 — 어댑터는 *품질*을 판단하지 않는다. *양과 신선도*는 판단한다.**
이 둘을 섞으면 "필터 금지"라는 말이 비용 가드까지 뜯어내게 만든다. 셋으로 나눠 본다:

| 어댑터가 하는 것 | 예 | 왜 |
|---|---|---|
| ✅ **양(비용) 경계** | `max_posts` · `following_max_expanded` · `watchlist_max_accounts` · `replies_max_anchors` | Apify는 **아이템당 과금**이다. 안 재고 긁으면 계정이 죽는다(실제로 죽었다) |
| ✅ **신선도 경계** | `max_post_age_days`(=2) · 서버측 `after` 파라미터 | 낡은 글은 "실시간 신호"가 아니고, **받는 순간 이미 낸 돈**이다. 서버측 `after`가 클라이언트 `_too_old`보다 항상 낫다(돈을 안 낸다) |
| 🔴 **품질 판단 — 금지** | "이 사람은 마케터 같다" · "슬롭이다" · 한글비율 컷 | 전부 judge·사람의 일이다. 어댑터에서 자르면 **근거도 없이 조용히 사라진다** |

**유일한 품질 하드컷 = 링크드인 `excludeCurrentJobTitles`(리크루터)** — 소스가 서버측에서 빼주는 것이라
2,500명 천장을 그들이 먹는 걸 막는다. 앱스토어 `new_entry_only`는 필터가 아니라 **채널 정의**다.

⚠️ **경계값을 조이는 것과 품질로 자르는 것은 다르다.** `drop_replies`가 그 교훈이다 —
"리플 전용 작성자 12명이 노이즈"라는 이유로 리플을 통째로 버렸는데, 그 안에 진성 창업자가 있었다.
지금 `false`다. **애매하면 자르지 말고 착지 후 판단에 위임한다(조용한 삭제 없음).**

**#3 — 착지 전 2단 dedup 필수**: `land`는 착지 직전 `dedup.filter_new`로 기존과 겹치는 후보를 뺀다.
① 주간 DB `LinkedIn URL`/홈페이지 URL 정확매칭(go-forward) + ② 레거시 DB 이름 퍼지 + land 자체 App ID/Post URL.
겹침 키가 하나라도 있으면 재착지하지 않는다. 대신: URL이 최강 키이므로 링크드인 후보엔 `linkedin_url`을 꼭 채운다.

**#4 — 스키마 교집합만 쓴다**: `_fetch_schema`로 실제 property/type를 읽어 **존재+타입 일치** 필드만 payload에 넣는다.
DB 스키마가 바뀌어도 코드가 죽지 않게. 대신: 새 필드는 어댑터가 `signals`에 담고 매핑 `intended` 목록에 한 줄 추가.

**#5 — provenance 보존 · 어댑터 드롭세이프**: 모든 Candidate에 `adapter`(예: `linkedin.harvestapi`/`linkedin.listly`)
태그를 남기고, 어댑터별 raw를 분리 저장한다. 한 어댑터를 꺼도(config `enabled:false`) 나머지 파이프라인은 무손상.
대신: 링크드인처럼 소스가 둘이면 서로 다른 등록명으로 두고, 병합은 dedup 단계에서 공통 키로 흡수.

**#6 — 스레드는 인물 1행**: 착지 키가 `threads:{핸들}`이고, 그 사람의 글은 노션 **페이지 본문**에
블록으로 누적된다(`core/fold.py`가 judge **뒤**·land **앞**에서 접는다). 글 1행으로 돌아가면
"주 N명 발굴"을 셀 수 없고, 여러 층에 걸린 사람이 흩어져 안 보인다.
대신: 갱신은 **덮어쓰기가 아니라 합치기**다 — `Post Count`는 본문의 실제 글 수(`len(have)+len(new)`),
`Sourcing Layers`/`Matched Keyword`는 **합집합**, `Post URL`·`Status`·`Captured At`은 **건드리지 않는다**
(`_PATCH_EXCLUDE`). 이걸 어기면 3주간 5편 쓴 사람이 `Post Count=1`이 되고 최초 발굴 근거가 지워진다.

**#7 — 비용 가드는 숫자가 *두 개*다. 하나로 합치지 마라.**

| | config 키 | 언제 | 걸리면 |
|---|---|---|---|
| **게이트** | `apify.preflight_max_usd` (現 $12) | 실행 **전**, 추정치 기준 | **한 푼도 안 쓰고** 중단 → **공짜**다 |
| **차단선** | `apify.budget_usd` (現 $14) | 실행 **중**, 누적 추정 기준 | **이미 쓴 돈은 못 돌려받고** 뒤 채널만 0건 → **손해**다 |

게이트는 공짜라 빡빡해도 되고, 차단선은 손해라 **추정에 딱 붙이면 안 된다.**
정상 변동(+40%)은 통과시키고 액터가 아이템을 10배 뱉는 **사고**만 잡는 높이여야 한다.
🔴 둘을 같은 값으로 두는 순간 "추정 $9.79 / 상한 $10" 같은 게 되어 **아이템이 조금만 더 나와도
링크드인이 통째로 0건**이 된다. 그건 가드가 아니라 사고다. (`estimate_cost.py`가 이 간격이
20% 미만이면 경고한다.)

대신: `run_actor`의 실행옵션(`memory`/`maxTotalChargeUsd`)은 **쿼리 파라미터**로 보낸다.
payload에 넣으면 액터가 모르는 키라 무시하고, 가드가 안 걸린 채 돈이 나간다.

**#7-c — 층·축을 감싼 `try/except`는 실패를 ⚠️ 한 줄로 삼킨다. 주기적으로 "정말 도는가"를 확인하라.**

2026-07-20에 **팔로잉그래프 층이 엿새간 죽어 있던 걸** 발견했다. 07-14에 시드 스펙을
`{username, runs}` 딕트로 바꾸면서 `_seed_runs`는 고쳤는데 `_known_handles`를 안 고쳐서
`'dict' object has no attribute 'strip'`로 층이 통째로 죽었다. 그런데 로그엔
`⚠️ threads following_graph 층 실패` **한 줄**뿐이라 수백 줄 사이에 묻혔다.

왜 이게 최악인가 — **FG는 정밀도 1위 층이다**(19.1% · 전체 빌더 60명의 **35%**를 혼자 물어오고
키워드 층과 교집합이 **0명**이다). 이 층이 못 잡으면 아무도 못 잡는 사람들이다.
게다가 크래시가 `run_actor`(=지출) **뒤**에 나서 **팔로잉 확장을 사고 나서 전량 버렸다.**

대신:
- 같은 값을 두 곳에서 해석하지 마라. 시드 형식 정규화는 **`_seed_name()` 하나**로 모았다
  (`core/cadence.py`·`_expand`와 같은 원칙).
- 층별 산출 0건은 **HEALTH 경고**로 띄운다(이미 있다). 그 경고를 무시하지 마라.
- `rehearse.py`를 **정기적으로** 돌려라. 이 버그를 잡은 게 리허설이다(무과금).

**#7-b — 돈 주고 산 액터 원본은 무조건 남긴다**: `run_actor`가 `data/raw/_actor/{slug}_{시각}.json`에
응답을 그대로 떨군다. `data/raw/*.json`은 **파싱이 끝난 Candidate**라, 어댑터가 필드명을 잘못 읽으면
거기엔 이미 값이 없다 — 원본이 없으면 파싱 버그 하나 고치려고 **재수집($)** 해야 한다
(07-13 LinkedIn Full모드 `tenure_months` 0/24 사고가 정확히 이것이었다). 원본이 있으면 **$0에 고친다.**

**#8 — 노션 옵션이 없으면 값은 조용히 사라진다**: `_known_options_only`가 select/multi_select의
미등록 값을 버린다(경고는 1회뿐). `keywords.yaml`에 키워드를 추가하거나 새 select 값을 쓰기 전에
**반드시** `tools/notion_migrate.py --apply`를 돌린다. 순서를 뒤집으면 착지는 "✅ 성공"인데 칸은 빈다.

**#9 — 액터 검색어에서 *띄어쓰기는 OR다*** (2026-07-14 실측): 액터는 공백이 든 검색어를
**두 토큰으로 쪼개 OR 검색**한다. 회수 글 본문에 그 말이 **실제로 든 비율**:
| 공백형 | | 붙임형 | |
|---|---|---|---|
| 유저 인터뷰·1인 개발·앱스토어 심사·베타 오픈·첫 유저 | **0%** | 웨이팅리스트 | 87% |
| 심사 리젝 | 2% | 공동창업자·창업했습니다 | 80% |
| 바이브 코딩 | 5% | 출시했습니다 | 74% |
| 법인 설립 | 14% | 피드백·사이드프로젝트·인디해커 | 55~71% |

→ `"앱스토어 심사"`가 **'심사'**만 매칭해 **재건축 안전진단·시청 공약점검** 글을 물어왔다.
**워치리스트가 네일샵·시청으로 채워진 뿌리가 이것이다.**
⚠️ 단 **붙임은 필요조건이지 충분조건이 아니다** — `바이브 코딩`(공백)은 26.7%로 살아있고
`만들었습니다`(공백 없음)는 0/22다. 진짜 결정 요인은 **"그 토큰을 빌더만 쓰는가"**다.
→ 새 키워드를 넣으면 **구 매칭률을 측정하라.** 5% 미만이면 정밀도 문제가 아니라 **표기 문제**다.

**#10 — 판단은 세어서 하라. 샘플로 하지 마라.**
정답지가 있다: **`data/research/labels.json`** (513명 라벨 · 빌더 60명 · 기저율 **11.7%**).
감으로 판단했다가 뒤집힌 것들:
- "팔로잉그래프는 말차라떼를 데려온다 → 폐기" → 세어보니 **정밀도 1위(19.1%)**, 전체 빌더의 **35%**를
  혼자 물어왔고 **키워드는 그 21명 중 0명**을 잡았다. 키워드층도 88.5%가 비빌더다(FG는 80.9%).
- "팔로워 많은 사람만 보자" → **역방향.** 빌더 중앙값 **517** vs 비빌더 **2,188**.
- "AI 툴 이름이 좋은 키워드일 것" → 실사용자가 **강의팔이·AI뉴스봇**. 빌더 12.5% = 기저율.

🔴 **한 번의 실행으로 키워드를 죽이지 마라.** 기저율 11.7%에서 20명 뽑아 빌더 0명일 확률이 **8.3%**다
(12번에 1번 오판). 7개를 동시 검사하면 **전부 정상이어도 "최소 1개가 0명"일 확률이 97%**다.
→ 킬룰은 `keywords.yaml: kill_rules`에 **사전등록**돼 있다. 누적 n으로 판정하라.

**#11 — 팔로업 필드는 전용 경로만 쓴다** (`Last Seen`·`Last Checked`·`재검토`).
생성/병합 payload(`_build_landing_properties`)에 싣지 마라 — 체크박스는 falsy도
`{"checkbox": false}`로 직렬화돼 **사람이 해제해 둔 재검토를 코드가 되살리거나 켜진 것을 끈다.**
켜기는 코드(land 병합 지점·recheck), 끄기는 사람. 그래서 셋 다 `_PATCH_EXCLUDE`에 들어 있다.
대신: 표기가 필요한 새 경로가 생기면 land()의 병합 지점처럼 **행의 실제 속성(_ROW_PROPS)을
확인하고 직접 주입**하라(스키마 교집합을 안 거치므로 미확인 주입은 PATCH 전체를 400으로 죽인다).

⚠️ **판정 옵션(select)을 늘렸으면 `tools/notion_migrate.py`의 상수도 같이 늘려라.** 한쪽만 고치면
   **다음 마이그레이션이 조용히 되돌린다.** 실측(2026-07-27): 07-19에 신설한 Tier `Cut`이
   `TIER_OPTIONS`에 없어서, `--apply`가 옵션을 3개로 교체하며 `Cut`을 지우려 했다.
   `judge_cli._verdict_field()`는 **라이브 스키마에서** allowed를 읽으므로(문서가 아니라 스키마가
   정본) 그대로 뒀으면 `Cut` 판정이 전량 거부됐다. dry-run에서 잡았다 — **`--apply` 전에 dry-run을
   눈으로 읽어라.** `~ Tier: [...] → [...]` 처럼 **줄이는** 변경이 보이면 멈춰라.

**#12 — 액터가 `current*`라고 이름 붙인 필드를 "현재의 전부"로 믿지 마라.**

현재 포지션은 **`currentPosition[]` ∪ `experience[]` 중 `endDate == "Present"`** 로 구한다.
그리고 그 해석은 **`_positions()` 한 곳에서만** 한다(회사명·직함·tenure·지문이 전부 그걸 쓴다).

왜 — 실측(2026-07-27, 저장된 실액터 응답 271명):
| | |
|---|---|
| `currentPosition`이 진행 중 경력을 **누락**한 사람 | **117 / 271 (43%)** |
| 그 결과 **극초기(<12개월) 판정이 뒤집힌** 사람 | **17명** (탈락 14 · 편입 3) |
| 회사명이 바뀐 사람 | 108 / 271 (40%) |

나쁜 예 (2026-07-13~07-27에 실제로 착지된 행):
```
Example Person F · Tenure Months 4 · Axis [t-cto, t-stealth]
Raw Text "solving AI safety & security · 행정안전부 · Seoul"
→ 액터의 currentPosition에 **파트타임 자문위원(5개월) 하나만** 들어 있었다.
  극초기 창업자로 분류됐다. 실제로는 AIM Intelligence 공동창업 2년 3개월차 · Gitex 그랑프리 수상.
```
좋은 예:
```
Example Person F · Tenure Months 26 · 창업 개월 26 · 과거 스텔스 ✅
Raw Text "… · AIM Intelligence · 행정안전부 · Seoul · solving AI security"
→ experience의 Present를 흡수. 창업 직함이 있으면 tenure를 **그 안에서만** 고른다
  (자문·멘토 같은 짧은 부업이 창업 시점을 덮어쓰는 걸 막는다).
```

**기계 검증 — 새 액터·새 스크래퍼 모드를 붙이면 착지 전에 반드시 돌린다 ($0):**
```bash
"$PROJ/.venv/bin/python" "$PROJ/tools/check_present_coverage.py"    # rc=1이면 착지 금지
```
저장된 액터 원본으로 **우리 파서(`_positions`)가 Present를 몇 % 놓치는가**를 잰다.
⚠️ **액터의 누락률(41.8%)이 아니라 파서의 누락률로 판정한다** — 전자는 고쳐도 안 변하는 액터의
   성질이라, 그걸 기준으로 삼으면 체커가 **항상 빨간불이 되어 아무도 안 본다.**
| 파서 누락률 | 조치 |
|---|---|
| **10% 이상** | ❌ rc=1. **파서를 고치기 전에 착지하지 마라.** 그 채널의 신선도 지표가 통째로 틀린다 |
| 3~10% | ⚠️ 고치고 가되, 영향 받은 행 수를 로그에 남긴다 |
| 3% 미만 | ✅ 통과 (현재 **0.0% / 287명**) |

⚠️ **파생 규칙 — 지문(`_fingerprint`)의 재료를 바꿨으면 `_FP_VERSION`을 올려라.**
안 올리면 저장된 사람 전원의 지문이 달라져 **다음 실행에 전부 "변화 감지"로 오탐**한다
(실측: `linkedin_seen.json` **344명 전원이 v1**이었다 = 344명 재착지 + `재검토` 일괄 점등).
구버전 지문은 `_fp_comparable()`이 **비교 불가 → 조용히 재기준선**으로 처리한다.
</CRITICAL>

→ 착지 필드 매핑 전체: `core/notion.py._build_landing_properties` · 채널 스키마: `references/notion-schema.md`

---

## Project 위치

```
~/.claude/skills/net-sourcing/project
```

- 러너(머신별 재생성 venv): `project/.venv/bin/python`
- 엔트리포인트: `project/main.py`
- 설정(핸드오프 1파일): `project/config.yaml`
- 키워드/검색 스펙(수동 관리): `project/keywords.yaml`
- 자격증명(출력·커밋 금지): `.env` (Notion·Apify·Naver·ProductHunt)

편의 변수: `PROJ="$HOME/.claude/skills/net-sourcing/project"`

## 설계 원칙

- **어댑터(수집, AI 없음) ↔ 코어(판단·적재) 분리.** 모든 채널은 공통 `Candidate` 스키마로 정규화되므로,
  채널 API/우회가 바뀌면 `adapters/`만 교체하고 `core/`는 손대지 않는다.
- **judge는 최종 결정자가 아니라 트리아지(2차).** 지금은 수집·착지까지만. 판단은 명백한 것만 하드컷하고
  나머지는 근거와 함께 사람에게 넘기는 방식으로 추후 구현.
- **메일 발송·확정은 하지 않는다.** 이 스킬은 소싱까지만.

## 서브커맨드

| 커맨드 | 하는 일 | 참조 |
|---|---|---|
| `collect --channel {appstore\|threads\|linkedin\|linkedin_harvestapi\|linkedin_listly\|all}` | 수집만 → `data/raw/…json` (AI 없음, 어댑터별 분리 저장) | `references/<channel>.md` |
| `land --channel …` | 수집 raw → **2단 dedup** → 주간 소싱 DB에 Channel 태그로 착지 | `references/notion-schema.md` |
| **`run --channel …`** | **collect → dedup → land 일괄 = '스킬 한 번 실행'** | — |
| **`judge-export`** | Tier가 빈 행 → `data/judge_queue_*.json` (스레드는 **본문 글 전부** 동봉) | `references/judge-criteria.md` |
| **`judge-apply --file`** | 판단 결과 → 노션 `Tier`/`AI Reason`/`One Liner` | — |
| `screen` | [무동작] raw → 중복제거 + AI 판단 → `candidates_*.json` | — |
| `sync` | [무동작] 판단 통과분을 극초기 DB로 승격 | `references/notion-schema.md` |
| `report` | [무동작] 수요일 미팅용 다이제스트 | — |
| **`recheck`** | [리텐션·스레드] 대상군 인물의 **bio + 새 글**을 다시 봄 — 변화만 `재검토` ✅. **수동 전용**, $0.005/인 | `--dry-run`으로 대상·비용 먼저 |
| **`recheck-linkedin`** | [리텐션·링크드인] 프로필 지문 diff + **이탈임박**(tenure [9,12)) — 변화만 `재검토` ✅. **수동 전용**, $0.004/인 | ✅ **유료 응답 파싱 검증됨**(2026-07-21 · `--limit 5` 스모크 $0.02, 원본 대조) |
| `schedule {install\|uninstall\|status}` | launchd. **매일 잡이 이미 있으면 설치를 거부**한다(이중 과금) | `assets/net-sourcing.plist` |

`land`에 `--dry-run` 을 붙이면 **쓰지 않고** "신규 N행 / 갱신 M행 / 본문 P편"만 출력한다.

## 리텐션 (2026-07-20 개편) — 발굴이 아니라 **상태 변화 감지**

앱은 원샷이지만 사람은 다시 움직인다(이직·창업·런칭). **수집이 3채널, 리텐션은 2채널**이다
(앱스토어는 리텐션 대상이 아니다 — 결정 #13, 판단 없는 $0 보드로 유지).

| 레인 | 무엇 | 비용 | 롤백 |
|---|---|---|---|
| ① 재조우 표기 | 병합 순간 `Last Seen`+`재검토` 자동 표기. 링크드인은 90일 창 **안**에서도 지문 비교 | $0 (이미 산 데이터) | `followup.enabled: false` |
| ② `recheck` (스레드) | bio diff **+ 새 글 감지**. 대상군 = 타깃/빌더/Tier=Catch **+ `recheck.always_include`** | $0.005/인 · 월 ~$1.3 | 안 부르면 무동작 |
| ③ `recheck-linkedin` | 프로필 지문 diff + **이탈임박**. 링크드인엔 재확인 경로가 **아예 없었다** | $0.004/인 · 월 ~$0.12 | 안 부르면 무동작 |
| ~~④ 워치리스트~~ | **폐기됨(stage11)** — ②가 같은 인구를 5.2배 싸게·더 많은 글로·bio까지 본다 | — | `layers`에 되넣기 |

**주기 = 전원 7일 균일**(사용자 확정). 실측 근거: `latestPosts` 4편 창의 중앙값이 **7.1일**이고
절반(49.7%)이 7일 이하 → 더 느리면 다작자를 구조적으로 놓친다. 대상 ~60명이라 균일해도 월 $1.3다.
⚠️ 문헌(Cho & Garcia-Molina 2003)이 **"변화율 비례 배분은 균일보다 못하다"**를 반증했다 —
"많이 쓰는 사람을 더 자주"로 가지 마라.

**이탈임박 = tenure `[9, 12)`개월.** 발굴 축에 `재직 1년 미만` 서버측 필터가 걸려 있고
링크드인 타깃 정답지 19명은 **전원 tenure<6**이다 → 12개월을 넘으면 축에서 사라져 **영구 이탈**한다.
30일 주기면 9·10·11개월에 3회 본다. 🔴 **상한 12가 없으면** tenure 72·89개월인 사람이
'임박'으로 잡힌다(실제로 그렇게 짰다가 dry-run에서 발견 — 181명 $0.72 → 31명 $0.12).

### 🔴 `latestPosts` — 새 글 감지는 공짜다. 단 정렬을 믿지 마라
bio 액터가 `biography`와 함께 **이미 주는데 버리고 있었다**(실측 632/639명 보유·본문 98%).
→ 새 글 감지 **추가비용 $0**.
**최신 글은 반드시 `max(taken_at)`으로 고른다** (`core/followup.py::newest_post`).
실측: 최신순으로 오는 건 **49.8%**뿐 — `latestPosts[0]`을 쓰면 **243/606명(40.1%)에서 틀린다.**
(키워드 액터도 `sort=recent`인데 배송이 최신순이 아니다. 이 액터군의 공통 성질로 보인다.)

**필드 의미 (혼동 금지):** `Last Seen` = 마지막 **새 정보** 착지일(순수중복은 기본 미갱신 —
`touch_on_duplicate`) · `Last Checked` = recheck/bio백필이 확인한 날(빈-bio 재구매 방지 마커) ·
`재검토` = 변화 감지 표식. **🔴 재검토는 코드가 켜기만 한다 — 확인 후 끄는 건 사람이다.**
사람 뷰는 "재검토 ✅" 필터 뷰로 본다.

## 주 1회 판단 세션 (반자동 judge)

크론은 **수집·착지까지만** 한다. 판단은 사람(Claude Code 세션)이 한다 — API 키가 필요 없고,
상한 없는 LLM 지출이 생기지 않는다. (`config.judge.llm.enabled: false`)

```bash
"$PROJ/.venv/bin/python" "$PROJ/main.py" judge-export             # 전 채널 DB · 무제한
"$PROJ/.venv/bin/python" "$PROJ/main.py" judge-export --channel LinkedIn   # 채널만
#  → references/judge-criteria.md 를 읽고 큐를 판단해 verdicts json 작성
#     [{"page_id": "...", "verdict": "<그 행의 allowed 중 하나>", "reason": "...", "one_liner": "..."}]
"$PROJ/.venv/bin/python" "$PROJ/main.py" judge-apply --file data/judge_verdicts_{날짜}.json
```

- **판정 어휘는 채널마다 다르다** — 스레드·링크드인 = `Tier`(Catch/Trend/Wait) ·
  앱스토어 = `판정`(Pass/Drop, Tier 칸 자체가 없다). 큐 파일이 **행마다 `verdict_field`·`allowed`를
  들고 온다** — 문서를 믿지 말고 그걸 써라.
- 🔴 **`judge-export`는 채널 DB만 읽는다. base DB는 기본 제외**(`--include-base`로만 포함).
  base에는 채널 DB로 사본 복제된 스레드 635·링크드인 344의 **원본이 그대로 남아 있어**
  (결정 10-b, 아카이브 미실행) 같이 읽으면 같은 사람을 두 번 판정한다.
- `--limit` 기본 **0 = 무제한**. 예전 기본값 400이 07-16 실행을 정확히 400에서 끊어
  **링크드인을 큐에 아예 못 넣었다.** 스모크용으로만 지정해라(첫 채널에서 소진된다).
- `judge-apply`는 **쓰기 전에 전량 검증**한다. 판정값이 그 행의 `allowed`에 없거나 page_id가
  큐에 없으면 **아무것도 쓰지 않고 중단**한다. (그냥 쓰면 노션이 값을 조용히 버리고 PATCH는
  200을 준다 — 로그엔 "적용 완료", 실제 판정은 전부 빈칸이 된다)
- 앱스토어 DB엔 `AI Reason` 칸이 **없다.** 그 채널의 사유는 `data/judge_reasons_{날짜}.json`
  사이드카에 남는다(조용한 삭제 없음). 노션에서 보려면 그 DB에 칸을 추가해야 한다.

`--channel linkedin`은 별칭 → `linkedin_harvestapi`(무인·대량) + `linkedin_listly`(반자동 파일드롭) 둘 다.

## 실행법

```bash
PROJ="$HOME/.claude/skills/net-sourcing/project"
"$PROJ/.venv/bin/python" "$PROJ/main.py" run --channel all           # 3채널 수집 → dedup → 착지
"$PROJ/.venv/bin/python" "$PROJ/main.py" collect --channel linkedin_harvestapi
"$PROJ/.venv/bin/python" "$PROJ/main.py" land --channel all           # 수집분만 dedup→착지
```

1. 사용자가 채널/커맨드를 안 밝히면 물어본다. 기본은 `run --channel all`.
2. stdout 진행 로그(✅/🎉/⚠️)를 요약해 전달. 특히 `skip`·`실패`·`토큰 없음`·`⚠️ HEALTH`(0건 반환 = 액터 파손 의심) 라인을 표면화.
3. 착지가 404/0건이면 HARD-GATE 두 전제(통합 DB 공유·토큰)부터 확인하라고 알린다.

## 채널 세부 (references/ — 필요할 때만 읽기)

- `references/appstore.md` — **전체(genre 36) 종합 차트 · 무료 · 2주 전 대비 신규진입만**(`new_entry_only`).
  자체 히스토리 없어도 `baseline_bridge`로 보드 스냅샷 재사용해 즉시 판정.
- `references/threads.md` — **3층(키워드 백본·팔로잉그래프·리플라이)**. 액터 slug 검증됨, payload 정렬은 첫 실행.
  시드계정·앵커포스트(`keywords.yaml`)가 비면 해당 발굴층은 조용히 no-op.
- `references/linkedin.md` — **2어댑터: HarvestAPI(무인·대량·no-cookie) + Listly(반자동 xlsx/csv 드롭)**.
  넓게 UNION → URN/URL dedup → 소프트 스코어링 → (judge 추후).
- `references/judge-criteria.md` — 그물망 판단 기준 (판단 에이전트/2차).
- `references/notion-schema.md` — 주간 소싱 DB 채널별 필드 매핑·Channel/LinkedIn URL/주차 등.

## 프록시 · 헬스체크

- **프록시**(`config.apify.proxy`): 기본 `none`(무료). 데이터센터 IP 차단 의심(`⚠️ HEALTH` 0건 다발) 시
  `residential`(Apify 유료 $8/GB)로 한 줄 전환. `residential`일 때 `proxy_country`로 지역 지정.
- **0건 헬스체크**: Apify 액터가 0건 반환하면 `⚠️ HEALTH` 경고(Meta doc_id 주간 로테이션·차단 감지). 알림 목적지=로그.

## 스케줄

🔴 **현재 메인 파이프라인 크론은 꺼져 있다**(결정 #6, 수동 실행). 돌고 있는 건 **앱스토어 전용
크론 2개뿐**($0). 켜기 전 조건은 아래 [크론 켜기 전 체크리스트]를 보라.

### 층·축별 주기 (2026-07-20 신설) — "얼마나 자주 사느냐"가 주 레버다

| 어디 | 노브 | 현재 |
|---|---|---|
| 스레드 **층** | `channels.threads.layer_cadence` | keyword·replies `always` · following_graph **`biweekly`** |
| 링크드인 **축** | `keywords.yaml` 축별 `cadence` | flow-new-founder·**gap-practitioner `biweekly`** · stealth-us `monthly` · 나머지 `always` |

- 어휘의 정본은 **`core/cadence.py`** 하나다(always/daily/weekly/biweekly/monthly/quarterly).
  두 채널·추정기가 같은 표를 본다 — 복사하면 언젠가 한쪽만 고쳐진다.
- 🔴 **표에 없는 이름을 쓰면 크게 경고하고 weekly로 처리한다.** 예전엔 `.get(cadence, 7)`이
  **조용히** 강등해서 "절감이 왜 안 나지"를 며칠 뒤에야 알았다.
- 🔴 **`cadence`는 고정 주기가 아니라 최소 간격이다.** 수동·비정기 실행이면 실간격이
  `ceil(gap/실행리듬)×실행리듬`으로 늘어난다 — 10일 리듬이면 격주 축은 **20일**마다 돈다.
- 상태: `data/threads_layer_state.json` · `data/linkedin_axis_state.json`.
  **비어 있으면 전 층/축이 한 번은 돈다**(모르면 도는 쪽) → 첫 실행은 최악주 요금이다.

### 링크드인 축 6개 (2026-07-27) — **앞의 5개와 6번째는 논리가 반대다**

| 축 | 찾는 것 | 모수 | p | 주기 |
|---|---|---|---|---|
| `flow-new-founder` | 방금 창업 직함이 된 사람 | 237 | 10 | 격주 |
| `stealth-kr` / `stealth-us-krschool` | 회사명이 Stealth | 444 / 145 | 4 / 6 | 매주 / 월1 |
| `early-kr` / `posted-early` | 극초기 스톡 / 최근 글 | 1,348 / 496 | 3 / 2 | 매주 |
| **`gap-practitioner`** | **퇴사하고 아직 아무것도 안 올린 실무자** | **67,881** | 2 | 격주 |

🔴 **6번째 축에는 `currentJobTitles`·`yearsAtCurrentCompanyIds`·`excludeCurrentJobTitles`를 걸지 마라.**
전부 **현 직함이 있어야 성립**하는 조건이라 공백자를 구조적으로 배제한다(실측: 5축으로 모은 271명 중
공백자 **0명**). 리크루터 제외도 같은 이유로 `pastJobTitles` 축엔 자동 미적용이다 — 걸면 "공백형 0명"이
나와 **축이 고장난 것으로 오진**한다. 산출 0이면 어댑터가 🔴 HEALTH를 찍는다.

**공백형은 칸이 다르게 찬다** — `Tenure Months`·`창업 개월`이 **빈칸**(해당 없음)이고 `공백 개월`이 찬다.
그래서 뷰를 갈라야 한다(🚀 창업형 = 창업 개월↑ / 🕳️ 공백형 = 공백 개월↑).
⚠️ 공백형 뷰 필터를 `Tenure IS EMPTY`로 걸지 마라 — **날짜가 없어 못 재는 사람 15명**이 섞인다
(빈칸=미확인이지 공백이 아니다). `공백 개월 ≥ 3 OR 본업 공백 ≥ 3`으로 건다.

**리텐션이 이 축의 절반이다.** 공백자는 tenure가 빈칸이라 `이탈임박[9,12)`에 안 걸린다 →
`recheck_linkedin`에 `(공백 OR 본업공백) [3,18)`을 넣어야 **"무직 → Founder @ X"로 바뀌는 순간**을 잡는다.
상한 18이 없으면 공백 62개월인 사람을 영원히 재확인한다(이탈임박 상한을 빠뜨렸던 것과 같은 함정).

### 크론 켜기 전 체크리스트
1. ✅ 월 예산 가드(`monthly_budget_usd`) — 있음. 크론은 `--force-budget`를 **쓰지 마라**.
2. ✅ 실패 감지 — 전멸/과반실패면 **종료코드 1** + `data/last_run_health.json`.
   `install-cron-appstore.sh status`가 맨 위에 보여준다.
3. ⬜ **실패 알림(푸시)** — 아직 없다. 지금은 사람이 `status`를 봐야 안다(웹훅 URL 필요).
4. ⬜ 결정 #6 뒤집기 — 주 1회면 월 ~$35라 "매일 $250" 근거는 성립하지 않는다. **사용자 결정.**

- ~~**수집·착지 = 매일 08:00 무인**~~ (현재 미로드. `install-cron.sh`는 `--channel all` =
  **매일 돌리면 월 $250**이다 — `install-cron-appstore.sh`와 헷갈리지 마라).
- **판단 = 반자동**(주 1회 Claude Code 세션). `config.judge.llm.enabled: false` —
  크론이 LLM을 부르지 않는다. true로 두면 누가 `.env`에 키를 넣는 순간 **상한 없는 LLM 패스가
  조용히 되살아난다**(크론이 매일 도는데 아무도 모른다).
- 🔴 **launchd가 두 벌 있다** — `com.kv.net-sourcing.daily`(매일)와 `com.kakaoventures.net-sourcing`
  (매주 화, `assets/net-sourcing.plist`). **둘 다 로드되면 화요일에 이중 실행·이중 과금**된다.
  `main.py schedule install`은 매일 잡이 떠 있으면 **설치를 거부**한다.

## 주의 / 한계

- **착지 전제(HARD-GATE 재확인)**: 통합을 주간 DB(`EXAMPLE_ID`)에 공유 안 하면 404, 토큰 없으면 해당 채널 0건.
- **앱스토어 신규진입 = baseline 필요**: 2주 전 스냅샷 대비 판정. 히스토리 얕으면 판정 폭이 좁다(`baseline_bridge`로 보드 재사용).
- **스레드 발굴 2층 = 스모크 검증 완료**(2026-07-13): 팔로잉그래프(igview, @zenonplane→10명)와
  리플라이(앵커 2개→댓글 17건·고유 작성자 15명) 둘 다 실데이터로 작동 확인.
  단 `keywords.yaml` 시드계정·앵커가 비면 여전히 no-op다.
  ⚠️ **팔로잉그래프는 글이 없어도 사람을 착지시킨다**(`Post Count=0` = "발견했지만 아직 말 안 한 사람").
- **링크드인 Listly = OFF**(`enabled: false`). 드롭 폴더가 비어 매 실행 no-op였고, harvestapi 축이
  12개로 늘어 필요가 없다. Listly가 하던 자유 키워드 검색은 `searchQuery` 축이 대체한다.
- **레거시 대조 정확도(후속)**: `existing_dedup_keys`는 현재 주간 DB 자기대조 중심 — 극초기+스타트업 DB 재타깃은 후속(정확도↑).
- **판단은 2차 이월**: `screen`/`sync`(L1 AI·승격)는 추후. 지금 `run`은 수집→dedup→착지까지만.
- **비원자적 쓰기**: 착지 중 실패해도 dedup로 재실행 중복은 방지되나, 부분 실패 시 로그의 `✗ 착지 실패` 라인 확인 후 재실행.
