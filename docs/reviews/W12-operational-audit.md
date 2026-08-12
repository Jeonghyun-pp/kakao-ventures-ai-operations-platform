# W12 운영 감사 — 코드가 아니라 "코드와 세상의 어긋남"

감사일 2026-08-13 (KST 00:05 · UTC 2026-08-12T15:05) · 읽기 전용 · 프로덕션 무변경
대상 리포 `~/kv-automation` @ `dd8c460` · 실데이터 `~/kv-data` (KV_DATA 미변경, 읽기만)

## 이 감사가 무엇을 보는가

어제 적대 리뷰 4본은 **코드 안에서 닫히는 결함** 67건을 찾았다. 이 감사는 그 넷이 구조적으로
못 보는 것만 본다 — 코드는 맞는데 **선언·문서·설치·실적이 서로 다른** 자리다.
검증(테스트 14본)이 이 축을 못 보는 이유는 단순하다: **테스트는 스크래치 KV_DATA 와 합성
launchd 를 쓴다.** "이 plist 가 이 맥에 실제로 설치돼 있나"는 어떤 합성 픽스처도 답할 수 없다.

각 발견 표기:
- **가역성** — 되돌릴 수 있나 (틀린 수정을 했을 때)
- **구조 신호** — ①같은 뿌리 3건+ ②단위·소유가 자원과 안 맞음 ③진실이 두 곳
  ④고칠 때마다 새 게 나옴 ⑤검증이 그 축을 구조적으로 못 봄

---

## 축 ① 실행 상태: 선언 ↔ 설치 ↔ 실적

### 실측 기준선

설치된 launchd 잡은 **3개뿐**이다.

```
$ ls ~/Library/LaunchAgents/ | grep kv
com.kv.approvals.plist
com.kv.net-sourcing.appstore.plist
com.kv.scheduler.plist

$ launchctl list | grep -i kv
-	0	com.kv.approvals
-	0	com.kv.net-sourcing.appstore
-	0	com.kv.scheduler
```

`kv_core.workers.WIRED` (kv-core/kv_core/workers.py:191-197) = 5개:
`bplan-score · mail-triage · appstore-snapshot · judge · meeting-mail`

전 task 대조표 (선언 = config/tasks.yaml):

| task | 선언 cadence/runner | WIRED | 실행체 실존 | 마지막 성공(실측) | 판정 |
|---|---|---|---|---|---|
| sourcing | weekly / local(기본) | ✗ | — | 워터마크 없음 | 🔕 침묵 (의도) |
| judge | daily / local | ✓ | scheduler | 2026-08-12 | ✅ 단 문서와 어긋남(축②) |
| meeting-mail | daily / local | ✓ | scheduler | 2026-08-12 | ✅ |
| sourcing-followup | quarterly / local(기본) | ✗ | — | 워터마크 없음 | 🔕 침묵 (의도) |
| appstore-snapshot | daily / local | ✓ | scheduler | 2026-08-12 | ✅ |
| appstore-sourcing | daily / **launchd-direct** | ✗(정상) | ✅ plist 설치됨 | 표기상 2026-08-12 | 🔴 **F1-3** |
| bplan-score | daily / local | ✓ | scheduler | 2026-08-12 | ✅ |
| mail-triage | daily / local | ✓ | scheduler | **없음** | ⚠️ **F1-4** |
| bplan | daily / local(기본) | ✗ | ❌ **plist 미설치** | **2026-08-02 (11일 전)** | 🔴 **F1-1** |

---

### F1-1 🔴 `bplan` — "자체 plist 로 돈다"는 선언이 거짓이고, 관측기가 그 침묵을 "정상"으로 승인한다

**①위치**
- `config/tasks.yaml:142-157` (task `bplan`, `runner` 키 **없음** → 기본값 `local`)
- `config/tasks.yaml:154` — `sync/drop ✅(자체 plist)`
- `skills/mail-harness/references/pipeline.md:44` — `# launchd: 평일 07:30 (~/Library/LaunchAgents/com.kv.mail-harness.plist)`
- `kv-core/kv_core/health.py:323-324` (침묵을 만드는 코드)

**②실측 증거**

선언된 plist 는 설치돼 있지 않다. 템플릿만 리포에 있다.

```
$ ls ~/Library/LaunchAgents/com.kv.mail-harness.plist
ls: ...: No such file or directory

$ find ~/kv-automation -name "*mail-harness*.plist*" -not -path "*/.venv/*"
./skills/mail-harness/project/com.kv.mail-harness.plist.template     ← 템플릿뿐

$ ls ~/kv-automation/config/launchd/
com.kv.approvals.plist   com.kv.scheduler.plist                      ← mail-harness 없음
```

원장이 정지를 증언한다.

```
$ ls -la ~/kv-data/mail-harness/bplan/
-rw-r--r--  4846  7 30 11:29 drop_ledger.json      ← 14일 전
-rw-r--r--  1503  8  2 23:44 sync_ledger.json      ← 11일 전
$ ls -la ~/kv-data/mail-harness/harness/
-rw-r--r--  ... 7 17 11:25 digest_latest.md        ← 27일 전
```

그런데 orrery 는 이걸 문제로 세지 않는다.

```
$ .venv/bin/python -m kv_core.orrery --no-net
║  🔕 bplan              daily      러너 미배선(워터마크 없음이 정상)
```

**③실환경에서 무슨 일이 일어나나**

비플랜 접수(sync)와 드랍메일(drop)이 **11일째 아무도 안 돌린다.** hello@ 로 들어온 비플랜은
노션에 등재되지 않고, 드랍 대상은 초안조차 안 생긴다. 그리고 이 정지는 **경보 대상이 아니다** —
`health.py:323` 은 `t.name not in WIRED and t.runner == "local"` 이면 `unwired`(🔕, 침묵)로
확정한다. 이 분기에는 **경과일 검사가 없다.** 하루가 지나든 100일이 지나든 🔕 다.

핵심은 여기다. **`runner` 를 잘못 선언한 것이 침묵을 샀다.**
`runner: launchd-direct` 로 선언했다면 `health.py:258-296` 의 완전한 신선도 사다리를 탔고
(`헬스 파일 없음(관측 불가)` → `unknown` ⚠️, 혹은 `성공 기록 0 (N일째)` → `never_bad` 🔴)
11일 전에 시끄러웠을 것이다. **기본값 `local` 이 관측을 끈다.**

문서(`notes`)만 "자체 plist"라 말하고, 그 문장은 어떤 코드도 읽지 않는다. 선언 필드(`runner`)와
서술 필드(`notes`)가 **서로 다른 말을 하는데 기계는 서술을 안 본다.**

**④수정 방향**
1. (즉시·가역) `config/tasks.yaml` 의 `bplan` 에 `runner: launchd-direct` 를 명시 →
   관측이 즉시 🔴 로 바뀐다. 실행은 하나도 안 바뀌므로 안전하다.
2. (구조) `health.py` 의 `unwired` 분기에 **경과일 상한**을 넣는다 —
   "미배선 상태로 `freshness_limit × N` 을 넘으면 🔕 가 아니라 ⚠️". 의도적 미배선(`sourcing`,
   `sourcing-followup`)과 **잊힌 미배선**은 지금 코드가 구분하지 못한다.
3. (근본) `notes` 의 실행 주체 주장을 기계가 읽는 필드로 승격하거나, `runner` 와 `notes` 가
   충돌하면 load 시 fail-loud 하도록 `registry.load()` 에 대조를 넣는다.

**가역성** ✅ 완전가역 (선언 한 줄 · 실행 무변경)
**구조 신호** ③진실이 두 곳(`runner` 필드 vs `notes` 서술) · ⑤검증이 그 축을 구조적으로 못 봄
(합성 테스트는 `~/Library/LaunchAgents` 를 안 본다) · ②단위·소유가 자원과 안 맞음

---

### F1-2 🔴 `bplan` 의 `stages` 3개 중 어느 것도 워터마크를 갖지 않는다 — 부분 정지가 원리적으로 관측 불가

**①위치** `config/tasks.yaml:149` — `stages: [sync, scoring, deliver]`, `config/tasks.yaml:155-157`

**②실측 증거**

`notes` 스스로 이 문제를 진단해 놓고 절반만 고쳤다.

```
config/tasks.yaml:155-157
  scoring 은 **별도 task `bplan-score`** 로 분리했다(2026-08-12 W11) — sync/deliver 는
  mail-harness 자체 plist 가 돌리고 판정만 kv-core 스케줄러가 돌린다. 한 이름에 두 러너를
  묶으면 어느 쪽이 실패했는지 워터마크가 구분하지 못한다.
```

`scoring` 은 분리돼 워터마크를 얻었다.

```
$ cat ~/kv-data/mail-harness/cadence/bplan-score.json
{"first_attempt":"2026-08-12","last_attempt":"2026-08-12","last_success":"2026-08-12"}
$ ls ~/kv-data/mail-harness/cadence/
bplan-score.json      ← 이것뿐. bplan.json 없음
```

그런데 `stages` 는 여전히 3개(`sync, scoring, deliver`)를 선언한다 — 이미 떼어낸 `scoring` 을
포함한 채로. 그리고 남은 `sync`/`deliver` 는 워터마크가 **하나도 없다.**

**③실환경에서 무슨 일이 일어나나**

`notes` 가 지목한 병("한 이름에 두 러너를 묶으면 어느 쪽이 실패했는지 워터마크가 구분하지
못한다")이 **`sync` 와 `deliver` 사이에 그대로 남아 있다.** sync 는 되는데 deliver 만 죽어도,
반대여도, 관측에 차이가 없다 — 둘 다 워터마크가 없으니까. 실제로 지금 원장을 보면
`sync_ledger` 는 8/2, `drop_ledger` 는 7/30 로 **3일 어긋나 있고**, 이건 두 단계가 서로 다른
시점에 멈췄다는 뜻인데 어떤 관측도 이 사실을 표면화하지 않는다.

`scoring` 을 분리한 수정은 옳았지만 **같은 뿌리의 나머지 두 개를 안 건드렸다.**

**④수정 방향** `bplan` 을 `bplan-sync` / `bplan-deliver` 로 마저 쪼개고 `stages` 를 실제와
맞춘다. 또는 stage 별 워터마크를 도입한다(더 큰 변경).

**가역성** ✅ 완전가역 (선언 변경)
**구조 신호** ①같은 뿌리 3건+ (bplan sync/deliver/notes-runner 전부 같은 "선언 단위가 실행
단위와 다름") · ④고칠 때마다 새 게 나옴 (scoring 만 떼고 멈춘 자리)

---

### F1-3 🔴 `appstore-sourcing` 은 23일 중 11일만 돌았는데 orrery 는 ✅ 다 — 헬스 파일이 채널 간에 덮어써진다

**①위치**
- `config/tasks.yaml:89-101` (`runner: launchd-direct`, `cadence: daily`, `status: done`)
- `kv-core/kv_core/health.py:262` — `p = paths.data_root() / t.skill / "last_run_health.json"`
- `skills/net-sourcing/project/core/pipeline.py:144-148` — writer

**②실측 증거**

선언은 daily 다. 실적은 절반이다.

```
$ python - <<'EOF'   (cron-appstore.log 의 'run <date>' 전수)
보유 11 일 / 기대 23일
결손 12 일: 07-22, 07-23, 07-24, 07-25, 07-27, 07-29, 07-30,
            08-01, 08-02, 08-04, 08-08, 08-09
EOF
```

더 나쁜 것: **8/9 은 수집은 됐는데 착지가 안 됐다.**

```
$ grep -oE "appstore_20[0-9-]+\.json" cron-appstore.log | sort -u | tail
... appstore_2026-08-07.json  appstore_2026-08-09.json  appstore_2026-08-10.json ...
$ grep "land 완료" cron-appstore.log | tail
... (run 2026-08-07) ... (run 2026-08-10) ...      ← 08-09 의 land 완료가 없다
```

그런데 orrery 는 이 task 를 ✅ 로 부른다.

```
║  ✅ appstore-sourcing  daily    0일 전 정상 (plist 직접 실행) (last_success=2026-08-12)
```

그 ✅ 의 출처를 열어 보면 — **앱스토어 실행이 아니다.**

```
$ cat ~/kv-data/net-sourcing/last_run_health.json
{ "ts": "2026-08-12T15:16:02",
  "channel": "linkedin",              ← 링크드인 수동 실행이 쓴 것
  "ok": true,
  "counts": { "linkedin_harvestapi": 297 },
  "last_ok_ts": "2026-08-12T15:16:02",
  "first_ts": "2026-08-12T08:30:23" }

$ stat -f '%Sm' ~/kv-data/net-sourcing/cron-appstore.log
Aug 12 08:33:54 2026               ← 앱스토어의 실제 마지막 실행
```

**③실환경에서 무슨 일이 일어나나**

`health.py:262` 는 헬스 파일을 **task 가 아니라 skill 로 키잉한다** (`t.skill`). 그런데
`pipeline.py:144` 는 `--channel` 이 무엇이든 **같은 파일 하나**에 쓴다. net-sourcing 은
appstore·threads·linkedin 세 채널을 돌리므로, **아무 채널이나 성공하면 `appstore-sourcing`
task 가 초록불을 받는다.**

지금 화면의 ✅ 는 8/12 15:16 에 사람이 손으로 돌린 링크드인 수집(297건) 의 것이다.
앱스토어 크론이 내일부터 영원히 안 돌아도, 누군가 링크드인이나 스레드를 한 번 돌리면
`appstore-sourcing` 은 계속 ✅ 다. **12일치 결손이 이미 이 방식으로 안 보였다.**

`first_ts: 2026-08-12T08:30:23` 도 거짓이다 — 이 잡은 최소 7/18 부터 돌았다. 즉
`never_bad` 판정의 기준선(`age = now - first_d`)도 오염돼 있다.

`health.py` 는 스스로 "판정의 단 하나의 정본"이라 선언하고(파일 헤더) 실제로 판정 로직은
한 곳에 모았지만, **그 판정이 먹는 입력이 여러 잡의 공유 자원**이다. 판정을 통일해도
입력이 공유면 답은 여전히 하나로 뭉갠다.

**④수정 방향**
1. writer 를 채널별로 분리 — `last_run_health_<channel>.json`, reader 는 task→channel 매핑으로 읽는다.
2. 또는 launchd-direct task 에도 **워터마크**를 준다(스케줄러가 실행하지 않아도 실행체가
   워터마크를 쓰게). 그러면 결손일이 `late/stale` 로 자동 표면화된다.
3. `first_ts` 를 파일 재생성 시 잃지 않도록 별도 보존.

**가역성** ⚠️ 부분가역 — 파일 스키마를 바꾸면 기존 `first_ts`/`last_ok_ts` 이력은 복구 불가
(현재 값이 이미 오염돼 손실은 작다)
**구조 신호** ③진실이 두 곳(하나의 헬스 파일이 3채널을 대표) · ②단위·소유가 자원과 안 맞음
(파일 단위=skill, 소비 단위=task) · ⑤검증이 그 축을 구조적으로 못 봄
(`_synthetic_health.py` 는 헬스 파일을 **직접 써서** 판정만 검사한다 — writer 와 reader 가
같은 키 공간을 쓰는지는 어떤 테스트도 묻지 않는다)

---

### F1-4 ⚠️ `mail-triage` 는 배선 3분 뒤에 감사 시점이 왔다 — 최초 실행이 한 번도 관측된 적 없다

**①위치** `config/tasks.yaml:122-140` · `kv-core/kv_core/workers.py:193`

**②실측 증거**

```
$ stat -f '%Sm' kv-core/kv_core/workers.py
Aug 12 21:57:50 2026                       ← mail-triage 를 WIRED 에 넣은 시각
$ tail ~/kv-data/_scheduler/launchd.log
└─ scheduler --run 끝 2026-08-12T12:54:05+00:00   ( = 21:54:05 KST )
🔕 mail-triage: due 이나 러너 미배선 — 건너뜀(워터마크 불변).
```

배선이 마지막 스케줄러 발화보다 **3분 45초 늦었다.** 그래서 로그의 "미배선" 은 옛말이지만,
아직 한 번도 돌아본 적이 없다.

```
$ ls ~/kv-data/mail-harness/cadence/
bplan-score.json                            ← mail-triage.json 없음
$ .venv/bin/python -m kv_core.orrery --no-net | grep mail-triage
║  ⚠️ mail-triage        daily     가동 이력 없음
```

**③실환경에서 무슨 일이 일어나나**

`health.py:326` 의 `idle`(⚠️, 관측만·알림 아님) 상태다. 다음 발화에서 `triage/main.py run` 이
**hello@ 인박스에 대해 처음으로** 돌아간다 — 아카이브가 자동(`autonomy.archive_auto: true`)인
경로다. 발송은 코드로 잠겨 있으니(`messages().send` 0개) 외부로 나가는 것은 없지만,
**아카이브는 인박스를 실제로 바꾼다.** 최초 실행은 무인이 아니라 사람이 보는 앞에서 한 번
돌리는 게 맞다.

그리고 `idle` 은 `_ALERTABLE = {"stale", "never_bad"}` 에 없다 — 배선해 놓고 스케줄러가
영영 안 깨어나도 **알림이 안 간다.** F1-1 의 `unwired` 무기한 침묵과 같은 계열이다.

**④수정 방향** 다음 실행 전에 `KV_NO_SEND=1` + 스크래치로 1회 수동 드라이런. 그리고 `idle`
에도 경과일 상한을 준다(배선 후 N일 무실행 = `never_bad`).

**가역성** ✅ (아카이브는 Gmail 에서 복구 가능)
**구조 신호** ⑤검증이 그 축을 구조적으로 못 봄 · ①같은 뿌리 3건+ (`unwired`·`idle`·`never`
셋 다 시간 상한이 없거나 약하다)

---

### F1-5 ⚠️ `appstore-sourcing` 의 `notes` 가 단일 최댓값을 일평균으로 제시한다

**①위치** `config/tasks.yaml:101` — `수집→노션 착지 138행/일`

**②실측 증거**

```
$ grep "land 완료" ~/kv-data/net-sourcing/cron-appstore.log
240건(07-21) 219건(07-26) 110건(07-28) 179건(07-31) 138건(08-03)
 71건(08-05)  90건(08-06)  69건(08-07) 119건(08-10)  66건(08-11) 62건(08-12)
```

`138` 은 8/3 하루의 값이다. 최근 5회 평균은 **81건**, 추세는 하락(119→66→62).

**③실환경에서 무슨 일이 일어나나** 용량·비용 산정의 근거 숫자가 실제보다 70% 높다.
추세 하락(중복제거 대조군이 커지며 신규가 줄어든다)이 문서에 안 잡혀 있어, 언제 이 채널이
"신규 0" 에 수렴하는지 아무도 모른다.

**④수정 방향** 고정 숫자를 지우고 관측으로 대체 — 이미 로그에 있다.

**가역성** ✅ **구조 신호** ③진실이 두 곳(문서의 상수 vs 로그의 실측)

---

## 축 ② 문서 주장 ↔ 코드 실제

`반자동` `주 1회` `수동` `아직` `미구현` `예정` `컷오버 전` `꺼져 있다` 를 전 문서에서 뽑아
코드·config·실행 실적과 1:1 대조했다. **32건이 어긋났고**, 아래는 실환경 영향이 있는 것만이다.

---

### F2-1 🔴 `net-sourcing/SKILL.md` 가 8곳에서 "judge 는 반자동·주 1회"라 말한다 — 매일 무인으로 20배치가 돈다

**①위치** `skills/net-sourcing/SKILL.md:12, 25, 421, 423, 424, 434, 580` ·
`skills/net-sourcing/project/SKILL.snapshot.md:399` 계열

**②실측 증거**

| 문서 | 코드·실적 |
|---|---|
| `SKILL.md:12` (frontmatter) — "**판단(Tier)은 반자동** — 주 1회 Claude Code 세션이 judge-export → judge-apply" | `config/tasks.yaml:28-39` `cadence: daily` `runner: local` → `workers.py:123-132,195` → `jobs/judge_job.py` |
| `SKILL.md:25` — "판단은 **반자동**(주 1회 Claude Code 세션)" | 8/12 하루에 배치 20개 무인 생성 |
| `SKILL.md:421,423` — "## 주 1회 판단 세션 (반자동 judge) / 크론은 **수집·착지까지만** 한다. 판단은 사람이 한다" | `com.kv.scheduler`(4시간 간격)가 매일 실행 |

```
$ ls ~/kv-data/net-sourcing/judge_verdicts_2026-08-12_judge-2026-08-12_*.json | wc -l
      20
$ grep "judge" ~/kv-data/_scheduler/launchd.log | tail -2
⚠️  런당 호출 상한 20배치 도달 — 잔여 ~741건은 다음 회차가 소화한다
[judge] judge-2026-08-12 → completed

$ grep -c "judge_job\|kv_core.scheduler" skills/net-sourcing/SKILL.md
0                      ← 599줄 문서에 실제 실행 경로가 한 번도 안 나온다
```

**③실환경에서 무슨 일이 일어나나**

노션 `Tier` 컬럼이 **매일 무인으로 60~80행씩 쓰인다.** 어제만 20배치 · 판정 분포
`{Threads/Cut:14, LinkedIn/Wait:12, …}`. 그런데 이 스킬을 읽고 들어온 세션(사람이든
Claude 든)은 "아직 아무도 판정 안 했겠구나, 내가 주간 세션을 돌려야겠다"고 판단한다.
그러면 **`judge-export` → 사람 판단 → `judge-apply` 를 손으로 돌려 이미 무인이 쓴 값을
덮어쓴다.** 되돌릴 수는 있지만(가역) 어느 값이 어느 주체의 것인지 구분할 방법이 없다.

이건 W12 적대 리뷰 4본이 전부 놓친 자리의 원형이다 — 코드는 정확히 설계대로 동작하고,
문서도 그 자체로 모순이 없다. **틀린 것은 둘 사이의 대응뿐이고, 그건 어느 파일 안에도 없다.**

**④수정 방향** SKILL.md 의 judge 절을 "무인 daily(`kv_core.scheduler` → `judge_job.py`),
수동 `judge-export/apply` 는 **점검·재판정용**"으로 다시 쓴다. frontmatter description 이
스킬 라우팅에 쓰이므로 :12 를 먼저.

**가역성** ✅ (문서 수정 · 이미 쓰인 Tier 값도 가역)
**구조 신호** ③진실이 두 곳(SKILL.md 서술 vs tasks.yaml 선언) · ⑤검증이 그 축을 구조적으로
못 봄(문서와 실행 주체를 대조하는 테스트가 없다)

---

### F2-2 🔴 문서가 근거로 드는 안전 스위치 `config.judge.llm.enabled` 는 **존재하지 않는 키**다

**①위치** `skills/net-sourcing/SKILL.md:424`, `:580-582`

**②실측 증거**

```
SKILL.md:580   판단 = 반자동(주 1회 Claude Code 세션). `config.judge.llm.enabled: false` —
SKILL.md:582   … 조용히 되살아난다**(크론이 매일 도는데 아무도 모른다).
```

```
$ .venv/bin/python -c "import yaml;c=yaml.safe_load(open('skills/net-sourcing/project/config.yaml'));
                       print(list(c['judge'].keys())); print('llm' in c['judge'])"
['channels','longlist_target','surface_threshold','include_borderline_in_sync',
 'batch_size','max_batches_per_run','reserve_calls_for_others','batch_chars_budget','max_field_chars']
False                                    ← judge.llm 이 없다
```

실재하는 유사 키는 다른 잡의 것이다: `meeting_mail.llm.enabled = False`(config.yaml:586-599).

**③실환경에서 무슨 일이 일어나나**

문서는 "LLM 이 안 불린다"는 **보증의 근거**로 이 키를 제시한다. 키가 없으므로
`config.get("judge",{}).get("llm",{}).get("enabled")` 는 `None`(falsey)이고, 아무도 이 값을
읽지 않는다. 즉 **존재하지 않는 스위치가 꺼져 있다는 이유로 안전하다고 말하고 있다.**

실제로는 `judge_job.py` 가 `kv_core.session`(= `claude -p`)을 매일 부른다. 어제 29콜 중
대부분이 judge 다. 문서가 걱정한 시나리오("크론이 매일 도는데 아무도 모른다")가 **이미
일어나 있고, 그걸 막는다던 스위치는 처음부터 없었다.**

**④수정 방향** :580-582 를 삭제하고 실제 통제 지점(`tasks.yaml` 의 `judge` task 존재 여부 ·
`workers.WIRED` · `max_batches_per_run: 20` · `resources.yaml` 의 claude 게이트)으로 대체.

**가역성** ✅ **구조 신호** ③진실이 두 곳 · ⑤검증이 그 축을 구조적으로 못 봄
(**문서가 인용한 config 키가 실존하는지 검사하는 장치가 없다** — 기계 검증이 가장 쉬운 축인데 비어 있다)

---

### F2-3 🔴 `mail-harness` 의 HARD-GATE #1 이 이미 깨져 있다 — 문서는 아직 미래형으로 경고한다

**①위치** `skills/mail-harness/SKILL.md:22-26` (HARD-GATE), `:133` (주의/한계)

**②실측 증거**

문서:

```
SKILL.md:22-26  <HARD-GATE>
1. **신·구 동시 운행 금지**: cutover 가 완료되기 전에는 apply·sync·drop 이 실행되는
   어떤 형태(크론·수동 run)도 시작하지 않는다. 예외 없음.
   … 컷오버 전 허용되는 것은 `--dry-run` 과 `triage`(읽기 전용) 뿐이다.
SKILL.md:133   **컷오버 전이다**: 지금 상태에서 `run`(non-dry)을 크론에 올리면 HARD-GATE #1 위반.
```

코드 — 이미 올라가 있다:

```
config/tasks.yaml:122-133   name: mail-triage / cadence: daily / runner: local
kv-core/kv_core/workers.py:183   subprocess.run([_PY, "triage/main.py", "run"], cwd=_MAIL_PROJ, …)
kv-core/kv_core/workers.py:195   "mail-triage": run_mail_triage,     ← WIRED

skills/mail-harness/project/triage/main.py:4-5
    apply  : 직전 계획을 실행 — 아카이빙 + 초안 생성.
    run    : triage + apply                      ← run 은 apply 를 포함한다
skills/mail-harness/project/triage/main.py:159-163
    if a == "archive":
        if not c["autonomy"]["archive_auto"]: … 건너뜀
        gmail.archive(svc, p["id"])              ← 인박스를 실제로 바꾼다
```

**③실환경에서 무슨 일이 일어나나**

HARD-GATE 는 `apply` 를 이름으로 지목해 금지했고, 크론에 올라간 것이 정확히 `apply` 를
포함하는 `run` 이다. 문서가 허용한 "triage(읽기 전용)"와 실제로 배선된 "`triage/main.py run`"은
**이름이 비슷하지만 다른 것**이다 — 전자는 스킬 이름, 후자는 서브커맨드.

다행히 아직 한 번도 실행되지 않았다(F1-4: 배선이 마지막 발화보다 3분 늦었다). **다음 발화가
HARD-GATE #1 의 첫 위반이 된다.** 지금이 되돌릴 수 있는 마지막 시점이다.

발송은 별개로 잠겨 있다(`messages().send` 리포 전체 0개, `send_enabled: false`) — 그래서
사고 등급은 "인박스 아카이브"까지고 외부로 나가는 것은 없다. 그러나 HARD-GATE 가 걱정한 것은
발송이 아니라 **원본 스킬과의 상태 공유(인박스·A1 커서·Bplan DB)** 였고, 그 위험은 그대로다.

**④수정 방향** 셋 중 하나를 **의식적으로** 고른다 —
(a) `references/cutover.md` 를 실행해 컷오버를 완료하고 HARD-GATE 를 해제,
(b) `mail-triage` 를 WIRED 에서 빼고 컷오버까지 대기,
(c) `run` → `triage`(계획만)로 낮추고 apply 는 사람이.
지금은 **아무도 고르지 않은 상태**가 배선돼 있다. 그게 문제다.

**가역성** ⚠️ 조건부 — 지금은 완전가역(미실행), 1회 실행 후에는 아카이브를 Gmail 에서
되돌려야 한다(가능하나 수동)
**구조 신호** ④고칠 때마다 새 게 나옴(W11 배선이 W5 의 게이트를 안 보고 진행됐다) ·
⑤검증이 그 축을 구조적으로 못 봄(HARD-GATE 는 산문이라 CI 가 읽지 않는다)

---

### F2-4 ⚠️ `mail-harness` description 이 6단 파이프라인을 약속하는데 2조각만 배선돼 있다

**①위치** `skills/mail-harness/SKILL.md:4-7` (frontmatter), `:17-18`

**②실측 증거**

```
SKILL.md:4-7   hello@ 메일 업무 **전체를 하나의 무인 파이프라인으로 실행한다**:
               selfcheck → 비플랜 sync → 트리아지 → 비플랜 drop → 무수정 발송률 측정 → 슬랙 다이제스트
SKILL.md:17-18 **상태(2026-07-17)**: … P3 섀도 병행 → P4 컷오버 → P6 발송 승격 순서로 **진행 중**
```

```
$ grep -rn "harness.py" ~/kv-automation --include="*.py" --include="*.plist*" | grep -v tests
(러너 0건 — WIRED 에도 없고 plist 도 없다)
$ ls -t ~/kv-data/mail-harness/harness/
digest_latest.md  run_20260717-112514.json  run_20260717-112440.json    ← 최신이 7/17
```

무인으로 도는 조각은 `mail-triage`(미실행) 와 `bplan-score` 둘뿐. 나머지 4단
(selfcheck · sync · drop · metrics/digest)은 **러너가 없다.**

**③실환경에서 무슨 일이 일어나나** F1-1 의 11일 정지와 같은 사건을 description 쪽에서 본
모습이다. "메일 업무의 유일한 진입점"이라 선언한 스킬이 실제로는 업무의 1/3 만 커버한다.
`:17-18` 의 "진행 중"은 27일째 같은 문장이다.

**④수정 방향** description 을 현재 커버 범위로 좁히고, 미배선 4단을 `tasks.yaml` 에 명시적
`status: slot` task 로 올린다 — **그러면 orrery 가 세어 준다.** 문서에만 있으면 안 세어진다.

**가역성** ✅ **구조 신호** ①같은 뿌리 3건+ · ③진실이 두 곳

---

### F2-5 ⚠️ `docs/task-registry-mirror.md` 가 전면 stale — 스케줄 task 9개 중 2개가 표에 없다

**①위치** `docs/task-registry-mirror.md:22-24, 43-51, 69`

**②실측 증거**

| 줄 | 문서 | 실제 |
|---|---|---|
| :48 | appstore-snapshot "✅ **GH primary + 로컬 shadow(컷오버 검증 중)**" | 로컬 primary, GH 은퇴 (`tasks.yaml:78,83` · `appstore-snapshot.yml:21-30` schedule 주석처리) |
| :46 | judge "🔶 배선 완료·**룰 대기**" | 룰 기입 완료 — `references/judge-criteria.md` 270줄, 8/11·8/12 판정 실행됨 |
| :50 | mail-triage "🔶 **컷오버전(W5)**" | WIRED 배선 완료 |
| :51,:69 | bplan "scoring **🔴**" / "judge-bplan 은 **미구현 슬롯**" | `bplan-score` task 존재·배선·실행 (`cadence/bplan-score.json` last_success 8/12) |
| :22-24 | "핵심 구조 변경: 판단·미팅메일을 **별도 task로 두지 않고** 부모 스킬에 스테이지로 흡수(사용자 결정)" | `judge`(:28)·`meeting-mail`(:42)·`bplan-score`(:103) **전부 별도 task** |
| :43-51 | 스케줄 task **7행** | tasks.yaml routine task **9개** — `meeting-mail`·`bplan-score` 행이 없다 |

**③실환경에서 무슨 일이 일어나나**

이 문서는 `config/tasks.yaml:2` 가 "설계 근거·전체 지도"로 지목하는 **유일한 설명서**다.
그 지도가 실제 영토와 6곳에서 다르고, :22-24 는 **사용자 결정이라고 표기된 설계 원칙이
번복된 사실을 기록하지 않았다**(judge 만 :69 에서 사후 정정, meeting-mail·bplan-score 는 방치).
설계 의사결정 이력이 여기서 끊긴다.

**④수정 방향** 이 문서를 **생성물**로 만든다 — `tasks.yaml` 에서 표를 렌더링하고 산문만
손으로 쓴다. 미러가 손으로 유지되는 한 이 발견은 재발한다(`registry.py:1` 이 이미
"코드 미러"라 부르는데 미러가 자동이 아니다).

**가역성** ✅ **구조 신호** ①같은 뿌리 3건+ · ④고칠 때마다 새 게 나옴 · ③진실이 두 곳

---

### F2-6 ⚠️ 런북 `00-bootstrap.md` 를 그대로 따르면 clone→run 이 실패한다 — W12 이식성 작업이 문서를 안 따라갔다

**①위치** `docs/runbook/00-bootstrap.md:37, 40, 110`

**②실측 증거**

```
:37   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
      → `-e ./kv-core` 누락 → import kv_core 실패.
        정본은 README.md:55 와 requirements.txt:8 주석: "pip install -r requirements.txt -e ./kv-core"

:110  `skills/*/com.kv.*.plist.template` 을 복사해 … ~/Library/LaunchAgents/ 에 둔다
$ ls skills/*/com.kv.*.plist.template
zsh: no matches found                    ← glob 0개 매치
   실제 경로는 한 단계 아래: skills/mail-harness/project/com.kv.mail-harness.plist.template

:40   스킬별 requirements.txt … [실측: 8개, _archive 2개 제외하면 6개]
$ find skills -name requirements.txt | wc -l
       7                                 ← 실측 7개(제외 시 5개)
```

**③실환경에서 무슨 일이 일어나나**

최신 커밋이 `feat(w12): 이식성 실측 — clone→run 을 재고, 문서 대신 테스트로 잠근다` 인데,
**부트스트랩 런북 자체는 그 실측을 반영하지 않았다.** "문서 대신 테스트로 잠근다"는 방침이
문서를 방치해도 된다는 뜻으로 작동했다 — 테스트(`_synthetic_portability.py`)는 통과하지만
사람이 읽는 절차는 3곳에서 틀렸다. 그리고 :110 때문에 **새 맥에서는 어떤 plist 도 설치되지
않는다**(F3-3 과 같은 뿌리).

**④수정 방향** :37 을 README:55 와 동일 문자열로 맞추고, :110 을 `config/launchd/` 순회로
바꾼다(F3-3 의 수정과 같은 작업). :40 의 "[실측]" 숫자는 명령으로 대체.

**가역성** ✅ **구조 신호** ⑤검증이 그 축을 구조적으로 못 봄 · ①같은 뿌리 3건+

---

### F2-7 🔴 `docs/runbook/03-disk-write.md` 가 백업이 사라진 뒤에도 "삭제해도 안전"이라 안내한다

**①위치** `docs/runbook/03-disk-write.md:23-24`

**②실측 증거**

```
03-disk-write.md:23-24
  90일 이전 스냅샷 — **GH data 브랜치에 사본이 있으므로 로컬 삭제 안전**
  find ~/kv-data/net-sourcing/snapshots -name "*.json" -mtime +90 -delete
```

그 백업은 은퇴했다.

```
.github/workflows/appstore-snapshot.yml:21-30    schedule: 블록 주석 처리(수동 dispatch 만)
config/tasks.yaml:83                             ✅ [2026-08-11] GH Actions **은퇴 완료**
```

**③실환경에서 무슨 일이 일어나나**

디스크가 찼을 때 사람이 여는 문서가 이것이다. 2026-08-11 이후 스냅샷은 **GH 사본이 없으므로**
이 명령은 90일 뒤부터 **영구 삭제**가 된다. 같은 워크플로 파일 5행이 경고한 바로 그 손실이다.
앱스토어는 과거 순위를 다시 주지 않는다(`tasks.yaml:72-73` 이 명시) — **복구 불가.**

지금 당장은 안전하다(은퇴가 8/11 이라 90일 경과분은 아직 전부 GH 에 있다). 위험은 2026-11-09
부터 시작된다. 이런 종류의 **시한부 오류**는 적대 리뷰가 원리적으로 못 잡는다 — 오늘 실행하면
정답이 나오기 때문이다.

**④수정 방향** 은퇴 커밋에서 같이 고쳤어야 했다. `-delete` 를 지우고 "이동 후 확인"으로
바꾸거나, 로컬 아카이브 대상을 명시한다.

**가역성** 🔴 **비가역** — 이 명령이 실행되면 그 스냅샷은 복구 수단이 없다
**구조 신호** ④고칠 때마다 새 게 나옴(은퇴는 했는데 은퇴에 의존하던 문서를 안 따라갔다) ·
⑤검증이 그 축을 구조적으로 못 봄(시한부 오류)

---

### F2-8 ⚠️ 잔여 문서 stale — 실환경 영향이 작은 것

| 파일:줄 | 문서 | 실제 |
|---|---|---|
| `skills/net-sourcing/SKILL.md:11` | "메인 크론은 꺼져 있고 **앱스토어 전용 크론($0)만 돈다**" | judge·meeting-mail·bplan-score 가 매일 돈다. $0 도 아니다(claude 구독 쿼터 소비) |
| `skills/net-sourcing/SKILL.md:493` | "돌고 있는 건 앱스토어 전용 크론 **2개뿐**" | 앱스토어 전용은 **1개**(`com.kv.net-sourcing.appstore`) |
| `skills/net-sourcing/SKILL.md:102` | "config를 $29로 맞추라는 경고가 **매 실행 찍힌다 — 아직 안 맞췄다**" | `resources.yaml:27` 은 이미 29.0. `gate.py:244` 는 `our_cap > platform_cap` 일 때만 경고 → 29==29 면 **발생 불가**. 없는 경고를 기다리고 있다 |
| `config/tasks.yaml:26` | "meeting-mail … **리포 미이식**" | 이식·배선 완료(`meeting_mail_job.py` · WIRED · 별도 task) |
| `README.md:35` | "`.github/` 상시 실행이 필요한 잡" | GH 에 상시 실행 잡이 더 이상 없다 |
| `README.md:25` | "`gate/job/health` **골격** — 본체는 W2/W3/W6" | 이미 본체(gate 15KB · job 29KB · health 32KB). 구조 블록에 `scheduler`·`registry`·`approval`·`orrery`·`workers`·`config/` 가 통째로 빠져 있다 |
| `docs/runbook/README.md:28` | "합성 스위트 **14본**" | `kv-core/tests/_*.py` **20개**, CI 는 `_synthetic_slice` 만 빼고 **19본** 실행 |
| `docs/adr/0003:32,113` | "`core/llm.py` 는 **은퇴하고**" | 두 파일 다 존재(16.7KB · 5.0KB), `requirements.txt:14` 에 `anthropic>=0.40.0` 잔존. ADR 이 선언한 은퇴가 코드에서 미실행 |
| `skills/ai-consumer-pulse/SKILL.md:5,118` | "**주 1회** 수집·분석" | 주 1회로 도는 장치 없음(task·plist·crontab 전무). 다이제스트는 `2026-W29`·`W30` 2개뿐, W31~W33 없음(raw 는 W32 까지 수집됨) |

---

## 축 ③ 죽은 참조

### F3-1 🔴 `appstore-rank` 스킬은 실행 명령의 파이썬 경로가 죽어 있다 — 첫 줄부터 실패한다

**①위치** `skills/appstore-rank/SKILL.md:19, 25-28`

**②실측 증거**

```
skills/appstore-rank/SKILL.md:14   PROJ=~/kv-automation/skills/net-sourcing/project
skills/appstore-rank/SKILL.md:19   cd "$PROJ" && .venv/bin/python tools/board.py --port 8000 --genre 36

$ ls -d ~/kv-automation/skills/net-sourcing/project/.venv
ls: ...: No such file or directory
```

venv 는 리포 루트에 하나뿐이다(`~/kv-automation/.venv`). `workers.py:28` 은 이걸 알고
`_PY = paths.repo_root() / ".venv" / "bin" / "python"` 로 절대경로를 쓴다 — 즉 **코드는 맞고
스킬 문서만 틀렸다.** 같은 오류가 "수동 명령(참고)" 4줄에 반복된다(SKILL.md:25-28).

**③실환경에서 무슨 일이 일어나나** 이 스킬은 "설명만 하지 말고 아래를 실제로 실행하라"
(SKILL.md:8)고 지시한다. 그 실행이 `zsh: no such file or directory` 로 죽는다. **스킬의
존재 목적(보드 띄우기) 전체가 실패한다.**

**④수정 방향** 4곳의 `.venv/bin/python` → `~/kv-automation/.venv/bin/python`.

**가역성** ✅ **구조 신호** ③진실이 두 곳(문서의 경로 vs `paths.repo_root()`)

---

### F3-2 🔴 `appstore-rank` SKILL.md 가 은퇴한 plist 를 "없으면 재등록하라"고 지시한다 — 따르면 이중 수집이 생긴다

**①위치** `skills/appstore-rank/SKILL.md:35-39` · `skills/appstore-rank/install-cron.sh:10,19,27`

**②실측 증거**

```
skills/appstore-rank/SKILL.md:35
  변동값(Δ)은 매일 자동 스냅샷(launchd `com.kv.appstore-rank-snapshot`, 매일 05:10)이 …
skills/appstore-rank/SKILL.md:39
  없거나 꺼져 있으면 `~/kv-automation/skills/appstore-rank/install-cron.sh` 를 실행해 (재)등록한다.

$ launchctl list | grep appstore-rank
(출력 없음)                                    ← 지시문의 조건이 지금 참이다
```

그 스크립트가 무엇을 하는지:

```
install-cron.sh:19   <key>Label</key><string>com.kv.appstore-rank-snapshot</string>
install-cron.sh:23   <string>$PROJ/tools/board.py</string>
install-cron.sh:24   <string>--snapshot-all</string>
install-cron.sh:27   <dict><key>Hour</key>5<key>Minute</key>10</dict>
install-cron.sh:35   launchctl load -w "$PLIST"
```

같은 일을 이미 스케줄러가 한다:

```
kv-core/kv_core/workers.py:82   subprocess.run([str(_PY), "tools/board.py", "--snapshot-all"], cwd=_PROJ, …)
config/tasks.yaml:87            이제 이 task 의 유일한 실행 주체는 로컬 launchd com.kv.scheduler 다.
```

**③실환경에서 무슨 일이 일어나나**

`com.kv.appstore-rank-snapshot` 은 은퇴했는데 **문서가 그 사실을 모르고, 부재를 "고장"으로
읽어 재설치를 시킨다.** 지시를 따르면 05:10 에 스냅샷을 한 벌 더 돌린다 —
`config/tasks.yaml:87` 이 "유일한 실행 주체"라 못 박은 문장이 그 순간 깨지고,
**불변규칙 01(이중실행 금지) 위반**이 문서 지시로 발생한다.

애플 SSR 은 429 를 준다(8/12 로그에 실제로 `429 Too Many Requests` 5회 → RSS 폴백). 05:10 과
09:00 이 같은 84차트를 긁으면 레이트리밋 압력이 배가되고, **정상 실행 쪽이 폴백으로 밀려
데이터 품질이 떨어진다.** 비용이 아니라 품질로 청구되는 이중실행이라 더 안 보인다.

부수적으로 `install-cron.sh:11` 은 로그를 `$PROJ/data/snapshot.log` 에 쓰는데,
`skills/net-sourcing/project/data` 는 `~/kv-data/net-sourcing` 심볼릭 링크다 — 우연히
맞지만, `paths.py` 의 "경로 리터럴을 코드에 두지 않는다"는 규율 밖이다.

**④수정 방향** SKILL.md:35-39 를 "스냅샷은 `com.kv.scheduler` 의 `appstore-snapshot` task 가
담당한다"로 고치고, `install-cron.sh` 를 `_archive/` 로 내리거나 실행 시 loud 거부하게 한다
(net-sourcing 의 `core/scheduler.py:37-41` 이 이미 그 패턴을 갖고 있다 — **거기서만 하고 여기선
안 했다**).

**가역성** ✅ (문서 + 스크립트 은퇴)
**구조 신호** ①같은 뿌리 3건+ (은퇴한 실행체를 가리키는 문서 3건: F3-2·F3-3·F3-4) ·
④고칠 때마다 새 게 나옴 (컷오버는 했는데 컷오버를 가리키는 문서를 안 따라갔다)

---

### F3-3 🔴 `com.kv.mail-harness.plist` — 존재하지 않는 실행체를 2곳이 가리킨다

**①위치** `skills/mail-harness/references/pipeline.md:44` · `config/tasks.yaml:154`

**②실측 증거** F1-1 참조. 템플릿(`skills/mail-harness/project/com.kv.mail-harness.plist.template`)
은 있고 설치본은 없으며 `config/launchd/` 에도 없다.

**③실환경에서 무슨 일이 일어나나** F1-1 의 11일 정지. 여기서 추가로 짚을 것은
**부트스트랩 경로의 구멍**이다 — `config/launchd/` 는 2개만 담고 있어서, 새 맥에서
`docs/runbook/00-bootstrap.md` 를 따라도 `com.kv.net-sourcing.appstore`(실제로 돌고 있는 것)와
`com.kv.mail-harness`(돌아야 하는 것) **둘 다 설치되지 않는다.** W12 커밋
(`feat(w12): 이식성 실측 — clone→run 을 재고`)이 이식성을 다뤘는데, 이식성 테스트는
"clone 후 코드가 도는가"를 보지 "clone 후 **스케줄이 사는가**"를 보지 않는다.

**④수정 방향** 설치되어야 할 plist 전량을 `config/launchd/` 로 모으고, 부트스트랩이
`ls config/launchd/*.plist` 를 순회하도록 한다. 그러면 "선언된 실행체 = 리포에 있는 파일"이
되어 F1-1 같은 유령 plist 가 원리적으로 못 생긴다.

**가역성** ✅ **구조 신호** ③진실이 두 곳 · ⑤검증이 그 축을 구조적으로 못 봄

---

### F3-4 ⚠️ `com.kakaoventures.net-sourcing` / `com.kv.net-sourcing.daily` — 둘 다 없는데 코드가 "현재 로드된 것"이라 단언한다

**①위치** `skills/net-sourcing/project/core/scheduler.py:11-19` · `skills/net-sourcing/SKILL.md:583-585`
· `skills/net-sourcing/project/SKILL.snapshot.md:409`

**②실측 증거**

```
core/scheduler.py:16   #    · com.kv.net-sourcing.daily  — install-cron.sh, **매일 08:00** (현재 로드된 것)
core/scheduler.py:11   LABEL = "com.kakaoventures.net-sourcing"

$ launchctl list | grep -E "kakaoventures|net-sourcing.daily"
(출력 없음)                    ← 둘 다 로드돼 있지 않다
$ ls ~/Library/LaunchAgents/ | grep -E "kakaoventures|daily"
(출력 없음)                    ← 파일도 없다
```

`main.py schedule install` 은 이 죽은 라벨의 부재를 근거로 설치를 **허용**한다
(`core/scheduler.py:37-41`: `if _loaded(DAILY_LABEL): 거부`).

**③실환경에서 무슨 일이 일어나나**

주석의 "(현재 로드된 것)" 은 거짓이므로, 이 파일을 읽는 사람은 **없는 잡을 있다고 믿는다.**
더 중요한 건 가드가 헛돈다는 것 — `_install()` 은 `com.kv.net-sourcing.daily` 만 확인하고
**지금 실제로 도는 `com.kv.net-sourcing.appstore` 는 안 본다.** `main.py schedule install` 을
치면 매주 화 08:00 에 `--channel all`(스레드+링크드인, 회당 $3~8) 잡이 설치되고,
07:00 앱스토어 잡과 별개로 돈다. 이중 과금 가드가 **은퇴한 라벨을 지키느라 현역 라벨을 놓쳤다.**

**④수정 방향** `DAILY_LABEL` 검사를 `com.kv.net-sourcing.*` 접두 검사로 넓히고, 주석의
"(현재 로드된 것)" 을 실측 명령으로 대체한다. 또는 이 서브커맨드 자체를 은퇴시킨다
(kv-core 스케줄러가 정본이므로 스킬 자체 스케줄러는 존재 이유가 사라졌다).

**가역성** ✅ **구조 신호** ①같은 뿌리 3건+ · ②단위·소유가 자원과 안 맞음(과금 자원의
소유가 스킬 스케줄러와 kv-core 스케줄러로 갈라져 있다)

---

### F3-5 ⚠️ `kv_core/notion.py` — 206줄이 아무 데서도 호출되지 않는 데드코드

**①위치** `kv-core/kv_core/notion.py` (전체)

**②실측 증거**

```
$ grep -rn "kv_core import notion\|kv_core\.notion\|from \.notion\|from \. import notion" \
    --include="*.py" ~/kv-automation | grep -v ".venv"
kv-core/kv_core/notion.py:20:    from kv_core import notion       ← 자기 독스트링의 사용 예시 1줄
```

프로덕션 호출 **0건**. 테스트도 1본만 스친다. 실제 노션 접근은 전부
`skills/net-sourcing/project/core/notion.py`(1,375줄)와 `skills/mail-harness/project/bplan/main.py`
가 한다. (내용 대조는 축 ④ 참조.)

**③실환경에서 무슨 일이 일어나나**

지금은 무해하다. 위험은 **다음에 쓰는 사람**에게 있다 — `kv_core` 안에 노션 클라이언트가
있으면 새 kv-core 잡을 짤 때 자연스럽게 이걸 임포트한다. 그런데 이 206줄은 1,375줄이
7주간 축적한 것(레이트리밋 재시도·페이지네이션·property 타입 처리·2단 dedup)을 갖고 있지
않다. **"플랫폼에 있으니 정본이겠지"라는 합리적 추론이 틀린 답으로 간다.**

**④수정 방향** 삭제하거나, 파일 상단에 "이것은 정본이 아니다 — `net-sourcing/core/notion.py`
를 써라"를 박는다. 삭제가 낫다(코드는 git 에 남는다).

**가역성** ✅ 완전가역 **구조 신호** ③진실이 두 곳

---

### F3-6 ⚠️ `judge_strikes.json` — 디스크의 스키마와 코드의 스키마가 다르다. 3건이 매 읽기마다 조용히 버려진다

**①위치** `~/kv-data/net-sourcing/judge_strikes.json` ·
`skills/net-sourcing/project/jobs/judge_job.py:100-128`

**②실측 증거**

디스크(2026-08-11 23:53 기록):

```
$ cat ~/kv-data/net-sourcing/judge_strikes.json
{"00000000-0000-0000-0000-72484c7a05d4": 1,
 "00000000-0000-0000-0000-fb6952211cff": 1,
 "00000000-0000-0000-0000-ee02e24cf8fd": 1}          ← 값이 int
```

코드는 `[횟수, 마지막실패일]` 2원소 리스트만 받는다:

```
judge_job.py:100   def _strikes() -> dict:
judge_job.py:105       """page_id → [횟수, 마지막 실패일]. 오래된 것은 흘려보낸다."""
judge_job.py:125       if isinstance(v, list) and len(v) == 2 and str(v[1]) >= cutoff:
judge_job.py:126           out[pid] = v                 ← int 는 조건에 걸려 전부 탈락
judge_job.py:132   return int(v[0]) if isinstance(v, list) else 0     ← int 면 0 반환
```

**③실환경에서 무슨 일이 일어나나**

세 행의 스트라이크가 **매 읽기마다 통째로 버려진다.** 방향은 fail-open(0으로 읽힘)이라
"멀쩡한 행이 영구 격리"되는 반대 사고는 안 난다 — 여기까지는 다행이다.

문제는 **브레이크가 안 걸린다**는 것. 이 세 행이 계약 위반을 반복해도 `_MAX_STRIKES`(3)에
영원히 도달하지 않고, 매일 큐에 다시 들어가 claude 호출을 태운다. `judge` 는 어제 하루
**20배치 상한을 다 쓰고 "잔여 ~741건"** 을 남겼다 — 큐가 이미 포화라 낭비가 그냥 낭비가
아니라 다른 행의 자리를 빼앗는다.

그리고 이건 W12 적대 리뷰가 못 잡은 전형이다. `_synthetic_judge_paths.py:218` 은
**신 스키마만 써서** 테스트한다 — 마이그레이션 안 된 실파일이 있다는 사실은 합성 픽스처가
구조적으로 만들 수 없다.

**④수정 방향** `_strikes()` 에 구스키마 승격을 넣는다(`isinstance(v, int)` → `[v, today]`),
또는 파일을 1회 마이그레이션. 전자가 낫다 — 다음 스키마 변경 때도 산다.

**가역성** ✅ (fail-open 이라 최악도 현상 유지)
**구조 신호** ③진실이 두 곳(디스크 스키마 vs 코드 스키마) · ⑤검증이 그 축을 구조적으로
못 봄(합성은 항상 최신 스키마를 쓴다)

---

### F3-7 ✅ 확인했으나 발견 없음 — 죽지 않은 참조들

빠짐없이 대조했고 **실존이 확인된** 것들을 기록해 둔다(다음 감사가 다시 안 파도록):

| 참조 | 위치 | 실측 |
|---|---|---|
| `board.py` 위치 | `skills/appstore-rank/SKILL.md:32` | ✅ `net-sourcing/project/tools/board.py` 실존 |
| `rank_lookup.py` | `skills/appstore-rank/SKILL.md:33` | ✅ 실존 |
| `com.kv.scheduler` | 런북 4곳 | ✅ 설치·로드됨 |
| `com.kv.approvals` | 런북·plist | ✅ 설치·로드됨 |
| 리포 plist ↔ 설치본 | `config/launchd/*` | ✅ `diff` 결과 2본 모두 **완전 일치**(드리프트 없음) |
| `bplan/main.py:query_to_drop_pages` | `config/tasks.yaml:117` | ✅ 실존 |
| `#Squad-비플랜` | 23곳 | ✅ 표기 일관(`#Squad-비플랜` 단일) |
| naive ts 처리 | `health.py:_ts_to_utc_date` | ✅ naive→KST 가정 후 UTC 변환 — writer(`pipeline.py:146` naive KST)와 **일치** |

---

## 축 ④ 진실이 두 곳인 것

**전수 조사했다.** 클라이언트·커서/원장·어휘/상수·기준 문서·설정 5범주에서 중복 **18건**을
찾았고, 각각에 대해 **내용을 실제로 비교해** 갈라짐 여부를 판정했다 —
**갈라짐 확정 13건 · 중복이나 아직 일치 5건.**

"아직 일치"도 기록한다. 그게 이 축의 핵심이다: **갈라지지 않은 중복은 안전한 게 아니라
아직 안 터진 것**이고, 어느 쪽이 정본인지 코드에 표시가 없으면 다음 수정이 반반 확률로 틀린다.

---

### F4-1 🔴 Tier 어휘가 9곳에 있고 3값과 4값이 섞여 있다 — 같은 사고가 이미 한 번 났다

**①위치** (9곳)

| 위치 | 값 |
|---|---|
| `skills/net-sourcing/project/tools/notion_migrate.py:56` `TIER_OPTIONS` | `["Catch","Trend","Wait","Cut"]` |
| `config/contracts.yaml:29` `judge-tier.verdict.enum` | `[Catch, Trend, Wait, Cut]` |
| `skills/net-sourcing/references/judge-criteria.md:17` | `Catch \| Trend \| Wait \| Cut` |
| `skills/net-sourcing/SKILL.md:543` | `Tier ≠ Cut` (4값 전제) |
| **`skills/net-sourcing/project/core/llm.py:227`** | **`["Catch","Trend","Wait"]`** ← Cut 없음 |
| **`skills/net-sourcing/project/core/report.py:57`** | **`{"Catch":0,"Wait":1,"Trend":2}`** ← Cut 없음(→3 폴백) |
| **`skills/net-sourcing/references/notion-schema.md:13`** | **`Catch \| Trend \| Wait`** |
| **`skills/net-sourcing/references/notion-schema.md:89-92`** "Tier 정의" | **3개만 정의** |
| **`skills/net-sourcing/SKILL.md:434`** | **`Tier`(Catch/Trend/Wait)** ← 3값 |

**②실측 증거 — 이미 갈라져 있나: YES**

`SKILL.md` 는 **자기 안에서 갈라져 있다** (:434 는 3값, :543 은 4값).

두 곳이 각자 "여기가 정본과 일치해야 한다"고 주장하는데 서로 다르다:

```
notion_migrate.py:54-55
  #   (문서가 아니라 라이브 스키마가 정본이다) `Cut` 판정이 전부 거부되거나 조용히 버려진다.
  #   → **Tier를 늘리면 여기도 같이 늘려라.** 한쪽만 고치면 다음 마이그레이션이 되돌린다.
notion_migrate.py:56   TIER_OPTIONS = ["Catch", "Trend", "Wait", "Cut"]

core/llm.py:227
  "tier": {"type":"string","enum":["Catch","Trend","Wait"]},   # 노션 Tier select 옵션과 **정확히** 일치해야 한다
```

**두 주석 모두 "정확히 일치해야 한다"고 쓰고, 서로 일치하지 않는다.**

그리고 이 병은 이미 사고를 냈다 — 리포가 스스로 기록해 놨다:

```
skills/mail-harness/references/bplan-criteria.md:9-13
  2026-08-11 그물망 judge에서 문서 상단 어휘표에 라벨 하나를 빠뜨렸더니
  그 라벨로 갈 길이 막혀 348건이 종단 라벨로 쏠렸다
skills/net-sourcing/references/judge-criteria.md:82
  397건 중 Cut 348(87.7%)
```

**③실환경에서 무슨 일이 일어나나**

지금 무인 judge 가 매일 노션에 Tier 를 쓴다(어제 20배치). 판정 경로에 따라:
- `judge_job.py` → `contracts.yaml`(4값) 검증 → Cut 통과 ✅ (어제 실적에 `Threads/Cut:14` 등)
- `core/llm.py` 경로(`report`·구 파이프라인) → enum 3값 → **Cut 을 낼 수 없다**
- `report.py:57` → Cut 이 정렬키 3으로 폴백 → 롱리스트 맨 뒤로 뭉친다
- 누군가 `notion_migrate.py` 를 돌리면 라이브 스키마를 4값으로 되돌린다 —
  `notion-schema.md`(3값)를 정본으로 믿고 스키마를 손보면 **다음 마이그레이션이 되돌린다**
  (주석이 예고한 그대로)

**어휘가 판정 규칙보다 강하다.** 라벨 하나의 유무가 87.7% 쏠림을 만들었고, 그 구조가
아직 9곳에 그대로 있다.

**④수정 방향** Tier 어휘를 **`config/contracts.yaml` 단일 정본**으로 확정하고(이미 검증에
쓰이므로 자연스럽다), `llm.py`·`report.py`·`notion_migrate.py` 가 거기서 읽게 한다.
문서 3곳은 값을 적지 말고 정본을 가리킨다. CI 에 "어휘 리터럴이 contracts.yaml 밖에
나타나면 실패" 검사를 넣으면 재발이 원천 차단된다.

**가역성** ✅ (Tier 값은 노션에서 되돌릴 수 있다)
**구조 신호** ③진실이 두 곳(9곳) · ①같은 뿌리 3건+ · ④고칠 때마다 새 게 나옴
(2026-08-11 에 한 번 고쳤는데 3곳만 고쳤다) · ⑤검증이 그 축을 구조적으로 못 봄

---

### F4-2 🔴 월 예산 상한 $45 vs $29 — "이중 진실원 종식" 선언 후에도 skill 쪽이 그대로다

**①위치** `config/resources.yaml:27` (29.0) vs `skills/net-sourcing/project/config.yaml:150` (45.0)

**②실측 증거 — 이미 갈라져 있나: YES**

```
$ grep -n "monthly_budget_usd" config/resources.yaml skills/net-sourcing/project/config.yaml
config/resources.yaml:27                       monthly_budget_usd: 29.0
skills/net-sourcing/project/config.yaml:150    monthly_budget_usd: 45.0

config/resources.yaml:24
  # [중간점검 E3, 2026-08-05] 45.0 → 29.0 — **이중 진실원 종식.**
```

종식되지 않았다. 그리고 **$45 는 사문이 아니다** — 도달 가능한 경로가 있다:

```
skills/net-sourcing/project/core/pipeline.py:228
  cap = _v.ceiling_usd or float((cfg.get("apify") or {}).get("monthly_budget_usd") or 0)
                       ↑ falsey 면 $45 로 폴백

kv-core/kv_core/gate.py:230-237
  live = probe() if probe else None
  if live is None:
      v.warnings.append("… 월 가드가 이번 실행에 적용되지 않는다")
      return v                    ← ceiling_usd 가 None 인 채로 반환
```

즉 **kv_core.gate 의 Apify 사용량 조회가 실패하면 상한이 $29 → $45 로 조용히 올라간다.**
그 조회는 지금 이 환경에서 실제로 실패한다:

```
$ .venv/bin/python -m kv_core.orrery --no-net | grep apify
║  apify : 조회 불가(토큰 없음/오프라인)
```

(단, 그 경우 `pipeline.py:234` 의 `if used is None: return` 이 가드를 통째로 건너뛰므로
**$45 가 물리려면 두 usage 조회가 서로 다르게 성공/실패해야 한다** — `gate` 는
`_USAGE_PROBES["apify"]`, pipeline 은 `apify.usage_usd()` 로 **서로 다른 클라이언트**를 쓴다.
조회원이 두 개라는 사실 자체가 이 조건을 가능하게 만든다.)

**③실환경에서 무슨 일이 일어나나**

`resources.yaml:14-17` 이 직접 이름 붙인 위험 — *"$29~$45 구간은 가드가 있다고 믿지만
실제로는 없는 구간"* — 이 **제거된 게 아니라 조건부가 됐다.** 평시엔 안 보이고 조회가
어긋난 실행에서만 열린다. 그리고 Apify STARTER 실한도가 $29 이므로, $29 를 넘긴 실행은
**돈은 쓰고 결과는 반쪽**이 된다(플랫폼이 중간에 끊는다).

**④수정 방향** `config.yaml:150` 을 지운다(폴백을 없앤다). 폴백이 필요하면 상한이 아니라
**0(=차단)** 이 안전한 기본값이다. 그리고 usage 조회원을 하나로 합친다.

**가역성** 🔴 **비가역** — 초과분은 이미 청구된 돈이다
**구조 신호** ③진실이 두 곳 · ②단위·소유가 자원과 안 맞음(같은 Apify 계정을
net-sourcing 과 ai-consumer-pulse 가 공유하는데 상한 선언이 스킬별로 있다) ·
④고칠 때마다 새 게 나옴

---

### F4-3 🔴 폐기된 노션 DB UUID 가 코드 주석 2곳 · 문서 2곳에 정본처럼 남아 있다

**①위치**

| 위치 | 값 | 성격 |
|---|---|---|
| `skills/net-sourcing/project/config.yaml:55` | `10fb616a-…` | ✅ **정본** |
| `skills/net-sourcing/project/core/notion.py:3` (모듈 docstring) | `ec585ca6-…` **(확인됨)** | 🔴 폐기 |
| `skills/net-sourcing/project/core/notion.py:746` | `그물망 소싱 DB(36c0ac54)에 착지` | 🔴 폐기 |
| `skills/net-sourcing/references/notion-schema.md:22` | `36c0ac54-…` | 🔴 폐기 |
| `skills/net-sourcing/references/notion-schema.md:60` | `ec585ca6-…` | 🔴 폐기 |
| `skills/net-sourcing/project/.env.example:5` | `극초기 DB(ec585ca6…)에 연결해야 200` | 🔴 폐기 |

**②실측 증거 — 이미 갈라져 있나: YES**

리포가 이 UUID 들을 이미 사망 선고했다:

```
skills/net-sourcing/SKILL.md:135
  ⚠️ 옛 문서의 `EXAMPLE_ID`/`EXAMPLE_ID`는 **UUID 오기로 404였다**(2026-07-12 확정). 정본은 config.yaml 뿐이다.
skills/net-sourcing/references/notion-schema.md:5
  > ⚠️ 이 문서의 옛 ID(36c0ac54 / ec585ca6)는 **폐기됐다.** 정본은 config.yaml 이다.
```

그런데 **`notion-schema.md` 는 5행에서 폐기를 선언하고 22행·60행에서 그 값을 다시 쓴다.**
그리고 `core/notion.py:3` 은 폐기된 값 옆에 **"(확인됨)"** 이라고 적어 놓았다.

같은 파일 안 이중선언도 있다:

```
skills/net-sourcing/project/config.yaml:83
  startup_db_id: "32c0ac54-…"   # ⚠️ 이름은 startup인데 **실제 값은 Bplan 3.0**이다(역사적 혼동).
                                 #    아래 dedup_db_ids가 정본.
skills/net-sourcing/project/config.yaml:91
  - {label: "Bplan 3.0 DB", id: "32c0ac54-…"}
```

**③실환경에서 무슨 일이 일어나나**

`.env.example:5` 가 특히 나쁘다 — **신규 설치자가 읽는 파일**이고, 지시대로 하면 404 나는 DB 에
integration 을 연결하고 정작 필요한 DB 에는 안 한다. 그러면 첫 실행이 403/404 로 죽는데,
원인이 "권한 없음"으로 보여서 진단이 엉뚱한 데로 간다.

`core/notion.py:3` 의 "(확인됨)" 은 더 교묘하다 — 코드를 읽는 사람에게 **가장 신뢰도 높은
신호**(모듈 docstring + 확인 표기)를 주면서 틀린 값을 준다.

**④수정 방향** 폐기 UUID 5곳 전부 삭제(값을 안 적고 `config.yaml` 을 가리킨다).
`config.yaml:83` 의 `startup_db_id` 는 이름을 고치거나 제거. CI 에 "폐기 UUID 문자열이
리포에 나타나면 실패" 검사 1줄이면 영구 봉인된다.

**가역성** ✅ **구조 신호** ③진실이 두 곳 · ①같은 뿌리 3건+

---

### F4-4 🔴 비플랜 판정 기준이 6곳에 있고 Orange 기준선이 29% vs 7% 로 4배 갈라져 있다

**①위치** `~/Desktop/BPLAN/비플랜_판단룰_v1.md` 외 4개 vs
`skills/mail-harness/references/bplan-criteria.md`

**②실측 증거 — 이미 갈라져 있나: YES (수치·규칙 양쪽)**

| 항목 | Desktop `비플랜_판단룰_v1.md` | 리포 `bplan-criteria.md` |
|---|---|---|
| Orange 기준선 | `:197,200,201,204` — "Evan 은 28건 중 **29%(8건)** 를 Orange 로 유지", "**Evan 기준선 ~29% 를 참고하되**" | `:190-201` — 122건 중 8건 = **7%**, 전체 499건 중 1.6%. 그리고 명시적 반박: *"🔴 이전 룰 문서는 Orange 기준선을 **29%** 로 적었다. 그건 28건짜리 비무작위 표본에서 나온 값이고, 전체 122건 기준 **실제는 7%** 다. **4배 차이다.**"* |
| HG-1 임계값 | `:54` — "…**셋 중 2개 이상이면 칼드랍, 1개면 Red 후보로 강등**" | `:35-46` — **카운팅 규칙이 통째로 없다.** "어느 하나로도 칼드랍하지 마라"만 남음 |
| HG-2 도메인 | `:` — 키워드에 **`onchain/crypto` 포함**, "매칭 시 트랙션 무시하고 칼드랍" (같은 문서 위쪽 [중요 수정]은 *"크립토는 제외한다"* — **문서 내부 모순**) | crypto 를 임계값에서 제거, 경고문으로만 |
| HG-2 예시 | "Charting → 칼드랍(크립토 온체인)" (같은 문서가 곧바로 *"good_example 으로 지정한 Charting·PrediX 조차 실제 점수는 Red"* 라 반박) | Charting 예시 없음 |
| CR-2b TAM 하드캡 | `:104` 300억/1,000억 | `:110-127` 동일 ✅ **일치**. 단 근거딜 실명(바로봄·체크크립토·내딛음·위시스트·비타니마) 이 리포본에서 **삭제** — "왜 이 임계값인가"가 한쪽에만 |

무인 판정자가 읽는 것은 **리포 사본 하나뿐**이다:

```
skills/mail-harness/project/jobs/bplan_score_job.py:52
  CRITERIA = PROJ.parent / "references" / "bplan-criteria.md"
$ grep -rn "Desktop/BPLAN" ~/kv-automation --include="*.py"
(0건 — 코드는 Desktop 문서를 한 번도 안 읽는다)
```

**③실환경에서 무슨 일이 일어나나**

**기계는 리포본을 읽고 사람은 Desktop 본을 연다.** Desktop 쪽이 54KB 로 더 크고 7/30 수정으로
더 최신처럼 보인다. 사람이 거기서 룰을 고치면 **판정에 아무 영향이 없다** — 그런데 고친
사람은 반영됐다고 믿는다. 반대로 사람이 Desktop 의 29% 기준으로 무인 판정 결과를 검수하면
**Orange 가 4배 적게 나온 것을 "판정기가 보수적"이라 오독**한다.

`bplan-score` 는 정확도가 전량-Red 기준선(73%)을 아직 못 넘었고(`tasks.yaml:119`), 그 튜닝을
위해 매일 산출물을 쌓는 중이다. **기준선 숫자가 4배 틀리면 튜닝 자체가 잘못된 목표를 향한다.**

HG-1 의 "셋 중 2개 이상" 카운팅 규칙 소실은 더 직접적이다 — 리포본만으로는 **판정을 재현할
수 없다.** 왜 이 건이 칼드랍이고 저 건이 Red 인지 설명하는 규칙이 리포에 없다.

**④수정 방향** Desktop 5개 파일을 리포로 흡수하거나(권장), 흡수가 어려우면 각 파일 상단에
**"⚠️ 이 문서는 실행되지 않는다. 정본은 `skills/mail-harness/references/bplan-criteria.md`"**
를 박는다. HG-1 카운팅 규칙은 리포본에 복원한다.

**가역성** ⚠️ 부분가역 — 판정 결과는 `점수` 컬럼만 쓰므로 되돌릴 수 있다(`tasks.yaml:115`
안전선). 그러나 **잘못된 기준선으로 몇 주 튜닝한 시간은 안 돌아온다**
**구조 신호** ③진실이 두 곳(6곳) · ⑤검증이 그 축을 구조적으로 못 봄
(리포 밖 파일은 어떤 CI 도 안 본다) · ②단위·소유가 자원과 안 맞음

---

### F4-5 🔴 "이 사람을 언제 확인했나" 가 3곳에 기록되고 3주 벌어져 있다

**①위치**
- 로컬 `~/kv-data/net-sourcing/followup_state.json` (쓰기 `core/followup.py:36-37`)
- 노션 `Last Checked` property (쓰기 `tools/backfill_bio.py:179`, `tools/recheck.py:16`)
- 잡 커서 `~/kv-data/net-sourcing/jobs/<job>/cursor.json` `done_keys` (쓰기 `kv_core/job.py:434-501`)

**②실측 증거 — 이미 갈라져 있나: YES**

```
$ head -c 200 ~/kv-data/net-sourcing/followup_state.json
"li:chriswdore": {"last_checked":"2026-07-21","next_due":"2026-08-20", …}
$ stat -f '%Sm' ~/kv-data/net-sourcing/followup_state.json
Jul 27                                    ← 3주 전에 멈춤

$ tail -1 ~/kv-data/net-sourcing/followup_events.jsonl | cut -c1-40
{"ts": "2026-08-12…                       ← 이벤트는 8/12 까지 append 중
```

그리고 워터마크와 잡 커서가 같은 날 서로 다른 말을 한다:

```
$ cat ~/kv-data/net-sourcing/cadence/judge.json
{"last_attempt":"2026-08-12","last_success":"2026-08-12","first_attempt":"2026-08-04"}
$ cat ~/kv-data/net-sourcing/jobs/judge-2026-08-12/cursor.json
{"done":0,"skipped":0,"done_keys":[]}
$ cat ~/kv-data/net-sourcing/jobs/judge-2026-08-12/state.json
{"state":"completed","updated":"2026-08-12T00:58:41Z"}
```

**"성공했다"(워터마크) 와 "한 건도 처리 안 했다"(커서) 가 같은 날짜에 동시에 참이다.**
`config/contracts.yaml:15` 의 `min_records: 1` 계약과도 어긋난다. (실제로는 그날 20배치
593건을 처리했다 — 즉 **커서가 실적을 기록하지 못했다.**)

같은 계열이 하나 더:

```
$ cat ~/kv-data/net-sourcing/judge_strikes.json      # 3개 page_id 에 시도 기록 있음
$ cat ~/kv-data/net-sourcing/jobs/judge-2026-08-12/cursor.json   # done_keys: []
```

**③실환경에서 무슨 일이 일어나나**

"이 대상을 이미 처리했나"라는 **하나의 질문에 저장소가 3개**다. 재개(resume)·중복방지·
재관찰 주기 판단이 각각 다른 파일을 본다:
- `followup.py` 는 `followup_state.json` 을 보고 "3~4개월 재확인" 주기를 판정 → 7/21 에 멈춰
  있으므로 **전원이 8/20 에 한꺼번에 due 가 된다**(Apify 비용 폭발 경로)
- `recheck`·`backfill_bio` 는 노션 `Last Checked` 를 쓴다 → 로컬은 모른다
- `job.py` 의 커서는 잡 내부 재개용인데 지금 비어 있어 **크래시 후 재개가 처음부터 다시 돈다**

커서가 비어 있는 것은 무해해 보이지만, `judge` 는 3시간 타임아웃 잡이다. 중간에 죽으면
**이미 판정한 593건을 다시 claude 에 태운다.**

**④수정 방향** 셋의 역할을 명시적으로 분리한다 — 커서=잡 내부 재개(휘발), 노션 `Last Checked`=
대상별 재관찰 주기(정본), 로컬 `followup_state.json`= 캐시(파생, 노션에서 재생성 가능).
그리고 `judge_job` 이 `job.py` 커서를 실제로 갱신하도록 고친다(지금 비어 있는 것이 버그다).

**가역성** ✅ (전부 재생성 가능한 상태)
**구조 신호** ③진실이 두 곳(3곳) · ②단위·소유가 자원과 안 맞음 · ①같은 뿌리 3건+

---

### F4-6 ⚠️ 노션·Gmail 클라이언트가 2~3벌 — 정책 상수는 아직 같고, 편집 이력은 이미 갈라졌다

**①위치**
- 노션: `kv-core/kv_core/notion.py` (206줄, **호출자 0**) vs `skills/net-sourcing/project/core/notion.py` (1,375줄, 라이브)
- Gmail: `kv-core/kv_core/gmail.py` (169줄) vs `skills/mail-harness/project/triage/core/gmail.py` (200줄) vs `skills/_archive/hello-mail/project/core/gmail.py` (195줄)

**②실측 증거 — 이미 갈라져 있나: YES (구현), NO (정책 상수)**

정책값은 **아직** 같다 — `RATE_SLEEP 0.34` · `MAX_RETRY 4` · `NET_BACKOFF 15.0` ·
`MAX_CHILDREN 100` · `MAX_TEXT 1900` 전부 일치.

구현은 갈라졌다:

| 항목 | kv-core | net-sourcing |
|---|---|---|
| 토큰 조달 | `secrets.get("NOTION_TOKEN")` (단일 저장소) | `os.environ.get("NOTION_TOKEN")` (프로세스 env) |
| API 버전 | 상수 `NOTION_VERSION` (:35) | 헤더 하드코딩 (:33) |
| 재시도 코드 | `_RETRIABLE` 상수 (:54) | 튜플 리터럴 인라인 (:74, :100) |
| rich_text 청크 | `rich_text(text, max_items=10)` 인자화 | `_rt_chunks(text)` 10 하드코딩 |

**복사 시점 이후 한쪽만 편집됐다는 직접 증거** — 같은 사고 기록 주석이 다르다:

```
net-sourcing/core/notion.py:128   `Example Person M`의 bio에 `🦖🦹🎮` 3개
kv-core/kv_core/notion.py:145     한 프로필의 bio에 이모지 3개가        ← 익명화 편집이 여기만
```

Gmail 은 **자격증명 경로가 갈라졌다**:

```
_archive/hello-mail/…/gmail.py:27-28   TOKEN_FILE = PROJ / "token.json"          (스킬 로컬)
mail-harness/triage/core/gmail.py:32-33 secrets.credential_path("google/token.json")  ($KV_CONFIG)
```

그리고 kv-core 판은 **세 번째 계보**다 — `inbox_messages`/`archive`/`draft_forward` 가 없고,
HTML→텍스트 정규식(`_TAG`/`_BR`, :93-94)은 kv-core 에만 있다. 즉 **"메일 본문을 어떻게
텍스트로 만드나"가 두 구현에 서로 다르게 존재한다.**

`Notion-Version: 2022-06-28` 은 **14곳**에 흩어져 있고(상수화된 건 1곳), 값은 전부 같다 —
노션이 버전을 올리면 13곳을 손으로 찾아야 한다.

**③실환경에서 무슨 일이 일어나나**

지금 사고는 없다. 위험은 **다음 수정**에 있다 — 레이트리밋을 한 곳에서 튜닝하면 다른 쪽은
그대로다. `kv_core/notion.py` 는 특히 나쁜 함정이다(F3-5): 호출자가 0인데 플랫폼 위치에
있어서 "정본이겠지"라는 합리적 추론이 1,375줄이 축적한 것을 잃는 쪽으로 간다.

**④수정 방향** `kv_core/notion.py` 삭제(F3-5). Gmail 은 `_archive/` 사본을 리포에서 완전히
제거하거나(git 에 남는다) `.gitignore`/CI grep 제외로 컨텍스트에서 빼낸다 — 지금은
`grep` 과 LLM 컨텍스트에 매번 3벌이 다 걸린다. `Notion-Version` 은 `kv_core` 상수 하나로.

**가역성** ✅ **구조 신호** ③진실이 두 곳 · ①같은 뿌리 3건+

---

### F4-7 ⚠️ `ai-consumer-pulse` 만 데이터 루트가 2개이고 두 쪽의 교집합이 0이다

**①위치** `skills/ai-consumer-pulse/project/data/` vs `~/kv-data/ai-consumer-pulse/`

**②실측 증거 — 이미 갈라져 있나: YES (교집합 0)**

```
$ find skills/ai-consumer-pulse/project/data/raw -name "*.json" | wc -l
      10
$ find ~/kv-data/ai-consumer-pulse/raw -name "*.json" | wc -l
    4211
$ comm -12 <(ls skills/ai-consumer-pulse/project/data/raw/_actor|sort) \
           <(ls ~/kv-data/ai-consumer-pulse/raw/_actor|sort) | wc -l
       0                                  ← 겹치는 파일 0개
```

- 리포 안 `_actor/`: `EXAMPLE_ID_5a9b4a043fe0…` 10건 (7/31 실행분)
- kv-data `_actor/`: `..._2026-07-17T09…`, `_2026-07-20T15…`, `harvestapi-linkedin-…` 6건

```
$ git check-ignore -v skills/ai-consumer-pulse/project/data
skills/ai-consumer-pulse/.gitignore:29:data/       ← 추적 안 됨 = 백업도 없음
```

다른 스킬은 규약을 지킨다:

```
$ ls -l skills/net-sourcing/project/data
lrwxr-xr-x → /home/example/kv-data/net-sourcing        ← 심볼릭 링크로 단일화
$ ls skills/mail-harness/project/data skills/investment-memo/data
No such file or directory                                      ← paths.data() 만 쓴다
```

**③실환경에서 무슨 일이 일어나나**

`paths.py:9-12` 가 이유를 이미 써 놨다 — *"`git clean -xdf` 는 `.gitignore` 된 파일도 지운다.
… 실측(2026-07-27)으로 확인했다."* **`ai-consumer-pulse` 의 7/31 액터 원응답 10건은
`git clean -xdf` 한 번에 사라진다.** 유료 액터 응답이고 백업이 없다.

그리고 두 루트가 갈라져 있으므로 "언제 무엇을 수집했나"의 답이 **어느 디렉토리를 보느냐에
따라 다르다.** 다이제스트가 W29·W30 만 있는데 raw 는 W32 까지 있는 것(F2-8)과 같은 뿌리다.

**④수정 방향** net-sourcing 과 같은 심볼릭 링크로 통일하고, 리포 안 10건을 kv-data 로 이동
(**이 감사는 읽기 전용이므로 이동하지 않았다** — 사람이 결정할 일).

**가역성** 🔴 **비가역** — `git clean -xdf` 가 먼저 돌면 복구 수단이 없다
**구조 신호** ②단위·소유가 자원과 안 맞음 · ③진실이 두 곳 · ⑤검증이 그 축을 구조적으로 못 봄
(`_synthetic_portability.py` 는 `paths.data()` 를 쓰는 코드를 검사하지 실데이터가 규약 밖에
있는지는 안 본다)

---

### F4-8 ⚠️ 메일 카테고리 목록이 4곳에 있고 부분집합이 서로 다르다

**①위치** `triage/core/rules.py:23-29` · `triage/core/llm.py` · `triage/core/route.py:149` ·
`references/routing.md:17-32` (+ `_archive/hello-mail/references/routing.md`)

**②실측 증거 — 이미 갈라져 있나: YES**

| 위치 | 카테고리 수 |
|---|---|
| `rules.py:23-29` `LLM_CATEGORIES` | **9개** (INBOUND_KO/EN, TRUSTED_REF, EVENT_SPEAKER, PARTNERSHIP, LP_CUSTODY, RECRUIT, IMPERSONATION, UNKNOWN) |
| `rules.py` 전체 상수 | **15개** (+ BPLAN_INTAKE, AD_NOISE, MASS_BLAST, WEBFLOW, NAMED_PARTNER) |
| `route.py` | **13개** — `NAMED_PARTNER`·`UNKNOWN` 심볼이 **없다**. `:149` 무명 fallback `return {"action":"forward","to": r["slack_bplan"]}` 로 처리 |
| `references/routing.md:17-32` | **15개** (+ `partners` 명단을 문서에 재기술) |

그리고 `routing.md` 자체가 2벌이다:

```
$ diff skills/_archive/hello-mail/references/routing.md skills/mail-harness/references/routing.md
```
59줄 vs 69줄. 갈라진 규칙 — `escalate` 의 정의가 다르다("인박스에 두고 리포트만" vs
"다이제스트 🚩, 영구 사람"), `protect` 의 정의가 다르다("아무것도 안 함" vs "비플랜 접수
메일 — bplan sync 소관"), harness 판에만 `never_auto_send` 표기 추가, archive 판에만 `태스크` 열.

**③실환경에서 무슨 일이 일어나나**

`mail-triage` 가 **다음 발화에 처음 돌아간다**(F1-4). 그때 LLM 은 9개 어휘로 분류하고,
`route.py` 는 13개를 알고, 문서는 15개를 말한다. LLM 이 9개 밖 라벨을 내면
`route.py:149` 의 무명 fallback 으로 흘러 **전부 `#Squad-비플랜` 으로 포워드된다.**

`tasks.yaml:140` 은 이 폴백을 안전장치로 설명한다 — *"현재 미반영분은 폴백(#Squad-비플랜
공유)으로 흘러 사람이 본다."* 맞다. 다만 **어휘가 4곳에 갈라져 있으면 폴백으로 흐르는 양이
설계보다 훨씬 많아진다**, 그리고 그건 F4-1(Tier 348건 쏠림)과 정확히 같은 사고 모양이다.

**④수정 방향** `rules.py` 를 어휘 정본으로 확정하고 `llm.py`·`route.py`·`routing.md` 가
거기서 파생되게 한다. `_archive/` 판 삭제. **첫 실행 전에** 하는 게 맞다.

**가역성** ✅ (아카이브·초안까지라 되돌릴 수 있다)
**구조 신호** ③진실이 두 곳(4곳) · ①같은 뿌리 3건+ (Tier·카테고리·판정어휘가 같은 병) ·
④고칠 때마다 새 게 나옴

---

### F4-9 ⚠️ 잔여 중복 — 저위험이나 기록해 둔다

| 항목 | 위치 | 갈라짐 |
|---|---|---|
| `SKILL.md` vs `SKILL.snapshot.md` | net-sourcing (599줄 vs 425줄, `diff` 196줄) | **YES** — 월상한 $29 vs $45 · 링크드인비 $14.2 vs $13.3 · 합계 "천장 초과" vs "여유 $10". snapshot 에 CRITICAL #11·#12 **94줄이 없다.** 상단에 "🧊 동결본" 경고가 있어 *관리된* 갈라짐이나, 둘 다 스킬 디렉토리 안이라 LLM 이 둘 다 읽는다 |
| 극초기 DB 행수 | `config.yaml:82`=1,274 · `SKILL.md:134`=1,272 · `snapshot:123`=1,272 · `judge-criteria.md:236`=1,283 | **YES** (3개 값) |
| bplan 파이프라인 코드 2벌 | `_archive/bplan-automation/project/main.py`(688줄) vs `mail-harness/project/bplan/main.py`(783줄) | **YES** — harness 판에만 멱등 레저·원자쓰기·`--dry-run`·`@@HARNESS_RESULT@@`. archive 판은 `load_dotenv`, harness 판은 `secrets.load()` |
| `threads_follow_cursor.json` vs `.pending` | `~/kv-data/net-sourcing/` | **NO** (둘 다 `{"cursor":60}`) — 단 7/27자 pending 이 영구 잔류 |
| `.appstore_db_id` 고아 사본 | `~/kv-data/net-sourcing/.appstore_db_id` vs `config.yaml:74` | **NO** (값 동일) — 그러나 **읽는 코드가 0줄**이라 config 만 바꾸면 조용히 갈라진다 |
| 모델명 `claude-opus-4-8` | 스킬마다 config.yaml + `llm.py DEFAULT_MODEL` 쌍, 총 6곳 | **NO** (전부 일치) — `kv_core/session.py:61` 의 `model` 파라미터에 기본값이 없어 **중앙 정본이 없다** |
| 채널 태그 어휘 | `core/notion.py:751` `CHANNEL_LABEL` vs `notion-schema.md:31` | **NO** — 단 `CHART_KO`(:753) 주석이 *"어댑터의 `CHART_LABEL` 과 값이 같아야 한다"* 고 자기 인정 |
| Slack 발송 경로 | `kv_core/notify.py`(webhook) + `kv_core/slackbot.py`(bot) | **중복 아님** — `notify.py:6` 에 2경로 근거 명시, `digest.py:93-95` 가 통합 완료 기록 ✅ |
| 문서가 가리키는 상태파일 부재 | `SKILL.md:509`·`snapshot:394` 가 `threads_layer_state.json` 을 지목 | **YES** — 그 파일은 존재하지 않는다. 실제는 `threads_watch_cursor.json`(`adapters/threads.py:412`)·`threads_follow_cursor.json`(:685) |
| `kol-source-list.md` | `ai-consumer-pulse/SKILL.md:10` 이 정본으로 지목 | **YES** — `find` 결과 **0건**. 소스 정의는 `project/adapters/{rss,youtube,podcast,hn}.py` 에 산재. `:4` 는 "40+ 소스"라 하나 `references/sources-verified.md` 는 **27행** |

---

## 축 ⑤ 실데이터 상태

### F5-1 ⚠️ DLQ 2건 — 재투입 경로가 없어 영구 격리다

**①위치** `~/kv-data/net-sourcing/dlq/judge_dlq.jsonl` · `~/kv-data/net-sourcing/dlq/meeting_mail_dlq.jsonl`

**②실측 증거**

```
$ wc -l ~/kv-data/net-sourcing/dlq/*.jsonl
       1 judge_dlq.jsonl
       1 meeting_mail_dlq.jsonl
```

**judge_dlq** (2026-08-12T00:48:52Z):

```json
{"parse_error": "JSON 후보가 15개 — 모호해서 채택하지 않는다(fail-closed)",
 "raw": "[\n {\"page_id\": \"3b20ac54-…\", \"verdict\": \"Catch\", \"reason\": \"P1·P2·P3·P6 모두 충족. 요리GO — …\"}, …"}
```

버려진 `raw` 안에는 **완성된 판정이 여러 건** 들어 있다(요리GO=Catch, 커넥틴=Catch 등,
근거 문단까지 온전하다). 파싱만 실패했다.

**meeting_mail_dlq** (2026-08-12T00:59:27Z):

```json
{"record": {"to": "", "subject": "[카카오벤처스] 제로넥스 미팅 요청의 건",
            "body": "안녕하세요, 제로넥스 OOO 대표님 … (본문 완성)"},
 "errors": ["to 가 비어있음"]}
```

같은 실행에서 만들어진 다른 2건은 `to` 가 채워졌다:

```
$ cat ~/kv-data/_approvals/meeting-mail-3b20ac54-…-def654091108.json
"payload": {"to": "deals@example.vc", …}
```

**③실환경에서 무슨 일이 일어나나**

DLQ 는 **쌓기만 하고 빼내는 코드가 없다.** orrery 가 건수를 세어 보여주는 게 전부다
(`orrery.render_text` 의 `DLQ 격리` 절). 재투입 커맨드도, 런북 절차도 없다.

- judge: 유효한 판정 15건 상당이 노션에 안 들어갔다. 그 행들은 "미판정" 으로 남아
  **다음 회차 큐에 다시 들어가 claude 를 또 태운다.** 어제 큐 잔여가 741건이라
  이 재소비가 공짜가 아니다.
- meeting-mail: 제로넥스 건은 본문이 완성됐는데 `to` 만 비어서 통째로 버려졌다. 같은 배치의
  형제 2건은 `to: hello@` 로 채워졌으므로, **수신자 채움 로직이 일부 입력에서만 실패**한다
  (`config/tasks.yaml:54` 는 "수신자 W8 고정 hello@" 라 선언한다 — 고정값인데 비어 있다는
  건 채움 자체가 안 돈 경로가 있다는 뜻).

**④수정 방향** ① `to` 를 선언대로 **상수로 강제**(빈 값이면 채워 넣기, 계약 위반이 아니라
정상 경로) ② judge 파서에 "후보 N개면 첫 배열을 채택하되 검증으로 거른다" 완화 또는
DLQ 재투입 커맨드 신설 ③ DLQ 건수에 **경과일** 을 붙여 오래된 격리가 눈에 띄게 한다.

**가역성** ✅ (DLQ 는 원본 보존 — 재투입은 언제든 가능)
**구조 신호** ④고칠 때마다 새 게 나옴(fail-closed 를 강화한 결과가 유실 경로가 됐다) ·
②단위·소유가 자원과 안 맞음(격리는 잡이 하는데 해소 소유자가 없다)

---

### F5-2 ⚠️ 승인 큐 2건이 24시간째 미결 — 만료·독촉 개념이 없다

**①위치** `~/kv-data/_approvals/` · `kv-core/kv_core/approval.py` · `kv-core/kv_core/approvals_tick.py`

**②실측 증거**

```
$ .venv/bin/python -m kv_core.orrery --no-net | grep -A3 "승인 큐"
║ ── 승인 큐 (⏳미결 2 · 실행대기 0 · 오늘 ✅0 ❌0 📤0 · 누적 2) ──
║  ⏳ [mail] meeting-mail · meeting-mail-00000000-0000-0000-0000-3c460938a046
║  ⏳ [mail] meeting-mail · meeting-mail-00000000-0000-0000-0000-1a5e1dba6eb5

$ grep created ~/kv-data/_approvals/meeting-mail-*.json
"created": "2026-08-12T00:59:44Z"      ← 감사 시점 기준 14시간 (KST 8/12 09:59)
```

만료 로직 부재:

```
$ grep -n "expire\|TTL\|만료" kv-core/kv_core/approval.py kv-core/kv_core/approvals_tick.py
(만료 관련 히트 0 — 나오는 것은 flock 의 "회수(stale reaping)라는 개념 자체가 필요 없다" 뿐)
```

**③실환경에서 무슨 일이 일어나나**

`approvals_tick` 은 10분마다 돌며(`com.kv.approvals`, `StartInterval 600`) 미결 건을 슬랙에
announce 하고 반응을 수거한다. 그런데 **아무도 반응하지 않으면 영원히 미결**이다.
`_RECONCILE_CAP`(approvals_tick.py:197)은 "미결이 상한을 넘으면 그건 승인 정체 자체가
이상 신호"라 주석했지만, **그 이상 신호를 발생시키는 코드는 없다** — 상한 초과 시
"오래된 건은 다음 틱"이라고 출력할 뿐이다(:203).

지금 2건이라 무해하다. meeting-mail 이 매일 최대 3건씩 큐에 넣으므로(어제 로그: `상한 3건 —
잔여 25건`), 사람이 일주일 안 보면 **20건 넘게 쌓이고 그때도 orrery 한 줄에 숫자만 커진다.**
그 사이 초안의 근거(창업자 프로필·트랙션)는 낡는다.

**④수정 방향** 미결 건에 `created` 기준 경과일을 표시하고, N일 초과 미결을 `health.py` 의
alertable 로 올린다. 만료(자동 거부)는 위험하니 **알림만** 이 맞다.

**가역성** ✅ **구조 신호** ⑤검증이 그 축을 구조적으로 못 봄(합성 승인 테스트는 즉시
결정하는 시나리오만 돈다 — "아무도 며칠간 안 본다"는 시간 축이라 픽스처가 못 만든다)

---

### F5-3 ⚠️ claude 게이트가 어제 경고선 30 에 1콜 못 미쳐 멈췄다 — 상한이 실수요보다 낮다

**①위치** `~/kv-data/_usage/claude/2026-08-12.jsonl` · `config/resources.yaml:38`

**②실측 증거**

```
$ wc -l ~/kv-data/_usage/claude/2026-08-12.jsonl
      29                       ← 전부 {"ok": true}
$ .venv/bin/python -m kv_core.orrery --no-net | grep claude
║  claude: 오늘 29콜 (경고 30 · 차단 50) — UTC 일 단위·KST 09시 리셋

config/resources.yaml:38   # 현 baseline ~5콜/일(judge 25배치 상한 포함해도 <30) — Max x20 여유 반영해 느슨하게.
```

그리고 그날 judge 는 상한에 부딪혀 멈췄다:

```
~/kv-data/_scheduler/launchd.log
⚠️  런당 호출 상한 20배치 도달 — 잔여 ~741건은 다음 회차가 소화한다(큐 필터가 자연 커서).
```

**③실환경에서 무슨 일이 일어나나**

주석이 근거로 든 "baseline ~5콜/일" 은 **6배 틀렸다.** 실제 29콜이고, 그것도 judge 가
20배치에서 잘리고 meeting-mail 이 3건에서 잘린 **억제된 수치**다. 억제를 풀면 하루
100콜을 훌쩍 넘는다(judge 잔여만 741건 ÷ 배치당 ~20행 = 37배치).

지금 구조는 상한 3개가 겹쳐 있다 — 잡별 배치 상한(20), 게이트 경고(30), 게이트 차단(50).
**실제로 작동한 것은 잡별 배치 상한이고**, 게이트는 그 아래에서 한 번도 발화하지 않았다.
그래서 "게이트가 지키고 있다"는 믿음은 아직 실증된 적이 없다. 반대로 배치 상한을 올리면
게이트(50)가 즉시 물리는데, 게이트가 물릴 때 judge 가 어떻게 실패하는지도 실증된 적이 없다.

**④수정 방향** 주석의 "baseline ~5콜/일" 을 실측(29)으로 갱신하고, 상한 3개 중 **어느 것이
구속 조건인지** 를 문서에 명시한다. 배치 상한을 올릴 계획이면 게이트 차단 경로를 먼저 실증.

**가역성** ✅ **구조 신호** ③진실이 두 곳(주석의 baseline vs `_usage` 실측) ·
②단위·소유가 자원과 안 맞음(같은 자원에 상한이 3개, 소유자가 3곳)

---

### F5-4 ⚠️ judge 8/4·8/5 실패는 재시도된 적이 없는데 "해결됨"으로 집계된다

**①위치** `~/kv-data/net-sourcing/jobs/` · `kv-core/kv_core/health.py:_attempt_no` 계열

**②실측 증거**

```
$ for d in ~/kv-data/*/jobs/*/; do echo "$(basename $d): $(cat $d/state.json)"; done
judge-2026-08-04:        {"state":"failed",    "updated":"2026-08-04T14:25:34Z"}
judge-2026-08-05:        {"state":"failed",    "updated":"2026-08-05T00:00:05Z"}
meeting-mail-2026-08-05: {"state":"failed",    …}
meeting-mail-2026-08-05-r2: {"state":"completed", …}      ← 재시도 있음
meeting-mail-2026-08-06: {"state":"failed", …} / -r2: completed
meeting-mail-2026-08-09: {"state":"failed", …} / -r2: completed
```

meeting-mail 은 실패마다 `-r2` 가 붙어 같은 period 를 다시 돌았다. **judge 는 `-r2` 가 없다** —
8/4·8/5 는 실패한 채로 끝났고, 8/11·8/12 의 **다른 period** 성공이 워터마크를 전진시켰다.

```
$ cat ~/kv-data/net-sourcing/cadence/judge.json
{"last_attempt":"2026-08-12","first_attempt":"2026-08-04","last_success":"2026-08-12"}
```

orrery 는 이걸 이렇게 부른다:

```
║ ── 실패 잡 (최근 7일, 미해결 0건 · 해결됨 4건) ──
║  (미해결 없음)
```

**③실환경에서 무슨 일이 일어나나**

`catch_up: coalesce` + `watermark: last_success` 조합에서 **실패한 period 는 되돌아오지 않는다.**
coalesce 는 항상 `[now]` 만 실행하므로(`scheduler.py:68-69`), 8/4 의 실패는 8/5 에 재시도되는
게 아니라 **8/5 라는 새 period 로 대체**된다.

judge 의 경우 실무 피해는 작다 — 미판정 행은 큐에 남아 다음 회차가 집으므로 자연 복구된다
(그래서 "해결됨" 이 아주 틀린 말은 아니다). 하지만 **관측이 그 논리를 알고 그렇게 부르는 게
아니다.** `health.py` 는 "나중 `last_success` 가 있으면 해결"이라는 일반 규칙을 적용할 뿐이고,
같은 규칙이 **큐 재수거가 없는 잡**(예: 하루치 스냅샷)에 적용되면 영구 결손을 "해결됨"으로
지운다. F1-3 의 appstore 12일 결손이 정확히 그 모양이다.

**④수정 방향** "해결됨" 을 **같은 period 의 후속 시도(`-rN`)** 로 좁히고, 다른 period 의
성공은 "대체됨(period 결손)" 으로 따로 센다. 잡별로 "큐 재수거가 있는가" 를 선언하면
관측이 둘을 구분할 수 있다.

**가역성** ✅ (관측 변경만) **구조 신호** ①같은 뿌리 3건+ · ⑤검증이 그 축을 구조적으로 못 봄

---

### F5-5 ✅ 고아 상태 — 확인했고 발견 없음

빈 축을 통과로 두지 않기 위해 실측을 남긴다.

```
$ grep -l '"running"' ~/kv-data/*/jobs/*/state.json
(출력 없음)                       ← running 인 채 남은 잡 0건 · 전부 completed|failed 로 종료

$ find ~/kv-data -name "*.lock"
~/kv-data/_approvals/meeting-mail-…-def654091108.lock   0바이트  8/12 10:01
~/kv-data/_approvals/meeting-mail-…-fab711c0cafe.lock   0바이트  8/12 10:01
~/kv-data/net-sourcing/body_index.lock                 25바이트  7/21 16:03

$ ls ~/kv-data/_locks/
(비어 있음)                       ← heavy_slot 잔존 락 0건
```

`_approvals/*.lock` 은 잔존 고아가 **아니다.** `approval.py:222-233` 이 `fcntl.flock` 을 쓰고,
주석이 그 이유를 명시한다 — *"커널이 소유권을 관리하므로 프로세스가 죽으면 … 회수(stale
reaping)라는 개념 자체가 필요 없다"*. 0바이트 파일이 남는 것은 설계된 관용구다.
`body_index.lock`(7/21, 25바이트)은 같은 계열의 잔존 파일이며 현재 사용되지 않는 인덱스
빌더의 것으로, 무해하나 청소 대상이다.

`_scheduler/` 에는 `launchd.log` 만 있고 워터마크는 스킬별
`~/kv-data/<skill>/cadence/<task>.json` 에 있다 — 선언(`scheduler.py:12` 주석)과 실제가 일치한다.

---

## 종합 — 32건이 세 뿌리에서 나온다

### 뿌리 1 — 실행 주체가 코드 밖에 있으면 관측이 꺼진다 (11건)

| | 실행 주체 | 관측 결과 |
|---|---|---|
| F1-1 bplan | 없는 plist | 🔕 **침묵** (11일 정지) |
| F1-3 appstore-sourcing | 설치된 plist | ✅ (12일 결손, 남의 채널 성공을 자기 것으로) |
| F1-4 mail-triage | 스케줄러 (미실행) | ⚠️ 무기한 |
| F3-2 appstore-rank-snapshot | 은퇴한 plist | 문서가 **재설치를 지시** |
| F3-3 com.kv.mail-harness | 템플릿만 존재 | 부트스트랩이 설치하지 않는다 |
| F3-4 net-sourcing.daily | 존재한 적 없는 plist | 이중과금 가드가 **이걸** 지킨다 |

kv-core 스케줄러 관할(`runner: local` + WIRED)의 5개는 워터마크·잡 디렉토리·DLQ·실패 이력까지
전부 관측된다. **경계 밖으로 한 발 나가면 관측이 0 이다 — 중간이 없다.** `health.py` 는
판정을 한 곳에 모으는 데 성공했지만 **관측 대상의 경계를 넓히지는 않았다.**

**가장 값싼 수정:** `config/launchd/*.plist` 를 실행체의 정본으로 삼고 `tasks.yaml` 의 `runner`
와 부팅 시 1회 대조한다 → F1-1·F1-3·F3-2·F3-3·F3-4·F2-6 이 한 번에 닫힌다.

### 뿌리 2 — 어휘·상수가 여러 곳에 있고, 어느 쪽이 정본인지 코드에 표시가 없다 (12건)

Tier(9곳, 3값 vs 4값) · 메일 카테고리(4곳, 9/13/15개) · 예산 상한(2곳, $29 vs $45) ·
노션 DB UUID(6곳, 폐기값 4개 잔존) · 비플랜 기준선(6곳, 29% vs 7%) · 클라이언트(노션 2벌 ·
Gmail 3벌) · DB 행수(3값) · 모델명(6곳).

이 뿌리는 **이미 실사고를 냈다** — 2026-08-11 Tier 라벨 하나 누락 → 397건 중 348건(87.7%)
종단 라벨 쏠림. 그때 3곳을 고쳤고 **6곳이 남았다.** 같은 구조가 메일 카테고리에 그대로
복제돼 있고, `mail-triage` 는 다음 발화에 처음 돈다.

**가장 값싼 수정:** 어휘 정본을 `config/contracts.yaml` 하나로 확정하고, CI 에 "어휘 리터럴이
정본 밖에 나타나면 실패" grep 1줄. 폐기 UUID도 같은 방식으로 영구 봉인된다.

### 뿌리 3 — 문서가 시간을 못 따라간다 (9건)

judge 무인화(W6) · appstore GH 은퇴(W11) · mail-triage 배선(W11) · bplan-score 분리(W11) ·
예산 $29 통일(W10) — **다섯 번의 컷오버가 전부 코드는 옮겼고 그 코드를 설명하던 문서는 안
옮겼다.** 그래서 F2-1(반자동 8곳) · F2-3(깨진 HARD-GATE) · F2-5(레지스트리 미러) ·
F2-7(사라진 백업을 근거로 삭제 안내)이 나왔다.

특히 F2-7 은 **시한부 오류**다 — 오늘 실행하면 정답이 나오고 2026-11-09 부터 영구 손실이 된다.
적대 코드 리뷰는 이런 것을 원리적으로 못 잡는다.

**가장 값싼 수정:** `docs/task-registry-mirror.md` 를 `tasks.yaml` 에서 렌더링하는 생성물로
바꾼다(손으로 유지되는 미러는 반드시 갈라진다). 그리고 문서가 인용한 config 키의 실존을
CI 가 검사한다 — F2-2(존재하지 않는 `judge.llm.enabled` 를 안전 근거로 인용)가 이걸로 닫힌다.

---

## 검증이 이 축을 못 본 이유 (기록)

합성 스위트 19본은 전부 **스크래치 `KV_DATA` + 합성 픽스처**로 돈다. 그래서 구조적으로 못 보는 것:

| 못 보는 것 | 대표 발견 |
|---|---|
| `~/Library/LaunchAgents` 의 실제 설치 상태 | F1-1 · F3-2 · F3-3 · F3-4 |
| writer 와 reader 가 같은 키 공간을 쓰는가 | F1-3 (헬스 파일이 3채널 공유) |
| 실파일의 스키마가 코드 스키마와 같은가 (픽스처는 항상 최신 스키마) | F3-6 (strikes int vs [n,day]) |
| 산문 게이트(HARD-GATE)·문서 주장 | F2-1 · F2-3 · F2-5 |
| 문서가 인용한 config 키의 실존 | F2-2 |
| 리포 **밖** 파일 (`~/Desktop/BPLAN/`) | F4-4 |
| 시간 경과가 만드는 오류 (미래에만 틀린다) | F2-7 · F5-2 |
| 실데이터의 누적 상태 (DLQ·미결·결손일) | F5-1 · F5-2 · F5-4 |

**기계 검증으로 옮길 수 있는 것 4개** (전부 grep 수준):
1. `tasks.yaml` 의 `runner` ↔ `config/launchd/*.plist` 실존 대조
2. 문서가 인용한 `config.<key>` 경로가 실제 yaml 에 있는지
3. 어휘 리터럴(Tier·카테고리)이 정본 파일 밖에 나타나는지
4. 폐기 UUID 문자열이 리포에 나타나는지

---

verdict: 32 findings (축① 5 · ② 8 · ③ 6 · ④ 9 · ⑤ 4)

