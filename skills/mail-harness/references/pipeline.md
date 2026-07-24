# 파이프라인 — 단계 정의 · 실패 격리 · 크론

## 단계

```
phase1:  selfcheck → bplan_sync → triage
         (여기서 끊고 UNKNOWN을 사람/Claude가 reclassify)
phase2:  apply → bplan_drop → metrics → digest
```

| 단계 | 하는 일 | 실패 시 |
|------|---------|---------|
| `selfcheck` | config 불변식·Gmail 토큰·라벨·상태파일 검증 | **전체 중단** + 다이제스트 (유일한 전체 중단) |
| `bplan_sync` | 시트 신규 접수 → 노션 등록 + 접수메일 라벨 이동 | 격리 — 계속 진행, 다이제스트 보고 |
| `triage` | 인박스 스캔 → 분류 → 계획(plan_*.json). 읽기 전용 | 격리 (계획 없으면 apply는 자연 스킵) |
| `apply` | 계획 실행: 아카이빙 + 초안 생성 | 격리 |
| `bplan_drop` | 노션 To Drop → 드랍 초안 (+지난 실행 체크박스 복구) | 격리 |
| `metrics` | 측정 대기열의 초안 vs Sent 대조 → metrics.jsonl | 격리 |
| `digest` | 슬랙 웹훅 + data/digest_latest.md | 파일 백업은 항상 남음 |

- **sync가 triage보다 먼저인 이유**: 접수 메일을 인박스에서 미리 치워 triage의
  protect 대상을 줄인다. sync가 실패해도 triage의 protect가 접수 메일을 지키므로 안전.
- 각 단계는 독립 서브프로세스(타임아웃 300s). `@@HARNESS_RESULT@@ {json}` 마지막
  라인을 오케스트레이터가 파싱한다. **결과 라인이 없는데 결과가 있어야 하는 단계면
  실패로 간주한다** — 조용한 실패를 허용하지 않는다.

## 실패 대응 (다이제스트에 ❌/⛔가 있을 때)

- `selfcheck ❌ 토큰`: 해당 포크 디렉터리에서 사람이 reauth 실행(브라우저 필요).
  bplan은 `project/bplan/reauth.py`, triage는 원본 hello-mail과 동일 토큰 계열.
- `bplan_sync ⛔ 노션 등록 통신 오류(생성 여부 불명)`: 유일하게 sync 루프를 멈추는
  케이스. 노션 DB에서 해당 기업 페이지 존재를 눈으로 확인 → 있으면
  `bplan/data/sync_ledger.json`에 해당 순번이 기록됐는지 확인 후 재실행(레저가 중복을
  막는다). 없으면 그냥 재실행.
- `접수 메일 미발견 ⚠️`: 노션 등록은 됐고 메일 이동만 생략된 상태. 메일함에서 수동
  검색해 라벨 이동. (원본과 달리 파이프라인은 멈추지 않는다.)
- 상태 파일 손상 ❌: 사람이 파일을 열어 마지막 유효 상태로 복구하기 전까지 실행 금지.

## 크론 (컷오버 후에만 — HARD-GATE #1)

Claude Code 헤드리스로 UNKNOWN 분류까지 무인 수행(구독 비용, API 키 불필요):

```bash
# launchd: 평일 07:30 (~/Library/LaunchAgents/com.kv.mail-harness.plist)
claude -p "/mail-harness 아침 파이프라인: phase1 → UNKNOWN reclassify → phase2. \
다이제스트의 🚩 항목은 축약 없이 보고." --allowedTools "Bash(*/harness.py*) Bash(*/main.py*) Read"
```

- 권한 프롬프트가 없어야 한다 — allowlist를 사전 구성 (또는 사용자가 검토한
  settings.json 등록).
- 헤드리스 실패 대비 폴백: 같은 시각 +30분에 순수 `harness.py run`을 2차 크론으로
  걸 수 있다(UNKNOWN은 슬랙 폴백이라 안전). 단 중복 실행은 processed/레저가 막는다.
- 구독 사용량은 대화형과 공유 — 아침 이른 시간 권장.
