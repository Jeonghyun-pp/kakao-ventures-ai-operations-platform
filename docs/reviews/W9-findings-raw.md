# W9 적대 리뷰 원문 아카이브 (2026-08-07 세션 트랜스크립트에서 복원, 2026-08-10)

출처: ~/.claude/projects/-Users-jeonghyunpark-Desktop-Jeonghyun-study/c6a974dd-....jsonl 라인 6102/6115/6128
원 output 파일(/private/tmp/.../tasks/*.output)은 이미 삭제됨 — 트랜스크립트가 유일본이었다.

<task-notification>
<task-id>a45a64e277d80b5b6</task-id>
<tool-use-id>EXAMPLE_ID_267d37b7eac6</tool-use-id>
<output-file>/private/tmp/claude-501/-Users-jeonghyunpark-Desktop-Jeonghyun-study/00000000-0000-0000-0000-d9a71fb46a6e/tasks/a45a64e277d80b5b6.output</output-file>
<status>completed</status>
<summary>Agent "W9리뷰A: 승인 경로 수정분" finished</summary>
<note>A task-notification fires each time this agent stops with no live background children of its own. The user can send it another message and resume it, so the same task-id may notify more than once.</note>
<result>프로브 완료. 실발송 0 (스크래치 KV_DATA + `KV_NO_SEND=1` + slackbot/gmail/notify/alerts 전부 몽키패치 스텁), 실 `~/kv-data` 는 읽기만 했다.

---

# 적대 리뷰 — approval.py / approvals_tick.py (W9-1.5 사후 검증)

**전제 확인**: 두 합성 테스트 모두 **RC=0 전량 통과**한다. 아래 16건은 전부 그 초록불 아래에서 살아있다. 빌더=파인더 문제가 실재한다.

---

## 🔴 HIGH

### H1. `approval.save()` 무락 전체덮어쓰기가 `decide` 의 락을 무력화 — 거부건에 초안이 생성된다
`approval.py:159-161` · `approvals_tick.py:187-188, 193-197`

- **문제**: 오늘 `decide` 를 `heavy_slot("approvals-write")` 로 감쌌지만, `save()` 는 락을 안 잡고 **레코드 전체를 덮어쓴다**. `_execute` 는 루프 진입 시점의 스냅샷 `r` 을 들고 Gmail API 호출(초 단위)을 건넌 뒤 그 스냅샷에 draft_id 를 붙여 저장한다. 그 사이의 결정은 사라진다.
- **근거(프로브 P5)**: `approved` 상태에서 `_execute` 진입 → create_draft 도중 CLI 가 `rejected` 기록 → **최종 `decision='approved'`, `draft_id='draft-1'`, 초안 1건 생성**. 거부 사실이 디스크에서 소멸.
- **시나리오**: 심사역이 카드를 보고 "아 이건 아니다" 하고 CLI 로 거부 → 마침 10분 틱이 실행 중 → 메일 초안이 만들어지고 거부 기록은 없어진다. `no-approval-no-execute` 가 명시적으로 깨진다.
- **수정**: `save()` 를 read-modify-write 로 바꾸고 `approvals-write` 락 안에서만 필드 병합(전체 덮어쓰기 금지), 또는 `_execute` 가 draft_id 를 쓰기 직전 재조회해 `decision=='approved'` 를 재확인.

### H2. 같은 뿌리 — `_announce` 가 슬랙 POST 도중 내려진 결정을 지운다
`approvals_tick.py:84-90`

- **문제**: `undecided()` 스냅샷 → `slackbot.post()`(타임아웃 15초 × 최대 30건) → `r["slack_ts"]=...; approval.save(r)`. 창이 H1 보다 훨씬 크다.
- **근거(프로브 P6)**: announce 중 CLI 가 `approved` 기록 → 최종 `decision=None`. 결정이 지워지고 그 건은 다시 미결로 돌아간다.
- **수정**: H1 과 동일(필드 병합 저장). slack_ts 만 쓰는 부분 갱신으로.

### H3. `approvals-write` 유령 락 = decide 영구 no-op, CLI 는 "이미 결정됨" 으로 거짓보고, 알림 0 — **실환경 전용**
`approval.py:176-189` · `job.py:288-340`

- **문제**: `heavy_slot._stale()` 은 pid 생존만 본다(나이 무시). 맥이 자거나 SIGKILL 로 틱이 죽어 락이 남고, 그 pid 가 재사용되면 `_stale()=False` → 모든 `decide` 가 `SlotBusy` → `return load(key)` = **미결 레코드**. 쓰기는 영원히 안 일어난다.
- **근거(프로브 P3)**: `_locks/approvals-write.lock` 에 `1`(launchd, 상시 생존) 기입 → `decide` 반환 `decision=None`, 디스크 `decision=None`, **CLI rc=2 + "이미 None(None) — 변경 없음"**, `_reconcile` 실행 시 알림 **0건**.
- **시나리오**: 슬랙 ✅ 를 매 틱 읽고 매 틱 decide 를 부르지만 디스크는 영원히 미결 → 카드가 계속 미결로 남고, 사람은 CLI 로 승인해도 "이미 결정됨" 을 본다. 아무도 모른다. (현재 `~/kv-data/_locks/` 는 비어 있어 잠복 상태 — 재부팅/슬립 1회면 발현 가능)
- **수정**: `_stale()` 에 락파일 mtime 기반 age 폴백 추가(예: 10분 초과 = stale) + `decide` 의 SlotBusy 폴백을 "재조회했는데 여전히 미결이면 예외/실패 반환" 으로 바꿔 조용한 no-op 을 없앤다.

### H4. `gmail-inbox` 유령 락 = `_execute` 영구 침묵 — **실환경 전용**
`approvals_tick.py:171-175`

- **문제**: 같은 stale 문제 + 여기는 `return 0` 이고 알림도 없다. 그런데 이 락은 **현재 아무것도 막지 않는다**: `gmail-inbox` 를 선언한 task 는 mail-triage·bplan 뿐이고 둘 다 `workers.WIRED` 에 없다(WIRED = appstore-snapshot·judge·meeting-mail). 즉 **이득 0, 잠금 실패 리스크만 순증**.
- **근거(프로브 P13)**: 락에 pid `1` 기입 → 3틱 연속 `_execute=0`, 초안 0, 알림 0, `approved_unexecuted=['x-1']` 그대로. 로그는 `⚠️ gmail-inbox 자원 점유 중` 한 줄. 틱 요약(`execute 0`)은 idle 과 구분 불가.
- **수정**: 배선 전까지 락 제거하거나, SlotBusy 가 N틱 연속이면 `alerts.push` 로 에스컬레이션.

---

## 🟠 MED

### M5. CLI `approve/reject` 가 TypeError 로 크래시 — `r and` 가드가 절반만 있다
`approval.py:248-253`

`if r and r.get("decision") != want:` 는 rc 2 만 막고, 바로 아래 `print(f"✅ … {r['decision']}")` 는 **가드가 없다**. `decide` 가 `None` 을 반환하는 경로(락 안에서 `load` 실패 = 레코드가 그 사이 삭제됨)에서 터진다.
**근거(프로브 P4)**: `💥 CLI 크래시: TypeError: 'NoneType' object is not subscriptable`.
**시나리오**: "스모크 후 정리" 로 `_approvals/` 를 지우는 습관 + 동시에 CLI 승인 → 스택트레이스. 자동화가 감싸면 rc 1 로 오독.
**수정**: `if r is None: print("🔴 결정 기록 실패"); return 1` 을 `decide` 직후에.

### M6. orrery 승인 통계의 `❌거부` 는 구조적으로 항상 0, 실행완료도 즉시 소멸 (관측 손실)
`approval.py:125-140` ↔ `orrery.py:297-303, 353`

`_is_closed` 가 거부·실행완료를 `pending()` 에서 빼는데, orrery 통계는 `pending()` 기반이다.
**근거(프로브 P7)**: 승인2·거부1·미결1·초안1건 생성한 하루 뒤 → 렌더 `승인 큐 (⏳미결 1 · ✅승인 1 · ❌거부 0)`. 거부 1건과 실행완료 1건이 사라졌다. `❌거부` 는 **어떤 입력에도 0** 인 죽은 카운터다.
**시나리오**: 하루에 초안 5건을 만들어도 다이제스트는 "미결 0" 뿐 — "오늘 무슨 일이 있었나" 를 보여주는 표면이 0. 사고(P5 같은 오실행)도 다이제스트에 안 나타난다.
**수정**: `_approvals()` 를 `_iter_queue()` 기반 + 오늘자 필터로 바꿔 approved/rejected/executed 를 각각 세고, pending 은 "미결" 표시에만 쓴다.

### M7. 승인된 judgment/summary 는 종결 조건이 없어 `pending` 에 무한 누적
`approval.py:125-135`

`_is_closed` 의 세 조건은 ① `sent`(주석이 "리포에 True 로 만드는 코드 없음" 이라 명시한 죽은 필드) ② `rejected` ③ `mail + draft_id`. **승인된 judgment 는 어디에도 안 걸린다.**
**근거(프로브 P8)**: `decision=approved, kind=judgment, sent=False, draft_id=None` → `_is_closed=False`.
**시나리오**: judgment 승인이 쌓이면 매 틱 `_iter_queue` 전량 스캔 + orrery 의 `✅승인 N` 이 단조증가 → A-F12 가 고치려던 "거짓 백로그" 가 kind 만 바꿔 재발.
**수정**: `_is_closed` 에 `decision is not None and kind != "mail"` 추가(판단은 결정 즉시 종결).

### M8. 기본 `dedup_key = f"{task}-{kind}"` — 서로 다른 요청이 조용히 병합되고, 호출부는 구분할 수단이 없다
`approval.py:80-83`

**근거(프로브 P1)**: `request("meeting-mail","mail", A社/a@x)` 후 `request("meeting-mail","mail", B社/b@x)` → 같은 key, **큐 파일 1개**, B 가 받은 레코드는 `subject='A社'`, `recipient_of='a@x'`. 반환값에 "신규/기존" 플래그가 없어 호출부가 병합 사실을 알 수 없다.
**시나리오**: meeting_mail_job 은 명시 key 를 쓰지만, 새 호출부(mail-triage 배선 시)가 기본값을 쓰면 하루 N건 중 1건만 살고 **나머지는 성공처럼 보이며 증발**한다. 게다가 mail 이라 수신자까지 첫 요청 것으로 고정된다.
**수정**: `dedup_key` 를 필수 인자로 승격(기본값 제거) — 못 하면 최소한 `request()` 가 `("record", created_new: bool)` 을 반환하거나 병합 시 loud 로그.

### M9. `executing_at` 오탐 — 정상 재시도마다 "초안 중복 가능" 알림, 성공 후에도 잔존
`approvals_tick.py:183-188, 194-197`

마커는 create_draft **전에** 저장되므로 "create_draft 가 raise 했다(초안 0건)" 와 "초안 만들고 죽었다" 를 구분하지 못한다. 그런데 실패 보상 경로는 정상 설계 경로다.
**근거(프로브 P9)**: Gmail 502 → 재시도 → `⚠️ 이전 실행 흔적 있음(중복 초안 가능)` + `alerts.push('approval-exec', '초안 중복 가능')`. 실제 초안 총계는 **1건, 중복 아님**. 성공 후에도 `executing_at='2026-08-07T04:10:50Z'` 잔존.
**시나리오**: Gmail 일시 장애 1회 = "Gmail 확인 요망" 오탐 1회. 잔존 마커 때문에 나중에 draft_id 가 지워지는 어떤 경로(실패 보상)에서도 계속 재발화 → 늑대소년.
**수정**: `except` 안에서 "create_draft 진입 전 실패면 `executing_at` 제거", 성공 시 `r.pop("executing_at")`. 마커는 create_draft 직전에만 유효한 것으로.

### M10. CLI-only / `KV_NO_SEND` 환경에서 `slackbot-down` + `announce-backlog` 가 매일 오탐
`approvals_tick.py:92-96, 77-83` · `slackbot.py:36-38` ↔ `notify.py:39-41`

`slackbot.post` 는 `KV_NO_SEND` 에서 `None`(=실패) 을 돌려주는데 `notify.slack` 은 `True`(=성공) 를 돌려준다 — **킬 스위치 규율이 비대칭**이다. 그래서 킬 스위치를 켠 환경에서 announce 는 전건 실패로 집계되고, 그 실패 경보는 webhook 으로 정상 발송된다.
**근거(프로브 P10)**: post→None 3건 → `announce=0 · 알림=['slackbot-down']`, **다음 틱도 동일**(하루 1회 dedup 이므로 매일 1회). 그리고 announce 실패건은 slack_ts 를 못 얻어 영구히 `undecided` 에 남으므로 30건 초과 시 `announce-backlog` 까지 매일 추가.
**현 실환경**: `SLACK_BOT_TOKEN`·`SLACK_APPROVAL_CHANNEL` 은 `~/kv-config/.env` 에 있어 지금은 안 터진다. 문서가 지원 모드로 명시한 **CLI-only** 와 CI/개발 실행에서 터진다.
**수정**: `slackbot.post` 가 `KV_NO_SEND`/미설정 을 "실패" 가 아닌 별도 센티널로 반환하고, `_announce` 는 그것을 fail 카운트에서 제외.

### M11. `request()` 는 파손 레코드에 raise — 파일 1개가 스테이지 전체를 죽인다
`approval.py:82-83`

`_iter_queue`/`load` 는 파손을 관용(skip + loud)하는데 `request()` 의 멱등 분기만 `json.loads` 를 맨몸으로 호출한다.
**근거(프로브 P11)**: `pending()=[]`(관용) vs `request()` → `💥 JSONDecodeError`.
**시나리오**: 틱이 `save` 중 SIGKILL(맥 슬립) → 파손 파일 1개 → 다음 `meeting_mail_job` 의 `request-approval` 스테이지가 통째로 실패, 이후 매 회차 반복. `job.atomic_write` 가 있어 확률은 낮지만 0 은 아니다.
**수정**: `_iter_queue` 와 같은 try/except 로 감싸 파손이면 재생성.

### M12. 승인 카드가 본문의 일부만 보여준다 — 승인 전 전문 확인 경로가 없다
`approvals_tick.py:53`

`body[:200]`. 그런데 draft-after-approve 라 **Gmail 초안은 승인 후에 생긴다** → 승인 시점에 전문을 볼 수단은 `python -m kv_core.approval show &lt;key&gt;` 뿐인데 카드에 그 안내가 없다(카드 문구는 "✅ 승인 · ❌ 거부").
**근거(프로브 P14)**: 1200자 본문 → 카드 200자(16%).
**시나리오**: 심사역이 메일 앞 200자만 보고 ✅ → 뒷부분에 claude 가 만든 오류/환각이 있어도 걸러지지 않는다. W8 의 승인 게이트 자체가 형식화된다.
**수정**: 카드에 `show` 명령 안내 + 본문 길이 표기, 또는 스레드 답글로 전문 첨부.

---

## 🟡 LOW

### L13. `heavy_slot` 의 빈 락파일 = stale 판정 → `decide` 의 짧은 쓰기에 동시진입 창
`job.py:296-325` (신규 사용자 = `approval.decide`)
`os.open(O_EXCL)` 성공 후 `os.write(pid)` 전 창에서 제3자가 보면 락파일은 비어 있고, `_stale()` 은 `int("" or "0")=0 → True` 로 회수 가능이라 답한다. **근거(프로브 P2)**: 빈 락파일 `_stale()=True`. 게다가 `__exit__` 은 무조건 `unlink` 하므로 A 가 나가며 B 의 락을 지운다. `decide` 처럼 마이크로초 단위 임계구역에서는 이 창의 비중이 상대적으로 크다.
**수정**: 락 내용을 `os.open` 전에 임시파일에 쓰고 `os.link`/`rename` 으로 원자 배치, 또는 `_stale()` 이 "빈 파일 + 생성 후 5초 미만" 을 not-stale 로.

### L14. `SLACK_APPROVAL_USER` 미설정 = 승인 화이트리스트 사실상 비활성 — **실환경**
`approvals_tick.py:127, 113-115`
A-F10 의 "인가 비대칭" 은 `only_user` 가 있을 때만 작동한다. 실 `.env` 에 그 키가 **없다**(확인: `SLACK_APPROVAL_CHANNEL` 만 존재). **근거(프로브 P12)**: `only_user=None` → 낯선 사용자의 ✅ → `('approved','slack:U_RANDOM_STRANGER')`. 현재 방어는 "채널이 비공개" 하나뿐이고 그건 코드 밖 사실이다.
**수정**: `.env` 에 `SLACK_APPROVAL_USER` 기입, 또는 미설정 시 기동 로그에 loud 경고.

### L15. 테스트 자체의 오염 — `alerts.push`/`alerts.scan` 이 복구되지 않는다
`tests/_synthetic_approvals_tick.py:146, 176`
`gmail.create_draft`(108→114)와 `slackbot.post`(119→128)는 복구하는데, `_alm.push`(146)와 `_al.scan`(176)은 끝까지 스텁으로 남는다. 그래서 A-F2/A-F2b/A-F4 구간에서 발생한 실제 알림(M9 의 `approval-exec` 오탐 포함)이 **전부 보이지 않는다** — 실제로 테스트 출력에 `⚠️ m-fail: 이전 실행 흔적 있음(중복 초안 가능)` 이 찍히는데 아무 체크도 안 한다. "테스트가 만든 전제가 결함을 가린" 정확한 사례.
**수정**: `try/finally` 로 원복 + "이 시나리오에서 발생해선 안 되는 알림" 을 음성 단언으로 추가.

### L16. `_reconcile` 이 실제 기록 없이도 `n` 을 올린다
`approvals_tick.py:151-152` — `approval.decide(...)` 의 반환값을 버린다. `None`(레코드 소멸)이나 SlotBusy no-op(H3)에서도 `decide N` 이 로그에 찍혀 요약이 거짓말을 한다.
**수정**: 반환 레코드의 `decision` 이 의도한 값일 때만 카운트.

### L17. launchd 가 큐 디렉토리 안에 로그를 쓴다
`~/Library/LaunchAgents/com.kv.approvals.plist` → `StandardOutPath = ~/kv-data/_approvals/launchd.log`. `*.json` glob 이라 파싱 오염은 없지만 ① `orrery._approvals()` 의 `exists()` 가드가 로그 때문에 항상 참이 되고 ② "큐 정리" 로 디렉토리를 지우면 틱 로그가 통째로 유실된다.
**수정**: 로그를 `_scheduler/` 나 `_approvals_log/` 로 분리.

---

## verdict: 17 findings
**high 4 · med 8 · low 5**

**실환경에서만 드러나는 것(최우선)**: H3(approvals-write 유령 락 → 승인 시스템 전체 무음 무력화) · H4(gmail-inbox 유령 락 → 실행 영구 침묵, 그것도 아무것도 안 지키는 락 때문에) · M6(하루치 활동이 다이제스트에 0으로 보임) · L14(승인 화이트리스트 미설정).

**W9-1.5 사고의 일반화가 확인된 지점**: 
- M9 — 테스트가 실패 보상 경로를 검증하면서 그 경로가 내는 **오탐 알림을 스텁으로 가렸다**(L15 와 한 쌍). first_attempt 사고와 같은 모양: 테스트가 주입한 전제(스텁된 push)가 실환경의 상태(실제 알림 발화)와 다르다.
- M6/M7 — `_is_closed` 는 합성 테스트에서 "pending 에서 빠지는가" 만 물었고, **빠진 뒤 그 정보가 어디로 가는가**(orrery 통계)는 아무도 안 봤다. 실데이터가 한 번도 없었으므로 드러날 기회도 없었다.
- H1/H2 — 오늘 `_reconcile` 굶주림을 고치며 `_announce` 에 같은 함정을 심었던 것과 **정확히 같은 패턴**: `decide` 에 락을 붙이면서 `save` 를 빼먹었고, 그 결과 `_announce`·`_execute` 두 곳에 동시에 심겼다. 합성 테스트는 단일 프로세스 순차 실행이라 원리적으로 못 잡는다.

**프로브 파일**: `/private/tmp/claude-501/…/scratchpad/probe1.py`, `probe2.py` (스크래치 KV_DATA, 외부 호출 0)</result>
<usage><subagent_tokens>105865</subagent_tokens><tool_uses>34</tool_uses><duration_ms>553351</duration_ms></usage>
</task-notification>
<task-notification>
<task-id>a6bc941123687dcfc</task-id>
<tool-use-id>EXAMPLE_ID_1c75f8b4a941</tool-use-id>
<output-file>/private/tmp/claude-501/-Users-jeonghyunpark-Desktop-Jeonghyun-study/00000000-0000-0000-0000-d9a71fb46a6e/tasks/a6bc941123687dcfc.output</output-file>
<status>completed</status>
<summary>Agent "W9리뷰B: 알림·계측 수정분" finished</summary>
<note>A task-notification fires each time this agent stops with no live background children of its own. The user can send it another message and resume it, so the same task-id may notify more than once.</note>
<result>## 사고 보고 먼저 (내 실수)

**슬랙에 실제 메시지 1건이 나갔다.** probe2 의 마지막 케이스에서 `KV_NO_SEND` 의 truthy 판정을 확인하려고 값을 빈 문자열로 두고 `notify.slack("probe")` 를 호출했는데, 실 webhook 이 설정돼 있어 그대로 발송됐다.

증거: probe2 P2-d 출력 마지막 줄 — `KV_NO_SEND='' → notify.slack 반환 True` 이면서 `🚫` 로그가 없다(= 킬 스위치를 안 탔다는 뜻이고, True 를 반환하는 다른 경로는 status 200 뿐이다). 별도 확인으로 `secrets.get("SLACK_WEBHOOK_URL")` → `True`.

채널에서 `probe` 한 줄 지우면 된다. 지시 위반이고, 이후 프로브는 전부 `notify.slack` 하드 스텁 + `KV_NO_SEND=1` 고정으로 돌렸다(추가 발송 0). 역설적으로 이게 아래 H1 의 실증이다 — 킬 스위치는 환경변수 하나고, 그 하나가 어긋나면 결과가 즉시 실채널로 새거나(이번 방향) 전부 사라진다(반대 방향).

---

## Findings

### 🔴 HIGH — 실환경에서만 드러남

**H1. `KV_NO_SEND` 가 알림을 억제하는 게 아니라 파괴한다 — dedup 마커를 태운다**
`kv-core/kv_core/alerts.py:70-73` + `notify.py:39-41`

`notify.slack` 이 킬 스위치에서 **True** 를 반환한다 → `push` 가 이걸 발송 성공으로 판정하고 마커에 `{"delivered":true}` 를 쓴다.

```
P2-a: KV_NO_SEND=1 → push 반환 'sent' · 마커 내용 {"delivered":true,"ts":"2026-08-07T04:09:31Z"}
P2-b: 같은 날 스위치 끄고 재푸시 → 'deduped'   ← 그날 알림 영구 유실
P2-c: orrery._alerts_today() = ['gate-block-claude']  ← 안 나간 알림을 "발송됨"으로 보고
P2-d: KV_NO_SEND='0' / 'false' / 'no' 전부 스위치 ON (truthy 함정)
```

시나리오: 셸에 `export KV_NO_SEND=1` 해둔 채 `python -m kv_core.scheduler --run` 이나 approvals_tick 을 수동 실행 → 그날치 마커가 delivered 로 소진 → 이후 launchd 틱(4h/10분)은 전부 no-op. 게이트 차단·never-succeeded·부분결손 알림이 그날 통째로 사라지고, 장부와 orrery 는 "발송됨"이라고 말한다. **킬 스위치가 켜져 있다는 사실을 표시하는 표면이 어디에도 없다**(P5-d: orrery 에 `KV_NO_SEND`·`SLACK_WEBHOOK` 언급 0, 마커의 `delivered` 도 안 읽음). `KV_NO_SEND=0` 으로 "끄려는" 직관이 정반대로 작동하는 것까지 겹친다.

**H2. 다이제스트도 같은 스위치를 타고, "발송 실패는 침묵하지 않는다"는 방어까지 무력화된다**
`scheduler.py:224-227`

`sent = orrery.push_slack(d)` → KV_NO_SEND 면 True → `if not sent:` 폴백 출력(A9)도 안 돈다. 즉 스위치가 새는 순간 **즉시 알림과 다이제스트가 동시에** 죽고, 그 사실을 로그로 알리는 경로마저 함께 닫힌다.

**H3. 보상 재푸시는 `gate_block` 전용 — 나머지 6개 push 지점의 발송 실패는 영구 유실**
`alerts.py:207` · `workers.py:77`

```
P7-a: 슬랙 5xx 중 push("partial-appstore-snapshot") → 'failed'
      마커 = []            (claim 반납 — 흔적 0)
      events.jsonl = False (append_event 는 gate_block 만 한다)
P7-b: 슬랙 복구 후 scan() = [] · 발송 0건
P7-d: 7개 push 호출부 중 반환값을 쓰는 곳은 gate.py:270 하나
```

최악 시나리오: `run_appstore_snapshot` 이 82/84 부분결손을 감지해 "해당 차트는 **영구 손실 가능**" 을 push 한다. 슬랙이 그 순간 5xx 면 claim 이 반납되고 아무 흔적도 안 남는다. 그 잡은 `return True` 라 `last_success` 가 전진 → 그날 다시 due 가 아니다 → **재시도 경로 자체가 없다.** 영구 데이터 손실을 알리는 알림이 영구 유실된다. approvals_tick 계열이 무사한 건 10분 틱이 우연히 재시도해 주기 때문이지 설계가 아니다.

**H4. `_auth_suspect` 는 실환경에서 구조적으로 발동 불가 — 그리고 인증 만료가 정확히 그 상태를 만든다**
`alerts.py:113-125`, `93-110`

실 상태: `~/kv-data/_usage/` **디렉토리 자체가 없다**(claude 호출 0). 그리고 `~/kv-data/_scheduler/launchd.log` L113-136 을 보면 judge 는 8/6 에 5회, 8/7 에 2회 시도해 전부 `criteria preflight` 에서 멈췄다 — **claude 근처에도 못 갔다.**

```
P3-a: usage 부재         → _usage_tail(3)=[] · _auth_suspect()=None
P3-b: 어제 3연속 실패 + 오늘 호출 0 → None   ← "오늘 발생분" 조건(B-F7)이 봉인
P3-c: 이틀 전 실패는 tail 이 아예 안 읽힘   → None
```

즉 이 알림은 "누군가 claude 를 오늘 3번 실패시켜 줄 때까지" 대기한다. judge 룰이 비어 있는 지금 그 일은 일어나지 않는다. 살아있음을 능동 확인하는 카나리아가 없어서, **인증 만료와 "며칠째 안 부름"이 관측적으로 완전히 동일하다.** W9 B-F7 이 추가한 "오늘 발생분" 조건은 이 침묵을 한 겹 더 강화했다.

---

### 🟡 MED

**M1. never-succeeded 에 백오프·상한·에스컬레이션이 전무 — 매일 같은 문장을 영원히**
`alerts.py:142-156`

```
P1: first_attempt=8/4 고정, 30일 시뮬 → 27일 연속 발동 · 마커 27개
    첫: "배선 후 3일간 한 번도 성공하지 못했다"
    끝: "배선 후 29일간 한 번도 성공하지 못했다"   ← 일수만 바뀐다
```

judge 는 사람이 `judge-criteria.md` 룰을 기입하기 전엔 **구조적으로 성공이 불가능**하다(preflight fail-loud). 즉 사람이 액션하기 전엔 절대 멈추지 않는 알림을 하루 1회 영구히 보낸다 — alert fatigue 의 교과서 정의고, 이 파일 헤더가 스스로 경계하는 "늑대소년(T3)" 그 자체다. 유예만 있고 상한이 없다.

**M2. `scan()` 의 "클래스별 격리"가 3번째 클래스에는 적용 안 됨**
`alerts.py:198-212`(gate_block 보상 루프가 `_try` **밖**) · `alerts.py:186`(`_try` 의 except **안**에서 push 가 raise 하면 전파)

```
P4-a: 보상 루프에서 OSError(28) → 🔴 scan() 이 예외로 죽음
P4-b: _try 의 except 안 push raise → scan() 밖으로 전파
```

디스크 풀/권한 오류 하나로 `_auth_suspect`·`_task_failures` 가 멀쩡해도 scan() 전체가 정지한다. 상위 approvals_tick(A-F4)이 잡아 `approvals-tick-error` 를 push 하려 하지만 **같은 이유로 그 push 도 실패**한다. 헤더 주석의 "한 클래스가 죽어도 나머지는 돈다"가 반만 참이다.

**M3. 경과일은 로컬 날짜, 마커·이벤트 필터는 UTC 날짜 — 혼용**
`alerts.py:148` `_date.today()` vs `alerts.py:64,201` `_utc_today()`

워터마크의 `first_attempt`/`last_attempt` 는 `scheduler._utc_today()` 산 값이다. KST(UTC+9)에서 00:00~09:00 사이엔 로컬 날짜가 UTC 보다 하루 앞서므로 유예 경계에서 하루 일찍 발동한다. 더 중요한 건 **CI 가 `TZ: Asia/Seoul` 을 고정해 로컬과 맞춰버려서 이 클래스가 CI 로는 영원히 안 잡힌다** — W2 TZ 사고 방지책이 동시에 이 버그의 눈가리개다.

**M4. `alerts.yaml` 이 never-succeeded 를 선언하지 않는다 — 사문 키를 지우며 반대편 사문을 만들었다**
`config/alerts.yaml` 전체 · `alerts.py:132,151`

새 클래스의 유예 = `cadence.freshness_limit(t) × task_failure.consecutive_periods`. yaml 에는 `task_failure` 만 있고, 그 값 하나가 **두 클래스를 동시에** 움직인다는 사실이 어디에도 없다. judge 의 실제 유예가 3일인 것도 yaml 만 봐선 알 수 없다(freshness_limit 은 코드 + tasks.yaml). "임계는 여기서만 조정한다"는 파일 선언과 어긋나고, B-F13 이 지운 `dedup.per_identity_per_day` 와 **정확히 같은 종류의** "값을 바꿨는데 왜 안 변하지" 사고가 반대 방향으로 예약돼 있다.

**M5. 레거시 `pushed: true` 를 미발송으로 오판 → 중복 재푸시**
`alerts.py:207` (`!= "sent"`) · `gate.py:271`. 이전 버전(`7ccd6d1~1:gate.py:269`)은 boolean 을 기록했다.

```
P4-c: {"pushed": true} 레코드 → scan() = ['gate-block-apify'] · 재푸시 1건
```

실환경 완화: `~/kv-data/_alerts/events.jsonl` 이 **존재하지 않는다**(게이트 차단 이력 0). 지금은 잠복이고, 백업/다른 머신 KV_DATA 복원 시 되살아난다. `in ("sent", True)` 한 줄로 닫힌다.

**M6. "전부 실패한 날"에 슬랙 트래픽이 0이 될 수 있다**
`scheduler.py:222-224` (`n_ok &gt; 0 or has_red`) + `judge_job.py:193-199` (preflight 는 **잡을 만들지 않는다**)

```
P6-a: judge=⚠️ · meeting-mail=✅ · appstore=✅ · failures=[]
      has_red=False · n_ok=0 → 발송 조건 False → 다이제스트 미발송
P6-b: 즉시 알림은 never-succeeded 1건, 2차 틱부터 dedup
P6-c: 마커가 이미 소진된 상태면 → 총 슬랙 트래픽 0
```

실증 보강: `~/kv-data/net-sourcing/jobs/` 에 `judge-2026-08-06`·`judge-2026-08-07` 디렉토리가 **없다**(8/4·8/5 만 있다). 그런데 launchd.log 는 그 이틀간 judge 를 7번 시도해 전부 실패했다고 기록한다 — preflight 가 잡을 안 만들어서다(의도된 설계). 결과적으로 **orrery 의 "실패 잡" 섹션과 다이제스트 게이팅이 judge 의 실패를 구조적으로 못 본다.** H1 과 겹치면 완전 침묵.

---

### 🟢 LOW

**L1. `_safe_id` 100자 절단 + 문자 치환 충돌** — `alerts.py:48`
```
P4-d: 100자 초과 접미사 다른 두 identity → _safe_id 동일 (True)
      'net-sourcing/threads' 와 'net-sourcing:threads' → 둘 다 'net-sourcing_threads'
```
현 identity 는 전부 짧아 실발생 0. `[:88] + "-" + sha1[:8]` 이면 구조적으로 닫힌다.

**L2. `first_attempt` 백필이 사람 손이었다 — 구조가 아니다** — `scheduler.py:144`
setdefault 는 앞으로만 옳다. judge 의 `first_attempt: 2026-08-04` 는 오늘 수동 기입한 값이다. "시도는 있는데 first_attempt 가 없는" 워터마크가 다시 생기면(백업 복원·수동 편집) 폴백이 `la` 로 가서 경과일 0 → **다시 침묵**한다. 이 폴백을 "보수적"이라 부르지만 실효는 **알림 무한 연기**다. `first_attempt` 부재 자체를 한 번 알리는 편이 안전하다.

**L3. 메타 검증이 문자열 존재만 본다** — `_review_regressions.py:277`
테스트 파일에 `"KV_NO_SEND"` 문자열이 있는지만 확인한다(주석에만 있어도 통과). 그리고 **프로덕션 실행 경로에 KV_NO_SEND 가 없는지는 아무도 단언하지 않는다** — H1 의 근본. plist 3본이 깨끗한 건 오늘 시점의 사실이지 구조가 아니다.

---

## 브리핑 질문에 대한 직답

**"CI 가 `KV_NO_SEND=1` 로 도는데 알림이 실제로 나가는지는 CI 가 절대 검증 못 한다 — 그 공백을 무엇이 메우나"**

→ **현재 아무것도 메우지 않는다.** P5-d 로 확인: `alerts.py` 에 canary/heartbeat 개념 0건, orrery 는 마커 파일명만 읽고 `delivered` 필드도·`KV_NO_SEND` 상태도·웹훅 설정 여부도 표시하지 않는다. 실발송이 살아있음을 아는 유일한 경로가 "사람이 슬랙에서 뭔가를 봤다"인데, 그건 알림 체계가 정확히 못 하게 설계된 일(가짜 침묵)이다.

최소 처방 두 줄: ①하루 1회 `alert-canary` 를 **실제로** 발송(다이제스트에 묻어가도 된다) ②`push` 가 `KV_NO_SEND` 상태를 마커에 `{"no_send":true}` 로 기록하고 orrery 가 "🚫 킬 스위치 켜진 채 N건 억제됨"을 렌더 — 이러면 H1 이 med 로 내려간다.

**나머지 헌팅 항목 판정**: never-succeeded 반복성 = M1 확인 · claim 반납/마커 소모 = H1 확인(무해하지 않다) · `_auth_suspect` 호출0 구분 = H4 확인(구분 불가) · `_try` 무한재귀 = M2 확인(재귀는 아니고 예외 전파) · boolean `pushed` 오탐 = M5 확인(단 실환경엔 events.jsonl 부재) · KV_NO_SEND 누출 표시 = H1(표시 수단 0) · `_safe_id` 충돌 = L1(가능하나 현 실발생 0).

**verdict: 13 findings** (high 4 · med 6 · low 3)

## 프로브 증거

전부 스크래치 KV_DATA + `KV_NO_SEND=1` + `notify.slack` 하드 스텁. 실 KV_DATA 는 읽기만(워터마크는 스크래치로 복사).

- `/private/tmp/claude-501/-Users-jeonghyunpark-Desktop-Jeonghyun-study/00000000-0000-0000-0000-4803b58091f8/scratchpad/adv_probe5.py` — 실 registry 12 task 커버리지 + 실 워터마크로 `scan()` → `['never-succeeded-judge']` 재현
- `.../adv_probe6.py` — 총침묵 창(다이제스트 게이팅 × dedup)
- `.../adv_probe7.py` — 보상 재푸시 gate_block 전용 실증
- `.../probe1.py`, `probe2.py`, `probe5.py` 는 다른 세션이 같은 스크래치 이름으로 덮어썼다(내가 실행한 시점 출력은 위 인용이 원본). probe3/probe4 는 남아 있다.
- 참조한 실환경 파일: `~/kv-data/_alerts/sent/never-succeeded-judge-2026-08-07`, `~/kv-data/net-sourcing/cadence/*.json`, `~/kv-data/_scheduler/launchd.log`, `~/kv-data/net-sourcing/jobs/`, `~/Library/LaunchAgents/com.kv.*.plist`(3본 전부 `KV_NO_SEND` 없음 재확인)
- 로컬 재현: `_synthetic_alerts` 20/20 · `_review_regressions` 전량 통과 — **위 13건은 전부 현재 CI 가 초록인 채로 통과시키는 것들이다.**</result>
<usage><subagent_tokens>125593</subagent_tokens><tool_uses>56</tool_uses><duration_ms>670774</duration_ms></usage>
</task-notification>
<task-notification>
<task-id>a66cb7707e0e3fd9c</task-id>
<tool-use-id>EXAMPLE_ID_9d8982c15c6d</tool-use-id>
<output-file>/private/tmp/claude-501/-Users-jeonghyunpark-Desktop-Jeonghyun-study/00000000-0000-0000-0000-d9a71fb46a6e/tasks/a66cb7707e0e3fd9c.output</output-file>
<status>completed</status>
<summary>Agent "W9리뷰C: 관측·러너·CI 수정분" finished</summary>
<note>A task-notification fires each time this agent stops with no live background children of its own. The user can send it another message and resume it, so the same task-id may notify more than once.</note>
<result>모든 검증 완료. 실 데이터를 스크래치에 복제해 프로브를 돌렸고, 실 `~/kv-data`·리포·슬랙은 무변경을 확인했다(diff 공백).

---

# 적대 리뷰 결과 — kv-automation @11a060e

**최우선 렌즈 결론: 같은 유형이 6건 더 있다.** 그중 3건은 orrery 의 resolved 판정 자체에 있고, **실데이터에서 "미해결 2건"이 "0건"으로 뒤집히는 것을 재현했다.**

## 🔴 HIGH

### H1 · `orrery.py:188` — cap=10 이 미해결 실패를 조용히 잘라낸다 (실증)
`rows.sort(ts desc)` → `rows[:cap]` 이 **resolved 필터보다 먼저** 적용된다. 해결된 최신 실패가 10건 쌓이면, 오래된 **진짜 미해결이 리스트 밖으로 밀려나고 카운트에서도 사라진다.**

실데이터 복제본 + 해결된 노이즈 12건 주입:
```
반환 rows=10 (cap 10) · 미해결=0 → []
실제 미해결 진실값 = judge-2026-08-04, judge-2026-08-05 (2건)
렌더:  ── 실패 잡 (최근 7일, 미해결 0건 · 해결됨 10건) ──
슬랙:  (미해결 줄 없음 = 침묵)
```
**이게 오늘 겪은 사고와 정확히 같은 형태다** — "고쳤다는 알림이 실환경에서 침묵". meeting-mail 은 이미 실패→r2 성공 패턴을 하루 2건씩 생산하므로 재시도가 잦아지면 5일이면 도달한다. 테스트(`_synthetic_orrery.py:125`)는 실패 2건만 만들어 cap 을 **한 번도 건드리지 않는다**.

### H2 · `orrery.py:185` — resolved 판정 ②가 날짜 절단이라 "성공 뒤에 난 실패"를 해결로 본다 (실증)
`resolved = bool(ls and ls &gt;= str(r["ts"])[:10])` — 워터마크는 날짜, 실패 ts 는 시각인데 **날짜로 잘라 비교**한다.
```
[B4] 신규 base · 실패 ts 2026-08-07T23:59 vs last_success 2026-08-07
     → resolved: True
```
실환경 시나리오: meeting-mail 이 09:00 성공(워터마크 8/7) → 밤에 수동 재실행이 실패 → **미해결 0건으로 표시되고 슬랙에서 사라진다.** 이 분기는 테스트 커버리지가 **0**이다(⑧은 합성 skill `_orrery-w9` 라 registry 매칭이 아예 안 돼 branch① 만 탄다).

### H3 · `orrery.py:180` — resolved 판정 ①이 시간 순서를 안 본다 (실증)
`(skill, base) in completed_bases` 는 **같은 base 에 completed 가 하나라도 있으면** 그 base 의 모든 실패를 해결로 본다. 순서 무관.
```
[B2] 오늘 00:00 completed(meeting-mail-2026-08-07)
     + 23:00 failed(meeting-mail-2026-08-07-r2) → resolved: True
```
성공한 잡을 재실행해 깨뜨려도 🔴 가 안 뜬다.

### H4 · `orrery.py:84-91` vs `alerts.py:141-155` — never-succeeded 에서 관측과 알림이 갈린다 (실증)
`_freshness` 는 `if not last_success:` 에서 **조기 return** 하며 `freshness_limit` 을 아예 안 본다. 며칠을 실패해도 영원히 ⚠️.
```
judge 워터마크: {'last_attempt': '2026-08-07', 'first_attempt': '2026-08-04'}
orrery._freshness    → {'status': '⚠️', 'note': '시도는 있으나 성공 0'}
alerts._task_failures → ('never-succeeded-judge', '🔴 judge: 배선 후 3일간 한 번도 성공하지 못했다')
```
실 슬랙 다이제스트: `*KV-ORRERY* — ✅3 ⚠️1 🔴0`. **judge 가 4일째 전멸인데 헤더는 🔴0 이다.** cadence.py:38 주석이 명시적으로 방지하겠다던 "다이제스트는 ✅인데 알림은 🔴" 그 자체.

### H5 · `scheduler.py:144` — `setdefault` 는 기존 never-succeeded task 의 시계를 리셋한다
```
이전(구스키마): {'last_attempt': '2026-07-01'}     ← 한 달째 매일 실패 중
run_due 1회 후:  {'last_attempt': '2026-08-07', 'first_attempt': '2026-08-07'}
```
`period` 는 coalesce 라 항상 오늘이다. **실 `judge.json` 의 `first_attempt: 2026-08-04` 는 이 코드가 만들 수 없는 값** — 손으로 넣은 것이고, 리포에 백필 코드는 없다(`grep first_attempt` 결과 scheduler/alerts/test 뿐). 게다가 `_synthetic_alerts.py:184` 는 `"구 스키마는 폴백으로 미발동(오탐 0)"` 을 **정답으로 못박아** 이 리셋을 CI 가 보증한다. 다음에 배선되는 task 도 같은 침묵을 반복하고, 그때도 사람이 손으로 고쳐야 한다.

### H6 · 부재 감지기가 부재의 원인 안에 산다
🔴 stale 판정(`_freshness`)은 orrery 에만 있고, orrery 슬랙은 **`scheduler --run` 끝에서만** 발송된다(`scheduler.py:218-227`). `approvals_tick`(10분 plist)은 `alerts.scan()` 만 부르는데, `alerts._task_failures` 에는 **staleness 판정이 없다** — `last_attempt` vs `last_success` 격차만 본다. 스케줄러가 죽으면 두 값이 **함께 멈춰 격차가 고정**되므로 영원히 무알림. 건강한 시스템에서 스케줄러가 죽으면 푸시·다이제스트 양쪽 다 침묵하고, MTTD 는 사람이 "요즘 조용하네" 할 때까지다. **질문하신 "정상적으로 조용한 날 vs 스케줄러 정지"는 구분되지 않는다.**

## 🟠 MED

### M1 · `scheduler.py:222` — 다이제스트 게이트가 해결분까지 센다
```python
has_red = any(r["status"] == "🔴" for r in d["tasks"]) or bool(d["failures"])
```
`d["failures"]` 는 **resolved 포함 원본**인데 `render_slack` 은 unresolved 만 표시한다. 실측:
```
has_red = True  (tasks 🔴: False / failures 총 4건 / 미해결 2건)
```
plist 는 `StartInterval 14400`(4시간) + 09:00 → 하루 ~6틱. judge 를 고친 뒤에도 **해결된 실패 4건이 7일간 창에 남아 매 틱 다이제스트를 띄우고, 내용은 "미해결 0건"** 이다. W9 가 표시만 필터하고 게이트는 안 고쳤다 — alert fatigue 가 그대로 남았다.

### M2 · `orrery.py:106-127` + `alerts.py:135` — launchd-direct 는 `freshness_days` 선언이 사문
`_launchd_direct_health` 는 `freshness_limit` 을 **안 쓴다**(`gap` + 하드코딩 grace). 그리고 `alerts._task_failures` 는 `if not t.scheduled or (t.runner != "local" and not t.shadow): continue` 로 launchd-direct 를 **통째로 제외**한다.
```
appstore-sourcing  runner=launchd-direct  freshness_limit=1  alerts커버=False
[D2] freshness_days=5 선언 → cadence.freshness_limit = 5
     orrery._launchd_direct_health(8/11, 5일 경과) = {'status': '🔴', ...}   ← 선언 무시
```
매일 노션에 138행을 착지시키는 잡이 **푸시 알림 커버리지 0**이고, 신선도 SLO 선언도 안 먹는다. `tasks.yaml` 에 `freshness_days` 선언이 0개라 이 불일치가 지금껏 안 드러났다 — **선언 경로를 처음 쓰는 곳이 하필 유일하게 안 먹는 곳**이다.

### M3 · `meeting_mail_job.py:121-139` — attempt 간에는 여전히 claude 재작업 (실증)
```
attempt1 종단=failed · drafts.jsonl 에 2건 기록됨
attempt2(r2) 의 done_ids = ∅ → todo = ['pid-0','pid-1','pid-2']
▶ 이미 만든 pid-0·pid-1 을 다시 호출 (claude 콜 3회)
선필터(승인 큐 파일)로 걸러지나? → 없음 → 못 거른다
```
`_drafts_path` 가 job_id 별이고 `-r2` 는 새 job_id 다. 선필터는 `_approvals/meeting-mail-&lt;pid&gt;.json` 만 보는데, attempt1 이 `request-approval` 에 도달 못 하고 죽었으면 큐 파일이 없다. **C-H2 는 attempt 내부만 고쳤다.** config 주석의 `"실질은 3콜"` 은 틀렸고, 최악치 9콜이 실질이다(불변식 계산은 다행히 9로 맞음).

### M4 · meeting-mail 이 3일째 진공 성공 중 (실환경)
```
meeting-mail-2026-08-05-r2 / 08-06-r2 / 08-07 → candidates.json = []  (전부 2 bytes)
_approvals/ 에 승인 레코드 0건 · _usage/claude/ 디렉토리 자체가 없음(claude 콜 0)
```
`s_draft` 는 `if not todo: return` 으로 조기 반환하므로 `made==0 and failed&gt;0` 진공 가드에 **도달하지 못한다**. `min_records: 0` 과 합쳐, "Catch 행이 진짜 0" 과 "Tier 필터·property 명·토큰이 깨져서 0" 이 **구분 불가**하다. 그런데 워터마크는 매일 `last_success` 를 전진시키고 orrery 는 `✅ 0일 전 성공` 이다. 규칙05(진공 통과 금지)가 이 경로에서만 뚫려 있다.

### M5 · `drafts.json` → `drafts.jsonl` 전환이 실환경 미검증
실 잡 디렉토리 산출물은 전부 `drafts.json`(구 코드). 커밋 7ccd6d1(`drafts.jsonl` 도입)은 **오늘 12:12:55**, 마지막 실런은 **오늘 09:00 KST**. C-H2 신규 코드는 실데이터에서 한 번도 안 돌았고, 첫 실행은 내일 09:00 이다.

### M6 · `validate.py:144-151` — DLQ 무한 성장 + orrery 전량 로드
회전·상한·삭제 없음. `validate_text` 는 파싱 실패 시 **claude 원문 전체**(`"raw": text`)를 덤프한다.
```
하루치(9콜 전량 파싱실패) 후 = 100,107 bytes / 9줄 → 1년 추정 34.8 MB
```
`orrery._dlq_summary` 는 매 `gather()` 마다 `read_text()` 로 전량 로드한다(하루 ~6틱 + 수동 호출). meeting-mail 이 halt→quarantine 로 바뀌면서 **깨진 출력이 이제 파이프라인을 안 세우고 계속 쌓는다** — 성장률이 W9 이전보다 커졌다.

### M7 · `alerts.py:148` — KST/UTC 혼용
`_date.today()`(로컬 KST)로 경과일을 재는데, 비교 대상 `first_attempt` 와 dedup 마커(`_utc_today()`)는 UTC 다. 00~09 KST 구간에서 하루 어긋나 유예가 조기 만료된다. 같은 모듈 안에 `_utc_today()` 가 이미 있는데 이 함수만 로컬을 쓴다.

## 🟡 LOW

- **L1 · `_chaos_lab.py:94`** — `time.sleep(1.6)` 후 kill. 실측: 총 런타임 2.54s, collect 완료는 프로세스 시작 후 ~0.15s. kill 이 collect_done **이전**에 떨어지면 재개가 COLLECT 를 다시 돌아 `collects == 1` 이 깨진다. 현재 마진 약 1.45s — GH 러너 콜드스타트가 그보다 느리면 flaky. 하한 대신 이벤트(`stage_done: collect`) 폴링으로 바꾸는 게 맞다.
- **L2 · `ci.yml` `TZ: Asia/Seoul`** — 테스트는 `datetime.now(timezone.utc).date()` 로 날짜를 만들고 `alerts` 는 `_date.today()`(=KST)로 잰다. 15:00~24:00 UTC 창에서 하루 어긋난다. 지금은 `consecutive_periods: 3` 유예가 커서 안 터지지만, 이 값을 1~2 로 조이는 순간 **시각 의존 flaky** 가 된다.
- **L3 · `workers.py:42`** — `date.today()`(KST) vs `period`(UTC). 00~09 KST 실행 시 board.py 가 쓴 **새 KST 날짜** 파일을 세고 워터마크는 **옛 UTC 날짜**로 전진 → 09:01 KST 에 UTC 가 넘어가면 같은 날 84차트를 한 번 더 긁는다($0 이라 무해하나 30분 런이 2회).
- **L4 · `orrery.py:164-167`** — `state.json` 이 파손된 **completed** 잡은 `completed_bases` 에 안 들어간다 → 대응 실패가 미해결로 오탐. (오탐 방향이라 안전하나 기록.)

## ✅ 헌팅했으나 실데이터에서 무해 판정

| 의심 | 검증 결과 |
|---|---|
| `_job_base` 의 `-r\d+$` + 날짜 하이픈 | 정상. `meeting-mail-2026-08-06-r2` → `meeting-mail-2026-08-06` |
| `base[:-11]` 이 하이픈 포함 task 명에서 깨지나 | 정상. `meeting-mail`(idx12='-'), `judge`(idx5='-') 둘 다 정확히 복원 |
| `last_ok` 키 `skill/name` vs 디렉토리명 | 실데이터 전부 `net-sourcing` 일치. appstore-snapshot 은 잡 미생성이라 미매칭 자체가 없음 |
| `_alerts_today` 의 `p.name[:-len(today)-1]` | 정상. identity 에 날짜가 들어간 `backfill-2026-08-07` 도 정확히 복원 |
| orrery read-only 계약 | **실데이터 형태로 실측** — `diff -rq` 결과 신규/변경 파일 0. `_approvals` 존재 분기(테스트가 안 타는 쪽)도 부작용 0 |
| `resources=["claude"]` 로 노션 무락 | judge_job 도 `["claude","notion"]` 을 잡으므로 judge↔meeting-mail 은 **여전히 직렬**. 겹치는 건 `sourcing`(apify+notion) 뿐이고 meeting-mail 쪽은 읽기 전용 + `Tier=Catch` 필터라 신규 착지행은 어차피 제외 — 무해 |

## 가장 먼저 고칠 것 (순서)

1. **H1** — `rows[:cap]` 을 `unresolved` 분리 후로 옮기고, 미해결은 cap 을 적용하지 않는다. 오늘 사고의 직계 재발이다.
2. **H4** — `_freshness` 의 never-succeeded 분기에서 `first_attempt` + `freshness_limit` 으로 🔴 승격. 이게 있어야 `has_red` 가 judge 를 잡는다.
3. **H2/H3** — resolved 를 **시각 비교**로, 그리고 "그 base 의 **가장 최신** attempt 가 completed 인가"로 바꾼다.
4. **H6** — `approvals_tick`(10분 독립 plist)에 staleness 스캔을 추가. 관측자를 관측 대상 밖으로 뺀다.
5. **H5** — `first_attempt` 백필(구스키마면 `last_attempt` 가 아니라 **워터마크 파일 mtime** 또는 명시적 마이그레이션) + `_synthetic_alerts.py:184` 의 "미발동이 정답" 단언을 뒤집는다.

**verdict: 17 findings** (high 6 · med 7 · low 4)</result>
<usage><subagent_tokens>132028</subagent_tokens><tool_uses>38</tool_uses><duration_ms>680717</duration_ms></usage>
</task-notification>
