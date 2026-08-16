# 미검증 3경로 — 합성으로 태워 계약 확보 (2026-08-16)

프로덕션에 산출물이 0건이라 **결과물이 어떻게 생겼는지 아무도 몰랐던** 세 경로다.
재빌드를 안 하기로 했으므로 이 코드는 그대로 언젠가 **처음 돈다** — ①은 사람이 ✅ 를 누르는 순간,
②는 토큰이 만료되는 날, ③은 축이 소진되는 날. 그 전에 합성으로 태웠다.

프로브: `scratchpad/rebuild/probes/probe1_approval.py` · `probe2_claude_fail.py` (재현 가능)
안전: KV_DATA 스크래치 강제 · Gmail·Slack·subprocess 전부 스텁 · **네트워크 0** · 운영 데이터 무변경 확인

---

## ① 승인 결재·실행

**태우기 전 상태**: 레코드 14/14 가 `decision:null`, 실행 5필드 0/14, `decisions/` 디렉토리 없음.
`announce` 만 돌았고(`slack_ts` 14/14) `reconcile → decide → execute` 는 이력 0.

### 확보한 계약

**결재 후 레코드에 붙는 필드**
```json
{ "decision": "approved",
  "decided_at": "2026-08-16T01:48:54Z",
  "decided_by": "slack:U_HUMAN",        // "cli" | "slack:<user_id>"
  "draft_id": "draft-001",
  "executed_at": "2026-08-16T01:48:54Z" }
```
🔴 **`executing_at` 은 성공 시 삭제된다.** 의도 마커라 남으면 다음 틱이 "중복 초안 가능"으로 오탐한다.
🔴 `status` 는 **저장되지 않는다** — `status_of()` 가 계산한다(실측 `executed`).

**결정 마커** `_approvals/decisions/<key>.<created14>.decision`
```
파일명   syn-race.20260816014854.decision       ← created 에서 숫자만 14자
내용     rejected|p1                            ← "<decision>|<by>"
```

### 검증한 시나리오 5종

| | 시나리오 | 결과 |
|---|---|---|
| A | 정상 왕복 ✅ | `decision=approved` · 초안 1건 · `status_of()=executed` |
| B | 거부 ❌ | **execute 0 · 초안 0건** — no-approval-no-execute 성립 |
| C | ✅+❌ 동시 | `rejected` — **fail-safe(비가역 방향으로 안 기움)** |
| D | **진짜 프로세스 2개 경합** | 마커 **1개**(`rejected\|p1`) · 양쪽이 같은 답을 받음 |
| E | 초안 생성 **중** 결정 변경 | execute 0 · `draft_id` 없음 · **`orphan_draft_id=draft-002`** + 알림 |

🔴 **D·E 가 이 프로브의 핵심이다.** W9 가 고쳤다고 기록한 결함 셋이 여기 걸려 있었고,
그동안 **동시성 랩과 합성으로만** 검증됐다:
```
N1  first-decision-wins 가 경합에서 8/8 붕괴 → O_EXCL 결정 마커       → D 로 확인
N2  거부된 건이 status=executed 로 확정      → 초안 생성 후 재확인    → E 로 확인
N3  마커가 key 에만 묶여 옛 결정이 되살아남   → created 화신에 바인딩  → D 의 파일명으로 확인
```

**남은 미검증**: `execute_error`(Gmail 실패 시 보상 경로)는 아직 안 태웠다.

---

## ② claude 실패 기록 → 판정 → 알림

**태우기 전 상태**: `_usage/claude/*.jsonl` 124줄 **전부 `ok:true`**, `err` 0건.

### 확보한 계약 — 장부에 남는 네 모양

```jsonl
{"ts":"…+00:00","ok":false,"err":"timeout"}                              // subprocess timeout
{"ts":"…+00:00","ok":false,"err":"empty_output"}                         // rc 0 + 빈 출력 = 인증 만료
{"ts":"…+00:00","ok":false,"err":"rc1:Invalid API key · please run /login"}  // rc≠0, stderr 앞 80자
{"ts":"…+00:00","ok":true}                                               // 성공 — err 필드 없음
```
🔴 **`FileNotFoundError`(claude 미설치·PATH 부재)는 장부에 안 남는다.** spawn 자체가 없어 쿼터를
안 태웠기 때문이다 — 설계대로다. 대신 `SessionError` 로 시끄럽게 죽는다.

### 알림 사슬이 실제로 작동한다

| 시나리오 | `streak` | `today` | 알림 |
|---|---:|---|---|
| 오늘 연속 3회 실패 (인증 만료 전형) | 3 | True | 🔴 `auth-suspect-claude` **발동** |
| 연속 2회 (임계 3 미달) | 2 | True | 침묵 ✅ |
| 어제만 3연속, 오늘 호출 0 | 3 | **False** | `auth-suspect` 침묵(B-F7) → 대신 `auth-unknown` |
| 성공 기록 0 · 호출 있음 | 1 | True | 🔴 `auth-unknown-claude` |
| 마지막 성공 4일 전 | 0 | False | ⚠️ `auth-unknown-claude` (카나리아, 임계 3일) |

🔴 **W9 B-F1 이 고친 것이 처음 발동 확인됐다** — *"`ok=(rc==0)` 이라 인증 만료가 `ok:true` 로
기록돼 auth-suspect 가 자기 목적 시나리오에서 영원히 미발동했다."* 이제 `empty_output` 이
`ok:false` 로 남고, 3회 연속이면 알림이 뜬다.

---

## ③ 링크드인 축 소진·되감기

**태우기 전 상태**: 6축 전부 `exhausted:false`. 소진 후 경로가 한 번도 안 돎.

### 확보한 계약

```
커서       rewind:true        → 항상 1페이지 (소진 개념 없음)
           exhausted:true     → 1페이지로 되감김
           그 외              → next_page
되감기     rewind 축은 refresh/exhausted 를 **보지 않는다** — cadence 만 지킨다
           커서 축은 exhausted 이후 refresh 주기(미선언 시 **quarterly**)
```

**축별 "소진 시 다시 돌기까지"** (실측)

| 축 | cadence | refresh | rewind | 소진 후 |
|---|---|---|---|---|
| flow-new-founder | biweekly | — | ✅ | 14일 |
| stealth-kr | always | monthly | | 30일 |
| stealth-us-krschool | monthly | — | ✅ | 30일 |
| **early-kr** | always | **미선언** | | 🔴 **90일** (기본값 quarterly) |
| gap-practitioner | biweekly | monthly | | 30일 |
| posted-early | always | monthly | | 30일 |

### 🟠 발견 — 되감기가 사실상 도달 불가한 축 둘

```
gap-practitioner   모수 67,881명 = 100p · pages 2 · biweekly
                   → 소진까지 50회 실행 × 14일 = 약 2년.  refresh:monthly 가 사문이다
early-kr           refresh 선언이 없어 기본값 quarterly(90일)
                   → 플로우성 축인데 되감기가 분기 1회
```
2026-07-14 에 `recent-job-kr` 이 *"되감기가 6.5개월에 한 번 일어나 실시간을 못 잡았다"* 로
`rewind:true` 를 도입한 그 형태가 **두 축에 남아 있다.** 이건 계약이 아니라 **튜닝 결정**이니
결정 목록에 올린다(⑨).

### 📌 정정 — `stealth-us-krschool` 3주 정지는 정상이다

상태 실측이 *"축 하나가 조용히 멈춰 있다"* 고 했으나, 이 축은 `cadence: monthly` 이고
`last_run=2026-07-27` 이라 다음 도래일이 **08-26** 이다. 오늘 08-16 → **아직 미도래.** 정상 동작.

---

## 남은 미검증

```
승인 execute_error   Gmail 실패 시 보상(결정 유지 + 다음 틱 재시도) 경로
축 소진 실제 발생      exhausted=true 가 실제로 찍히는 순간(빈 페이지 판정)
```
둘 다 위험이 낮아 계약에 "미검증"으로 표시하고 넘긴다.
