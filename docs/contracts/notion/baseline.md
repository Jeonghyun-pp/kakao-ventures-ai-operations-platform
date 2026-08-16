# baseline.md — 재빌드가 '같은 결과'를 냈는지 재는 기준선

측정 시각 기준 실측(2026-08-16 KST). 개인정보 원문 없음.

## ① `판단 점수`·`판단 이유`·`프로필 점수`·`점수 근거` — 어느 DB에 있고, 실제로 쓰이나

| 칸 | 존재하는 DB | 타입 | 채워진 행 / 총행 |
|---|---|---|---|
| `판단 점수` | ch_링크드인 | number | **0** / 1151 |
| `판단 이유` | ch_링크드인 | rich_text | **0** / 1151 |
| `프로필 점수` | base_주간소싱 | number | **0** / 1470 |
| `프로필 점수` | ch_링크드인 | number | **0** / 1151 |
| `점수 근거` | base_주간소싱 | rich_text | **0** / 1470 |
| `점수 근거` | ch_링크드인 | rich_text | **0** / 1151 |

## ② `Tier`(및 앱스토어 `판정`) 허용값 · 분포 · 판정 완료율

| DB | 칸 | 선언된 허용값 | `Cut` 선언? | 분포 | 판정완료 / 총행 | 완료율 |
|---|---|---|---|---|---|---|
| base_주간소싱 | `Tier` | Catch / Trend / Wait / Cut | ✅있음 | (빈칸)=1470 | **0** / 1470 | 0.0% |
| ch_링크드인 | `Tier` | Catch / Trend / Wait / Cut | ✅있음 | Wait=409 · Trend=295 · Cut=239 · Catch=206 · (빈칸)=2 | **1149** / 1151 | 99.8% |
| ch_스레드 | `Tier` | Catch / Trend / Wait / Cut | ✅있음 | Cut=518 · Trend=57 · Wait=53 · Catch=7 | **635** / 635 | 100.0% |
| ch_앱스토어 | `판정` | Pass / Drop | ❌없음 | (빈칸)=2671 | **0** / 2671 | 0.0% |
| dedup_극초기 | `Tier` | Catch / Trend / Wait | ❌없음 | (빈칸)=448 · Trend=423 · Catch=299 · Wait=128 | **850** / 1298 | 65.5% |

## ③ 비플랜 DB(mail-harness) — `점수` · `To do` 등 select 분포

### dedup_bplan2 — Bplan 2.0 DB (1) (행 0)

- **`Status`** (select) 선언: 미팅 예정 | 미팅 후 미결정 | Fwd 후 미결정 | Drop 완료 | 투자 완료 | 리서치 후 논의
  - 분포: 
- **`점수`** (select) 선언: Green | Orange | Red | 칼드랍
  - 분포: 
- **`To do`** (select) 선언: To Drop | Fwd | 미팅요청 | Q&A | Hold | 자료조사 | Toss deal | 기존 검토중 | 기존에 검토중 | 기존 검토 건
  - 분포: 

### dedup_bplan3 — Bplan 3.0 DB(3.23 ~ing) (행 562)

- **`Status`** (select) 선언: Fwd 후 미결정 | 미팅 예정 | 미팅 후 미결정 | Drop 완료 | 투자 완료 | 리서치 후 논의 | Fwd to Slack
  - 분포: Drop 완료=385, (빈칸)=175, 미팅 예정=1, Fwd 후 미결정=1
- **`드랍메일`** (checkbox) 선언: (체크박스)
  - 분포: true=503, false=59
- **`담당자`** (select) 선언: 소피아 Sofia | 김명준Jayden Kim | 에반 Evan
  - 분포: 소피아 Sofia=225, 에반 Evan=172, 김명준Jayden Kim=165
- **`fwd to whom?`** (select) 선언: ReviewerTwo | ReviewerOne | zero
  - 분포: (빈칸)=541, ReviewerTwo=15, ReviewerOne=5, zero=1
- **`점수`** (select) 선언: 칼드랍 | Red | Orange | Green | ?
  - 분포: Red=508, 칼드랍=32, Orange=15, ?=6, Green=1
- **`to do`** (select) 선언: Newly Added | Review Done | Mail Fwd | To Drop | 미팅요청 | Q&A | Hold
  - 분포: To Drop=460, Newly Added=43, Review Done=43, Mail Fwd=12, 미팅요청=3, Hold=1

## ④ 텍스트 칸의 2,000 UTF-16 코드유닛 한계 접촉

| DB | 칸 | max len16 | ≥1900 행수 | ==2000 행수 |
|---|---|---:|---:|---:|
| ch_링크드인 | `Raw Text` | 2000 | 46 | 33 |
| ch_링크드인 | `변화 요약` | 1142 | 0 | 0 |
| dedup_bplan3 | `팀이력` | 2000 | 5 | 5 |
| dedup_bplan3 | `비즈니스모델` | 2000 | 4 | 3 |
| dedup_bplan3 | `솔루션` | 2000 | 3 | 2 |
| dedup_bplan3 | `문제정의` | 1754 | 0 | 0 |
| dedup_bplan3 | `추가내용` | 2000 | 12 | 10 |
| dedup_bplan3 | `경쟁사` | 2000 | 6 | 6 |
| dedup_bplan3 | `경쟁력` | 2000 | 6 | 5 |
| dedup_bplan3 | `서비스정의` | 939 | 0 | 0 |
| dedup_극초기 | `논의 포인트` | 1797 | 0 | 0 |
| dedup_스타트업본DB | `드랍 사유` | 1231 | 0 | 0 |

## ⑤ 타입·형식 이상치

- **base_주간소싱**: `Release Date`(date) 미래 날짜 2행; `Release Date`(date) 2015 이전 10행
- **ch_링크드인**: 특이사항 없음
- **ch_스레드**: 특이사항 없음
- **ch_앱스토어**: `Release Date`(date) 미래 날짜 8행; `Release Date`(date) 2015 이전 10행
- **dedup_bplan2**: 특이사항 없음
- **dedup_bplan3**: 특이사항 없음
- **dedup_극초기**: `링크`(url) http로 시작 안 하는 값 16행
- **dedup_스타트업본DB**: `✅ 홈페이지`(url) http로 시작 안 하는 값 94행

## ⑥ 채널 DB ↔ base DB 인물 중복 (LinkedIn 정규화키 · 핸들)

- `base_주간소싱`: linkedin `/in/` 키 344 · 핸들 894
- `ch_링크드인`: linkedin `/in/` 키 1150 · 핸들 1141
- `ch_스레드`: linkedin `/in/` 키 0 · 핸들 635
- `ch_앱스토어`: linkedin `/in/` 키 0 · 핸들 0
- `dedup_bplan2`: linkedin `/in/` 키 0 · 핸들 0
- `dedup_bplan3`: linkedin `/in/` 키 0 · 핸들 0
- `dedup_극초기`: linkedin `/in/` 키 0 · 핸들 0
- `dedup_스타트업본DB`: linkedin `/in/` 키 0 · 핸들 0

| A | B | /in/ 키 교집합 | 핸들 교집합 |
|---|---|---:|---:|
| base_주간소싱 | ch_링크드인 | 260 | 259 |
| base_주간소싱 | ch_스레드 | 0 | 634 |