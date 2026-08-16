# kv-data 상태 계층 실측 인벤토리

측정일 2026-08-16 · 대상 `~/kv-data` · **읽기 전용** (쓰기 0건)
**전수: 8,606 파일 / 976.2 MB 를 100% 열었다.** 경로 패턴 기준 **220 유형**.
산출 스크립트: `classify.py`(유형 분류) · `induce_all.py`(전 파일 스키마 유도) · `events.py`/`events2.py`(잡 이벤트) · `appr.py`(승인)

---

## 0. 최상위 분포

| 디렉토리 | 파일 | 용량 | 성격 |
|---|---:|---:|---|
| `net-sourcing/` | 4,112 | 784 MB | 스킬 상태 + 원본캐시 + 잡 |
| `ai-consumer-pulse/` | 4,273 | 163 MB | 거의 전부 본문 캐시 |
| `mail-harness/` | 132 | 1.5 MB | 스킬 상태 + 잡 |
| `_locks/` | 29 | 116 KB | kv-core |
| `_approvals/` | 23 | 184 KB | kv-core |
| `_alerts/` | 22 | 88 KB | kv-core |
| `_usage/` | 5 | 20 KB | kv-core |
| `hello-mail/` | 4 | 64 KB | **은퇴 스킬 잔재** |
| `investment-memo/` | 4 | 88 KB | 참조 xlsx만 |
| `_reports/` `_scheduler/` | 2 | 208 KB | md 1 + 로그 1 |

**용량의 96%가 4개 유형**: `raw/appstore_<D>.json`(317MB) · `raw/_actor/*`(168MB) · `pulse/raw/**`(148MB) · `cut_<D>.json`(274MB). 나머지 216개 유형 합계 = 69 MB.

---

## 1. kv-core 가 스키마를 정의하는 것 (계약 원재료)

| # | 유형 | 파일수 | 크기 | 형식 | 항목수 | 이상치 |
|---|---|---:|---:|---|---:|---|
| C1 | `<skill>/cadence/<task>.json` | 5 | 99 B 고정 | JSON obj | 3필드 고정 | 0 |
| C1b | `<skill>/cadence/<task>.lock` | 5 | **0 B** | 빈 파일 | — | 0 |
| C2 | `<skill>/jobs/<id>/events.jsonl` | **41** | 48 KB | JSONL | 489 이벤트 | **찢긴 tail 0건** |
| C3 | `<skill>/jobs/<id>/state.json` | 41 | 60–63 B | JSON obj | 2필드 | 0 |
| C4 | `<skill>/jobs/<id>/cursor.json` | 41 | 50–4,183 B | JSON obj | 3–4필드 | 0 |
| C5 | `<skill>/jobs/<id>/request.json` | 41 | 47–53 B | JSON obj | 2필드 | 0 |
| C6 | `<skill>/jobs/<id>/artifacts/*` | 60 | 1.3 MB | 7가지 형태 | — | 형식 3건 불일치(§4) |
| C7 | `_approvals/<key>.json` | **11** | 184 KB | JSON obj | 12필드 고정 | 0 (단 §4-A) |
| C7b | `_approvals/<key>.lock` | 11 | **0 B** | 빈 파일 | — | 0 |
| C8 | `_alerts/policy/<id>.json` | 4 | 27 B 고정 | JSON obj | 1필드 | 0 |
| C9 | `_alerts/sent/<id>-<D>` | 18 | 58 B 고정 | JSON obj(무확장자) | 2필드 | 0 |
| C10 | `_alerts/events-<D>.jsonl` | 2 | 3.2 KB | JSONL | 12 | 0 |
| C11 | `_locks/*.lock` | 29 | 15–16 B | 텍스트 | `pid epoch` | 0 |
| C12 | `_usage/claude/<D>.jsonl` | 5 | 5.8 KB | JSONL | 120 | 0 |
| C13 | `<skill>/dlq/*.jsonl` | 3 | 41 KB | JSONL | 4 | 0 |
| C14 | `<skill>/last_run_health/<ch>.json` | 1 | 196 B | JSON obj | 7필드 | §4-D |

잡 41개 내역: net-sourcing meeting-mail 14 · judge 8 / mail-harness bplan-sync 13 · bplan-score 6.

---

## 2. 스킬이 스키마를 정의하는 것

### net-sourcing (상태 24 유형)

| 유형 | 파일 | 크기 | 형식 | 항목 | 키 정규화 |
|---|---:|---:|---|---:|---|
| `linkedin_seen.json` | 1 | 139 KB | obj(map) | **1,148** | `urn:<축약URN>` 100% |
| `linkedin_seen.json.bak-before-fpv2` | 1 | 23 KB | obj(map) | 259 | `urn:` 키 · 지문 무버전 |
| `linkedin_seen.json.bak-2026-07-27` | 1 | 39 KB | obj(map) | 344 | **원시 `ACoAA…` 키 247 + 기타 97** |
| `linkedin_axis_state.json` | 1 | 602 B | obj(map) | 6축 | 축 slug |
| `linkedin_watchlist.json` | 1 | 3.4 KB | **array of str** | 72 | — |
| `threads_anchors.json` | 1 | 936 B | array of str | 16 | — |
| `threads_watchlist.json` | 1 | 15 B | array of str | 1 | — |
| `threads_watch_cursor.json` | 1 | 14 B | obj | 1필드 | — |
| `threads_follow_cursor.json` | 1 | 14 B | obj | 1필드 | — |
| `threads_follow_cursor.json.pending` | 1 | 14 B | obj | 1필드 | **원자쓰기 잔재** |
| `threads_last_success.json` | 1 | 22 B | obj | 1필드 | — |
| `followup_state.json` | 1 | 1.1 KB | obj(map) | 5 | `li:<slug>` |
| `followup_events.jsonl` | 1 | 8.6 KB | JSONL | 19 | — |
| `judge_strikes.json` | 1 | 118 B | obj(map) | 2 | page_id |
| `judge_queue_<D>.json` | 8 | 18.4 MB | obj{rows[]} | 400/파일 | page_id |
| `judge_verdicts_*` (3세대) | **97** | 1.46 MB | array | **1,805** | page_id |
| `cut_<D>.json` | **20** | 274 MB | array | **157,022** | 없음 |
| `research/labels.json` | 1 | 83 KB | obj{builders,all} | **513** | handle |
| `research/targets.json` | 1 | 188 KB | array | 635 | handle |
| `research/threads_labeled.json` | 1 | 488 KB | array | 635 | handle |
| `last_run_health.json` (단수) | 1 | 196 B | obj | 7필드 | **은퇴본** |
| `last_run_health.json.bak-20260728` | 1 | 123 B | obj | 6필드 | 은퇴본 백업 |
| `fixtures/*.json` | 11 | 1.4 MB | array | — | 테스트 고정물 |
| `dedup_body_cache.json` `dedupe_linkedin_*` `backfill_tenure_*` `bias_sample_*` | 4 | 16.1 MB | obj/array | — | 1회성 분석 산출 |

**원본 캐시(리텐션 대상 아님)**: `raw/appstore_<D>.json` 26본 317MB · `raw/_actor/<actor>_<TS>.json` 136본 168MB · `raw/threads_<D>` 7 · `raw/linkedin_harvestapi_<D>` 6 · `raw/{listly,newchan,linkedin_listly,disquiet,velog}_<D>` 13
**스냅샷**: 3,618본 6.3MB — 하루 **160파일** 고정(§3)

### mail-harness (상태 15 유형)

| 유형 | 파일 | 형식 | 항목 |
|---|---:|---|---:|
| `triage/plan_<TS>.json` | 11 | array | 441 메일 |
| `triage/history/plan_<TS>.json` | 4 | array | 109 (3본이 상위와 **바이트 동일**) |
| `harness/run_<TS>.json` | 4 | obj | 파이프라인 실행록 |
| `harness/digest_latest.md` / `templates_checksum.txt` | 2 | 텍스트 | sha256 1줄 |
| `bplan/sync_ledger.json` | 1 | obj(map, 키=**시트 행번호**) | 29 |
| `bplan/drop_ledger.json` | 1 | obj(map, 키=page_id) | 35 |
| `bplan_sync_floor.json` | 1 | obj | `last_ts` 1필드 |
| `bplan_queue_<D>.json` | 2 | obj{rows:[]} | **0 (빈 구스키마)** |
| `bplan_queue_<D>_<job>.json` | 4 | obj{rows[]} | 신스키마 |
| `bplan_verdicts_<D>_<job>_<N>.json` | 3 | array | 19 |
| `dlq/bplan_dlq.jsonl` | 1 | JSONL | 1 |

### ai-consumer-pulse (상태 5 + 캐시 2)

| 유형 | 파일 | 형식 | 항목 |
|---|---:|---|---:|
| `state/seen.jsonl` | 1 | JSONL | **4,205** (원본 캐시 파일수와 정확히 1:1) |
| `state/health.json` | 1 | obj(map, 키=source_id) | 137 소스 |
| `trends/mentions.jsonl` | 1 | JSONL | 1,499 |
| `themes/registry.jsonl` | 1 | JSONL | 40 |
| `unc_arch.json` | 1 | obj | **API 에러 응답이 저장됨(§4-G)** |
| `raw/<WEEK>/<SOURCE>/<page>.json` | 4,205 | obj | 본문 캐시 |
| `analysis/<WEEK>/{items.jsonl,queue.json,themes.json,audit.md}` | 15 | 혼합 | 917 |
| 설계·소스 md/yaml | 24 | 텍스트 | — |

### hello-mail (아카이브 스킬)
`plan_<TS>.json` 4본, 64 KB, 전부 2026-07-13~17. **mail-harness/triage/history/ 와 md5 완전 일치 3본** + `plan_20260715-173243` 과 `173428` 은 서로도 동일 내용(중복 저장). 새 데이터 없음 — 순수 사본.

---

## 3. 증가 속도 (날짜별 파일)

| 시리즈 | 하루 파일 | 하루 용량 | 최근값 |
|---|---:|---:|---|
| `snapshots/*` | **160** | 0.4 MB | 08-10~08-15 매일 정확히 160 |
| `raw/appstore_<D>.json` | 1 | **13.7–17.8 MB** | 08-15 13.7 MB |
| `cut_<D>.json` | 1 | **12.4–16.6 MB** | 08-15 12.4 MB |
| `pulse/raw/<WEEK>/` | 주 645–1,315 | 주 5.6–58.2 MB | W32 1,191본 58 MB |
| `judge_verdicts_*` | 회당 17–20 | 0.3 MB | — |
| `_usage/claude/<D>.jsonl` | 1 | 0.4–2 KB | 7–41 호출/일 |

**총 증가 ≈ 30 MB/일.** 리텐션 정책 파일은 어디에도 없다.

---

## 4. 이상치·구스키마·부채 (전건 수치)

| # | 항목 | 수치 |
|---|---|---|
| A | `_approvals` 11건 전부 `decision`/`decided_at`/`decided_by` = **null** — 결재된 승인 0건. `draft_id`·`executed_at`·`executing_at` 도 0건 → 실행 경로가 한 번도 안 돌았다 | 11/11 |
| B | `payload.contact_verified` 가 **9/11** 에만 존재 (2건 구스키마). `payload.founder_email` 은 11/11 **빈 문자열** | 2 / 11 |
| C | `judge_verdicts_*` 에 오타 필드 **`verdter`** 존재 — 52/1,805행(2.9%). 전부 `verdict` 와 **값이 동일한 중복** | 52행 / 52파일 |
| D | `last_run_health` **이중 경로**: 단수 `last_run_health.json`(ts 08-13, 은퇴) + 신형 `last_run_health/appstore.json`(ts 08-15). 단수본이 **2일 stale** | 2 |
| E | `threads_follow_cursor.json.pending` — 원자쓰기 스테이징 파일이 삭제 안 됨. 본문과 내용 동일(`{"cursor":60}`) | 1 |
| F | `applied_ids.jsonl` 9본이 **JSONL 이 아님** — 확장자만 jsonl, 내용은 맨 UUID 텍스트 줄. 파서가 전건 실패(1,803줄) | 9파일 / 1,803줄 |
| G | `ai-consumer-pulse/unc_arch.json` 은 상태가 아니라 **API 400 에러 응답**(`{"errors":[{"msg":"Invalid value"}]}`)이 그대로 저장됨 | 1 |
| H | `bplan_queue_<D>.json` 2본이 `{"rows":[]}` 12바이트 — 잡ID 없는 **구 명명세대** 잔재 | 2 |
| I | `hello-mail/` 은퇴 스킬 데이터 4본이 mail-harness 와 **바이트 동일 중복** | 4 |
| J | `triage/` 와 `triage/history/` 가 같은 plan 3본을 **양쪽에 중복 보관** | 3 |
| K | 스냅샷 **네임스페이스 2종 동시 기록**: `appstore_<cat>_<chart>_<D>` 75/일 + `appstore_board_<cat>_<chart>_<D>` 84/일. 은퇴본 `appstore_<N>_<D>`(8본, 07-07~07-10)·`appstore_rss_new_<D>`(1본) 잔존 | 159/일 |
| L | **실패했는데 부작용을 남긴 잡 4개** — `judge-2026-08-14`(applied 257) · `bplan-score-2026-08-{14,15,15-r2}`. 단 재실행분과 **중복 적용 0건** | 4잡 |
| M | 브리핑이 지목했으나 **존재하지 않는 파일**: `threads_layer_state.json` · `triage/processed.json` · `_approvals/decisions/` · `pulse/state/mentions.jsonl` · `bplan/{sync,drop}_ledger` 는 은퇴가 아니라 **현역** | 4 |
| N | `_alerts/sent` 3 identity(`meeting-mail-vacuum`·`never-succeeded-judge`·`partial-appstore-snapshot`)는 policy 파일이 **없음** — `_forget` 이 지웠고 마커만 남음(설계대로) | 3/7 |
| O | `net-sourcing/body_index.lock` 은 `_locks/*.lock` 과 **내용 형식이 다름** (`pid YYYY-MM-DD HH:MM:SS` vs `pid epoch`) | 1 |
| P | `linkedin_seen` 지문 `v1` 잔존은 **0건**. 대신 **무버전 12건**(1.0%) · 빈 지문 `v2\|\|` **62건(5.4%)** | 74/1,148 |
| Q | `cut_*` 룰 사망 구간: `대기업/기관 개발사` 가 08-06~08-12 **7일 연속 0건** (그 전 7–13건/일) | §5 |
| R | `_usage/claude` 120건 전부 `ok=true`, `err` 필드 0건 — 실패 기록이 한 건도 안 남았다 | 120/120 |
| S | 승인 11건 중 `contact_channel` 전부 `linkedin_dm` — 이메일 경로 0건 | 11/11 |

---

## 5. `cut_*.json` `_cut_reason` 접두 분포 (20본 · 157,022행)

접두는 딱 **3종**뿐이다.

| 날짜 | 행수 | 출시 180일 초과 | 대기업/기관 개발사 | 홍보성 |
|---|---:|---:|---:|---:|
| 07-11 | 857 | 856 | 1 | 0 |
| 07-12 | 411 | 411 | 0 | 0 |
| 07-13 | 15 | **0** | 0 | **15** |
| 07-14 | 9,540 | 9,528 | 12 | 0 |
| 07-18 | 9,127 | 9,119 | 8 | 0 |
| 07-21 | 9,539 | 9,530 | 9 | 0 |
| 07-26 | 9,497 | 9,487 | 10 | 0 |
| 07-28 | 9,506 | 9,498 | 8 | 0 |
| 07-31 | 9,495 | 9,486 | 9 | 0 |
| 08-03 | 9,446 | 9,439 | 7 | 0 |
| 08-04 | 9,484 | 9,471 | 13 | 0 |
| 08-05 | 9,442 | 9,431 | 11 | 0 |
| **08-06** | 9,424 | 9,424 | **0** | 0 |
| **08-07** | 9,431 | 9,431 | **0** | 0 |
| **08-10** | 9,394 | 9,394 | **0** | 0 |
| **08-11** | 9,415 | 9,415 | **0** | 0 |
| **08-12** | 9,409 | 9,409 | **0** | 0 |
| 08-13 | 8,069 | 8,068 | 1 | 0 |
| 08-14 | 8,606 | 8,605 | 1 | 0 |
| 08-15 | 6,915 | 6,915 | 0 | 0 |
| **합계** | **157,022** | 156,917 (99.93%) | 90 | 15 |

**해석 2개.**
1. `대기업/기관 개발사` 룰이 **08-06~08-12 5회 실행 연속 0건**, 08-13/14 각 1건으로 겨우 복귀 → 룰이 죽었다가 반쯤 살아난 구간이다. 07-14~08-05 평균 9.7건/일과 비교하면 명백한 회귀.
2. `홍보성` 은 **07-13 단 하루만** 15건 — 스레드 전용 실행일이고, 그날은 `출시 180일 초과` 가 0건이다. 즉 두 룰은 서로 다른 파이프라인에 붙어 있고 **`cut_*.json` 은 채널이 섞인 단일 파일**이다. 재빌드가 채널을 분리하려면 `_cut_reason` 이 유일한 단서다(레코드에 채널 필드가 없다).

---

## 6. `research/labels.json` — 사람이 만든 정답지 (절대 덮지 말 것)

- 구조: `{"builders": [handle …60], "all": [{handle,is_builder,category,reason,uncertain} …513]}`
- `builders` 는 `all` 에서 `is_builder==true` 인 60개와 **집합적으로 완전 일치** → 파생 인덱스, 원본은 `all`.
- `is_builder`: true 60 / false 453 (11.7%)
- `uncertain`: true **109** / false 404 (21.2%가 사람도 확신 못 함)
- `category`: **210종 자유 텍스트**, 통제 어휘 없음. 최빈 `불명` 65 · `크리에이터` 49 · `인디개발자` 33. `오프라인 가게`(8)/`오프라인가게`(5), `직장인 개발자`(4)/`직장인개발자`(2) 처럼 **띄어쓰기만 다른 중복 라벨**이 존재한다.
- handle 중복 0건.
- `reason` 은 원문 인용을 포함한 사람 서술 → 재생성 불가.

---

## 7. 못 읽은 파일

**0건.** 8,606/8,606 파싱 성공(에러로 분류된 6유형은 전부 내 스니퍼가 `.log`/UUID-텍스트를 JSON으로 시도한 것이고, 텍스트로는 정상 판독).
`.xlsx` 4본 · `.DS_Store` 1본은 바이너리로 판정만 하고 내용은 열지 않았다(스키마 대상 아님).
