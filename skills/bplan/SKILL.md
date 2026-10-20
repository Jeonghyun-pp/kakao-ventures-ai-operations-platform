---
name: bplan
description: >
  비플랜 접수(구글폼)를 노션 행으로 옮기고, 폼 텍스트 + 덱을 읽어 한 건 한 번 판정한다(칼드랍·Red·Orange·Green·?).
  문이 열리면 노션 점수·볼만 카드(슬랙)·드랍 초안까지 하고, 심사역의 ⭕/❌/🙋 를 노션 to do 로 잇는다. 발송은 send_branches 뒤. 켜는 건 사람.
  Triggers on: "비플랜", "접수 판정", "드랍 초안", "/bplan". Do NOT trigger for: 메일 트리아지(→ mail).
schedule:
  - { cmd: run,  when: "매일 09:20", why: "sync → 판정 → 행동(문 뒤 — off 면 기록만) → 하루 보고서 → 백업. 메일 관찰 10시 보고서 앞" }
  - { cmd: sync, when: "60분마다",  why: "접수를 한 시간 안에 노션 행으로, 접수 메일을 3. Evan 라벨로 [사용자 결정 2026-10-20]. run 과 같은 자물쇠(bplan-sync). 실패는 슬랙 경보" }
---

# 비플랜

메일·링크드인과 같은 뼈대(`kvlib` store·gate·report·judgments)다. 2026-09-26 에 sync 위에 판정·행동·followup·보고서를 올렸다. **문은 off 다.**

## 🔴 밖으로 나가는 문 — `constraints.yaml bplan.outbound`

```
off     기록만. 무엇을 했을지 actions 에 dry 로                         ← 지금. 기준 깎는 기간
slack   카드·인수 기록·리마인드(슬랙 글)만 실제로 → card_channel(test → 심사역 안내 뒤 bplan)   ← 심사역이 시스템을 만나는 첫 순간
on      노션 점수·to do 이동 · 드랍 **초안**까지
send    send_branches: [칼드랍] — 초안을 실제 발송. 보고서 '발송 승격' 줄이 "켜도 된다"를 낸 뒤     ← 회사 이름으로 나가는 유일한 순간 (아직 코드 없음 — 메일의 gmail_send 한 문을 그대로 쓴다)
```
열린 것은 `report_slack`(review_test 요약 — 저녁 ops digest 로 묶임)뿐이다. **발송 코드는 아직 없다** — 넣을 때 A8b `SEND_CALLERS` 에 이 파일을 올리고 변이 테스트를 붙인다.

## 접수 한 건이 가는 길

```
sync      시트 → 노션 행(to do=Newly Added · 담당자 에반) → 접수 알림 메일에 `3. Evan` 라벨      (있던 것 — 규율은 아래 그대로)
judge     Newly Added 이고 판정 없는 행 → 덱(drive) + 폼 8칸 → 모델 한 번 → judgments(입력·출력·기준 해시)
            ├ 칼드랍·Red   → draft  접수 알림 스레드에 드랍 초안(ko/en 문안 · 같은 이메일 7일에 하나)
            ├ Orange·Green → card   슬랙 스레드: 요약·팀·되는 이유·리스크·노션/덱 링크 · "⭕ 미팅요청 · ❌ 드랍 · 🙋 내가 볼게"
            └ ?            → card   같은 스레드에 "애매 — 사람이 봐 주세요"
          그리고 score  노션 `점수` + `포워드 이유`(이유/리스크)
followup  카드 스레드 반응: ⭕ → to do=미팅요청 · ❌ → To Drop + 드랍 초안 · 🙋 → "맡았습니다" 기록. 조용하면 3일마다 3번 리마인드. **만기 없음**(접수는 답을 받아야 한다)
report    하루 보고서(파일 + review_test 요약). health 는 skills/ops
```
심사역이 이미 `to do` 를 움직인 행에는 아무것도 하지 않는다.

## 정답은 심사역의 `to do` 다 — 사람이 정답을 달지 않는다

```
To Drop                → drop     |  미팅요청 · Mail Fwd · Q&A → keep  |  Newly Added · Review Done · Hold → 미결(안 본다)
모델 점수 → 갈래:  칼드랍·Red → drop  ·  Orange·Green → keep  ·  ? → ?
```
`label` 이 매일 노션을 읽어 갈린 건만 human 으로 적는다(같아지면 지운다). 보고서 첫 줄이 **"드랍이라 했는데 심사역이 살린 건 N"** — 이 필터에서 가장 아픈 실수라 맨 위에.
표본은 첫날부터 있다: 결정 난 611행. `judge --past N` 으로 옛 행을 판정해 두면 기준을 고칠 때마다 `replay → diff` 로 과거 결정과 대조할 수 있다.

## 사람이 하는 것

```
매일    review_test 요약 → ~/kv-data/bplan/reports/<날짜>.md 의 🚩 절 → 패턴이 보이면 criteria.md → replay → diff
기준    skills/bplan/criteria.md — 점수 어휘는 여기 적지 않는다. 노션 `점수` 옵션을 라이브로 읽어 입력에 싣는다(옛 사고: 어휘표에 빠진 라벨로 348건 쏠림)
덱      🔴 폼 업로드 폴더를 서비스계정에 **읽기 공유**해야 덱이 읽힌다. 안 되면 "덱: 못 읽었다" 를 사실로만 넘긴다. 서비스계정 이메일은 시트 통로가 쓰는 SA 키의 client_email
        [2026-10-14] hello@ 업로드 폴더를 jayden@bplan-490807 SA 에 공유함(표본 40건 404 0). 이미지 덱·이미지 파일은 모델이 옮겨 적는다
        (judge.transcribe — Read 권한은 그 파일 하나 · 25쪽 68초). pptx·동영상·40MB 초과는 아직 "못 읽었다". 🔴 SA 프로젝트 소유자 미확인 — 이관 때 회사 소유로 교체
```

## 명령

```bash
cd ~/kv
./.venv/bin/python -m skills.bplan.run run [--dry-run]          # 매일 몫: sync → judge → act → 보고서 → 백업
./.venv/bin/python -m skills.bplan.run sync [--dry-run] [--limit N] [--since ISO]
./.venv/bin/python -m skills.bplan.run judge [--past N] [--limit N]   # 판정만 저장. --past 는 결정 난 옛 행(보정용, 행동 없음)
./.venv/bin/python -m skills.bplan.run act [--dry-run]           # 판정 있는 접수에 행동 — 문 뒤
./.venv/bin/python -m skills.bplan.run followup [--dry-run]
./.venv/bin/python -m skills.bplan.run label | replay [--limit N] [--criteria 파일] | diff --b r-<sha> | status
```

## 스케줄 [사용자 결정 2026-10-20]

`run` 매일 09:20 · `sync` 60분마다(프런트매터). 그전엔 사람이 `--dry-run` 을 읽고 손으로 돌렸다 — 10/15·10/19·10/20 세 번 계획과 실제가 같았고,
수동이라 생긴 실제 사고는 반대쪽이었다(사람이 안 돌려서 접수가 쌓였다). 판정·카드·드랍 초안은 그대로 문(`bplan.outbound` off) 뒤다.
`followup` 은 아직 올리지 않는다 — 카드가 안 나가는 동안 읽을 반응이 없다. 문을 `slack` 으로 열 때 `{ cmd: followup, when: "10분마다" }`.

## sync 의 규율 (그대로)

<HARD-GATE>
스케줄로 도는 sync 는 실패를 **그날 슬랙 경보로** 사람에게 알린다(`_alert` — 종료코드 ≠ 0 · Degraded · 예외, 종류마다 하루 한 번). 경보 없이 sync 를 스케줄에 두지 않는다.
잘못 **빠진** 접수는 아무도 모른다 — 전엔 사람이 dry-run 을 읽는 것이 그 눈이었고, 스케줄에선 이 경보가 그 눈이다. 다른 실행이 자물쇠를 잡고 있는 것(Busy)은 실패가 아니라 알리지 않는다.
</HARD-GATE>

- **#1 바닥선 이후만** = max(장부 마지막 착지, 노션 접수키 최대). 근거 없으면 오늘 자정. 금지: "노션에 없으면 전부 신규"(5,033건).
- **#2 멱등 키 = 접수키** `이메일|접수시각`. 시트 행 서수는 밀린다(옛 시스템: 같은 사람에게 드랍 6통).
- **#3 키 없는 옛 68행은 이메일 + "행 생성 ≥ 접수시각"** 으로. 기업명은 심사역이 고쳐 쓴다.
- **#4 노션에 행 없는 접수 메일은 안 건드린다** — 라벨 보류로 보고, 종료코드 2. `mail` 이 protect 로 지킨다.
- **#5 답장(Re:)은 안 잡는다.** 제목이 정확히 `…로부터 접수된 사업계획서` 로 끝날 때만.
- **#6 담당자·to do 는 라이브 옵션 대조.** 노션은 모르는 select 값을 주면 새 옵션을 조용히 만든다.
- **#7 시트 헤더는 접두사로, 하나라도 없으면 멈춘다.**

## 구성

```
run.py         sync(있던 것) · judge_new/judge_rows · act_all(score·card·draft) · followup · daily_report · run_all
judge.py       입력 조립(덱 + 폼 8칸 + 사실 · 난수 울타리) · 다섯 줄 파싱(값으로)
criteria.md    기준 — 사람이 깎는다. 하드게이트 2 · Red 축 4 · DQ-1 · OG-1/3 · GD-1 · 캘리브레이션. 라우팅은 결정 기록으로만
templates/     drop_ko.txt · drop_en.txt — 옛 시스템 문안 바이트 그대로
kv_transport/drive.py   덱 읽기(읽기 전용). 서비스계정은 시트와 같고 스코프만 drive.readonly
../../people.yaml       🙋 누른 사람 → 성함
```
```
DB       ~/kv-data/bplan/db/bplan.sqlite (judgments · actions)   백업 ~/kv-data/bplan/db/backup/
보고서    ~/kv-data/bplan/reports/<날짜>.md
장부      ~/kv-data/_ledger/bplan.jsonl — landed(접수키) · labeled(메일번호) · 옛 drop_drafted(9/18 손 초안 25건)
```
`constraints.yaml bplan.*`: outbound · send_branches · card_channel · report_channel · report_slack · deck_chars · remind_days · remind_max · drop_after_days · backup_keep.

## 결정 기록

- **Drop 전용 + 볼만 카드** [2026-09-06 · 09-25]. 전수 심사가 아니라 "확실히 아닌 것"을 빼고, 볼만한 것은 심사역이 5초에 판단할 카드로 넘긴다. 도메인 라우팅(게임→ReviewerTwo 등)은 자동화하지 않는다 — 옛 헬스 라우팅 4건 전부 드랍·홀드.
- **덱을 읽는다** [2026-09-25]. 폼만으로는 드랍은 가르지만 볼만의 요약이 얕다. 이미지 덱·미지원 형식은 "못 읽었다"를 사실로.
- **같은 이메일 드랍 초안 7일에 하나** [옛 결정]. 영구로 막으면 3개월 뒤 새 아이템 재접수에 답이 안 간다.
- **드랍 문안은 사람이 쓴 것** — 한 글자도 안 고쳤다. 언어는 한글 이름·기업명·한국 도메인이면 ko(옛 실측: 한국 창업자가 영문 거절을 받는 쪽으로만 틀렸다).
- **Status=Reviewing 같은 재정의는 여기 없다.** 노션 칸은 심사역이 쓰는 뜻 그대로.
- 9/16 손 스크리닝(76건: 드랍 46 · 볼만 20 · 애매 10)과 9/18 손 초안 25통은 `reports/screening-20260916.md`·장부 `drop_drafted` 에 남아 있다 — 첫 replay 의 대조 자료.
