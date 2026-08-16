# kv-data 상태 계층 — 유형별 계약 초안

측정 기준 2026-08-16 · 전 8,606 파일 실측 · **코드 선언(kv-core 소스)과 실제 파일을 대조**했다.
표기: `필드 등장률%` 는 그 유형에 속한 **전 항목**을 순회한 값. `빈값` 은 `null`/`""`/`[]`/`{}` 건수.
🔴 = 재빌드가 깨면 데이터가 손상되는 불변식 · ⚠️ = 부채/불일치

---

# PART A — kv-core 소유 (7종)

## A1. `<skill>/cadence/<task>.json` — 워터마크

**형식** JSON object · UTF-8 · **99 B 고정** · 5본 전부 동일 3필드.

| 필드 | 등장 | 타입 | 값 형식 | 규칙 |
|---|---:|---|---|---|
| `first_attempt` | 5/5 | str | `YYYY-MM-DD` | **최초 1회만 기록** (`setdefault`) |
| `last_attempt` | 5/5 | str | `YYYY-MM-DD` | **전진만** |
| `last_success` | 5/5 | str | `YYYY-MM-DD` | **전진만**, `ok` 일 때만 |

**값 형식은 날짜 문자열이다 — ISO 타임스탬프가 아니다.** 코드는 `period.isoformat()` 를 쓰고, 독자는 전부 `str(x)[:10]` 로 방어한다.

🔴 **전진 규칙은 락 안에서 결정해야 한다.** 코드는 `flock` 을 잡고 read-merge-write 하면서 `wm[k] = max(str(wm[k]), str(v))` 로 강제한다. 호출자가 비교해서 넘기면 안 된다.
🔴 **부분 갱신이어야 한다.** 통째 덮어쓰는 경로(`write_watermark`)가 코드에 남아 있지만 전진 가드가 없다 — 재빌드는 `update` 계열만 노출할 것.

**실측 불변식 검증**: 5/5 전부 `first_attempt ≤ last_success ≤ last_attempt`. 위반 0건.
현재값 — judge `2026-08-04/08-15/08-15`, meeting-mail `08-08/08-15/08-15`, appstore-snapshot `08-08/08-15/08-15`, mail-triage `08-12/08-15/08-15`, bplan-score `08-12/**08-14**/08-15` ← **성공이 1일 뒤처진 유일한 태스크**.

**빈 값의 의미**: `last_success` **키 자체가 없음** = 한 번도 성공 못 함(≠ 빈 문자열). 현재 5본 모두 존재.
**파손 처리**: JSON 파싱 실패 시 `<task>.corrupt-<epoch>` 로 **이름만 바꾸고 보존**, `{}` 로 진행. 삭제 금지.
**`.lock` 형제**: `<task>.lock`, **0 바이트, 내용 없음, 절대 삭제 안 함.** 실측 5/5 전부 0 B. 스테일 판정 규칙 없음(커널이 프로세스 사망 시 해제).

---

## A2. `<skill>/jobs/<id>/events.jsonl` — 이벤트 원장 (**유일한 진실**)

**형식** JSONL · UTF-8 · append-only · 이벤트마다 `flush`+`fsync`.
**실측 41잡 / 489 이벤트 / 48 KB.** 파일당 5–49 이벤트.

**봉투**: `{"ts": <UTC "%Y-%m-%dT%H:%M:%SZ">, "kind": <str>, ...}`
🔴 **ts 는 489/489 전부 `Z` 접미 UTC.** 오프셋(`+00:00`) 형식 0건. `_alerts`/`_usage` 는 반대로 오프셋 형식을 쓴다 — 두 포맷이 공존하니 재빌드는 파서를 관대하게, 라이터를 엄격하게.

### kind 유니온 — 실측 10종

| kind | 건수 | 부가 필드 | 코드 선언 |
|---|---:|---|---|
| `item_done` | 179 | `key`, `stage` | ✔ job.py |
| `state` | 155 | `from`, `to` (+`stages` 32, `error` 9) | ✔ job.py |
| `stage_done` | 95 | `stage` | ✔ job.py |
| `created` | 41 | `request` (obj) | ✔ job.py |
| `pipeline_error` | 9 | `error` | ✔ job.py |
| `meeting_mail_partial` | 4 | `remaining` | ✖ **스킬 정의** |
| `judge_partial` | 3 | `remaining` | ✖ **스킬 정의** |
| `bplan_budget_stop` | 1 | `used`, `ceiling` | ✖ **스킬 정의** |
| `judge_budget_stop` | 1 | `remaining`, `used`, `ceiling` | ✖ **스킬 정의** |
| `draft_rejected` | 1 | `page_id` | ✖ **스킬 정의** |

⚠️ **kind 는 닫힌 집합이 아니다.** kv-core 는 8종을 선언하지만 스킬이 임의 kind 를 자유롭게 emit 한다(실측 5종 추가, 10건). 반대로 코드가 선언한 `item_skipped`·`queued_wait`·`run_error` 는 **실측 0건**.
→ 재빌드 계약: **폴드(fold)가 아는 kind 는 `created`/`state`/`item_done`/`item_skipped`/`stage_done` 5종뿐이고, 모르는 kind 는 무시하되 보존한다.**

### 상태 전이 — 실측 5쌍만 등장

```
requested → queued   41    (41잡 전부)
queued    → running  41
running   → validating 32
validating→ completed  32
running   → failed      9
```
🔴 **41잡 전부에서 순서 규약 위반 0건.** 검사한 것: ① 첫 이벤트가 `created` 인가 ② 전이 사슬이 끊기지 않는가(`prev.to == next.from`) ③ 불법 전이가 있는가 ④ 종단 상태 뒤에 이벤트가 붙었는가. **네 항목 모두 41/41 통과.**
🔴 `ts` 단조 증가도 41/41 통과. **찢긴 tail 0건, 개행 없이 끝난 파일 0건.**

`validating → failed` 는 코드상 허용이지만 실측 0건. `partial` 상태는 존재하지 않는다(부분 완료는 `*_partial` 이벤트 + `completed` 로 표현).

**torn tail 규약**: 쓰기 전에 마지막 `\n` 까지 truncate → 읽을 때 **마지막 줄** 파손은 조용히 버리고, **중간 줄** 파손은 예외를 던진다(자동 복구 금지, 수동 격리 명령을 예외 메시지에 넣음). 재빌드도 이 비대칭을 지켜야 한다 — 중간 줄 파손을 조용히 넘기면 이벤트 유실이 은폐된다.

---

## A3. `<skill>/jobs/<id>/state.json` · `cursor.json` — **파생물**

둘 다 `events.jsonl` 의 폴드 결과이며 원자쓰기(tmp→fsync→rename)로만 갱신된다.

`state.json` = `{"state": <6종 중 1>, "updated": <ISO Z>}` · 41/41 정확히 2필드 · 60–63 B.
🔴 **실측 41/41 에서 `state.json.state` == 마지막 `state` 이벤트의 `to`.** 불일치 0건. 재빌드는 이 등식을 재구축 검증에 그대로 쓸 수 있다.

`cursor.json`:

| 필드 | 등장 | 타입 | 빈값 | 의미 |
|---|---:|---|---:|---|
| `done` | 41/41 | int | 0 이 다수 | 완료 항목 수 |
| `skipped` | 41/41 | int | 41건 전부 0 | 스킵 수 |
| `done_keys` | 41/41 | list[str] | 27본이 `[]` | **전 스테이지 합집합** |
| `by_stage` | 9/41 | obj{stage:[key]} | 6본이 `{}` | 스테이지별 |

🔴 **실측 41/41 에서 `cursor.done` == `item_done` 이벤트 수 == `len(done_keys)`.** 위반 0건.
⚠️ `by_stage` 는 **9/41 에만 존재**한다(초기 잡에는 없음). 재빌드가 필수로 두면 구파일 22본을 읽을 수 없다 — `없음 = 단일 스테이지` 로 해석해야 한다.
⚠️ `done_keys`(합집합)와 스테이지별 필터가 같은 이름을 쓰는 코드상 지뢰가 있다. 재빌드는 이름을 분리할 것.
⚠️ `cursor.json` 은 **25항목마다 + 루프 끝** 에만 flush 된다. `kill -9` 시 최대 24항목 뒤처질 수 있다 — **이벤트가 정본, cursor 는 캐시**. 실측에서는 뒤처진 파일 0건.

**빈 값 의미**: `done: 0` = 측정됐고 0건(≠미상). `done_keys: []` 는 27본에서 실제로 처리 항목이 없었음.

---

## A4. `<skill>/jobs/<id>/request.json`

`{"kind": <str>, "period": "YYYY-MM-DD"}` — 41/41 정확히 2필드, 47–53 B.
🔴 **실측 41/41 에서 `request.kind` 가 디렉토리명 접두와 일치**(`judge-2026-08-15` ↔ `kind:"judge"`).
⚠️ 코드는 이 파일을 **한 번도 읽지 않는다**(request 는 `created` 이벤트에서 복원). 재빌드는 이 파일을 삭제해도 되지만, 그러면 잡 디렉토리만 보고 종류를 알 방법이 디렉토리명뿐이 된다.

---

## A5. `<skill>/jobs/<id>/artifacts/*` — 잡별 산출물 (실측 7형태 / 60파일)

| 파일 | 잡종류 | 파일수 | 형식 | 레코드 필드 |
|---|---|---:|---|---|
| `candidates.json` | meeting-mail | 11 | JSON array | 12필드 100% 고정 · 460행 |
| `drafts.jsonl` | meeting-mail | 4 | JSONL | 9필드 100% + `contact_verified` **9/11** |
| `drafts.json` | meeting-mail | 3 | JSON array | **전부 `[]`** — jsonl 로 대체된 구형태 |
| `raw_<page8>.txt` | meeting-mail | 8 | **`.txt` 인데 6본이 JSON array** | 원문 초안 |
| `queue.json` | bplan-sync | 9 | obj{rows[]} | `key`(=`email\|ts`), `row`, `ts`, `props` |
| `queue_path.txt` | judge/bplan-score | 12 | 텍스트 1줄 | **절대경로** |
| `applied_ids.jsonl` | judge/bplan-score | 9 | ⚠️ **JSONL 아님** | 맨 UUID 1줄씩 |

`candidates.json` 필드(11본 460행 100% 등장): `page_id` `channel` `name` `url` `one_liner` `reason` `headline` `raw_text`(빈 16) `homepage`(빈 33) `linkedin_url`(빈 25) `author_username` `career`(빈 **397/460 = 86%**).
→ **빈 문자열 = 미상**. `career` 는 86%가 미상이므로 필수 필드로 승격하면 안 된다.

`drafts.jsonl` 11행: `founder_email` **11/11 빈 문자열**, `contact_channel` **11/11 `linkedin_dm`** — 이메일 발송 경로는 실데이터에 존재하지 않는다.

⚠️ `queue_path.txt` 가 `/home/example/kv-data/...` **절대경로를 박아 둔다**. 데이터 디렉토리를 옮기면 12본이 전부 깨진다. 재빌드는 `$KV_DATA` 상대경로로 바꿔야 한다.
⚠️ `applied_ids.jsonl` 은 확장자가 거짓말이다. 9본 1,803줄 전부 JSON 파싱 실패. 계약을 `applied_ids.txt` 로 정정하거나 `{"page_id": …}` 로 감쌀 것.
🔴 **applied_ids 중복 적용 0건**: judge 전 잡 합계 1,784줄 = distinct 1,784. `judge-2026-08-14`(failed, 257건 적용)와 `judge-2026-08-14-r2`(376건) 교집합 **0**. 재실행이 이미 적용된 건을 건너뛴다는 뜻 — **재빌드가 반드시 보존해야 할 멱등성 근거**.
⚠️ **실패 잡 4개가 부작용을 남겼다**(`judge-2026-08-14`, `bplan-score-2026-08-14/15/15-r2`). `state=failed` 라고 아무것도 안 했다고 가정하면 안 된다.

---

## A6. `_approvals/<key>.json` + `.lock` (+ `decisions/`)

**형식** JSON object · **11본 전부 정확히 동일한 12키** (키셋 변이 0).
키 형식: `meeting-mail-<Notion page UUID>` — `^[A-Za-z0-9._-]{1,120}$` 를 만족.

| 필드 | 등장 | 타입 | 실측값 | 성격 |
|---|---:|---|---|---|
| `key` | 11/11 | str | 파일명과 동일 | **저장** (파일명에서 추론 금지) |
| `task` | 11/11 | str | `meeting-mail` ×11 | 저장 |
| `kind` | 11/11 | str | `mail` ×11 | 저장 (`mail\|judgment\|summary`) |
| `origin` | 11/11 | str | `system` ×11 | 저장 (`system\|partner`) |
| `partner` | 11/11 | str | `deals@example.vc` ×11 | 저장 |
| `created` | 11/11 | str | `…Z`, 4개 시각 | 저장, 불변 |
| `payload` | 11/11 | obj | 10필드 | 저장 |
| `decision` | 11/11 | **null ×11** | — | 저장, **1회 기록** |
| `decided_at` | 11/11 | **null ×11** | — | 저장 |
| `decided_by` | 11/11 | **null ×11** | — | 저장 |
| `slack_ts` | 11/11 | str | 전부 유일 | 저장(2차 라이터) |
| `slack_channel` | 11/11 | str | `C000E8E4F75` ×11 | 저장(2차 라이터) |

`payload` 필드 유니온:
`to`(11) `subject`(11) `body`(11) `page_id`(11) `name`(11) `contact_channel`(11) `founder_email`(11, **빈 11**) `contact_handle`(11) `contact_source`(11) `contact_verified`(**9/11**)

### 코드 선언 vs 실제 파일 — 대조 결과

| 코드가 저장한다고 선언 | 실제 파일 | 판정 |
|---|---|---|
| `key task kind origin partner created payload decision decided_at decided_by` | 11/11 존재 | ✔ 일치 |
| `slack_ts` `slack_channel` (approvals_tick) | 11/11 존재 | ✔ 일치 |
| `executing_at` `draft_id` `executed_at` `execute_error` `orphan_draft_id` | **0/11** | ⚠️ 실행 경로 미검증 |
| 파생(비저장): `channel` `target` `status` `sent` `delivered` `modified` | 0/11 | ✔ 일치 — 파생 필드가 파일에 새지 않았다 |

🔴 **저장 필드 12개 / 파생 필드 6개의 경계가 실데이터에서 정확히 지켜지고 있다.** 재빌드가 `status`·`channel` 을 다시 저장하면 이 경계가 무너진다. `route_of`/`status_of` 는 읽을 때 계산할 것.
⚠️ **11/11 전부 미결재**다. `decision=null` 상태의 레코드만 실데이터에 존재하므로, 결재 후 필드 모양은 **실측 근거가 없다**. 재빌드는 `decide` 경로를 합성 데이터로 따로 검증해야 한다.
⚠️ `payload.contact_verified` 가 9/11 → **payload 는 열린 스키마**다. 필수 10필드로 못 박으면 구레코드 2본을 거부한다.

**`.lock` 형제**: 11/11, **0 바이트**, 삭제 안 함. `update()` 의 read-merge-write 를 감싼다.
**`decisions/`**: 🔴 **디렉토리가 존재하지 않는다.** 결재가 0건이라 한 번도 만들어진 적이 없다. 코드상 마커명은 `<key>.<created숫자14자>.decision`, 내용 `<decision>|<by>`, `O_EXCL` 로 생성하고 **절대 삭제하지 않는다**(같은 key 를 지웠다 다시 만들어도 옛 결정을 상속하지 않게 `created` 를 묶는다). 재빌드는 이 인카네이션 바인딩을 반드시 유지할 것.

---

## A7. `_alerts/*`

### `_alerts/policy/<id>.json`
`{"last_seen": "YYYY-MM-DD"}` — **4본 전부 27 B, 정확히 1필드.**
🔴 **파생 상태를 여기에 저장하지 마라.** 코드에 `first`/`count` 를 만드는 흔적이 있지만 쓰기 직전 버려지고, 실측 4/4 에도 없다. 의도된 설계다.
⚠️ 독자는 레거시 `last_sent` 도 읽지만 **라이터가 없다**(실측 0건).
**삭제 규칙**: 파싱 실패 → 삭제 · `last_seen` 이 미래 → 즉시 삭제 · 2일간 후보로 안 뜨면 삭제(해결로 간주).

### `_alerts/sent/<id>-<YYYY-MM-DD>` (확장자 없음)
`{"delivered": true, "ts": "<ISO +00:00>"}` — **18본 전부 58 B, 2필드.**
🔴 **중복 억제 키 = (identity, UTC 날짜). 하루 1회.**
🔴 **파일의 존재 자체가 신호이고, 내용은 아무도 읽지 않는다.** 독자는 파일명 뒤 10자만 파싱한다. 재빌드가 내용에 의미를 부여하면 안 된다.
🔴 **2단계 규약**: ① `O_EXCL` 로 클레임(프로세스 간 배제, 락 없음) ② 전송 성공 시 내용 덮어쓰기, **전송 실패 시 마커를 unlink** 해서 다음 틱이 재시도하게 한다. 이 보상 동작이 빠지면 알림이 영구 유실된다.
**백오프는 전부 파일명에서 파생**: 2일 미만 매일 → 7일 미만 2일마다 → 이후 7일마다. 4회째부터 `🔁` 접두.

실측 대조:
```
approvals-tick-error        08-14,08-15   policy.last_seen=2026-08-15
bplan-sync-vacuum           08-14,08-15   policy.last_seen=2026-08-15
gate-warn-claude            08-14,08-15   policy.last_seen=2026-08-15
taREDACTED_TOKEN 08-15         policy.last_seen=2026-08-15
meeting-mail-vacuum         08-11,08-12   policy 파일 없음  ← _forget 이 지움(정상)
never-succeeded-judge       08-07~08-10   policy 파일 없음  ← 정상
partial-appstore-snapshot   08-08~08-10   policy 파일 없음  ← 정상
```
🔴 **마커는 policy 보다 오래 산다.** 3/7 identity 가 policy 없이 마커만 갖고 있다. 재빌드가 "policy 없으면 마커도 지운다" 로 하면 전송 이력이 날아간다.

### `_alerts/events-<YYYY-MM-DD>.jsonl`
2본 · 12 레코드. 봉투 `{"ts": <ISO +00:00>, "kind", "identity", "text", ...meta}`.

| 필드 | 등장 | 비고 |
|---|---:|---|
| `ts` `kind` `identity` `text` | 12/12 | `observe()` 가 항상 붙임 |
| `task` | 9/12 | |
| `candidates` | 9/12 | int |
| `errors` | 2/12 | list |
| `resource` | 1/12 | `claude` |

실측 kind: `empty_harvest` 9 · `tick_error` 2 · `gate_warn` 1.
🔴 **파일명은 벽시계가 아니라 이벤트 자신의 `ts[:10]` 에서 온다.** 실측 12/12 일치(불일치 0). UTC 자정을 넘어가면 어제 파일에 써야 한다.
⚠️ `fsync` 없음 · 독자는 **어느 줄이든** 파손을 조용히 건너뛴다(job.py 와 반대). 소비 윈도는 달력 하루가 아니라 `[today-2, today]` 롤링이고, `identity` 로 dedup 하며 최신 `text` 를 남긴다. 200건 상한 초과 시 `alerts-events-truncated` 합성 후보를 넣는다.
레거시 단수 `_alerts/events.jsonl` 은 **존재하지 않는다**(코드는 아직 읽는다).

---

## A8. `_locks/*.lock`

**형식** 텍스트 · **15–16 B** · 내용 `"<pid> <epoch초>"` (예: `29471 1786783277`).
🔴 **내용은 진단용일 뿐, 소유권은 `flock` 이 결정한다.** 재빌드가 내용으로 소유자를 판정하면 두 홀더가 생긴다(과거 실제 결함).
🔴 **스테일 규칙 없음. 절대 unlink 하지 않는다.** 6시간 초과는 경고만 출력. 실측 29본이 전부 남아 있고 그게 정상이다.

실측 락 이름 29종: 리소스락 `claude` `notion` `notion-bplan` `gmail-inbox` `sheet-a1` `scheduler` `approvals` (7) + 잡락 `job-<skill>-<jobid>` (22).
⚠️ 리소스 이름은 화이트리스트(`claude notion notion-bplan apify gmail-inbox sheet-a1`)로 검증된다 — 오타가 나면 별도 락 파일이 생겨 직렬화가 조용히 쪼개진다. 실측에서 `apify.lock` 은 없다(아직 안 쓰임).
⚠️ **형식이 다른 락이 1본 있다**: `net-sourcing/body_index.lock` = `62978 2026-07-21 16:03:17` (25 B, epoch 대신 사람이 읽는 시각). `_locks/` 밖이고 스킬 소유다 — 통합할 것.
**cadence/approval 형제 락**(16본)은 다른 계약: **0 바이트, 내용 없음.**

---

## A9. `_usage/claude/<YYYY-MM-DD>.jsonl`

**형식** JSONL · 5본 · **120 레코드** · 336–1,968 B.

| 필드 | 등장 | 타입 | 실측 |
|---|---:|---|---|
| `ts` | 120/120 | str | ISO `+00:00` 오프셋 형식 |
| `ok` | 120/120 | bool | **true 120 / false 0** |

일별: 08-11 20 · 08-12 29 · 08-13 23 · **08-14 41** · 08-15 7.
⚠️ 코드는 실패 시 `err`(`timeout`/`empty_output`/`rc<N>:…`)를 함께 쓰게 돼 있는데 **실측 0건**. 즉 이 원장에는 성공만 기록돼 있고, `_alerts` 에는 같은 날 `gate_warn`(claude)이 떠 있다 — **두 신호가 어긋난다.** 실패가 이 파일에 도달하지 않는 경로가 있다는 뜻이라 재빌드 전에 확인이 필요하다.
⚠️ `kind` 필드가 없다(다른 JSONL 과 봉투가 다르다). 토큰/비용도 없다 — **호출 횟수만 세는 원장**이다. 게이트는 비어 있지 않은 줄 수만 센다.
🔴 파일명은 UTC 오늘. 게이트 한도(`ceiling`)는 이 파일의 줄 수로 판정된다 — 재빌드가 형식을 바꾸면 예산 게이트가 오작동한다.

---

## A10. `<skill>/dlq/*.jsonl`

3본 · 4 레코드. 봉투 `{"ts": <ISO +00:00>, ...entry}`, entry 는 **3형태**:

| 형태 | 실측 | 필드 |
|---|---:|---|
| 레코드 격리 | 2 | `record`(obj), `errors`(list) |
| 파싱 실패 | 2 | `parse_error`(str), `raw`(원문 전체) |
| 배치 가드 | 0 | `batch_error` |

실측 예: `net-sourcing/dlq/judge_dlq.jsonl` 2건 — 둘 다 `parse_error: "JSON 후보가 15개 — 모호해서 채택하지 않는다(fail-closed)"` + 39 KB 원문.
`mail-harness/dlq/bplan_dlq.jsonl` 1건 — `record.{page_id,score,fwd,reason}` + `errors`, `fwd`/`reason` 빈 문자열.
`net-sourcing/dlq/meeting_mail_dlq.jsonl` 1건 — `record.{to(빈),subject,body}` + `errors`.

🔴 **원문 절단 규칙**: 한 줄이 20,000자를 넘으면 `raw` 를 4,000자로 자르고 `"truncated": true` 를 붙인 뒤 20,000자로 하드 슬라이스. 5 MB 초과 시 `.jsonl.1` 로 1세대 로테이트.
⚠️ health 는 DLQ 의 **줄 수와 바이트만** 읽는다. 위 필드는 전부 사람이 읽으라고 있는 것 — 재빌드가 필드를 바꿔도 알람은 안 깨지지만 조사 능력이 사라진다.
⚠️ 코드 관점에서는 `<skill>/dlq/` 경로를 만드는 프로덕션 라이터가 없다(호출자가 경로를 넘긴다). 실측으로 **스킬 3본이 실제로 이 규약을 지키고 있다** — 규약을 코드로 끌어올릴 것.

---

## A11. `<skill>/last_run_health/<channel>.json`

`{"ts","channel","ok","degraded","counts","last_ok_ts","first_ts}` — 7필드.
실측 `net-sourcing/last_run_health/appstore.json`: `ts=2026-08-15T11:15:34`(**naive, 타임존 없음**), `channel="appstore"`, `ok=true`, `degraded=[]`, `counts={"appstore":7895}`, `last_ok_ts=ts`, `first_ts=2026-08-12T08:30:23`.

🔴 **naive 타임스탬프는 KST 로 해석된다** (독자가 9시간 뺀다). 재빌드가 UTC 로 바꾸면 헬스가 9시간 어긋난다. 차라리 offset-aware 로 바꾸고 독자도 같이 고칠 것.
🔴 `ts` 없으면 `unknown`. `degraded: []` = **정상**(측정됨), 키 없음 = 미상.
⚠️ 독자는 `ts`/`ok`/`last_ok_ts`/`first_ts` 4개만 읽는다. `channel`·`degraded`·`counts` 는 write-only.
⚠️ 라이터가 **원자쓰기를 안 쓴다**(평문 `write_text`). 잘린 파일이 나올 수 있다.
⚠️ **단수형 `net-sourcing/last_run_health.json` 이 아직 남아 있다** — `ts=2026-08-13`(2일 stale), `.bak-20260728` 도 함께. 은퇴본이니 재빌드는 읽지도 쓰지도 말 것. 채널 파일은 `appstore` 하나뿐이고 `threads`/`linkedin` 은 없다.

---

# PART B — 스킬 소유

## B1. `net-sourcing/linkedin_seen.json` 🔴 최중요

**형식** JSON object = **키→레코드 맵** · 139,530 B · **1,148 항목**.

**키 정규화**: `urn:<축약 LinkedIn URN>` (예: `urn:ACAAA-3860B`).
🔴 실측 1,148/1,148 이 `urn:` 접두. 대소문자 정규화 후 중복 0건.
🔴 백업이 두 단계 마이그레이션을 증언한다:
- `.bak-2026-07-27`(344) — 키가 **원시 전체 URN**(`EXAMPLE_ID_5d92e8d20ac1`) 247건 + 기타 97건. 접두 없음.
- `.bak-before-fpv2`(259) — 키는 이미 `urn:ACAAA183pkB` 로 축약됐지만 **지문은 무버전**.
→ **키 축약과 지문 v2 는 별개의 마이그레이션이었다.** 재빌드는 두 정규화를 각각 멱등으로 유지해야 한다.

**값** `{"d": "YYYY-MM-DD", "f": "<지문>"}` — 필드는 이 둘뿐(1,148/1,148).

**지문 버전 분포** (요청 항목):

| 버전 | 건수 | 비율 |
|---|---:|---:|
| `v2\|` | **1,136** | 99.0% |
| **무버전**(접두 없음) | **12** | 1.0% |
| `v1\|` | **0** | 0% |

🔴 **`v1` 잔존은 0건.** 대신 접두가 아예 없는 12건이 남아 있다 — 이게 실질적 구스키마 잔재다.
🔴 **빈 지문 `v2||` 비율: 62/1,148 = 5.4%.** (파이프를 벗긴 본문이 공백)
파이프 세그먼트 수: 2개 1,106 · 3개 36 · 4개 4 · 5개 1 · 6개 1 → **지문은 `v2|회사|직함` 고정 2세그먼트가 아니다.** 회사명에 `|` 가 들어가면 늘어난다. 파싱은 `split("|", 2)` 로 해야 한다.

**`d` 값**: 100% `YYYY-MM-DD`. 5개 날짜(`2026-07-11` 12 · `07-13` 238 · `07-27` 481 · `08-04` 120 · `08-12` 297) — **실행일 배치 도장**이지 관측일이 아니다.
🔴 **불변식: 키는 한 번 들어오면 사라지지 않는다(append-only 맵).** 1,148 > 344 > 259 로 단조 증가.
**빈 값 의미**: `f` 가 `v2||` = **지문을 만들 재료가 없었음(미상)**, ≠ "변화 없음". 리텐션 diff 가 이 62건을 변화로 오탐하면 안 된다.

## B2. `net-sourcing/linkedin_axis_state.json`
맵(축 slug → `{last_run, next_page, exhausted}`), **6축**. 602 B.
🔴 `next_page` 는 **전진만**. 실측 flow-new-founder 11 · stealth-kr 13 · stealth-us-krschool 7 · early-kr 10 · gap-practitioner 5 · posted-early 7.
🔴 `exhausted` 6/6 전부 `false` — **true 인 상태가 실데이터에 없다.** 소진 경로는 미검증.
⚠️ `stealth-us-krschool` 만 `last_run=2026-07-27` 로 3주 정지, 나머지 5축은 `2026-08-12`. 축 하나가 조용히 멈춰 있다.

## B3. threads 계열 (커서 3 + 리스트 3)
- `threads_watch_cursor.json` `{"cursor": 24}` · `threads_follow_cursor.json` `{"cursor": 60}` — 정수 1필드, **전진만**.
- ⚠️ `threads_follow_cursor.json.pending` — 내용이 본문과 동일한 `{"cursor":60}`. **원자쓰기 스테이징 잔재**. 재빌드는 `.pending`/`.tmp` 를 시작 시 청소할 것.
- `threads_last_success.json` `{"date": "2026-07-13"}` — **한 달 넘게 정지**.
- `threads_anchors.json` 16개 문자열 배열 · `threads_watchlist.json` 1개 · `linkedin_watchlist.json` 72개.
🔴 세 watchlist/anchors 는 **객체가 아니라 순수 문자열 배열**이다. 레코드로 승격하면 기존 파일을 못 읽는다.
🔴 **`threads_layer_state.json` 은 존재하지 않는다.** 브리핑이 지목했지만 파일이 없다 — 계약에서 빼거나, 있어야 할 것이 유실된 것인지 확인 필요.

## B4. `net-sourcing/followup_state.json` / `followup_events.jsonl`
상태: 맵(`li:<slug>` → `{last_checked, next_due, headline}`), **5건**. `next_due` 는 `last_checked+30일`.
이벤트: JSONL 19건 · 필드 `ts`(**naive, Z 없음**) `kind` `key` `by` + `name`/`summary`/`from`/`page`(16/19) + `old`/`new`(3/19).
🔴 `from` 값이 `v2|…` 지문이다 — **B1 의 지문 포맷이 여기로 새어 나온다.** 지문 형식을 바꾸면 이 원장도 같이 깨진다.
⚠️ `ts` 가 `Z` 없는 naive 형식(A2 와 다름). ⚠️ 5건/19건으로 사실상 죽은 기능.

## B5. `net-sourcing/judge_queue_<D>.json` (8본 18.4 MB)
`{"generated": "YYYY-MM-DD", "criteria": <프롬프트 지시문>, "count": int, "rows": [...]}`
`rows[]`: `page_id` `channel` `name` `url` `raw_text`(100%) + `handle`(7/8) `headline`(7/8).
⚠️ `handle`/`headline` 이 **8본 중 1본에 없다** — 구스키마. `count` 는 `len(rows)` 와 중복 저장(파생물이 저장돼 있음).
⚠️ `criteria` 에 프롬프트 문자열이 데이터 파일에 박혀 있다.

## B6. `net-sourcing/judge_verdicts_*` (97본 · 1,805행) — **명명 3세대**

| 세대 | 파일 | 패턴 |
|---|---:|---|
| 레거시(잡ID 없음) | 20 | `judge_verdicts_<D>_<NN>.json` (2026-08-11) |
| 잡스코프 | 57 | `judge_verdicts_<D>_judge-<D>_<NN>.json` |
| 재실행 | 20 | `judge_verdicts_<D>_judge-<D>-r2_<NN>.json` |

레코드 4필드 100%: `page_id` `verdict` `reason` `one_liner`.
**verdict 값 유니온(4종)**: `Cut` 763 · `Wait` 471 · `Trend` 353 · `Catch` 218.
⚠️ **오타 필드 `verdter` 가 52행(2.9%)에 있다.** 52행 전부 `verdict` 와 **값이 동일한 중복**이고, 52개 파일에 정확히 1행씩 흩어져 있다(08-11~08-14 전 세대). LLM 출력 파싱이 키를 흘린 것 — 재빌드는 이 필드를 **무시**하되, 검증기가 미지 필드에서 실패하면 안 된다.
🔴 page_id 중복 20건(전부 2회) — 재실행 배치 간 재판정. **verdicts 는 append-only 이력이지 최신 상태가 아니다.** 최신 판정을 얻으려면 파일 mtime/잡ID 순으로 접어야 한다.

## B7. `net-sourcing/cut_<D>.json` (20본 · 274 MB · 157,022행)
JSON array. 레코드 **14필드 전부 100% 등장** — `source` `name` `url` `one_liner` `founder` `released_at` `raw_text` `app_id` `ext_id` `linkedin_url` `homepage` `adapter` `signals` `_cut_reason`.
🔴 `_cut_reason` 은 **자유 텍스트**(`출시 180일 초과(2021-07-14)` 처럼 괄호에 근거를 담음). 접두는 3종뿐. 상세 분포·룰 사망 구간은 `state-inventory.md` §5.
🔴 **채널 필드가 없다.** `_cut_reason` 접두가 유일한 채널 단서(07-13 만 스레드).
⚠️ 하루 12–16 MB 씩 무한 증식. 컷된 레코드를 **원문 통째로** 보관한다 — 재빌드는 컷 사유 + 키만 남기는 형태를 검토할 것.

## B8. `net-sourcing/research/labels.json` 🔴 **사람이 만든 정답지 — 절대 덮지 말 것**
`{"builders": [handle …60], "all": [{handle, is_builder, category, reason, uncertain} …513]}`
🔴 `builders` ≡ `{h : all[h].is_builder}` **집합 완전 일치**(양쪽 차집합 0). 파생 인덱스이므로 **`all` 이 정본**.
🔴 5필드 513/513 전부 존재, handle 중복 0. `is_builder` true 60(11.7%) / `uncertain` true **109(21.2%)**.
⚠️ `category` **210종 자유 텍스트**, 통제 어휘 없음. `오프라인 가게`/`오프라인가게`, `직장인 개발자`/`직장인개발자` 같은 띄어쓰기 중복 존재.
🔴 `reason` 은 원문 인용을 포함한 사람 서술 → **재생성 불가**. 재빌드는 이 파일을 읽기 전용으로 취급하고, 마이그레이션이 필요하면 새 경로에 쓰고 원본은 남길 것.
동반: `research/targets.json`(635, `handle bio target builder layers kw followers posts`) · `research/threads_labeled.json`(635, `handle bio followers layers post_count posts[]{text,query,layer}`) — 둘 다 handle 키, 같은 635 모집단. `followers` `null` 15건 = **미상**, `posts`/`kw` 빈 리스트 94/248건 = **측정됐고 0건**.

## B9. mail-harness 상태
- `bplan/sync_ledger.json` — 맵, 키 = **구글시트 행번호 문자열**(`"5483"`…), 값 `{company}`. **29건.** 🔴 키가 행번호라 시트를 정렬하면 계약이 깨진다.
- `bplan/drop_ledger.json` — 맵, 키 = Notion page_id, 값 `{company, draft_created, checkbox_done}`. **35건, 35/35 둘 다 true.** 🔴 `false` 인 레코드가 실데이터에 없다(부분 실패 모양 미검증).
- 🔴 **둘 다 은퇴 파일이 아니라 현역이다.** 브리핑의 "은퇴 파일" 전제는 틀렸다.
- `bplan_sync_floor.json` `{"last_ts": "2026-08-13T21:37:50"}` — naive. 🔴 **전진만**(워터마크).
- `triage/plan_<TS>.json` 11본 441행: `id thread_id subject sender category why action note`(100%) + `to`/`body`(43%) + `unknown`(41%). 🔴 `to`/`body` **없음 = 초안 없는 액션**(archive 등), 빈 문자열이 아니다. `unknown: true` 만 존재하고 false 는 없다 — **키 부재 = false**.
- `triage/history/` 4본 중 3본이 상위와 md5 동일 → 중복 보관. `processed.json` 은 **존재하지 않는다**.
- `harness/run_<TS>.json` 4본 — 최상위 `started stages[] path bplan_sync bplan_drop plan_summary escalations metrics_summary dry_run phase2_done`. ⚠️ `bplan_sync`/`bplan_drop` 이 `obj|null` 이고 하위 키셋이 실행마다 다르다(`halted`/`gate` vs `created`/`failures`) — **닫힌 스키마로 못 박으면 안 된다**. `dry_run: true` 4/4 → **실행 모드 산출물이 실데이터에 없다.**
- `harness/templates_checksum.txt` — sha256 1줄 64자. 🔴 템플릿 변조 감지용, 값 자체가 계약.

## B10. ai-consumer-pulse 상태
- `state/seen.jsonl` — 4,205행 `{k, d}`. 🔴 **키 중복 0, `d` 단조 비감소, append-only.** 🔴 **행 수 4,205 == `raw/**` 캐시 파일 수 4,205 로 정확히 1:1.** 재빌드가 지켜야 할 가장 선명한 불변식.
- `state/health.json` — 맵(source_id → `{consecutive_zero, last_run, last_count, http_status}` 100% + `last_ok` 96% + `note` 6%), **137 소스**.
  🔴 `consecutive_zero: 0` = 측정됐고 정상. **`last_ok` 키 없음(5건) = 한 번도 성공 못 함** — 빈 문자열과 구분해야 한다.
  ⚠️ `last_run` 이 132/137 은 `2026-08-04`, 5개는 07-16~07-27 → **소스 5개가 수집에서 누락돼 있다.** `http_status` 429 2건, `consecutive_zero ≥ 3` 이 16건.
- `trends/mentions.jsonl` 1,499행 `{week, theme, org, item, event_ref, tier}`. `event_ref` **null 263 + 빈문자 909 = 1,172/1,499(78%)** 비어 있다.
- `themes/registry.jsonl` 40행 `{slug, name, definition, bucket, seeded_from, status, added}`. 🔴 `slug` 가 안정 키 — 분석 산출물이 `NEW:<slug>` 형태로 신규 테마를 표기하므로 접두 규약을 유지할 것.
- ⚠️ `unc_arch.json` — 상태가 아니라 **API 400 에러 응답**(`{"errors":[{"location":"query","param":"limit","value":60,"msg":"Invalid value"}]}`)이 저장됨. 재빌드 대상 아님, 삭제 후보.
- 캐시 `raw/<WEEK>/<SOURCE>/<page>.json` 4,205본: 공통 11필드 100%(`key source_id org tier track adapter title url published fetched_at content`) + 소스별 `kind`(28%) `quote_verbatim`(20%) `batch`/`tags`(4%) `duration_s`(2%). 🔴 `key` 가 seen.jsonl 의 `k` 와 같은 네임스페이스.

## B11. `hello-mail/` (아카이브 스킬)
`plan_<TS>.json` **4본뿐**. 스키마는 `mail-harness/triage/plan_*.json` 과 **완전 동일**(같은 11필드). 3본은 `mail-harness/triage/history/` 와 **md5 완전 일치**이고, `plan_20260715-173243` 과 `173428` 은 서로도 내용 동일.
🔴 **고유 데이터 0건.** 재빌드는 이 디렉토리를 이관 대상에서 제외해도 손실이 없다(단 삭제 전 md5 재확인 권장).

## B12. 스냅샷·원본 캐시 (재빌드 저순위)
- `snapshots/appstore_*_<chart>_<D>.json` — **문자열 배열**(앱 ID) 97–200개. 두 네임스페이스 동시 기록: `appstore_<cat>` 25카테고리×3차트=75/일, `appstore_board_<cat>` 42×2=84/일, `appstore_all_new` 1/일 → **160/일**.
  ⚠️ 은퇴본 `appstore_<N>_<D>`(차트 세그먼트 없음, 8본, 07-07~07-10) · `appstore_rss_new_<D>`(1본, `all_new` 로 대체) 잔존.
  `disquiet_products_<D>` 는 앱ID가 아니라 slug/UUID 혼합 문자열 배열 — **키 공간이 다르다**.
- `raw/appstore_<D>.json`(26본 317 MB) · `raw/threads_<D>`(7) · `raw/linkedin_harvestapi_<D>`(6) — **셋 다 동일한 정규화 레코드 13필드**(`source name url one_liner founder released_at raw_text app_id ext_id linkedin_url homepage adapter signals`). 🔴 **`adapter` 가 채널 판별 키**(`appstore`/`threads`/`linkedin.harvestapi`), `signals` 만 어댑터별로 다르다. 이게 수집 계층의 실질 계약이다.
  ⚠️ `signals.genre` 가 `list`(186,403) 와 `str`(827) 로 **타입이 섞여 있다**.
  ⚠️ `raw/{listly,linkedin_listly}_<D>.json` 7본은 전부 `[]`(2바이트) — 죽은 수집기.
- `raw/_actor/<actor>_<TS>.json` 136본 168 MB — `{slug, payload, items[]}`. Apify 원문 그대로. `items` 빈 것 4본.
- `fixtures/*.json` 11본 — 그중 3본이 `[]`(빈 픽스처).

---

# PART C — 재빌드가 반드시 지켜야 할 불변식 (실측 근거 있는 것만)

| # | 불변식 | 실측 근거 |
|---|---|---|
| I1 | `events.jsonl` 이 유일한 진실, `state.json`/`cursor.json` 은 폴드 결과 | 41/41 에서 `state.json.state`==마지막 `state.to`, `cursor.done`==`item_done` 수==`len(done_keys)` |
| I2 | 이벤트는 append-only + 이벤트마다 fsync, 쓰기 전 torn tail truncate | 41본 찢긴 tail 0, 개행 누락 0 |
| I3 | 상태 전이는 5쌍만, 종단 뒤 이벤트 금지, ts 단조증가 | 41/41 위반 0 |
| I4 | 이벤트 `kind` 는 **열린 집합** — 모르는 kind 는 무시하되 보존 | 스킬 정의 5종 10건 실재 |
| I5 | 워터마크는 락 안에서 `max()` 전진, `first_attempt` 는 1회만 | 5/5 `first ≤ success ≤ attempt` |
| I6 | 워터마크 파손 시 삭제 금지 — `.corrupt-<epoch>` 로 보존 | 코드 규약(실측 파손 0) |
| I7 | 승인은 저장 12필드 / 파생 6필드 경계 유지, 파생을 파일에 쓰지 말 것 | 11/11 파생 필드 유출 0 |
| I8 | 승인 결정은 `decisions/<key>.<created14>.decision` 마커로 1회만, 영구 보존 | 코드 규약(디렉토리 미생성 — 미검증) |
| I9 | 알림 중복억제 = (identity, UTC날짜), **파일 존재가 신호**, 내용은 안 읽음 | 18본 전부 58 B 동일 |
| I10 | 알림 전송 실패 시 마커 unlink(보상) — 안 하면 알림 영구 유실 | 코드 규약 |
| I11 | `_alerts/sent` 마커는 policy 보다 오래 산다 — policy 없다고 지우지 말 것 | 3/7 identity 가 마커만 보유 |
| I12 | `_alerts/events-<D>` 파일명은 벽시계가 아닌 이벤트 `ts[:10]` | 12/12 일치 |
| I13 | 락 소유권은 `flock` 만, 내용은 진단용, **절대 unlink/reap 금지** | 29본 전부 잔존이 정상 |
| I14 | `linkedin_seen` 키는 `urn:<축약URN>`, 맵은 append-only 단조증가 | 1,148/1,148 · 259→344→1,148 |
| I15 | 지문은 `v2\|` 접두 + `split("\|",2)`, 빈 지문 `v2\|\|` = **미상**(변화 아님) | 무버전 12, 빈 62 |
| I16 | `applied_ids` 는 멱등성 근거 — 재실행이 기존 적용분을 건너뛴다 | judge 1,784줄 전부 유일, 실패잡↔r2 교집합 0 |
| I17 | `state=failed` 여도 부작용은 남아 있을 수 있다 | 실패잡 4개가 artifacts 보유 |
| I18 | `research/labels.json` 은 사람 정답지 — **덮어쓰기·재생성 금지**, `all` 이 정본 | `builders` 는 `all` 에서 완전 파생 |
| I19 | pulse `seen.jsonl` 행 수 == `raw/**` 캐시 파일 수 | 4,205 == 4,205, 중복 0 |
| I20 | `judge_verdicts` 는 이력이지 최신 상태가 아니다 | page_id 중복 20건 |
| I21 | 키 부재 ≠ 빈 값: `last_ok` 부재=한번도 성공 못함 · `to`/`body` 부재=초안 없음 · `unknown` 부재=false | 5건 / 251건 / 261건 |
| I22 | `0`/`[]` 는 **측정된 0**, `null`/키부재가 **미상** | `skipped:0` 41/41 · `degraded:[]` · `followers:null` 15 |
| I23 | `raw/*` 수집 레코드는 어댑터 불문 13필드 고정, `adapter` 가 채널 판별키 | 189,593행 100% |
| I24 | naive 타임스탬프는 KST 로 해석된다(health 계열) | `last_run_health` 독자가 −9h |

---

# PART D — 코드 선언 vs 실제 파일 불일치 요약

| 항목 | 코드 선언 | 실제 파일 | 조치 |
|---|---|---|---|
| 이벤트 kind | 8종 닫힘 | **10종**(스킬 5종 추가), 코드 3종은 0건 | 계약을 열린 집합으로 |
| 승인 실행 필드 | `draft_id` `executed_at` `executing_at` `execute_error` `orphan_draft_id` | **0/11** | 합성으로 검증 필요 |
| 승인 `decisions/` | 마커 규약 상세 | **디렉토리 없음** | 미검증 경로 |
| `_alerts/policy` `first`/`count` | setdefault 후 버림 | 파일에 없음 | 죽은 코드 제거 |
| `_alerts/policy` `last_sent` | 독자만 있음 | 라이터 없음, 파일 0건 | 레거시 폴백 제거 검토 |
| `_alerts/events.jsonl`(단수) | 아직 읽음 | **없음** | 폴백 제거 |
| `_usage` `err` | 실패 시 기록 | **0/120**(전부 ok=true) | 실패 경로 확인 필요 |
| `<skill>/dlq/` 규약 | 프로덕션 라이터 없음(호출자 경로) | **스킬 3본이 실제로 준수** | 규약을 코드로 승격 |
| `last_run_health.json`(단수) | 역사적, 아무도 안 씀 | **파일 잔존 + 2일 stale** | 삭제 |
| `cursor.json` 독자 | 테스트만 | 41본 존재 | 유지(운영 진단용) |
| `request.json` 독자 | 없음 | 41본 존재 | 유지 or 폐기 결정 |
| 원자쓰기 미적용 | `_alerts/sent`·`events-*`·`_usage`·`last_run_health` | 실측 파손 0 | 위험 잔존 |
| `bplan/{sync,drop}_ledger` | (브리핑: 은퇴) | **현역 · 29/35건** | 계약에 포함 |
| `threads_layer_state.json` | (브리핑: 존재) | **없음** | 확인 필요 |
