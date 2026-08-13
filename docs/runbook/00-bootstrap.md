# 런북 00 — 새 맥에서 처음 돌리기 (부트스트랩)

> **이 문서는 추측이 아니라 실측이다.** 2026-08-12 에 실제로 빈 디렉토리에 clone 해서
> 돌려보고 적었다. 각 항목 뒤 `[실측]` 은 그때 확인한 사실이다.

로드맵이 정한 제품 완성의 세 조건 중 하나가 *"새 사람이 clone→run 으로 즉시 운전한다"* 다.
이 문서가 그 조건의 구현이자 측정 결과다.

---

## 0. 결론 먼저 — 무엇이 리포 안에 있고 무엇이 밖에 있나

리포는 **코드·설정 스키마·문서**를 갖고 있고, **자격증명과 런타임 상태**는 갖고 있지 않다.
이 분리는 의도된 것이다(비밀을 리포에 넣으면 안 된다). 문제는 분리 자체가 아니라
**밖에 뭐가 있는지 목록이 없는 것**이었다 — 그래서 이 문서를 쓴다.

| | 어디 | clone 에 포함 |
|---|---|---|
| 코드·테스트·런북·ADR | 리포 | ✅ [실측] ADR 4 · lessons 7 · 파일 295개 |
| 잡·스킬 소스 | 리포 | ✅ [실측] `skills/*/project/jobs/*.py` 포함 |
| **claude 로그인** | `~/.claude/` | ❌ |
| **API 자격증명** | `~/kv-config/.env` | ❌ |
| **Gmail OAuth 토큰** | `~/kv-config/` | ❌ |
| 런타임 상태(잡·큐·장부) | `~/kv-data/` | ❌ (없으면 자동 생성 — 이식 대상 아님) |

⚠️ `skills/net-sourcing/project/data` 는 `~/kv-data/net-sourcing` 으로 가는 **심볼릭 링크**다.
clone 에는 링크가 없다(생성됨). 경로를 다루는 도구는 이걸 감안해야 한다 —
`git check-ignore` 는 심링크 너머 경로에 `fatal: beyond a symbolic link` 를 낸다.
| launchd plist | `~/Library/LaunchAgents/` | 템플릿만 리포에 |

---

## 1. 순서

```bash
git clone <repo> ~/kv-automation && cd ~/kv-automation
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

스킬별 `requirements.txt` 가 따로 있다 [실측: 8개, `_archive` 2개 제외하면 6개].
합쳐서 필요한 외부 패키지는 이것뿐이다 — [실측] 깨끗한 venv(Python 3.14.6)에서
**충돌 없이 해석된다**(직접 22줄 → 전이 포함 74개 설치 예정):

```
anthropic · google-api-python-client · google-auth · google-auth-httplib2
google-auth-oauthlib · gspread · mcp[cli] · openpyxl · pdfplumber · Pillow
python-docx · python-dotenv · python-pptx · PyYAML · requests · slack-bolt
```

### 1-1. 여기까지 하면 **뭐가 되나** — 실측

설정을 하나도 안 넣은 상태에서:

```
합성 테스트        18/19 통과   ← 코드만으로 대부분 검증된다
judge --synthetic  PASS
bplan --synthetic  PASS
```

**유일한 실패가 `_synthetic_slice` 이고, 원인이 `claude -p` 인증이다.** 즉 코드는 온전하고,
막히는 지점은 딱 하나 — 로그인. 이게 이 시스템의 **부트스트랩 임계 경로**다.

---

## 2. 리포 밖 자산 — 이걸 채워야 돈다

### 2-1. claude 로그인 (`~/.claude/`) — **가장 먼저**

```bash
claude -p "reply with exactly: OK"     # 로그인 안 돼 있으면 여기서 안내가 나온다
```

[실측] `HOME` 을 다른 곳으로 바꿔 로그인 없는 상태를 만들면 `SessionError('claude -p
비정상 종료 rc=1: ')` 로 죽고, **판단·초안이 전부 실패한다**(`_synthetic_slice` ①②③ FAIL).
증상이 "rc=1 + 빈 stderr" 라 원인이 안 보인다 — 이 줄을 기억해 두면 30분을 아낀다.

- 이 시스템은 **API 키를 쓰지 않는다**(ADR 0003). `ANTHROPIC_API_KEY` 는 코드에 남아 있지만
  꺼져 있고, AI 통로는 구독 CLI(`kv_core/session.py`) 하나다.
- 자세한 건 [런북 01 — claude 인증 만료](01-claude-auth.md).

### 2-2. `~/kv-config/.env`

코드가 실제로 읽는 환경변수 [실측: AST 로 추출]. **없어도 도는 것**과 **없으면 그 기능이
죽는 것**을 갈라 적는다.

| 변수 | 없으면 |
|---|---|
| `NOTION_TOKEN` · `NOTION_DATABASE_ID` | judge·bplan-score·meeting-mail 전부 정지 |
| `APIFY_TOKEN` | 수집(sourcing) 정지 — 링크드인·앱스토어 |
| `THREADS_ACCESS_TOKEN` | 스레드 수집 정지 (`adapters/threads.py`) |
| `NAVER_CLIENT_ID` · `NAVER_CLIENT_SECRET` | 네이버 어댑터 정지 (`adapters/naver.py`) |
| `PRODUCTHUNT_TOKEN` | 프로덕트헌트 어댑터 정지 (`adapters/producthunt.py`) |
| `SLACK_BOT_TOKEN` · `SLACK_APPROVAL_CHANNEL` | 승인 카드가 안 뜬다(= 승인 표면 상실) |
| `SLACK_WEBHOOK_URL` | 다이제스트·알림 미발송 |
| `SPREADSHEET_URL` · `WORKSHEET_NAME` | 비플랜 접수(sync) 정지 |
| `GOOGLE_CREDENTIALS_FILE` | 비플랜 sync 정지 — 시트 읽기용 서비스계정 JSON 경로 |
| `SLACK_APPROVAL_USER` | 승인 인가 화이트리스트 미설정(거부는 누구나 가능 — 의도된 비대칭) |
| `KV_DATA` · `KV_CONFIG` | 기본값(`~/kv-data`·`~/kv-config`)으로 동작 |
| `KV_NO_SEND` | **테스트에서 반드시 1** — 실발송 차단 스위치 |

> 이 목록은 **손으로 적은 게 아니라 코드에서 뽑았다.** `_synthetic_portability.py` 가
> 매번 AST 로 다시 뽑아 이 표와 대조하므로, 새 환경변수를 쓰면 테스트가 빨간불이 된다.
> (실제로 이 표를 처음 쓸 때 수집 자격증명 4종이 빠져 있었고 그 테스트가 잡았다.)

### 2-3. Gmail OAuth

`~/kv-config/` 에 토큰이 있다. 만료되면 `skills/mail-harness/project/bplan/reauth.py`.

### 2-4. launchd

`skills/*/com.kv.*.plist.template` 을 복사해 경로를 채우고 `~/Library/LaunchAgents/` 에 둔다.
**plist 는 리포 밖 시스템 변경이라 사람 승인이 필요하다** — 자동화하지 않는다.

---

## 3. 검증 — 이 순서로 확인한다

```bash
# ① 코드 완결성 (설정 불필요)
for t in kv-core/tests/_*.py; do
  KV_DATA=$(mktemp -d) KV_NO_SEND=1 .venv/bin/python "$t" >/dev/null || echo "FAIL $t"
done

# ② 스킬 잡 (설정 불필요)
cd skills/net-sourcing/project && KV_DATA=$(mktemp -d) KV_NO_SEND=1 ../../../.venv/bin/python jobs/judge_job.py --synthetic
cd skills/mail-harness/project && KV_DATA=$(mktemp -d) KV_NO_SEND=1 ../../../.venv/bin/python jobs/bplan_score_job.py --synthetic

# ③ 인증 (설정 필요)
claude -p "reply with exactly: OK"

# ④ 스케줄러가 뭘 안다고 생각하는지
.venv/bin/python -m kv_core.orrery
```

🔴 **테스트는 반드시 각자 `KV_DATA` 스크래치로 돌려라.** 한 디렉토리를 공유하면 게이트
호출 장부·알림 마커가 테스트 간에 넘어가 **멀쩡한 테스트가 거짓 실패한다**(실제로 겪었다).

---

## 4. 이식성을 깨뜨리는 것들 — 실제로 겪은 것

### 4-1. `.gitignore` 가 소스를 먹는다 (2026-08-12)

`jobs/` 규칙이 런타임 잡 디렉토리를 겨냥한 것인데 **소스** `skills/*/project/jobs/` 까지
잡고 있었다. 기존 파일은 규칙보다 먼저 추가돼 살아남았을 뿐이라, **새로 만드는 잡 파일은
커밋에서 조용히 빠졌다.** 로컬은 멀쩡히 돌고 테스트도 통과하는데 clone 하면 스테이지가 없다.

> **교훈: "로컬에서 된다"는 이식성의 증거가 아니다.** 정기적으로 clone 해서 돌려봐야
> 안다. 그게 이 문서가 존재하는 이유다.

### 4-2. 측정 자체가 틀릴 수 있다

이 문서를 쓰면서 처음엔 "ADR·lessons 11개가 리포에 없다"고 판단했다. 실제로는 있었고,
`git ls-files` 가 한글 파일명을 **8진 이스케이프로 인용**해 출력하는데 `find` 는 raw UTF-8 을
내서 비교가 어긋난 것이었다. 정확히 재려면 `git -c core.quotepath=false ls-files`.

> **교훈: 이식성 점검 스크립트도 검증 대상이다.** 도구가 틀리면 "없는 문제"를 고치고
> "있는 문제"를 놓친다.

---

## 5. 남은 위험 — 지금은 되지만 미래에 깨질 것

### 5-1. 버전이 고정돼 있지 않다 [실측]

`requirements.txt` 는 하한(`>=`)만 있고 상한·핀이 없다. 오늘은 해석되지만
**내일 상위 릴리스가 나오면 같은 clone 이 다른 것을 설치한다.** DE 용어로
재현 가능한 빌드(reproducible build)가 아니다 — clone 은 같은데 결과가 다를 수 있다.

지금 당장 고칠 필요는 없다고 본다(의존이 22개로 적고 전부 주류 패키지다). 다만
**깨졌을 때 원인을 여기서 찾을 수 있게** 적어둔다. 고치려면 `pip freeze > requirements.lock`
을 만들고 부트스트랩이 lock 을 쓰게 하면 된다.

### 5-2. 아직 측정 안 한 것 — 정직하게

- **TTFR(time to first run)** — 새 사람이 처음 성공하기까지 걸리는 시간. 사람이 필요하다.
- **다른 맥에서의 실행** — 같은 기기에서만 확인했다. 경로·권한·PATH 가정이 남아 있을 수 있다.
  특히 `claude` CLI 의 PATH 는 launchd 에서 한 번 문제였던 이력이 있다(런북 01 원인 후보 2).
- **실제 `pip install` 완주** — `--dry-run` 으로 해석만 확인했다(설치 시 빌드 실패는 별개다).
  `lxml`·`cryptography`·`pypdfium2` 는 휠이 없으면 컴파일이 필요한 패키지다.
