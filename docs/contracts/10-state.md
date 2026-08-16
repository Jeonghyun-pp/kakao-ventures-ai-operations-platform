# [1] 상태 스키마 계약 — kv-core 소유분 (초안 A: 코드에서 유도)

🔴 **이 문서는 "코드가 그렇게 하겠다고 선언한 것"이다.**
에이전트 C 가 실제 파일 전수에서 유도한 결과(`state/state-schemas.md`)와 **대조해야** 완성이다.
둘이 어긋나는 곳이 곧 결함이다 — 이 리포의 지배적 실패 모드가 *"선언 vs 실체"* 이므로
두 출처를 따로 만들어 맞춰보는 것이 계약 작성 방법 자체다.

출처: `kv_core/{scheduler,job,approval,alerts,gate}.py` 정독(2026-08-15) + 라이브 파일 관찰

---

## 1. `<skill>/cadence/<task>.json` — 워터마크

```json
{"last_attempt": "2026-08-15", "last_success": "2026-08-15", "first_attempt": "2026-08-08"}
```

| | |
|---|---|
| 형식 | JSON object · 날짜는 **UTC** `YYYY-MM-DD` |
| 경로 | `$KV_DATA/<skill>/cadence/<task>.json` — task 이름이 파일명 |
| `last_success` | 성공했을 때만 전진. **due 판정의 기준선**(watermark 정책이 `last_success` 인 task) |
| `last_attempt` | 시도할 때마다 전진 |
| `first_attempt` | **최초 1회만** 기록하고 보존 — never-succeeded 알림의 경과일 앵커 |
| 불변식 | `last_*` 는 **전진만**(`max` 비교). `first_attempt` 는 `setdefault` |
| 갱신 | 읽기-병합-쓰기를 **flock 안에서**(`scheduler.update_watermark`). 밖에서 비교하면 그 사이가 창 |
| 부재 | 파일 없음 = **첫 실행**(정상). `{}` 반환 |
| 손상 | 🔴 `.corrupt-<ts>` 로 **보존**하고 `{}` 반환 + loud. **지우지 않는다** |

**어기면**: `first_attempt` 가 오늘로 되감기면 "배선 후 경과일"이 영원히 0 → never-succeeded
알림이 **영구 침묵**(실환경에서 실증됨). `last_success` 가 되감기면 이미 한 일을 다시 한다.

**재빌드 주의**: UTC 기준을 KST 로 바꾸면 날짜 경계에서 하루가 통째로 어긋난다
(2026-08-02 계층 A 사고 — 예약 실행 전멸).

---

## 2. `<skill>/jobs/<id>/events.jsonl` — 잡 장부 (**유일한 진실**)

```jsonl
{"ts":"2026-08-15T00:10:08Z","kind":"created","request":{"kind":"judge","period":"2026-08-15"}}
{"ts":"...","kind":"state","from":"queued","to":"running"}
{"ts":"...","kind":"stage_done","stage":"export"}
{"ts":"...","kind":"item_done","key":"<멱등키>","stage":"<스테이지>"}
{"ts":"...","kind":"pipeline_error","error":"NameError(...)"}
```

| | |
|---|---|
| 형식 | **append-only JSONL** · `ts` 는 UTC `%Y-%m-%dT%H:%M:%SZ` |
| job_id | `<task>-<YYYY-MM-DD>` · 당일 재시도는 `-r2`·`-r3` 접미사(하루 3회 상한) |
| kind | `created` · `state` · `item_done` · `item_skipped` · `stage_done` · `run_error` · `pipeline_error` · `queued_wait` + 잡별 커스텀(`judge_budget_stop` 등) — **[P3 에서 유니온 확정]** |
| 쓰기 | append + **fsync**. 쓰기 전 `_repair_torn_tail` 로 개행 없는 마지막 줄을 잘라낸다 |
| 파손 | **마지막 줄만** 깨질 수 있다(구조상). 중간 줄 파손은 규약 위반 → raise + 복구 절차 안내 |
| 불변식 | 부작용 **완료 후에** `item_done`. 순서가 뒤집히면 안 한 일이 "했다"로 남는다 |

**어기면**: 중간 줄이 깨지면 `rebuild`·`transition`·`current_state` 가 전부 죽어 그 잡이
**영구 벽돌**이 된다. 복구는 `job.quarantine_corrupt_events(skill, job_id)` — 자동으로 부르지 않는다
(자동 격리는 "장부가 깨졌다"는 사실 자체를 지운다).

---

## 3. `<skill>/jobs/<id>/{state,cursor}.json` — **파생물(캐시)**

```json
// state.json
{"state": "completed", "updated": "2026-08-15T02:15:13Z"}
// cursor.json
{"done": 25, "skipped": 0, "done_keys": [...], "by_stage": {"judge-apply": [...]}}
```

| | |
|---|---|
| 성격 | **캐시다.** `events.jsonl` 재생(`job.rebuild`)으로 언제든 복원 가능 |
| 갱신 | `create`·`transition`·항목 25개마다·잡 종료 시 |
| 신뢰도 | 🔴 **종단 상태(`completed`·`failed`)만 믿어도 된다.** 비종단이면 events 를 확인해야 한다 — `transition` 은 append→materialize 2단계라 그 사이 크래시하면 갈라진다 |

**재빌드 주의**: 관측자가 이 파일을 진실로 읽으면 안 된다. `health._failures` 가 그 실수를 했다가 고쳤다.

---

## 4. `_approvals/<key>.json` — 승인 레코드 = 라벨 저장소 = W10 측정 재료

```json
{"key":"meeting-mail-<page_id>","task":"meeting-mail","kind":"mail","origin":"system",
 "partner":"deals@example.vc","created":"2026-08-15T02:16:00Z",
 "payload":{"to":"...","subject":"...","body":"...","page_id":"...","name":"...",
            "contact_channel":"linkedin_dm","founder_email":"","contact_handle":"...",
            "contact_source":"...","contact_verified":false},
 "decision":null,"decided_at":null,"decided_by":null,
 "slack_ts":"1786760694.313669","slack_channel":"<채널id>"}
```

| | |
|---|---|
| `key` | 파일명이자 정체. `[A-Za-z0-9._-]{1,120}` — 경로 문자 금지 |
| **저장 필드** | key · task · kind · origin · partner · created · payload · decision · decided_at · decided_by · slack_ts · slack_channel · draft_id · executed_at · execute_error · executing_at · orphan_draft_id |
| **파생(저장 금지)** | `channel`/`target`(→`route_of`) · `status`(→`status_of`) · `sent`·`delivered`·`modified` — 전부 제거됨 |
| 불변식 | **저장된 값과 계산된 값이 공존하면 반드시 어긋나고, 사람은 저장된 쪽을 믿는다** |
| 갱신 | `update(key, **fields)` — 읽기-병합-쓰기를 **flock** 안에서. 스냅샷을 들고 있다 저장하면 그 사이 결정이 사라진다 |
| `kind` 의미론 | `mail` → 초안이 생기면 끝(`draft_id`) · 그 외 → 결정되면 끝 |

**실측(2026-08-15)**: 11건 전부 `decision:null`, `slack_ts` 있음, `contact_channel` 이 전원 DM.
→ **승인 경로 후반부(decide→execute)는 프로덕션 실행 이력이 0** 이다(§5 참조).

---

## 5. `_approvals/decisions/<key>.<created>.decision` — 결정 마커

| | |
|---|---|
| 형식 | 내용 `"<decision>|<by>"` · **`O_EXCL` 원자 선점** |
| 파일명 | key 가 아니라 **레코드의 화신**(`created` 타임스탬프에서 숫자만 14자)에 묶는다 |
| 왜 | key 만 쓰면 레코드를 지웠다 같은 key 로 재생성할 때 **옛 결정이 되살아난다** — 아무도 결정하지 않았는데 자동 승인/거부 |
| 성격 | 락이 아니다. **한 번 생기면 영원히 남는 게 정상** → stale 회수 개념 자체가 없다 |

🔴 **실측: 이 디렉토리가 존재하지 않는다.** = 프로덕션에서 `decide()` 가 한 번도 성공 경로를 탄 적이 없다.
재빌드에서 이 구간은 "돌아가던 걸 옮기는 것"이 아니라 **처음 켜보는 것**으로 다뤄야 한다.

---

## 6. `_alerts/` — 발송 마커 · 정책 · 관측 사실

```
sent/<identity>-<YYYY-MM-DD>     발송 성공 시에만 생성. 내용 {"delivered":true,"ts":...}
policy/<identity>.json           {"last_seen":"YYYY-MM-DD"} — 저장하는 건 이것 하나뿐
events-<YYYY-MM-DD>.jsonl        observe() 가 남긴 사실 {"ts","kind","identity","text",...}
```

| | |
|---|---|
| 마커 | `O_EXCL` 선점 → 발송 → 성공하면 내용 기록, **실패하면 claim 반납**(다음 틱 재시도) |
| identity | 파일명이 되므로 위생 처리. 100자 초과면 88자 + `-<sha1 8자>` |
| 정책 | **`last_seen` 만 저장.** 횟수·최초·마지막 발송일은 **마커에서 유도**한다 |
| 왜 | 파생을 저장했더니 실데이터에서 어긋났다 — 마커 유도 `count=4` vs 저장 `count=0` → 에스컬레이션이 영영 안 뜸 |
| events 파일 | **그 이벤트의 ts 기준 날짜**로 이름 짓는다(벽시계 아님). 읽는 쪽과 기준이 달라 UTC 자정에 유실됐던 이력 |

🔴 **미해결(2026-08-15 발견)**: `_from_events` 룩백이 2일인데 dedup 마커는 일자별이라,
**어제 관측된 사실이 오늘 다시 발송**된다. 어제 고친 문제가 오늘도 울려 "안 고쳐졌다"로 읽힌다.
→ 재빌드에서 룩백 창과 dedup 단위를 **같은 축**으로 맞춰야 한다.

---

## 7. `_locks/<자원>.lock` — flock 대상

| | |
|---|---|
| 성격 | **파일이 곧 락의 대상.** 존재 여부는 점유와 무관 |
| 획득 | `os.open(O_CREAT\|O_RDWR)` → `fcntl.flock(LOCK_EX\|LOCK_NB)` |
| 해제 | `flock(LOCK_UN)` + close. 🔴 **unlink 하지 않는다** |
| 내용 | `"<pid> <epoch>"` — **진단용일 뿐 판정에 쓰지 않는다** |
| 회수 | **없다.** 프로세스가 죽으면 커널이 푼다 → stale reaping 개념 자체가 불필요 |
| 이름 | 자원명(`claude`·`notion`·`apify`…) 또는 `job-<skill>-<job_id>` · `scheduler` · `approvals` |

**어기면**: pid 파일 락 시절, 6시간 초과 회수 뒤 원 소유자가 회수자의 락을 지워
**슬롯이 무주공산**이 됐다(실측 재현). 이름 오타는 별도 락이 되어 직렬화가 조용히 갈라진다.

---

## 8. `_usage/claude/<UTC날짜>.jsonl` — 구독 쿼터 프록시 계측

```jsonl
{"ts":"2026-08-15T02:10:18+00:00","ok":true}
{"ts":"...","ok":false,"err":"empty_output"}
```

| | |
|---|---|
| 1줄 = 1콜 | `session.run_session` 이 spawn **시도 후** 기록(성공·실패·timeout 공통) |
| `ok` | 🔴 **rc 가 아니라 실제 결과.** rc 0 + 빈 출력 = 인증 만료 → `ok:false, err:"empty_output"` |
| 게이트 | 줄 수 = 오늘 호출 수. `resources.yaml` 의 `daily_calls_{warn,block}` 과 비교 |
| 기준일 | **UTC** — KST 09:00 에 창이 리셋된다 |
| 미기록 | 게이트에 막힌 호출은 spawn 자체가 없으므로 카운트 안 함 |

**어기면**: `ok=(rc==0)` 으로 두면 **인증 만료가 성공으로 기록**되어 auth-suspect 알림이
자기 목적 시나리오에서 영원히 미발동한다(실증).

---

## 9. `<skill>/dlq/*.jsonl` — 격리 기록

| | |
|---|---|
| 형식 | append JSONL · `{"ts", "record"|"parse_error"|"batch_error", "errors":[...]}` |
| 회전 | 5MB 넘으면 `.1` 로 밀고 새로 시작(**보관 1세대**). 항목 20KB 초과면 원문 절단 |
| 원칙 | **조용한 삭제 없음.** 격리된 것은 지우지 않는다 |

---

## 10. `net-sourcing/linkedin_seen.json` — 스킬 소유 (돈으로 산 상태)

```json
"urn:ACAAA-3860B": {"d": "2026-07-27", "f": "v2|Wyss Institute at Harvard|Founder, CEO"}
"urn:ACAAA-Im7EB": {"d": "2026-08-12", "f": "v2||"}
```

| | |
|---|---|
| 규모 | dict · **1,148 항목** · 키 접두 `urn:` **100%**(전수 확인) |
| 키 | `urn:` + **canonical LinkedIn URN.** 스크래퍼 모드마다 3번째 글자·꼬리가 달라지므로 반드시 정규화 |
| `d` | 마지막 관찰일 `YYYY-MM-DD` |
| `f` | 지문 `"<버전>\|<회사명>\|<직함>"`. 재료를 바꾸면 **버전을 올린다** |
| 🔴 파이프 | **세그먼트가 2~6개다** — 2개 1,106 · 3개 36 · 4개 4 · 5·6개 2. 회사명/직함에 `\|` 가 든다. **`split("\|", 2)` 로 잘라야 한다.** `split("\|")` 로 짜면 36+건이 깨진다 |
| 🔴 버전 | `v1` 잔존 **0건**. 다만 **무버전 12건(1.0%)** — `_fp_comparable` 이 비교 불가로 처리해야 한다 |
| 빈 지문 | `v2\|\|` **62건(5.4%)** = 회사·직함 둘 다 미상. **"미상"이지 "변화"가 아니다** |

전수 실측 2026-08-16. 백업 2본이 **키 축약과 지문 v2 가 별개 마이그레이션이었음**을 증언한다 —
재빌드에서 둘을 한 번에 바꾸면 그 이력이 사라진다.

**어기면 (전부 실제 이력)**:
- 키를 raw URN 으로 → 1,148명 전원이 "처음 보는 사람" → **재수집 $5.20**
- 버전을 안 올림 → 344명 전원 v1 → **344명 재착지 + `재검토` 일괄 점등**
- `v2||` 를 변화로 읽음 → 미상 인원 전체에 재검토가 켜져 사람이 전부 다시 봄

---

## 남은 것 — [P3] 에서 채운다

스킬 소유 나머지 ~19본: `linkedin_axis_state` · `threads_layer_state` · `threads_anchors` ·
`judge_strikes` · `judge_queue_*` · `judge_verdicts_*` · `cut_*`(20본) · `last_run_health/*` ·
`research/labels.json`(정답지) · `fixtures/*` · mail-harness `bplan/`·`triage/`·`harness/` ·
pulse `state/seen.jsonl`·`mentions.jsonl`·`health.json`
