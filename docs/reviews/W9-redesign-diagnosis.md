# W9 재설계 진단 — 47건은 무엇의 증상인가

> 2026-08-10 작성. 원문은 [W9-findings-raw.md](W9-findings-raw.md).
> 이 문서는 **개별 결함 목록이 아니라 인과 구조**다. 무엇을 먼저 고쳐야 나머지가 따라 사라지는지를 정한다.

## 0. 왜 이 문서가 필요했나

2026-08-07 하루에 W8 적대 리뷰 **56건**을 전량 처분했다. 그 수정을 대상으로 돌린 W9 적대 리뷰가
**47건(high 14)** 을 새로 찾았고, **다수가 그날의 수정이 만든 것**이었다.

| 그날 넣은 것 | 그게 만든 결함 |
|---|---|
| 킬 스위치 `KV_NO_SEND` | 알림을 억제가 아니라 **파괴**(`True` 반환이 dedup 마커를 태움 → 그날 알림 영구 유실) |
| `decide` 에 락 추가 | 유령 락 하나로 승인 전체가 영구 no-op 되는 **새 단일 장애점** |
| resolved 강등 | cap 잘라내기가 필터보다 **앞** — 미해결 2건이 0건으로 표시(실증) |
| `first_attempt` setdefault | 기존 never-succeeded task 의 **시계를 리셋**, 게다가 새 테스트가 그 리셋을 정답으로 못박음 |
| never-succeeded 클래스 신설 | 백오프가 없어 사람이 액션할 때까지 **매일 영원히** 같은 문장 |

**"고칠수록 새로 생기는 국면"** 이다. 개별 수정을 47번 더 하면 다음 리뷰가 40건을 찾을 것이다.
그래서 사용자가 "멈추고 재설계"(선택지 C)를 골랐다.

---

## 1. 실측 복잡도 — 재설계의 근거

```
승인 레코드 필드 22개   생성 16 (key task kind origin partner channel target created
                              status sent delivered payload decision decided_at decided_by modified)
                      + tick 추가 6 (slack_ts slack_channel executing_at draft_id executed_at execute_error)

발송 지점 14곳         alerts:70(유일한 실발송) · approval:108 · tick:85,89,101,210,245 ·
                      gate:160,171,270 · workers:77 · scheduler:224 · orrery:394,407
                      ← 각자 다른 실패 처리·dedup·킬스위치 해석

판정 로직 8곳          alerts._auth_suspect · alerts._task_failures · cadence.freshness_limit ·
                      orrery._freshness · orrery._launchd_direct_health · orrery._recent_failures ·
                      approval._is_closed · workers._board_files_today
```

같은 질문("judge 는 건강한가")에 **orrery 는 ⚠️, alerts 는 🔴** 로 답했다.
`cadence.py` 주석이 스스로 "방지하겠다"고 쓴 *"다이제스트는 ✅인데 알림은 🔴"* 그 자체가 일어났다.

**복잡도가 검증 능력을 넘었다** — 이게 한 문장 진단이다.

---

## 2. 근원 5가지 (인과 구조)

47건은 독립 사건이 아니다. 다섯 개의 뿌리에서 갈라져 나온다.

```
R4 검증 사각 ──────────────────────────────┐
   (결함 계열을 원리적으로 못 봄)              │ 이것 때문에 R1~R3 의 수정이
                                            │ 초록불 아래에서 새 결함을 만든다
R1 파생 상태를 저장한다  ──┐                  │
R2 같은 질문에 답이 여러 개 ─┼── 모순·유실 ────┘
R3 발송 정책이 흩어져 있다  ─┘
R5 진공과 결손을 구분하지 않는다 ── 거짓 성공 / 거짓 경보
```

### R1 — 파생 상태를 저장한다 (derived state stored)

계산할 수 있는 것을 파일에 적어두면, 그 순간 **진실이 둘**이 된다. 둘은 반드시 어긋난다.

- 승인 레코드의 `status`·`sent`·`delivered`·`channel`·`target` 은 전부 다른 필드에서 계산 가능한데 저장돼 있다.
  `sent` 는 **True 로 만드는 코드가 리포에 없는 죽은 필드**인데 `_is_closed` 가 그것을 본다.
- 워터마크의 `first_attempt` 가 없으면 `last_attempt` 로 폴백한다 — 그런데 그 값은 매일 갱신되므로
  "배선 후 경과일"이 **영원히 0**이 되어 알림이 영구 침묵한다.

→ **A-M6 · A-M7 · A-M8 · C-H5 · B-L2**

### R2 — 같은 질문에 답하는 곳이 여러 개 (판정 8곳)

- *"이 task 는 건강한가"* → `orrery._freshness` 와 `alerts._task_failures` 가 **각자** 계산한다.
- *"이 실패는 해결됐나"* → `orrery._recent_failures` 만 계산하는데, `scheduler` 의 다이제스트 게이트는
  그 결과를 무시하고 **원본 개수**를 센다.
- *"launchd-direct 잡은 건강한가"* → `_launchd_direct_health` 는 `freshness_limit` 선언을 **안 본다**.

두 답이 다르면 사람은 더 낙관적인 쪽을 믿는다. 그래서 **judge 가 4일째 전멸인데 다이제스트 헤더는 🔴0** 이었다.

→ **C-H2 · C-H3 · C-H4 · C-M1 · C-M2 · A-M6**

### R3 — 발송 정책이 호출부마다 흩어져 있다 (발송 14곳)

dedup·백오프·보상 재푸시·킬스위치 해석·에스컬레이션이 **각 호출부의 재량**이다. 그래서:

- 보상 재푸시는 `gate_block` **한 kind 에만** 있다 — 7개 push 호출부 중 반환값을 쓰는 곳이 하나뿐이다.
- never-succeeded 에는 **백오프가 없어** 사람이 액션할 때까지 매일 영원히 같은 문장을 보낸다.
- 킬 스위치를 `notify.slack` 은 `True`, `slackbot.post` 는 `None`(실패) 로 해석했다 — **규율이 비대칭**.

→ **B-H1 · B-H2 · B-H3 · B-M1 · B-M2 · B-M5 · B-M6 · A-M10**

### R4 — 검증이 결함 계열을 원리적으로 못 본다 (메타 근원)

**47건 전부가 합성 12본 초록불 아래에서 살아 있었다.** 다섯 개의 사각이 있다.

| 사각 | 왜 못 보나 | 놓친 findings |
|---|---|---|
| ① **동시성** | 합성 테스트가 단일 프로세스 **순차** 실행이다 | A-H1 A-H2 A-H3 A-H4 A-L13 |
| ② **실환경 상태** | 테스트가 데이터를 **직접 만들어서** 결측 필드·구 스키마가 존재하지 않는다 | C-H4 C-H5 B-H4 B-L2 B-M5 |
| ③ **규모 경계** | N 이 작아 cap·상한에 **도달하지 않는다** | C-H1 |
| ④ **스텁이 부작용을 가림** | `alerts.push` 를 스텁으로 두고 복구하지 않아 **실제 발생한 알림이 안 보인다** | A-M9 A-L15 |
| ⑤ **시간 경과** | `now` 가 호출 시점 고정이라 **며칠 뒤 상태**를 만들 수 없다 | B-M1 C-H2 C-L2 |

이 다섯이 **"56건 고쳤더니 47건 생김"의 메커니즘**이다. R1~R3 를 아무리 잘 고쳐도 R4 를 놔두면
그 수정이 만든 새 결함을 또 못 본다. **그래서 R4 를 먼저 닫는다.**

### R5 — 진공(정당한 0)과 결손(고장난 0)을 구분하지 않는다

우리가 세운 규칙 05·07("진공 통과 금지 · 세어서 판정")이 정작 세 곳에서 뚫려 있다.

- `meeting-mail` 은 `candidates.json` 이 빈 배열이어도 `last_success` 를 전진시킨다
  (`if not todo: return` 조기 반환이 진공 가드에 도달하지 못한다) — **"Catch 가 진짜 0" 과
  "Tier 필터·property 명·토큰이 깨져서 0" 이 구분 불가**한데 orrery 는 `✅ 0일 전 성공` 이라 답한다.
- `appstore-snapshot` 은 반대 방향이다 — **7006(카지노) 유료 차트는 한국 스토어에 원래 없다.**
  빈 배열이 애플의 정답인데 유효성 검사가 결손으로 세어 **매일 거짓 경보**를 쏜다(8/8~ 실측).
- `_auth_suspect` 는 **"claude 를 한 번도 안 불렀다"와 "인증이 만료됐다"가 관측적으로 동일**하다
  (`_usage/claude/` 디렉토리 자체가 없다). 살아있음을 능동 확인하는 카나리아가 없다.

→ **C-M4 · B-H4 · (신규 실측: appstore 카지노 오탐)**

---

## 3. 47건 분류표

**상태 범례**: ✅Phase0 처분 · 🔧이번 재설계 대상 · 📋감수(근거 명시) · ⛔범위 밖("안 채운 것")

### 리뷰 A — approval.py / approvals_tick.py (17건)

| ID | 요지 | 근원 | 상태 |
|---|---|---|---|
| A-H1 | `save()` 무락 전체덮어쓰기 → 거부건에 초안 생성 | R1 R4① | ✅ |
| A-H2 | `_announce` 가 슬랙 POST 중 내려진 결정을 지움 | R1 R4① | ✅ |
| A-H3 | `approvals-write` 유령 락 → decide 영구 no-op | R4① | ✅ |
| A-H4 | `gmail-inbox` 유령 락 → `_execute` 영구 침묵 | R4① | ✅ |
| A-M5 | CLI approve/reject 가 `r=None` 에서 TypeError 크래시 | R1 | 🔧 D |
| A-M6 | orrery `❌거부` 항상 0 · 실행완료 즉시 소멸 | R1 R2 | 🔧 D |
| A-M7 | 승인된 judgment 는 종결 조건이 없어 무한 누적 | R1 | 🔧 D |
| A-M8 | 기본 `dedup_key` 가 서로 다른 요청을 조용히 병합 | R1 | 🔧 D |
| A-M9 | `executing_at` 오탐 + 성공 후 잔존 | R4④ | ✅ |
| A-M10 | 킬 스위치에서 `slackbot-down` 오탐 | R3 | ✅ |
| A-M11 | `request()` 가 파손 레코드에 raise | R1 | 🔧 D |
| A-M12 | 승인 카드가 본문 200자만 — 전문 확인 경로 없음 | — | 🔧 D |
| A-L13 | `heavy_slot` 빈 락파일 stale 창 | R4① | 🔧 E |
| A-L14 | `SLACK_APPROVAL_USER` 미설정 → 화이트리스트 비활성 | — | ⛔ 설정 |
| A-L15 | 테스트가 `alerts.push/scan` 을 복구하지 않음 | R4④ | 🔧 A |
| A-L16 | `_reconcile` 이 실제 기록 없이 카운트 증가 | R1 | 🔧 D |
| A-L17 | launchd 로그가 승인 큐 디렉토리 안에 있음 | — | 🔧 E |

### 리뷰 B — alerts · notify · scheduler (13건)

| ID | 요지 | 근원 | 상태 |
|---|---|---|---|
| B-H1 | `KV_NO_SEND` 가 알림을 억제가 아니라 파괴(마커 소모) | R3 | ✅ |
| B-H2 | 다이제스트도 같은 스위치 + 폴백 출력까지 무력화 | R3 | ✅ |
| B-H3 | 보상 재푸시가 `gate_block` 전용 — 나머지 6곳은 영구 유실 | R3 | 🔧 C |
| B-H4 | `_auth_suspect` 가 구조적으로 발동 불가 | R5 | 🔧 C |
| B-M1 | never-succeeded 에 백오프·상한·에스컬레이션 전무 | R3 R4⑤ | 🔧 C |
| B-M2 | `scan()` 의 클래스 격리가 3번째 클래스에 미적용 | R3 | 🔧 C |
| B-M3 | 경과일은 로컬(KST), 마커·이벤트 필터는 UTC — 혼용 | — | 🔧 E |
| B-M4 | `alerts.yaml` 이 never-succeeded 유예를 선언하지 않음 | R3 | 🔧 C |
| B-M5 | 레거시 `pushed: true` 를 미발송으로 오판 → 중복 재푸시 | R4② | 🔧 C |
| B-M6 | "전부 실패한 날" 슬랙 트래픽이 0이 될 수 있음 | R2 R3 | 🔧 C |
| B-L1 | `_safe_id` 100자 절단 + 문자 치환 충돌 | — | 📋 |
| B-L2 | `first_attempt` 백필이 사람 손이었다 — 구조가 아님 | R1 R4② | 🔧 B |
| B-L3 | 메타 검증이 문자열 존재만 확인 | R4 | 🔧 A |

### 리뷰 C — orrery · workers · meeting-mail · CI (17건)

| ID | 요지 | 근원 | 상태 |
|---|---|---|---|
| C-H1 | cap 이 resolved 필터보다 먼저 — 미해결이 잘림 | R2 R4③ | ✅ |
| C-H2 | resolved 판정이 **날짜 절단** 비교 | R2 | 🔧 B |
| C-H3 | resolved 판정이 **시간 순서**를 안 봄 | R2 | 🔧 B |
| C-H4 | never-succeeded 에서 관측(⚠️)과 알림(🔴)이 갈림 | R2 | ✅ |
| C-H5 | `setdefault` 가 기존 task 의 시계를 리셋 | R1 | 🔧 B |
| C-H6 | **부재 감지기가 부재의 원인 안에 산다** | R2 | 🔧 E |
| C-M1 | 다이제스트 게이트가 해결분까지 셈 | R2 | 🔧 B |
| C-M2 | launchd-direct 는 `freshness_days` 사문 + 알림 커버 0 | R2 | 🔧 B |
| C-M3 | attempt 간에는 claude 재작업 (커서가 job_id 별) | — | 🔧 E |
| C-M4 | meeting-mail 이 진공을 성공으로 기록 | R5 | 🔧 E |
| C-M5 | `drafts.json`→`jsonl` 전환이 실환경 미검증 | R4② | 📋 확인 |
| C-M6 | DLQ 무한 성장 + orrery 가 매 gather 마다 전량 로드 | — | 🔧 E |
| C-M7 | alerts KST/UTC 혼용 (**B-M3 과 같은 건**) | — | 🔧 E |
| C-L1 | `_chaos_lab` `sleep(1.6)` — 타이밍 의존 flaky | R4⑤ | 🔧 A |
| C-L2 | `ci.yml` `TZ: Asia/Seoul` 이 TZ 결함의 눈가리개 | R4② | 🔧 E |
| C-L3 | `workers` 가 KST date 로 세고 워터마크는 UTC period | — | 🔧 E |
| C-L4 | 파손된 completed 잡이 `completed_bases` 에서 누락 | — | 📋 안전방향 |

**집계**: ✅Phase0 처분 10 · 🔧재설계 대상 32 · 📋감수 4 · ⛔범위 밖 1
(C-M7 = B-M3 중복이므로 유니크 46건)

---

## 4. 범위에서 뺀 것 — "안 채운 것"

사용자 지시대로 **잘못 만든 것만** 고친다. 아래는 결손이지 결함이 아니다.

| 항목 | 왜 범위 밖인가 |
|---|---|
| judge 판정 룰 미기입 | 설계된 fail-loud. `judge-criteria.md` L1 의 A7·A8·A10 **3문항이 사용자 결정 대기** |
| 창업자 이메일 부재(구 C-L19) | 소싱 DB 스키마·제품 정의 문제. W10 발송 자동화의 전제 |
| `SLACK_APPROVAL_USER` 미설정 | `.env` 설정 |
| sourcing·mail-triage·bplan 미배선 | `workers.WIRED` 등재는 별도 결정 |
| 링크드인 L0 미구현 | `judge.py:125` 가 `""` 반환 — 룰 자체가 미정 |

단, **"안 채운 것을 시스템이 어떻게 보고하는가"는 범위 안**이다. judge 가 룰 대기로 매일 실패하는 것은
정상이지만, 그 사실을 **매일 같은 문장으로 영원히 알리는 것**(B-M1)은 우리가 잘못 만든 것이다.

---

## 5. 수정 순서 — 왜 이 순서인가

```
Batch A  검증 능력      R4 를 닫는다. 이걸 먼저 안 하면 B~E 의 수정이
         (사각 5종)      또 초록불 아래에서 새 결함을 만든다 — W9 의 재현.

Batch B  health.py      R2 를 닫는다. 판정 8곳 → 1곳.
         단일 판정       구 경로 삭제 전 **동등성 diff 0** 이 게이트.

Batch C  observe/       R3 를 닫는다. 발송 14곳 → 사실 기록 N + 결정 1곳.
         dispatch        정책(dedup·백오프·보상·킬스위치)이 한 번만 존재하게.

Batch D  승인 상태 축소   R1 을 닫는다. 22필드 → 9. 파생은 계산한다.

Batch E  잔여            R5 + 구조로 안 사라지는 개별 건.
```

**A 를 맨 앞에 둔 것이 이 순서의 핵심 판단이다.** 원 계획은 검증 확충(Phase 3)이 리팩터(Phase 2)
뒤였지만, 그러면 리팩터 자체를 "동시성·실환경·시간을 못 보는 테스트"로 검증하게 된다.
**그게 정확히 8/7 에 한 일이고 결과가 47건이었다.**

각 배치의 통과 기준은 하나다 — **신설 검증이 수정 전 코드에서 실패하고 수정 후 통과할 것.**
"테스트가 통과했다"가 아니라 "테스트가 그 결함을 실제로 재현한다"를 확인한다
(Phase 0 에서 `_p0_probes.py` 로 이미 검증한 방식: git stash → 9건 실패 → 수정 후 전량 통과).
