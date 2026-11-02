---
name: linkedin
description: 링크드인에서 극초기 창업자를 주 1회 사서 Tier 를 판정하고 노션에 전량 착지, 평일마다 5명(판정 순 4 + 탐색 1)을 #그물망 카드로 올린다. ✅ 면 연락 문안 + 극초기 승격 + 2일 뒤 리마인드, ❌ 는 "이번엔 관심 없음". 정답은 심사역 반응. 30일마다 프로필을 다시 긁어 바뀐 사람을 다시 올리고, 주 1회 반응에서 특징을 뽑아 기준 수정안을 낸다.
schedule:
  - { cmd: collect, when: "월 09:10", why: "이때만 돈이 나간다. 축별로 차례인 것만 돈다" }
  - { cmd: card,    when: "매일 11:00", why: "정현님이 훑은 결과가 반영된 뒤에 나간다. 평일만 — 주말엔 아무것도 안 한다" }
  - { cmd: followup, when: "10분마다", why: "심사역이 언제 누를지 우리가 못 정한다. 누르고 10분 안에 문안이 뜬다" }
  - { cmd: remind,   when: "매일 10:00", why: "✅ 2일 뒤 누른 분을 핑. 주말·공휴일엔 코드가 쉰다 — 그날 몫은 다음 평일에 나간다" }
  - { cmd: recheck,  when: "월 09:40", why: "collect 뒤. 감시 목록 중 30일 된 사람만 다시 긁는다 — 돈이 나가는 두 번째 자리(월 ≈ $6)" }
  - { cmd: learn,    when: "월 10:10", why: "recheck 뒤. 한 주 반응을 모아 특징 표 · 기준 수정안. 매일 돌리면 하루 5장이라 숫자만 흔들린다" }
---

# 링크드인 소싱

2026-09-25 에 메일과 같은 뼈대로 옮겼다(`kvlib` store·gate·report). **동작은 그대로다** — 골든 dry-run 넷이 전·후 동일, 특성화 테스트 27개가 먼저 잡았다.

## 🔴 밖으로 나가는 여섯은 문 하나를 지난다 — 공개 배포의 문은 **닫혀 있다**

```
$KV_CONFIG/deployment.yaml  linkedin.outbound: "off"     ← 공개 배포 기본값. 확인 후 slack/on을 명시한다
  buy       Apify 구매(돈) — collect·recheck   off·slack 이면 안 산다. 계획만 찍는다
  land      노션 착지                    〃 dry
  card      #그물망 카드(헤더+스레드)     slack 이상이면 나간다
  outreach  ✅ 스레드에 연락 문안          〃
  remind    ✅ 2일 뒤 #그물망 본문에 핑     〃
  promote   극초기 DB 승격 + 링크드인 행 표시   on 이어야. linkedin.promote: false 면 "gated"
  refresh   recheck 가 바뀐 사람의 노션 행을 갱신   on 이어야
  overview  카드가 나간 사람의 노션 행 본문에 상세 개괄(출처 포함)   on 이어야
```
`--dry-run` 은 이 실행만 문을 내리는 것(`Gate(force="off")`)이다. 무엇을 했(을)는지는 `~/kv-data/linkedin/db/linkedin.sqlite` 의 `actions` 에 남는다.
장부(`~/kv-data/_ledger/linkedin.jsonl`)는 그대로 **한 일의 기록**이고 card/followup 의 멱등 키다. 두 곳에 적히지만 역할이 다르다.
끄려면 `outbound: "off"` 한 줄. 되돌리기도 그 한 줄.

## 정답은 심사역의 반응이다 [사용자 결정 2026-10-14]

```
✅            관심 — 연락 문안·승격·리마인드                         학습: 양성
❌            이번 주엔 관심 없음 — 영구 아님(recheck 가 다시 올린다)    학습: 안 고름 · 그날을 '본 날'로 표시
본 날 무반응   그날 카드를 봤는데 안 고름                              학습: 안 고름 (❌ 와 같은 뜻)
안 본 날 무반응 스레드 전체에 반응 0 — 정답이 아니다(10/2~13 카드 40장 반응 0 — 바빴다)
```
노션 `Tier` 는 AI(claude + criteria.md)가 매긴 값이라 정답이 아니다 — 카드 순서에만 쓴다. 옛 보고서 🚩 절이 그걸 정답으로 대서 437/437 일치만 찍었다.
✅·❌ 가 같이 붙으면 ✅(연락은 누른 분 개인 계정 · 놓치는 쪽이 비싸다). ❌ 만이면 7일 기다린다(다른 분 ✅ 대기).
7일 뒤 늦게 누른 반응도 followup 이 최근 `learn_weeks`(4)주를 하루 한 번 다시 읽어 잡는다 — 늦은 ✅ 도 문안·승격·리마인드가 자동.

## 하루

```
월 09:10  collect   축별로 차례인 것만(주차 축 제외) → Apify → 원본 아카이브 → 장부로 중복 제거 → 사후 조건
                    → claude 50명 배치 판정 · **사람별 저장**(judgments: 모델이 본 줄·낸 줄·기준 해시) → 전량 노션 착지
매일 11:00 card     평일만. 반응 정답 동기화(label) → 재고 + 다시 올라온 사람 → 판정 순 4명 + 판정 하위(Wait·Cut)에서 탐색 1명
                    (탐색은 표시 안 함 — 위만 올리면 하위 판정이 맞는지 영영 모른다) → 헤더 + 사람별 스레드
                    고르기 전 이미 본 팀 거르기(10/21): 고를 사람의 회사명(+claude 다른 표기)을 스타트업 DB(패밀리 포함) 제목과 대조 →
                    맞으면 빼고 Legacy, 다음 사람이 채운다(known.py, 못 읽으면 멈춤). 고른 뒤 노션 본문 검색(claude.ai 노션 커넥터) →
                    `⚠️ 노션 기존 기록` 표시만(history.py, 실패해도 나감 · 전원 실패면 하루 한 번 알림)
                    → 하루 보고서(파일 + review_test 요약)
10분마다  followup  ✅ → outreach.md 문안(Reviewer A 심사역 문안, 10/16)에 {이름}(성 뺀 한글 이름)·{발신자}(누른 분)·{맞춤}·{궁금} 채워 스레드에 → 극초기 승격
                    {맞춤} = pitch.py: 최신 프로필 + 카드 때 웹 개괄(posted.overview) → claude 가 칸 구절(제품·성과·이력)+원문 근거 →
                    코드가 근거·숫자·끝말 대조 → Reviewer A 문장 틀에 끼움. 근거는 문안 아래(보내기 전 확인). 실패해도 첫 문장으로 나간다
                    7일째 결론: ❌ 있으면 reaction_no · 없으면 expiry · 하루 한 번 최근 4주 늦은 반응 훑기
매일 10:00 remind   ✅ 누른 날 + 2일(주말·공휴일이면 다음 평일, 목·금 → 월) → #그물망 본문에 "@누른분 김가람(회사) 님께 연락 보내셔야 합니다"
                    이름은 names.py 가 한글로(`가람 김` 는 규칙, 영문은 claude, 확신 없으면 원문 병기). 한 사람 한 번. [사용자 결정 2026-09-29]
월 09:40  recheck   감시 목록(국적·나이·✅·손댄 행 빼고 그때 안 맞았던 사람 전부) 중 30일 된 사람 → profile-scraper
                    → 회사·직함 지문 비교 → 바뀐 사람만 Tier 재판정·노션 갱신(Status New·변화 요약·Last Checked) → 다시 재고(🔄 줄)
월 10:10  learn     최근 4주 반응 다시 읽기 → 특징 표(구간별 ✅율·95% 구간, 표본 부족 표시) → 문턱 넘으면 criteria 수정안 + replay
                    → ~/kv-data/linkedin/reports/learn-<날짜>.md. 🔴 criteria.md 는 자동으로 안 바뀐다 — 사람이 승인
매일 12:55 ops health  평일에 오늘 보고서 파일이 없으면 review_test 에 한 줄 (skills/ops 가 여섯 스킬을 한 번에)
```
**전량 착지하는 이유**: 심사역이 노션을 직접 열 수 있어야 한다. 슬랙엔 그중 하루 5명만 간다.
🔴 **우리가 보내지 않는다.** 콜드 메시지는 심사역 개인 계정으로 나가는 글이라 글만 써서 올린다. **발신자는 ✅ 를 누른 분**이다 — `people.yaml` 명단 밖이면 이름을 비우고 `발신자 이름을 채워주세요`.

## 사람이 하는 것 — 기준 깎기

```
정답      심사역이 카드에 ✅·❌ 를 누른다. 그게 전부다 — card 가 매일 judgments(human) 으로 가져간다
보고서    ~/kv-data/linkedin/reports/<날짜>.md · 🚩 절 = 판정과 반응이 갈린 사람 · learn-<날짜>.md = 주간 특징 표·수정안
승인      learn 이 매주 지금 기준의 성적(Catch 중 ✅)을 찍고, 수정안이 더 나으면 반영 명령을 적는다 — 사람이 승인(Reviewer C yes/no)
          cp <수정안> skills/linkedin/criteria.md → python -m tools.linkedin_retier --apply (재고 Tier 다시 매기기)
기준      skills/linkedin/criteria.md
확인      run replay [--limit N] [--criteria 파일]   저장된 사람을 고친 기준으로 다시 판정 (Apify 0 · 노션 0)
          run diff --b r-<sha>                        무엇이 달라졌나 · 정답과는 어떤가
```
replay 는 기준 해시로 run 이름을 만든다. 같은 기준이면 이어서, 고치면 새 run. `live` 판정은 안 바뀐다.
저장은 2026-09-25 부터라 표본이 매주 25~100명씩 쌓인다. 옛 착지 664명은 원본 아카이브(`~/kv-data/linkedin/raw`)로 채울 수 있다(후순위).

## 명령

```bash
cd ~/kv
./.venv/bin/python -m skills.linkedin.run collect [--axis <축>] [--pages N] [--dry-run]
./.venv/bin/python -m skills.linkedin.run card [-n 5] [--dry-run]
./.venv/bin/python -m skills.linkedin.run followup [--dry-run]
./.venv/bin/python -m skills.linkedin.run remind [--dry-run]
./.venv/bin/python -m skills.linkedin.run status            # 축별 마지막 실행·다음 페이지·커버리지·재고
./.venv/bin/python -m skills.linkedin.run label             # 슬랙 반응 → human (card 가 매일 하지만 손으로도)
./.venv/bin/python -m skills.linkedin.run recheck [--limit N] [--dry-run]
./.venv/bin/python -m skills.linkedin.run learn [--dry-run]
./.venv/bin/python -m skills.linkedin.run replay | diff --b r-<sha>
```

## 구성

```
run.py         collect · card · followup · 보고서 · 진입점
judge.py       판정 — 줄 단위(judge_lines). collect 와 replay 가 같은 줄을 쓴다. 배치 50(쿼터 결정)
../../kvlib/judgments.py   판정 저장 · label(노션 Tier) · replay · diff — 네 스킬 공용
axes.py        수집 축(Apify 질의)과 사후 조건(reject) · 카드 기준(card_reject)
profile.py     Apify 원본 → 키·경력·신호
brief.py       카드 두 문장 + 회사명 (claude)     outreach.py  연락 문안 채우기 (원문 outreach.md)
pitch.py       연락 문안 맞춤 문단 (claude, 프롬프트 pitch.md) — 틀 고정·구절만, 원문 대조
known.py       스타트업 DB 회사명 대조 (claude 다른 표기 known.md + 노션 필터)   history.py  노션 본문 검색 표시 (커넥터, history.md)
names.py       ✅ 리마인드에 쓸 한글 이름 (규칙 → claude)
recheck.py     재확인 — 감시 목록 원칙 · 지문 비교 · 다시 재고로     features.py  카드의 특징(코드, 고정 목록)
learn.py       주간 학습 — 특징 표 · 수정안 · 사람 이름 유출 검사
../../tools/linkedin_reaction_backfill.py   정답을 반응으로 옮길 때 한 번(소급 · 입력 복원) — 다시 돌려도 된다
../../tools/linkedin_retier.py   기준을 바꾼 뒤 재고 Tier 다시 매기기(노션 덮어쓰기)
../../tools/linkedin_recheck_reassess.py   변화 규칙을 고친 뒤 그날 다시 올린 사람 재판정
criteria.md    Tier 기준 — 사람이 깎는다
../../people.yaml   심사역 명부(슬랙 ID → 성함). 메일과 같은 파일
```
`constraints.yaml linkedin.*`의 일반 업무 기준: card_channel(net) · report_channel(test) · card_tiers · followup_days · stock_warn_days · outreach_batch · outreach_pitch · card_known · card_history · remind_* · learn_weeks · learn_min_n · recheck_days · recheck_batch · recheck_max. 승격 게이트·티어·재고 규칙의 **근거는 그 주석에**.

## 알아둘 것 (지우지 않은 결정)

**재고가 많다고 안 사지 않는다** [사용자 결정 2026-09-29]. 전에는 안 나간 사람이 `backlog_cap`(100) 이상이면 안 샀다 — 08-24~09-28 매주 건너뛰는 사이 카드는 08-17 재고와 옛 시드만 돌았고, 산 날에 멈춘 재직·창업 개월로 걸러져 품질이 떨어졌다. 지출 상한은 축의 주기·페이지 수가 맡고, 카드는 같은 등급 안에서 **최근에 산 사람부터** 올린다. 옛 축 6개(posted-early·early-kr·stealth-kr·flow-new-founder·gap-practitioner·stealth-us-krschool)는 `parked` — s-* 가 대신한다. 재고는 **노션 기준**으로 센다 — 장부로 세면 card 와 다른 창고를 보게 된다(09-01: 닷새간 카드 0건).

**넣는 양 ≥ 쓰는 양** [사용자 결정 2026-10-15]. 9/29 뒤 2주 반 동안 산 건 10-12 의 3페이지(새 사람 21명)뿐이었고 카드는 주 25장을 썼다 — 재고가 7월 사람으로 늙었다. 원인 셋과 고친 것:
- 주기는 **주(월요일 기준)로** 센다(`_due`). 날짜 차로 세면 09-29(화) 수동 실행이 10-05(월) 정기 실행을 "6일째"로 밀어냈다.
- 커서가 **목록 끝에 닿으면 1페이지로** 돌아간다(장부 axis `wrapped`). 10-12 s-stealth 가 p19 에서 빈 페이지를 샀다.
- 페이지: s-recent-title·s-recent-class·s-serial 3 · s-stealth 2 · s-gap 1(2주) → 주 11페이지 ≈ $2.2.
- **s-serial(연쇄 창업자)** 축 신설 — ✅ 공통점 1순위(연쇄창업 48% · 기저 35%)를 살 때 잡는다. 현·과거 직함 모두 창업 직함 + 재직 2년 이하 + 10인 이하. 시험 구매 2회: 재직 조건 없이는 6년 된 회사 대표·40세+ 절반.
- **카드 재직 컷 3·12 → 24개월**(`RECENT_MONTHS`·`STEALTH_MAX_MONTHS`). 반응 63장에 컷별로 적용하니 재직은 ✅ 를 가르지 못했다(통과군 ✅율 33~36% 고정) — 3개월 컷은 ✅ 22명 중 13명을 버렸다. Reviewer B·Reviewer C에겐 사후 공유.

**Legacy** [사용자 결정 2026-09-29] — 국적·나이(`axes.legacy_reason`)·비창업(문안 판정)에 걸린 사람은 노션 `Status=Legacy`. 재고에 안 들고, 장부 landed 라 다시 처리되지 않는다. collect 는 외국인·40세 이상을 Tier 판정 없이 Legacy 로 착지하고, card 는 기존 행을 만나면 Legacy 로 바꾼다. card 는 카드 기준(`card_reject`)에 안 맞는 행도 전부 Legacy 로 보낸다 — 노션 행의 재직·공백·창업 개월은 산 날 값이고 아무도 다시 안 재며(옛 재검토 시스템 09-26 제거), 장부에 landed 라 다시 사지도 않으니 지금 안 맞으면 영영 안 맞는다. 사람이 손댄 행(타깃·DB로 업데이트·극초기 DB·Status 가 New 아님)은 건드리지 않는다. 사람이 Status 를 New 로 바꾸면 다시 재고가 된다(단 기준에 안 맞으면 다음 card 에서 다시 Legacy).

**후보는 노션 행에서, 정답도 노션에서.** 장부 landed 1,234행은 옛 시스템 시드라 `page_id` 가 없다. 장부로 후보를 뽑으면 그 사람들이 카드에 영원히 안 나온다.

**이름으로 대조하지 않는다.** `Minsu Kim` 같은 흔한 이름이 통째로 오제외됐다. 사람은 URL, 회사는 홈페이지·회사명 정규화. 스텔스 창업자의 가짜 홈페이지(`linkedin.com/company/stealth`, 15%)는 대조 키가 아니다.

**Status=Reviewing 은 "극초기에 등록됨"의 뜻** [2026-08-20]. 안 쓰이던 값을 재정의했다. 사람이 "검토 중" 뜻으로 켜면 안 된다. 극초기 행은 `2. 미팅요청` + `슬랙 발송? True` 로 넣는다 — 교통정리 뷰가 그 칸으로 거른다.

**Tier 는 값으로 찾는다.** claude 가 입력의 축 칸을 출력에 옮겨 위치가 흔들렸다(08-17, 116줄 20% 유실). 열 위치를 믿지 않는다.

**`currentPosition` 만 보면 안 된다**(1,167명 중 396명 누락) · **`endDate` 는 dict 다** · **URL 을 인코딩하지 마라**(한글 vanity 6%) · **점수 매겨서 줄 세우지 마라**(랭킹 정보량 ≈ 0, 실측 97명).

**알림은 하루 한 번, 종류마다** — followup 은 10분마다 돈다. 같은 실패를 144번 보내면 그 채널은 그날로 무시된다. 실패·재고 알림은 review_test 로(심사역 채널이 아니다).

**읽기에 성공한 스레드만 만기시킨다.** 망 오류 한 번에 사람이 조용히 사라지지 않게. 만기 7일 = 결론(카드가 매일 새로 오는 구조라 리마인드 대신) [사용자 결정 2026-09-25 유지].

**장부 ts** — 09-25 전 195줄은 슬랙 메시지 ts 가 `ts` 칸에 들어 있다(결함). 이후는 `slack_ts` 칸. 읽는 쪽은 둘 다 본다.

**추정 40세 이상은 안 올린다** [사용자 결정 2026-09-29 "나이가 많다"]. 생년이 없어 `profile.estimated_age` 가 학부 입학 연도(+19) 또는 첫 경력 연도(+23)로 역산해 노션 `추정 나이` 칸에 남기고 `card_reject` 가 본다(`axes.MAX_AGE`). 추정 불가는 통과. 첫 경력 역산은 나이를 **낮게** 잡는 쪽으로 틀린다(재학 중 인턴 · 잘린 경력) — 새는 쪽이지 버리는 쪽이 아니다. 옛 재고는 `tools/linkedin_age_backfill.py` 가 채웠다(122명 중 23명 제외 → 99명).

**카드에 회사 개괄 3줄** [사용자 피드백 2026-10-15 "회사 법인명 웹/링크드인에 검색해서 개괄 간단히"]. 올릴 사람만(하루 5명) `overview.py` 가 claude 웹 조사(`claude.web_research` — 도구는 WebSearch·WebFetch 둘뿐)로 무엇을·단계·회사(법인/개인사업자·인원·웹 노출)를 카드에 3줄, 같은 호출의 상세 8항목(출처 링크)을 노션 행 본문에 붙인다(문 `overview`). 지어내지 않고 못 찾으면 `확인 안 됨`(overview.md). 동시에 부르고 5명 ≈ 2분. 실패하면 그 사람만 개괄 없이 나간다 — 관문이 아니다. 끄기: `linkedin.card_overview`. 발단: 10명 시험에서 Catch 4명이 무역·대행·자문·토큰 사업이었는데 프로필로는 안 보였다.

**창업자가 아니면 안 올린다** [사용자 결정 2026-09-29]. 판정은 카드 문안을 쓰는 claude 가 같은 호출에서 낸다 — `창업 · 준비 · 비창업`(brief.md). `비창업`만 뺀다(`준비`는 축 B 경력단절의 대상이다). 직함 OR 목록으로 안 한 이유: 다음 표현에서 새고, "기존 회사 대표 승계"(Founder B)는 직함으로 원리상 안 갈린다. 판정은 장부 `screened` 에 남아 card·`_backlog` 가 같이 보고, 통과자의 문안은 올릴 때 재사용한다. 판정 못 받은 사람은 그날 안 나가고 다음에 재판정. `./kv linkedin screen` = 재고 전체 판정(슬랙·노션 0). 발단: 09-29 미리보기 5명 중 4명이 합류자·승계 대표. 첫 screen: 156명 → 창업 107 · 준비 15 · 비창업 34.

**외국인은 안 올린다 — 국적 기준, 거주지 아님** [사용자 결정 2026-09-28]. 해외 거주 한국인은 남기고 한국 거주 외국인은 뺀다. 액터에 국적 필터가 없어 사는 것은 못 막는다 — collect 가 산 뒤 판정 전에 `judge.nationality`(claude)로 가르고 뺀 수를 `post_filtered` 에 `<축>/외국인(판정)` 으로 남긴다. 옛 재고는 `tools/linkedin_nationality_backfill.py` 가 노션 `Korea Signal` 에 `외국인` 태그를 달았고 `card_reject` 가 그 태그를 본다(사람이 지우면 다시 나간다). 모르면 한국인 쪽. **KAIST 축은 예외**(유학생도 대상 — Reviewer C 요청 09-15, 09-28 재확인). 발단: 09-28 카드에 옛 시드 재고 외국인 2명이 나갔다 — 수집 때 거르는 `reject()` 는 시드 재고를 안 거쳤고, 카드 필터엔 국적 조건이 없었다. 재고 193명 중 37명이 외국인이었다(서울 거주 ~20 · 교환학생 한 줄로 `KR-학교` 받은 해외 거주자 ~10).

**학력 축(elite-kr · elite-us-kr · elite-gap)은 2026-09-01 에 은퇴했고 코드는 09-26 에 지웠다** — "학력은 안 건다"(사용자 결정). 1티어 35명 Catch 34% 가 학교 덕인지 스텔스 조건 덕인지 끝내 안 갈렸고, s-* 축이 대신한다. 그 안에 있던 실측은 여기만 남는다:
- 학교 23곳을 한 축에 넣고 locations 를 [한국, 미국]으로 두면 **한국 연고가 통째로 빠진다** — schools 도 locations 도 OR 라 "미국 학교 + 미국 거주"가 앞을 다 차지했다(50명 중 한국 학교 1명 · 모수 10,038). 축은 쿼리마다 한국 연고를 강제하게 가른다.
- 액터의 `schools` 필터는 학력 **아무 줄**에나 걸린다(대학원·교환 한 줄로 통과 115명 중 31명). 학교를 조건으로 쓰려면 **학부 줄의 정규화 이름 정확 일치**만 — 부분문자열 함정: `Seoul National University of Science and Technology`(서울과기대) · `Korea National Open University` · `Columbia College Chicago` · `Stanford Continuing Studies` · `UC Berkeley Extension`. 연도 미상은 컷(08-19 결정, 두 번 뒤집힌 끝).
- **10-15 정답(✅)으로 다시 쟀다**: 스텔스+명문대 축 ✅ 14/25 = 56% · 같은 8월의 학교 없는 스텔스 축 2/10 = 20%. 표본은 작지만 "학교 덕인지 스텔스 덕인지"가 처음 갈렸다. 다만 1페이지 25명 중 22명이 이미 본 사람(모수 325) — 되살린다면 월 1회 전량. Reviewer B 확인 대기.
- 경력단절은 **발굴이 아니라 리텐션 신호**다 — elite-gap 모수 297,197 · 천장 2,500 = 0.8%, 1페이지 25명 중 6개월내 공백 0명($0.20 실측). 액터에 "현 직장 없음" 필터가 없다.

## 공개 배포의 설정 위치

행동 허가(`outbound`), Slack 보고, 승격과 메일의 별도 허용 목록은 `$KV_CONFIG/deployment.yaml`에서 명시한다. 파일이나 스위치가 빠지면 닫힌 값으로 처리한다. 일반 업무 기준·API 한계는 `constraints.yaml`을 사용한다. 위의 날짜별 운영 기록은 과거 맥락이며 현재 배포 허가를 대신하지 않는다. 우선순위와 메일 예외 범위는 [운영 가이드](../../docs/OPERATIONS.md#로컬-배포-설정)를 따른다.
