---
name: mail-harness
description: >-
  카카오벤처스 hello@ 계정 메일 업무 전체를 하나의 무인 파이프라인으로 실행한다:
  selfcheck → 비플랜 sync → 트리아지(아카이빙·초안·라우팅) → 비플랜 drop →
  무수정 발송률 측정 → 슬랙 다이제스트. hello-mail·bplan-automation의 독립 포크로,
  원본 두 스킬은 콜드 스탠바이다.
  Triggers on: "메일 하네스", "메일 파이프라인", "아침 메일 처리", "메일 무인 실행",
  "/mail-harness". Do NOT trigger for: 개별 수동 작업(→ hello-mail / bplan-automation
  원본), 그물망 소싱(→ net-sourcing).
---

# Mail Harness

hello@ 메일 업무의 무인 하네스. 목표 상태: **매일 아침 사람이 안 봐도 인박스가 비어
있고, 나간 것·나갈 것·사람이 봐야 할 것이 슬랙 다이제스트 한 장에 남는다.**

> **상태(2026-07-17)**: P0~P2 구축 완료(파이프라인·다이제스트·측정·selfcheck).
> 현재 발송 없음(초안까지만). P3 섀도 병행 → P4 컷오버 → P6 발송 승격 순서로 진행 중.
> 단계별 정의: `references/pipeline.md` · 승격 절차: `references/autonomy.md`

<HARD-GATE>
1. **신·구 동시 운행 금지**: `references/cutover.md`의 컷오버가 완료되기 전에는
   apply·sync·drop이 실행되는 어떤 형태(크론·수동 run)도 시작하지 않는다. 예외 없음.
   — 인박스·구글시트 A1 커서·노션 Bplan DB는 원본 스킬과 물리적으로 공유된다. 병행
   실행은 중복 초안·행 건너뜀을 만든다. 컷오버 전 허용되는 것은 `--dry-run`과
   `triage`(읽기 전용) 뿐이다.
2. **never_auto_send는 발송 코드에 도달할 수 없다**: config의 `autonomy.never_auto_send`
   카테고리는 `send_enabled` 값과 무관하게 영원히 초안까지만이다. 예외 없음.
   — 발송 경로를 구현할 때(P6) 발송 함수 첫 줄에서 카테고리를 검사해 거부하는 코드로
   강제한다. 선언이 아니라 코드가 지킨다. selfcheck가 auto/never 목록의 모순을 잡는다.
</HARD-GATE>

---

## Non-Negotiable Rules
모든 실행에 적용. 위반 = 잘못된 메일이 회사 이름으로 나가거나, 파이프라인이 조용히 죽는다.

<CRITICAL>
**#1 — Webflow는 reply가 아니라 forward다**: 발신인 `no-reply-forms@webflow.com`은
포워드 초안 + 본문에서 파싱한 `이메일:` 주소만 쓴다. reply는 no-reply@로 반송된다
(2026-07-10 실제 발생: 550). 파싱 실패면 초안을 만들지 말고 escalate — 추측 금지.

**#2 — 확신이 없으면 UNKNOWN**: 애매한 분류를 억지로 고르지 않는다. UNKNOWN은
#Squad-비플랜으로 포워드되어 사람이 판단한다(가이드라인의 공식 폴백). 오분류는 조용히
틀리고, UNKNOWN은 아무것도 잘못되게 하지 않는다. 대신: 비용은 슬랙 메시지 하나다.

**#3 — 채용·사칭은 영구 사람 몫**: RECRUIT·IMPERSONATION은 항상 escalate. 채용 상태는
시점마다 바뀌고(라벨엔 2026 H2 Intern이 살아 있다), 사칭 문의(`person-35f314b98660@example.com` 사기
유포 중)는 PR 사안이다. 대신: 다이제스트 🚩로 올려 사람이 처리한다.

**#4 — 상태 기록은 액션 직후·원자적으로**: 노션 생성·초안 생성 직후 레저에 기록하되
tmp→rename으로 쓴다. 원본 bplan의 비원자 쓰기가 재실행 중복 초안을 만들었다.
대신: 재실행이 안전해지므로 실패 시 그냥 다시 돌리면 된다.

**#5 — selfcheck 실패만 전체 중단, 나머지는 격리**: 자격증명 없이 반쯤 도는 실행은
상태를 오염시킨다(중단이 맞다). 반대로 sync 하나가 죽었다고 트리아지까지 멈추면
조용히 쌓인다(격리가 맞다). 대신: 모든 실패는 다이제스트에 반드시 표기된다.

**#6 — 자동 발송은 항상 지연 발송**: (P6부터) 즉시 발송 금지. 초안 생성 → 슬랙 통지 →
veto window(4h) 경과 후 발송. veto window가 사람 거부권의 물리적 실체다.
대신: 급한 회신이 필요하면 사람이 초안함에서 직접 보낸다.

**#7 — 발송 이상 징후 폴백** — 순서대로:
  1순위: 해당 건만 초안 모드로 폴백, 나머지 계속
  2순위: 이번 실행의 발송 전체를 초안 모드로 폴백
  3순위: `send_enabled: false`로 내리고 다이제스트에 ⚠️ 보고
  금지: 이상 상태에서 큐를 비우려고 발송 강행
</CRITICAL>

→ 카테고리·라우팅: `references/routing.md` · 승격·측정: `references/autonomy.md`

---

## Project 위치

```
~/.claude/skills/mail-harness/project
```

- 러너: `project/.venv/bin/python` · 엔트리포인트: `project/harness.py`
- 설정(단일 진실): `project/config.yaml` — assignee·라우팅·autonomy·bplan·harness 전부
- 포크: `project/triage/`(hello-mail), `project/bplan/`(bplan-automation+결함수정)
- 자격증명(출력·커밋 금지): 각 포크 안의 `token.json`, `*credentials*.json`, `.env`
- 슬랙 다이제스트: `.env`의 `SLACK_WEBHOOK_URL` (없으면 `data/digest_latest.md`로만)

편의 변수: `PROJ="$HOME/.claude/skills/mail-harness/project"`

## 서브커맨드

| 커맨드 | 하는 일 | 메일함 변경 |
|---|---|---|
| `phase1` | selfcheck → bplan sync → triage(계획 수립) | sync의 라벨이동만 |
| `phase2` | apply(아카이브+초안) → bplan drop → metrics → digest | 있음 (발송 없음) |
| `run` | phase1 + phase2. UNKNOWN은 분류 없이 슬랙 폴백 | 있음 |
| `digest` | 마지막 실행 다이제스트 재출력 | 없음 |
| `--dry-run` | 모든 쓰기 생략 리허설 (컷오버 전 유일하게 허용되는 전체 실행) | 없음 |

```bash
PROJ="$HOME/.claude/skills/mail-harness/project"
"$PROJ/.venv/bin/python" "$PROJ/harness.py" run --dry-run    # 리허설
"$PROJ/.venv/bin/python" "$PROJ/harness.py" phase1           # 계획까지
"$PROJ/.venv/bin/python" "$PROJ/harness.py" phase2           # 실행+다이제스트
```

## 실행법 (Claude Code 세션에서 — 권장 경로)

이 스킬은 **Claude가 UNKNOWN 분류기 역할**을 맡는 설계다 (API 키 불필요, 구독 비용):

1. `phase1`을 돌린다. 출력 끝의 UNKNOWN 건수를 확인한다.
2. UNKNOWN이 있으면 해당 메일을 직접 읽고 판단해
   `"$PROJ/.venv/bin/python" "$PROJ/triage/main.py" reclassify <msg_id>=<CATEGORY> ...`
   로 주입한다. 판정 기준은 `references/routing.md`. **확신 없으면 UNKNOWN으로 둔다**(룰 #2).
3. `phase2`를 돌린다. 다이제스트가 슬랙(또는 `data/digest_latest.md`)에 남는다.
4. 다이제스트의 🚩 항목과 초안 안내를 사용자에게 그대로 전달한다.

무인(크론) 실행은 컷오버 후: `claude -p` 헤드리스로 위 1~3을 수행한다.
설정 방법: `references/pipeline.md` §크론.

## Quick Reference: 임계값

| 항목 | 값 | 어기면 |
|------|-----|--------|
| 자동 발송 승격 | 무수정 발송률 ≥95% · 최근 21일 · n≥5 | 승격 불가 (표본 부족 포함) |
| veto window | 4h | — |
| daily_send_cap | 10건/일 | 초과분 초안 폴백 |
| UNKNOWN 비율 | >30%/실행 | ⚠️ 경보 + 발송 폴백 |
| stage 타임아웃 | 300s | 해당 stage fail 처리 |
| 토큰 만료 | 재인증은 브라우저 필요 | selfcheck ❌ → 사람에게 통지 |

## 주의 / 한계

- **컷오버 전이다**: 지금 상태에서 `run`(non-dry)을 크론에 올리면 HARD-GATE #1 위반.
- **영문 인바운드는 아카이빙된다** — auto-reply 기발송 근거. 다이제스트에서 훑어볼 것.
- **협력 제안 거절 초안은 슬랙 패스 확인 후 발송** — never_auto_send 1호인 이유.
- **OAuth 재인증은 자동화 불가** — selfcheck가 잡으면 각 포크의 reauth를 사람이 실행.
- **템플릿 수정 시** Notion 원문도 같이 고치고 `core/selfcheck.py --ack-templates`.
