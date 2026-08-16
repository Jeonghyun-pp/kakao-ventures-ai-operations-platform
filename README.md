# kv-automation

카카오벤처스 인턴 업무 자동화 — 스킬과 공통 런타임.

## 지금 상태를 알려면

**→ [`docs/STATUS.md`](docs/STATUS.md)** — 현황·이번 변경 과정·남은 것·문서 지도.
이 리포에서 가장 먼저 읽는 문서다.

```
검증 전량:  .venv/bin/python kv-core/tests/run_suite.py
정본 둘:    config/tasks.yaml (무슨 일이 언제) · config/contracts.yaml (산출물 계약)
```

## 세 개의 루트

```
~/kv-automation/    코드     git 관리
~/kv-data/          데이터    rsync 단위. git 명령이 닿지 않는다
~/kv-config/        시크릿    별도 취급. 데이터와 백업 정책이 다르다
```

**왜 셋으로 나누나** — `git clean -xdf` 는 `.gitignore` 된 파일도 지운다(`-x` 가 그 뜻).
리포 안에 데이터를 두면 명령 하나로 사라진다. **`.gitignore` 는 선언이고 위치는 구조다.**
그리고 시크릿은 회전(rotate)이 가능하지만 데이터는 불가능하다 — 백업 정책이 달라야 한다.

## 구조

```
kv-core/          공통 런타임 (editable 설치, `import kv_core`)
  paths           $KV_DATA 기준 경로. 코드에 경로 리터럴을 두지 않는 단일 진입점
  secrets         .env·자격증명의 유일한 출처
  notion          노션 API를 안전하게 부르는 법 (인증·재시도·UTF-16 절단·스키마 조회)
  notify          Slack 단일 경로
  gate/job/health 골격 — 본체는 W2/W3/W6

skills/           활성 스킬 5종
  ai-consumer-pulse · appstore-rank · investment-memo · mail-harness · net-sourcing
  _archive/       콜드 스탠바이 (hello-mail · bplan-automation) — 실행 금지, 사유는 그쪽 README

docs/
  BASIC-STRUCTURE.md   12단계 구축 순서의 정본
  adr/                 설계 결정과 그 이유

.github/          상시 실행이 필요한 잡 (앱스토어 일일 스냅샷)   (W2)
.venv/            리포 전체가 공유하는 단일 가상환경 (Python 3.14)
```

## 원칙

- **자격증명과 실데이터는 커밋하지 않는다.** `$KV_DATA` 에는 외부 창업자 개인정보가,
  `investment-memo` 에는 포트폴리오사 재무가 실재한다.
- **경로는 `$HOME`·환경변수 기준.** 절대경로 하드코딩 금지 — 머신 이전이 불가능해진다.
- **시크릿 저장소는 `$KV_CONFIG/.env` 단일 파일(600).** macOS Keychain은 머신 종속이라 쓰지 않는다.
- **스킬은 다른 스킬의 파일을 직접 읽지 않는다.** 공유가 필요하면 `kv_core` 를 거친다.

## 설치

```bash
git clone <repo> ~/kv-automation
cd ~/kv-automation

# 1. 단일 venv + 공통 런타임
python3.14 -m venv .venv
./.venv/bin/pip install -r requirements.txt -e ./kv-core

# 2. 자격증명 (리포 밖)
mkdir -p ~/kv-config/google && chmod 700 ~/kv-config ~/kv-config/google
$EDITOR ~/kv-config/.env && chmod 600 ~/kv-config/.env
#   NOTION_TOKEN · APIFY_TOKEN · SLACK_WEBHOOK_URL · SPREADSHEET_URL ...
#   Gmail 은 ~/kv-config/google/{token,gmail_credentials,credentials}.json

# 3. 스킬을 Claude Code에 연결
for s in skills/*/; do
  [ "$(basename "$s")" = "_archive" ] && continue
  ln -sfn "$PWD/$s" ~/.claude/skills/$(basename "$s")
done

# 4. 검증
./.venv/bin/python -c "from kv_core import paths, secrets; secrets.load(); print(paths.data_root())"
```

`$KV_DATA` · `$KV_CONFIG` 로 위치를 덮어쓸 수 있다. 기본값은 `~/kv-data` · `~/kv-config`.
