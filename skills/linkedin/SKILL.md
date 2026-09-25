---
name: linkedin
description: 링크드인에서 극초기 창업자를 주 1회 사서 Tier 를 판정하고 노션에 전량 착지, 평일마다 5명을 #그물망 카드로 올린다. ✅ 면 콜드 메시지 초안 + 극초기 승격. 메일과 같은 뼈대(문·판정 저장·보고서·health).
schedule:
  - { cmd: collect, when: "월 09:10", why: "이때만 돈이 나간다. 축별로 차례인 것만 돈다" }
  - { cmd: card,    when: "매일 11:00", why: "정현님이 훑은 결과가 반영된 뒤에 나간다. 평일만 — 주말엔 아무것도 안 한다" }
  - { cmd: followup, when: "10분마다", why: "심사역이 언제 누를지 우리가 못 정한다. 누르고 10분 안에 초안이 뜬다" }
  - { cmd: health,   when: "매일 12:10", why: "card 는 자기가 안 돌았다고 말할 수 없다. 한 시간 뒤 다른 잡이 오늘 보고서 파일이 있는지 본다(주말은 조용히). 메일 health(12:00) 뒤" }
---

# 링크드인 소싱

2026-09-25 에 메일과 같은 뼈대로 옮겼다(`kvlib` store·gate·report). **동작은 그대로다** — 골든 dry-run 넷이 전·후 동일, 특성화 테스트 27개가 먼저 잡았다.

## 🔴 밖으로 나가는 다섯은 문 하나를 지난다 — 그리고 문은 **열려 있다**

```
constraints.yaml  linkedin.outbound: "on"      ← 살아 있는 시스템이다. off/slack 은 시험·중단용
  buy       Apify 구매(돈)               off·slack 이면 안 산다. 계획만 찍는다
  land      노션 착지                    〃 dry
  card      #그물망 카드(헤더+스레드)     slack 이상이면 나간다
  outreach  ✅ 스레드에 콜드 메시지 초안   〃
  promote   극초기 DB 승격 + 링크드인 행 표시   on 이어야. linkedin.promote: false 면 "gated"
```
`--dry-run` 은 이 실행만 문을 내리는 것(`Gate(force="off")`)이다. 무엇을 했(을)는지는 `~/kv-data/linkedin/db/linkedin.sqlite` 의 `actions` 에 남는다.
장부(`~/kv-data/_ledger/linkedin.jsonl`)는 그대로 **한 일의 기록**이고 card/followup 의 멱등 키다. 두 곳에 적히지만 역할이 다르다.
끄려면 `outbound: "off"` 한 줄. 되돌리기도 그 한 줄.

## 하루

```
월 09:10  collect   축별로 차례인 것만 → 재고(backlog_cap) 확인 → Apify → 원본 아카이브 → 장부로 중복 제거 → 사후 조건
                    → claude 50명 배치 판정 · **사람별 저장**(judgments: 모델이 본 줄·낸 줄·기준 해시) → 전량 노션 착지
매일 11:00 card     평일만. 노션 Tier 되읽기(사람이 고친 것 = 정답으로 저장, label) → 아직 안 나간 사람 좋은 순 5명 → 헤더 + 사람별 스레드
                    → 하루 보고서(파일 + review_test 요약)
10분마다  followup  ✅ → 누른 분 성함으로 콜드 메시지 초안(짧게·길게)을 스레드에 → 극초기 승격 · 7일 무반응은 만기(요일 안 봄)
매일 12:10 health   평일에 오늘 보고서 파일이 없으면 review_test 에 한 줄
```
**전량 착지하는 이유**: 심사역이 노션을 직접 열 수 있어야 한다. 슬랙엔 그중 하루 5명만 간다.
🔴 **우리가 보내지 않는다.** 콜드 메시지는 심사역 개인 계정으로 나가는 글이라 글만 써서 올린다. **발신자는 ✅ 를 누른 분**이다 — `people.yaml` 명단 밖이면 이름을 비우고 `발신자 이름을 채워주세요`.

## 사람이 하는 것 — 기준 깎기

```
정답      노션 `Tier` 칸을 고친다. 그게 전부다 — 다음 card 가 judgments(human) 으로 가져간다
보고서    ~/kv-data/linkedin/reports/<날짜>.md · 🚩 절 = 노션에서 고쳐진 Tier(판정 ↔ 정답 갈림) — 기준을 깎을 재료
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
./.venv/bin/python -m skills.linkedin.run status            # 축별 마지막 실행·다음 페이지·커버리지·재고
./.venv/bin/python -m skills.linkedin.run label             # 노션 Tier → human (card 가 매일 하지만 손으로도)
./.venv/bin/python -m skills.linkedin.run replay | diff --b r-<sha> | health
./.venv/bin/python tools/replay_linkedin.py                 # 원본 1,865행으로 파서 검증 (돈 0·망 0)
```

## 구성

```
run.py         collect · card · followup · 보고서 · 진입점
judge.py       판정 — 줄 단위(judge_lines). collect 와 replay 가 같은 줄을 쓴다. 배치 50(쿼터 결정)
judgments.py   판정 저장 · label(노션 Tier) · replay · diff
axes.py        수집 축(Apify 질의)과 사후 조건(reject) · 카드 기준(card_reject)
profile.py     Apify 원본 → 키·경력·신호
brief.py       카드 두 문장 + 회사명 (claude)     outreach.py  콜드 메시지 초안 (claude)
criteria.md    Tier 기준 — 사람이 깎는다
../../people.yaml   심사역 명부(슬랙 ID → 성함). 메일과 같은 파일
```
`constraints.yaml linkedin.*`: outbound · promote · card_channel(net) · report_channel(test) · report_slack · card_tiers · backlog_cap · followup_days · stock_warn_days · connect_note_chars · outreach_batch. 승격 게이트·티어·재고 규칙의 **근거는 그 주석에**.

## 알아둘 것 (지우지 않은 결정)

**재고가 스스로 구매를 조절한다.** 아직 안 나간 사람이 `backlog_cap`(100) 이상이면 사지 않는다. 첫 실행에 590명(117일치)이 쌓인 뒤 정했다. 재고는 **노션 기준**으로 센다 — 장부로 세면 card 와 다른 창고를 보게 된다(09-01: 닷새간 카드 0건).

**후보는 노션 행에서, 정답도 노션에서.** 장부 landed 1,234행은 옛 시스템 시드라 `page_id` 가 없다. 장부로 후보를 뽑으면 그 사람들이 카드에 영원히 안 나온다.

**이름으로 대조하지 않는다.** `Minsu Kim` 같은 흔한 이름이 통째로 오제외됐다. 사람은 URL, 회사는 홈페이지·회사명 정규화. 스텔스 창업자의 가짜 홈페이지(`linkedin.com/company/stealth`, 15%)는 대조 키가 아니다.

**Status=Reviewing 은 "극초기에 등록됨"의 뜻** [2026-08-20]. 안 쓰이던 값을 재정의했다. 사람이 "검토 중" 뜻으로 켜면 안 된다. 극초기 행은 `2. 미팅요청` + `슬랙 발송? True` 로 넣는다 — 교통정리 뷰가 그 칸으로 거른다.

**Tier 는 값으로 찾는다.** claude 가 입력의 축 칸을 출력에 옮겨 위치가 흔들렸다(08-17, 116줄 20% 유실). 열 위치를 믿지 않는다.

**`currentPosition` 만 보면 안 된다**(1,167명 중 396명 누락) · **`endDate` 는 dict 다** · **URL 을 인코딩하지 마라**(한글 vanity 6%) · **점수 매겨서 줄 세우지 마라**(랭킹 정보량 ≈ 0, 실측 97명).

**알림은 하루 한 번, 종류마다** — followup 은 10분마다 돈다. 같은 실패를 144번 보내면 그 채널은 그날로 무시된다. 실패·재고 알림은 review_test 로(심사역 채널이 아니다).

**읽기에 성공한 스레드만 만기시킨다.** 망 오류 한 번에 사람이 조용히 사라지지 않게. 만기 7일 = 결론(카드가 매일 새로 오는 구조라 리마인드 대신) [사용자 결정 2026-09-25 유지].

**장부 ts** — 09-25 전 195줄은 슬랙 메시지 ts 가 `ts` 칸에 들어 있다(결함). 이후는 `slack_ts` 칸. 읽는 쪽은 둘 다 본다.
