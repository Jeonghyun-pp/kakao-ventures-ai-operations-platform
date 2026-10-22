---
name: mail
description: 공용 메일함(hello@)을 매일 읽어 한 통에 한 번 분류하고, 문이 열려 있으면 아카이브·초안·슬랙 질문·(허용된 갈래만) 발송까지 한다. 켜는 건 사람이다.
schedule:
  - { cmd: observe,  when: "60분마다", why: "관찰 → 판정 → (열린 것만) 실행. 아카이브를 연 뒤 [2026-10-20] — 하루 한 번이면 10시 뒤에 온 메일이 다음 날까지 판정 없이 쌓인다. 하루 보고서·백업은 KST report_after_hour(10시) 뒤 첫 실행에서 한 번" }
---

# 메일

옛 실행부(plan/apply/followup · judge.py · brief.py, 2025-07~2026-09)는 2026-09-25 에 지웠다. 되돌리려면 `git checkout baseline-2026-09-21 -- skills/mail`.
그 전의 `mail-harness`(kv-automation)도 같은 날 지웠다. **메일을 처리하는 코드는 이 폴더 하나다.**

## 🔴 밖으로 나가는 문은 하나이고, 지금 닫혀 있다

```
constraints.yaml  mail_observe.outbound
  off     아무것도 안 한다. 무엇을 했을지만 actions 에 dry 로 적는다                     ← 지금
  slack   슬랙 글만 실제로 올린다(질문·리마인드·인계·포워드 → slack_channel). 초안·아카이브·발송은 dry
  on      메일함에도 쓴다(초안·아카이브). 발송은 그 위에 send_branches 허용 목록을 또 지난다 — 목록 밖 갈래는 초안까지

열려 있는 것   mail_observe.report_slack: on  → 아침 요약(저녁 ops digest 한 통으로 묶임)이 report_channel(test) 로
              mail_observe.open_kinds: [archive] + open_categories.archive  → 기계 알림·단체 발송·끝난 건의 감사 답장만 실제로 아카이브 [2026-10-20 1단계]
                + 답장을 실제로 보낸 투자 문의(ANSWERED): 보낸편지함에 그 사람 앞 메일이 그 뒤에 있으면 원본·나눠 보낸 메일까지 [2026-10-22]
                  초안만으론 안 치운다(안 보내면 원본이 남아야 한다)
                + 2단계 외국인 콜드·출자 수탁 [2026-10-22]. 영업(VENDOR_PITCH)은 아직 dry — 회사 소개로 시작하는 투자 문의를 영업으로 읽은 적이 있다
                아카이브한 메일은 하루 보고서 「🗄️ 아카이브한 메일」 절과 슬랙 요약 끝줄에 제목으로 남는다 — 잘못 빠진 것은 사람이 거기서 찾는다
                2단계(외국인 콜드·영업·출자 수탁)는 1~2주 사람 브리핑과 맞춰 본 뒤 open_categories 에 한 줄씩. 그 밖은 지금도 전부 dry
```
**켜는 순서(사람이 한다)**: `outbound: slack` → `slack_channel: bplan`(실채널) → `outbound: on` → `send_branches: [INBOUND_KO]`(보고서가 "켜도 된다"를 낸 갈래 하나씩).
되돌리기는 같은 줄을 되돌리는 것. 각 단계에서 새로 나가는 것은 정확히 하나씩이다.

발송 코드는 `kv_transport/gmail_send.py` 한 파일이고 부르는 곳은 `flow.py` 뿐이다(`check_intent` A8 · 변이 테스트 10/10).
`messages/send` · `smtplib` · 삭제는 어디에도 없다. 스코프로는 못 막는다(`gmail.modify` 에 send 가 든다) — **막는 자리는 코드고 그래서 A8 이 있다.**

## 하루

```
매시간 observe   수집(7일 되훑기, 본 id 는 건너뜀) → 규칙 → 모델(한 통 한 번, 도구 0) → SQLite
                → 행동 결정(route.decide) → 문 뒤에서 실행 또는 기록 → 슬랙 질문의 반응·답글 읽기 → 리마인드
                → (10시 뒤 첫 실행만) 하루 보고서(파일 + 슬랙 요약) → 온라인 백업(7세대)
12:55 ops health  오늘 보고서 파일이 있나 — 여섯 스킬을 skills/ops 가 한 번에 본다 (9/24 실전: 맥이 잠들어 안 돌았다고 정확히 알렸다)
```

```bash
cd ~/kv
./.venv/bin/python -m skills.mail.run observe                      # 오늘 몫 (밀린 날도 이걸로 — 7일을 되훑는다)
./.venv/bin/python -m skills.mail.run observe --op status
./.venv/bin/python -m skills.mail.run observe --op report [--days N]          # 아무 때나 보고서 → reports/latest.md
./.venv/bin/python -m skills.mail.run observe --op label --id <id> --category INBOUND_KO --why "…"   # 이 메일의 정답
./.venv/bin/python -m skills.mail.run observe --op replay [--limit N] [--criteria 파일]              # 고친 기준을 저장된 메일에
./.venv/bin/python -m skills.mail.run observe --op diff --a live --b r-<sha>                          # 무엇이 달라졌나
./.venv/bin/python -m skills.mail.run observe --op decide --id <id> --decision yes|no                 # 슬랙 없이 결정을 넣는다
./.venv/bin/python -m skills.mail.run observe --op backup | restore-test
```

## 사람이 매일 하는 것 — 기준 깎기

보고서 `~/kv-data/mail/reports/<날짜>.md` (요약은 슬랙 review_test). **먼저 볼 것** = UNKNOWN · 형식 실패 · 슬랙에 물었을 것 · 회신이 나갈 것.

```
기준          skills/mail/criteria.md      ← 정현님이 고치는 파일. 이것 하나다
정답          --op label                    ← 갈래를 켤 근거(gmail.promote: 21일 · 정답 5건 이상 · 일치 95%)
확인          --op replay → --op diff       ← 기준을 고친 뒤. 운영 판정(live)은 어떤 경우에도 안 바뀐다(한 통은 한 번)
```
replay 는 기준 파일의 해시로 run 이름(`r-<sha>`)을 만든다. 같은 기준이면 이어서 하고, 고치면 새 run 이다.
API 500 같은 일시 오류로 멈추면 다시 부르면 이어진다(9/22: 두 번 멈추고 이어서 205통).

## 행동 여섯 (route.decide — 정책은 여기 하나)

| 행동 | 갈래 | 새 흐름이 하는 것 | 문 off |
|---|---|---|---|
| `reply` | INBOUND_KO·EN(콜드 투자 문의) | `send_branches` 에 있으면 발송, 없으면 **초안** (flow._reply) | `send:dry`+`draft:dry` |
| `archive` | 광고·영업·출자·채용·폼 오류·사칭·시스템 안내 | 아카이브(멱등 — 이미 없으면 성공) | `archive:dry` |
| `forward` | 제휴 · UNKNOWN | `slack_channel` 에 게시 + 스레드에 전문. 제휴 거절 회신은 초안까지(영원히 수동) | `forward:dry` |
| `slack` | 행사·섭외 · 신뢰 경로 | 그래프: 질문 → **대기** → ⭕/❌/답글 → 초안·아카이브·인계(🙋) → 리마인드 | `post:dry` |
| `protect` | 접수 알림 · 사내 발신 | 안 함 | — |
| `escalate` | 주소 없는 웹플로우 | 도달하지 않는다 — 모델 어휘에 WEBFLOW 가 없어 내용으로 분류되고, 회신이면 no-reply 검사에서 `skipped` | — |

### 판단 흐름 (flow.py · LangGraph)
`post → wait → act → [ack]`. 멈춘 동안 상태는 같은 SQLite 의 체크포인트에 있어 며칠 뒤의 다른 실행이 잇는다.
부작용은 **멈추는 노드 앞**에 둔다(재개 때 멈춘 노드를 다시 돈다) + `actions` 기본키(메일, 종류)로 한 번 더 막는다.
무응답을 거절로 바꾸지 않는다 — `remind_days` 마다 `remind_max` 번 리마인드하고, 그 뒤엔 아침 요약 첫 줄에 ⚠️.
지명(`people.yaml`)은 **호칭이 붙어야** 핑한다 — `Cl«reviewer-three»ct`·`ch«reviewer-three»l` 오탐(2026-08-16). 부분일치로 되돌리지 마라.

### 자동 회신 (flow._reply) — 🔴 세 겹
```
나가려면              outbound: on  +  send_branches 에 그 카테고리  +  A8 통과
제외(다시 안 본다)     후속·재문의 · 보낼 수 없는 수신자(no-reply·우리 주소) · 같은 수신자에게 이미 · 대화가 이미 진행
문이 열려도 막힘       옛 장부(_ledger/mail.jsonl)에 그 메일이 있다 · 지금 실제 스레드가 움직였다 · 하루 상한 send_per_day
```
초안을 만들고 **그 초안을** 보낸다 — 보낸 초안은 사라지므로 같은 id 로 두 번 못 나가고, 실패하면 초안이 남아 사람이 보낼 수 있다.
`gmail.never_auto_send` 갈래(신뢰 경로·제휴 거절·채용·사칭)는 숫자와 무관하게 영원히 수동이다.

## 구성

```
run.py         진입점 (observe)
observe.py     수집 · 판정(한 통 한 번) · 보고서 · 백업 · replay/diff/label
flow.py        문(_out) · 기다림 없는 행동(act_all) · 판단 그래프 · 슬랙 해석 · RealIO
clean.py       본문 정리 · 인용 분리 · 웹플로우 전문
route.py       카테고리 → 행동 (정책의 진실원) · 슬랙 글 서식
rules.py       구조 규칙(접수 알림 · 사내 · 시스템 안내 · 웹플로우 · 광고 헤더) · 모델 어휘
criteria.md    모델이 읽는 기준 — 사람이 깎는다
people.yaml    심사역 명부(이름 → 슬랙 ID)
templates/     승인된 문안 — 코드가 안 부르는 것(webflow_*, fwd_intro)도 남긴다(결정은 아래에)
```
```
DB      ~/kv-data/mail/db/mail.sqlite (600 · 본문 포함)   백업 ~/kv-data/mail/db/backup/mail-<날짜>.sqlite
보고서   ~/kv-data/mail/reports/<날짜>.md · latest.md
옛 장부  ~/kv-data/_ledger/mail.jsonl — 읽기만(legacy_acted). 옛 실행부가 손댄 메일에는 새 흐름이 쓰지 않는다
옛 계획  ~/kv-data/mail/plans/ — 데이터로 남김. 읽는 코드 없음
```
`constraints.yaml mail_observe.*` 가 설정 전부다(문 · 허용 목록 · 상한 · 채널 · 리마인드). `check_intent` A6 이 숫자를 코드에 박지 않았는지 본다.

## 지키는 규율

```
인증부터 본다            인박스를 부르기 전에. 나중에 401 로 알면 "0통"과 구별이 안 된다
미상은 0통이 아니다       망이 죽으면 미상이고 종료코드가 0이 아니다. 목록이 상한에서 잘리면 partial 로 말한다
한 통은 한 번             (메일, run) 이 기본키. 재판정은 replay 로 다른 run 에
모델은 데이터만 본다       본문은 난수 울타리 안. 도구 0(--tools ""). 출력은 세 줄, 값으로 읽는다
쓴 것은 되읽는다          아카이브는 INBOX 라벨을, 발송은 SENT 라벨을 확인한다. 200 은 저장이 아니다
막았다고 성공으로 안 센다  dry 는 dry 다
사람이 사이에 만진 건 입력  이미 없어진 메일은 성공, 스레드가 움직였으면 건너뛰고 말한다
```

## 결정 기록 (코드에서 지웠지만 살아 있어야 하는 것)

**웹플로우에 답장하면 반송된다.** 발신이 `no-reply-forms@webflow.com` 이라 2026-07-10 에 550 반송, 문의자는 답을 못 받았다.
문의자 주소는 **본문에서** 파싱하고(`clean.webflow`), 못 찾으면 회신하지 않는다. 주소를 추측하지 않는다.

**웹플로우 문의 유형별 결정** (옛 `route._webflow()` 분기의 주석. 코드는 도달 불가라 걷어냈다 2026-09-21)
```
투자 문의   문의자에게 구글폼 안내. 문안 templates/webflow_invest_ko.txt · _en.txt (본문 언어로 고른다)
TIPS 문의   문의자에게 TIPS 안내.    문안 templates/webflow_tips.txt
멘토링 문의  회신하지 않고 아카이빙 [결정 2026-08-18] — 오피스아워가 '22년 하반기부터 중단돼 안내할 내용이 없다. 재개되면 되살릴 자리
섭외 문의   비플랜 채널로
그 밖       사람에게
```
폼의 `문의 유형` 칸은 안 쓴다 — 문의자가 드롭다운에서 고르는 값이라 우리 갈래와 맞지 않았다(실측 09-02: '섭외 문의'로 온 것이 사칭 확인). 내용을 읽어 가른다.

**채용과 사칭은 회신을 안 만들고 아카이빙한다** [2026-08-18]. 채용 템플릿이 전부 "안 뽑음"인데 채용 상태는 시점마다 바뀐다 —
틀린 답이 회사 이름으로 나가는 것보다 무응답이 낫다. 그 결정으로 오탐 비용이 커져(조용히 사라진다) 사칭 힌트에서 넓은 둘을 뺐다.
**행동을 바꾸면 그 행동을 부르는 규칙의 정밀도 기준도 같이 바뀐다.**

**출자·수탁(LP_CUSTODY)은 아카이빙** [2026-09-02]. 비플랜 방으로 보내도 아무도 안 움직인다. 대가: 진짜 LP 제안이 섞이면 조용히 사라진다.

**폼 오류(FORM_ERROR)는 아카이빙** [2026-08-18]. 전엔 INBOUND_KO 로 잡혀 폼이 안 된다고 말한 사람에게 "폼으로 접수하세요"가 다시 나갔다.

**사내 발신은 protect.** 광고 규칙에서 빼기만 하고 모델로 넘기면 UNKNOWN → 비플랜 포워드가 된다 — 읽으시라고 뺀 메일을 남의 채널로 보내는 셈(08-18). 빼는 것과 목적지는 별개다.

**비플랜 접수 메일은 절대 아카이빙하지 마라.** 비플랜 스킬이 그 메일을 제목으로 찾아 쓴다. 두 스킬 사이의 유일한 결합이고 `rules.classify` 의 규칙으로 지킨다.
창업자가 접수 알림에 보낸 답장("Re: …접수된 사업계획서")은 규칙이 접수 알림으로 잡지만, `observe.collect` 가 direction 으로 가른다(우리가 보낸 것만 self).

**헤더 하나로 아카이브하지 않는다.** 옛 실행부는 `List-Unsubscribe` 만으로 광고를 확정했고 사람이 메일 도구로 보낸 제휴 제안을 치웠다(09-09).
새 흐름은 규칙 결과를 기록만 하고 모델에게 "헤더 있음"을 사실로 준다. 기계 알림은 `SYSTEM_NOTICE`(criteria.md 의 절) 로 간다.

**애매하면 UNKNOWN.** 오분류는 조용히 틀리고 UNKNOWN 은 슬랙 메시지 하나 값이다.

**옛 실행부의 실측** (조사 2026-09-21): 3주간 초안 76개 중 55개가 안 나갔다 · 같은 메일 재판정 30% 변동 · 20일 중 13일만 완전 성공 ·
웹플로우 본문 886~3,904자 중 6~37자만 봄. 이 넷이 새 흐름의 설계(한 통 한 번 · 입력 저장 · 전문 · 사람 없이 나가는 발송)를 정했다.

## 이관 (리눅스)

`schedule.py` 가 맥이 아니면 crontab 블록을 만든다. 맥 전용 코드는 그 파일에만 있다(A13). `claude` 는 서버에서 hello@ 로 `claude login` 한 번.
Gmail·Slack 은 토큰. DB 는 백업 파일에서 복원(`--op restore-test` 가 그 시험). 백업은 `skills/ops`(매일 12:45 스냅샷)가 한다.
