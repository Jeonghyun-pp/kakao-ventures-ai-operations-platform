# kv — 인턴 한 명이 소프트웨어다

> **인턴 업무를 어떤 실패도 조용히 지나가지 못하는 최소 구조로 자동화한다.**
> 헷갈릴 때 두 번 묻는다 — *사람 인턴이라면 이걸 할까?* 그리고 *이게 조용히 실패할 수 있나?*

처음 왔으면 이 순서로 읽는다: 이 파일 → `./kv status` 한 번 → 각 `skills/<이름>/SKILL.md` → `docs/INTENT.md`.

## 무엇이 도는가

| 스킬 | 하는 일 | 문(outbound) | 정답(사람이 남기는 것) |
|---|---|---|---|
| `mail` | hello@ 메일 수집 → 한 통 한 번 판정 → (문이 열리면) 아카이브·포워드·초안·발송 | off | 노션·라벨 |
| `linkedin` | 월 1회 극초기 창업자 구매 → Tier 판정 → 노션 전량 착지 → 평일 5명 #그물망 카드 → ✅ 면 콜드 메시지 초안 | off (공개 배포 기본값) | 노션 Tier |
| `appstore` | 매일 25장르×4차트 → 6개월 내 신규 → 국적 판정 → One Liner 요약 → 한국 앱만 노션 착지 | off (공개 배포 기본값) | 노션 DB로 업데이트 체크 |
| `bplan` | 구글폼 접수 → 노션 행 → 덱 포함 판정 → 점수·볼만 카드·드랍 초안 → ⭕❌🙋 잇기 | off · 미설치 | 노션 to do |
| `memo` | 슬랙 스레드 "○○ 투심" + 자료 → 무인 초안 docx → 피드백 vN → ✅ | off · 미설치 | ✅ 시점 docx |
| `fund` | 슬랙 스레드 "○○ 제안서" + 노션 DB 링크 + 공고 서식 hwp → 원문 그대로 옮긴 hwp(모델 없음) → 노션 바뀌면 vN → ✅ | off · 미설치 | ✅ 시점 제출본 hwp |
| `ops` | kv-data 스냅샷 백업 · 보존 · 일곱 스킬 health 하나 · 하루 요약 한 통 · 현황판 | — | — |

**문은 사람이 연다.** `$KV_CONFIG/deployment.yaml` 의 `<스킬>.outbound` 값을 명시한다(off → slack → on). 누락된 행동·보고·승격 스위치는 닫히며, `constraints.yaml`의 과거 운영값으로 열리지 않는다. 코드는 문을 못 연다. 문이 닫혀 있어도 무엇을 했을지는 `actions` 표에 dry 로 남는다.
**기준은 사람이 깎는다.** `skills/<이름>/criteria.md`(투심은 `headless.md` + `skills/memo/engine/prompts/`). 저녁 요약·보고서의 🚩 절이 재료다.

## 한 입구

```bash
./kv status                 # 일곱 스킬 현황 한 화면 (문·오늘 건수·정답 누적·보고서·스케줄·마지막 실행)
./kv health                 # 막힌 것만 한 줄. 매일 12:55 자동
./kv digest                 # 하루 한 통 + 현황판 ~/kv-data/_reports/dashboard.html. 매일 18:30 자동
./kv install [--apply]      # SKILL.md frontmatter 의 스케줄 ↔ 실제 설치(launchd, 리눅스면 cron)
./kv check                  # 의도 검사 check_intent (A0~A13) · ./kv test — 테스트 전부
./kv <스킬> <명령…>          # 예: ./kv mail observe --op status · ./kv bplan run --dry-run · ./kv memo run --company X --folder …
```

## 세 루트와 뼈대

```
~/kv/          코드            ~/kv-data/     데이터(리포 안에 링크 안 만든다)      ~/kv-config/   시크릿
kvlib/         gate(문·actions) · judgments(판정 저장·label·replay·diff) · store · report · ledger · people
kv_transport/  gmail · slack · notion · claude · apify · sheets · drive · itunes — 밖에 닿는 유일한 자리(check_intent A1)
skills/<이름>/ run.py(진입점·흐름) · SKILL.md(하루·문·명령·결정 기록, frontmatter = 스케줄) · criteria.md
schedule.py    스케줄 정본은 SKILL.md · check_intent.py  의도를 기계로 센다(미적용을 통과로 세지 않는다) · tools/mutation_test.py  검사가 실제로 무는지
```

## 규율 (전부 검사가 문다)
망은 통로에서만 · 성공은 rc 가 아니라 출력으로 · 나가는 것은 문 뒤 · 허용 목록(금지 목록 아님) · 제약 숫자는 constraints.yaml · 스케줄 선언 = 설치 · 테스트는 프로덕션을 안 건드린다.

## 지금 사람 몫 (2026-09-26)
슬랙 봇 `files:read`·`files:write`(투심 레인) · 비플랜·투심 스케줄 설치 · 구글폼 폴더 서비스계정 공유 · `people.yaml` 실명 매핑 · 채널 전환(test → 실채널) · 서버 이관(`claude setup-token`) · 오프사이트 백업.
옛 시스템(kv-automation)은 걷어냈다(태그 `legacy-final-2026-09-26`). 투심 엔진은 `skills/memo/engine/` 으로 들여왔고(전용 venv), 옛 데이터는 `~/kv-data/_archive/`.

## 로컬 배포 설정

`./kv setup`은 닫힌 예제를 `$KV_CONFIG/deployment.yaml`로 복사한다. 행동·보고 스위치는 이 파일에서 바꾼다. `constraints.yaml`은 일반 API 한계와 업무 기준의 기본값이며, 그 안의 과거 활성화 값은 배포 허가가 아니다.

메일은 `outbound` 외에 `open_kinds` 예외와 `send_branches` 발송 허용 목록이 있다. 누락 시 둘 다 비어 있다. `open_kinds`를 명시하면 해당 종류를 허용하고, `open_categories`에 그 종류를 함께 적으면 해당 카테고리로 좁힌다. 카테고리 제한을 적지 않은 명시적 종류는 전체 범위에 적용된다. `--dry-run`은 이 예외도 닫는다.

`./kv doctor`는 로컬 구조·파일·권한과 명시적으로 열린 스위치를 확인한다. 원격 인증이나 API 권한을 검증하지는 않는다. 별도 `KV_CONFIG`/`KV_DATA`를 쓰면 같은 환경에서 `./kv install --apply`를 실행한다. 생성한 예약 작업에 두 경로가 저장되며 로그는 해당 데이터 루트의 `_logs`에 쌓인다. 경로를 바꾼 뒤에는 예약 작업을 다시 생성한다.
