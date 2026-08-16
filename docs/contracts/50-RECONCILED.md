# [1] 상태 계약 — 3출처 대조 결과 (2026-08-16)

```
출처 ①  초안 A          코드가 "그렇게 하겠다"고 선언한 것        (contracts/10-state.md · 내가 작성)
출처 ②  파일 실측        8,606 파일 전수에서 유도한 것            (state/state-schemas.md)
출처 ③  외부 실측        노션 10,102행 · 액터 8,038본 · 시트·Gmail·Slack
```

**대조 결과: 불일치 0건 · 보강 11건 · 정정 4건 · 미검증 3건.**
코드가 선언한 규약이 **실데이터에서 반증된 곳은 하나도 없다.** 이게 재빌드가 [2][3] 을 유지하는 근거다.

> 📌 **[2026-08-16 저녁 갱신] 이 문서는 40번(합성 태우기) 이전 상태로 쓰였다.**
> 아래 §D 의 미검증 3곳은 **그 뒤 전부 닫혔다.** 현재 상태는 §F 를 보라.
> 그리고 §D 이후에 계약 자체가 두 번 정정됐다(테스트 누출로 인한 "죽은 수집기" 오기재,
> 빈 파일 16본 → 7본). 그 근거는 `11-state-schemas.md` · `30-external-constraints.md` 안에
> 정정 블록으로 남겼다.

---

## A. 확정 — 3출처가 일치 (그대로 계약으로 굳힌다)

| 계약 | 코드 선언 | 실측 검증 |
|---|---|---|
| **`events.jsonl` 이 유일한 진실** | state·cursor 는 폴드 결과 | 41/41 `state.json.state` == 마지막 `state.to` · `cursor.done` == `item_done` 수 == `len(done_keys)` · **위반 0** |
| **상태머신** | 허용 전이만, 종단은 이동 없음 | 41/41 첫 이벤트 `created` · 전이 사슬 무단절 · 불법 전이 0 · 종단 뒤 이벤트 0 · ts 단조증가 |
| **append-only + fsync + torn tail 복구** | 마지막 줄만 깨질 수 있다 | **찢긴 tail 0건 · 개행 누락 0건** |
| **워터마크 전진 규칙** | `max()` 를 락 안에서 | 5/5 `first_attempt ≤ last_success ≤ last_attempt` |
| **승인 저장/파생 경계** | 파생(`status`·`channel`·`sent`…)은 저장 금지 | 11/11 **파생 필드 유출 0** |
| **락은 flock, unlink 금지** | 내용은 진단용 | 29본 잔존이 정상 |
| **알림 마커 = (identity, UTC날짜)** | 파일 존재가 신호 | 18본 전부 58 B 동일 |
| **알림 events 파일명 = 이벤트 ts** | 벽시계 아님 | 12/12 일치 |
| **`_alerts/policy` 는 `last_seen` 하나만** | 파생 저장 금지 | 4/4 정확히 1필드 |
| **`applied_ids` 멱등성** | 재실행이 기존 적용분 스킵 | judge 1,784줄 전부 유일 · 실패잡↔r2 **교집합 0** |

---

## B. 보강 — 코드에 없던 사실이 실측에서 나옴 (계약에 추가)

| # | 보강 내용 | 왜 계약인가 |
|---|---|---|
| B1 | 워터마크 값은 **날짜 문자열**이지 ISO 타임스탬프가 아니다 | 독자가 전부 `str(x)[:10]` 로 방어 중. 타임스탬프로 바꾸면 비교가 깨진다 |
| B2 | **ts 포맷이 두 벌 공존** — `events.jsonl` 은 `…Z` 100%, `_alerts`·`_usage` 는 `+00:00` | 파서는 관대하게, 라이터는 엄격하게 |
| B3 | `cursor.by_stage` 는 **9/41 에만 존재** | 필수로 두면 구파일 22본을 못 읽는다. **없음 = 단일 스테이지** |
| B4 | `_alerts/sent` 마커는 **policy 보다 오래 산다** (3/7 identity 가 마커만 보유) | "policy 없으면 마커도 지운다"로 짜면 전송 이력이 날아간다 |
| B5 | `last_run_health` 의 **naive ts 는 KST 로 해석**된다(독자가 −9h) | UTC 로 바꾸면 헬스가 9시간 어긋난다 |
| B6 | `raw/*` 수집 레코드는 **어댑터 불문 13필드 고정**, `adapter` 가 채널 판별키 | 189,593행 100%. 수집 계층의 실질 계약 |
| B7 | pulse `seen.jsonl` 행 수 == `raw/**` 캐시 파일 수 (**4,205 == 4,205**) | 가장 선명한 1:1 불변식 |
| B8 | `labels.json` 은 `all` 이 정본, `builders` 는 완전 파생 | 정답지 — `reason` 이 사람 서술이라 **재생성 불가** |
| B9 | `judge_verdicts` 는 **이력이지 최신 상태가 아니다**(page_id 중복 20건) | 최신 판정은 잡ID 순으로 접어야 얻는다 |
| B10 | **`state=failed` 여도 부작용은 남아 있다** — 실패잡 4개가 artifacts 보유 | "실패 = 아무것도 안 함"으로 가정하면 안 된다 |
| B11 | 시트 접수키 = `r[1]\|r[0]` = `이메일 주소\|타임스탬프`, 접두 매칭 14/14 | [0] A 실측. 위치 인덱스는 우연히 맞을 뿐 |

---

## C. 정정 — 내 초안 A 가 틀렸거나 부족했던 곳

| # | 초안 A | 실측 | 정정 |
|---|---|---|---|
| C1 | events `kind` = "8종 + 잡별 커스텀" | **실측 10종.** 코드 선언 3종(`item_skipped`·`queued_wait`·`run_error`)은 **0건**, 스킬 정의 5종이 실재 | `kind` 는 **열린 집합**. 폴드가 아는 건 5종(`created`·`state`·`item_done`·`item_skipped`·`stage_done`)뿐이고 **모르는 kind 는 무시하되 보존** |
| C2 | `linkedin_seen` 지문 = `v2\|회사\|직함` | **파이프 2~6개**(3개 36 · 4개 4 · 5·6개 2) · 무버전 12 · 빈 62 | `split("\|", 2)` 필수. `v1` 은 0건이고 **무버전 12건이 진짜 구스키마** |
| C3 | 승인 레코드 "저장 17필드" | 11/11 **정확히 12키 고정**. 실행 5필드(`draft_id`·`executed_at`…)는 **0/11** | 저장 12 / 실행 5는 **미검증**. `payload` 는 **열린 스키마**(`contact_verified` 9/11) |
| C4 | `_usage` `ok` = rc 아닌 실제 결과 | **120/120 전부 `ok=true`, `err` 0건** | 계약은 맞으나 **실패 경로가 한 번도 기록된 적 없다** → 미검증(§D) |

---

## D. 미검증 — 실데이터가 없어 계약을 확정할 수 없는 3곳

**재빌드는 이 셋을 "옮기는 것"이 아니라 "처음 만드는 것"으로 다뤄야 한다.**

| | 무엇이 없나 | 결과 |
|---|---|---|
| **승인 결재·실행** | `decision` 11/11 null · `decisions/` 디렉토리 없음 · 실행 5필드 0/11 | 결재 후 레코드 모양에 **실측 근거가 0**. 합성으로 따로 검증해야 한다 |
| **claude 실패 기록** | `_usage` 120/120 `ok=true` | 인증 만료·timeout 시 `err` 가 실제로 어떻게 적히는지 본 적이 없다 |
| **축 소진(`exhausted`)** | `linkedin_axis_state` 6/6 전부 `false` | 소진 후 rewind 경로 미검증 |

> ⚠️ `_usage` 와 `gate_warn` 이 어긋난다는 관찰은 **오해였다.** 게이트는 `ok` 를 안 보고 **줄 수만** 센다.
> 30줄을 넘으면 전부 성공이어도 경고가 뜬다 — 정상 동작이다.

---

## E. 두 에이전트를 합쳐야 보이는 것 (합성 결과)

**`cutover.md` 롤백 B-3 의 정체가 밝혀졌다.**

- mail-harness 감사: *"롤백 절차가 `bplan_drop_sent.jsonl` 에서 `checkbox_done: false` 를 찾으라는데, `_record_sent` 가 쓰는 필드는 `{email, page_id, date}` 뿐이다"*
- 상태 실측: *"**구** `bplan/drop_ledger.json` 의 값이 `{company, draft_created, checkbox_done}` — 35/35 둘 다 true"*

→ **문서가 옛 장부의 스키마를 설명하면서 새 파일을 가리키고 있었다.** 재빌드 계약:
새 장부(`bplan_drop_sent.jsonl`)에 `checkbox_done` 을 **되살려야** 롤백 절차가 성립한다
(초안 생성 직후 false, `mark_done` 성공 후 true 한 줄 추가).

**그리고 구 장부 2본은 은퇴가 맞다** — 쓰는 코드는 `_archive` 안에만 있고 `selfcheck` 는 변수명을
`_dead` 로 쓴다(mtime 07-30·08-02, 08-13 재빌드 이전). 상태 실측의 "현역" 판정은 **내용 유무를
생사로 오독한 것**이다. 다만 **지우지는 않는다** — 롤백이 "손으로 돌아가는 것"이라 복구 근거일 수 있다.

---

## F. 계약 확정 상태

```
✅ 확정   kv-core 소유 11종 (워터마크·events·state/cursor·request·artifacts·승인·알림 3종·락·쿼터·DLQ·헬스)
✅ 확정   스킬 소유 12종 (linkedin_seen·축상태·threads 계열·judge 큐/판정/컷·labels·mail-harness 5·pulse 5)
✅ 확정   [0] 7종 외부 제약 **56항목** (48 + base 대조원 · 절단 표식 · 재실측 정정)
✅ 확정   0% 컬럼 22개 성격 분류 → `60-zero-fill-columns.md`
```

## [2026-08-16 저녁] 미검증 3곳은 전부 닫혔다

| | 어떻게 닫혔나 | 근거 |
|---|---|---|
| 승인 결재·실행 | 합성 5시나리오(정상/거부/동시/**진짜 프로세스 경합**/초안 중 변경) | `40-unverified-paths.md` §① · `probes/probe1_approval.py` |
| claude 실패 기록 | 네 실패 모양(timeout·empty_output·rc≠0·미설치)을 태워 장부 모양 확보 | `40-unverified-paths.md` §② · `probes/probe2_claude_fail.py` |
| 축 소진 | rewind/exhausted/refresh 3분기 계약 확보 + 축별 소진 후 주기 실측 | `40-unverified-paths.md` §③ |

**그리고 40번이 "남은 미검증"으로 적은 것 중 둘은 이미 상설 검사가 있었다**(문서가 낡았던 것):

```
execute_error   _synthetic_approvals_tick:112 「⑤ 실패 보상: decision 유지 + execute_error 기록」
                _chaos_lab:234 도 같은 보상을 잰다
_usage err      _synthetic_alerts:63-76 이 ok:false + err(timeout/rc) 를 직접 써서 판정을 잰다
I8 결정 마커     실행하면 실제로 만들어진다 — `_synthetic_approval_decide` 8개,
                `_concurrency_lab` 은 `cc-3.20260816032318.decision → approved|parent` 형태로
                **경합 8회에서 마커 1개 수렴**을 잰다(파일 규약이 그대로 검증된다)
```

남은 진짜 미검증은 **`exhausted=true` 가 실제로 찍히는 순간**(빈 페이지 판정) 하나이고,
위험이 낮아 계약에 표시하고 넘긴다.

## 계약 ↔ 테스트 커버리지 (2026-08-16 신설)

계약은 문서라 코드가 어긋나도 조용하다. 그래서 불변식 24개를 테스트에 연결하고 게이트를 세웠다
(`kv-core/tests/_synthetic_contract_coverage.py`).

```
검사 17 / 24 · 미검증 7 (상한으로 잠금 — 늘면 CI 실패)
미검증 7: I4 kind 열린집합 · I14 linkedin_seen 키형식 · I15 지문 v2| ·
          I16 applied_ids 멱등 · I19 pulse seen==캐시수 · I20 judge_verdicts 이력성 ·
          I23 raw 13필드 고정
```

**이 7개가 [1] 층 계약의 현재 미완이다.** 문서로는 확정됐고, **기계가 지키는지는 아직 안 센다.**
