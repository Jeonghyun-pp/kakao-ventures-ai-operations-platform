# W0~W5 코드 리뷰 — 발견사항 리포트

> 2026-08-04 · Fable 5 × 6 에이전트(주차별 1개), 각 2단 패스(① 의도-부합+불변규칙 → ② 결함 헌팅)
> 대상: kv-core 프로덕션 15모듈 + 합성테스트 3종 + config 3종 (스킬 도메인 로직 제외)
> 근거: 전 항목이 실제 grep/실행/재현 출력 기반. W3·W4 합성테스트는 스크래치 KV_DATA에서 실행(실데이터 오염 0), W5는 실 claude 호출 0(순수 파이썬 프로브만). **리뷰 중 코드 수정 0** — 이 문서가 유일한 산출물.

---

## 한눈 요약

| 주차 | high | med | low | 계 | 의도-부합 |
|---|---|---|---|---|---|
| W0 시크릿·경로 | 0 | 2 | 5 | 7 | ✅ 위반 0 (비밀 커밋 0·단일 진입점 준수) |
| W1 공통 런타임 | 1 | 2 | 5 | 8 | ✅ 단방향 의존·규칙06 준수 (분기 1건 제외) |
| W2 게이트·계층A | 0 | 5 | 3 | 8 | 🔶 3브레이크 중 실동작 1개 |
| W3 잡러너·상태 | 2 | 4 | 5 | 11 | 🔶 kill-9 재개 실증 통과, 단 2개 구멍 실측 |
| W4 registry 플랫폼 | 2 | 5 | 7 | 14 | 🔶 골격 전반 conform, 스케줄러 의미론 2건 |
| W5 session·validate | 2 | 7 | 3 | 12 | 🔶 WAP 골격 conform, 경계 결함 다수 |
| **원시 합계** | **7** | **25** | **28** | **60** | |

**교차 발견(중복 제거) 4건** — 여러 에이전트가 독립적으로 같은 문제를 짚음(신뢰도 ↑):

| # | 발견 | 짚은 에이전트 | 통합 심각도 |
|---|---|---|---|
| D1 | `gmail.modify` 스코프가 send를 막는다는 주장은 **허위** (구글 문서상 send 허용 스코프 4종에 포함 — WebFetch 검증) | W3·W4·W5 | 🔴 high |
| D2 | `_assert_no_send`는 프로덕션 어디서도 호출 안 되는 데드코드 — 실 가드는 "send 코드 부재"뿐 | W3·W4·W5 | 🟡 med |
| D3 | `registry.py:73-74` 스킬명 리터럴 분기 = ADR 0001 kill-signal 잠재 (현재 no-op `pass`) | W1·W4 | 🔴 high |
| D4 | `heavy_slot` stale 락 회수 레이스 — 두 프로세스 동시 획득 가능 | W3·W4 | 🟡 med |

**중복 제거 후 유니크: 54건 (high 7 · med 22 · low 25)**

---

## ✅ 수정 결과 [2026-08-04, 브랜치 w6-prep-fixes 배치 1~4]

**High 7/7 전부 수정** + med 9건 동반 수정. 각 수정은 리뷰의 재현 시나리오를 상설 회귀 테스트로 승격해(`tests/_review_regressions.py`, 20 체크) 같은 잣대로 검증했고, 기존 합성테스트(worker·pipeline)와 **실 claude E2E 슬라이스 4/4**도 통과.

| 배치 | 수정 | 커밋 |
|---|---|---|
| 1 | H1(validating 재개 간선) · H2(torn tail 복구) · run() 실패 이벤트 | `d37e004` |
| 2 | H3(순차 드레인) · H4(`runner: external` 선언) · 규칙08(스케줄러 락) · D4(rename 회수) · H7(스킬명 분기 삭제) · cadence load 검증 | `c8bf982` |
| 3 | H5(`--strict-mcp-config`+stdin) · parse_json 모호=fail-closed · bool/int · results falsy · DLQ append · `min_records`(선언적 진공 방지) | `8bea626` |
| 4 | H6(스코프 주장 4곳 정정) · D2(데드코드 3곳 삭제) · 슬라이스 ④ AST 검사로 교체 | `5c2efa4` |

**보류(설계대로):** gate 재구조(run-budget·이중 진실원)=W6 쿼터 재타깃과 함께 · stdout→notify 승격(패턴①)=W7 관측 평면과 함께 · low 다수=W6 진행 중 처리.

---

## 🔴 High 7 — 전문 (전부 근거·재현 있음)

| # | 위치 | 문제 | 재현/근거 |
|---|---|---|---|
| H1 | `job.py:437-439` (전이표 78-85) | **validating 상태에서 죽으면 run()으로 영구 재개 불가** — `_ALLOWED["validating"]=(completed,failed)`인데 재개는 무조건 `→running` 전이 시도 | 실측: `ValueError: 허용되지 않은 전이: validating → running` 재현. run_pipeline은 이걸 잡아 멀쩡한 잡을 failed 강등 |
| H2 | `job.py:142-145` | **torn 마지막 줄 위에 다음 append가 이어붙어 병합줄 생성 → read_events 영구 크래시** — "깨질 수 있는 건 마지막 줄뿐" 규약이 스스로 깨짐 | 실측: 병합줄 생성 후 `JSONDecodeError` 재현. 잡 전체 판독 불능 |
| H3 | `scheduler.py:114-127` | **backfill+last_success에서 중간 주기 실패가 영구 구멍** — 나중 주기 성공이 워터마크를 덮어써 실패분이 다시는 due가 안 됨 | 실측: periods [8/2✗,8/3✓,8/4✓] → 익일 due=[8/5]만, 8/2 영구 소실. appstore Δ 시계열에 치명 |
| H4 | `config/tasks.yaml:40-50` + scheduler | **appstore-snapshot 이중실행 무가드** (불변규칙 01) — GH Actions가 이미 도는데 로컬 due 대상 등록, notes에 언급도 없음. `resources:[]`라 락 보호도 없음 | dry에서 due 확인. W6 `--run` 배선 순간 이중 수집 |
| H5 | `session.py:69-76` | **"기본 도구 0개"가 구조 보장 아님** — deny-list 3종뿐, 전역 settings.json allowlist·MCP 서버 상속. 프롬프트 주입 시 세션이 mcp 도구 사용 가능 | `--strict-mcp-config`/`--settings` 격리 부재 확인 |
| H6 (D1) | `gmail.py:5,15` | **"gmail.modify라 API 레벨에서도 send 불가" 주석 허위** — 실 방어는 "코드에 send 없음" 한 겹뿐인데 문서·ADR은 두 겹으로 믿게 함 | 구글 공식 문서 검증: modify는 messages.send 허용 스코프. 3개 에이전트 교차 발견 |
| H7 (D3) | `registry.py:73-74` | **kv_core 내부 스킬명 분기** — 현재 `pass`라 no-op이지만 조건·주석 모순, 다음 사람이 로직을 넣는 순간 ADR 0001 kill-signal 완성 | grep 실측. 속성 기반 검증으로 바꾸거나 두 줄 삭제 |

---

## 관통 패턴 — 60건을 가로지르는 5가지 반복 테마

리뷰의 학습 가치는 개별 버그보다 이 패턴에 있다. 같은 모양이 주차를 넘어 반복됐다:

**① "stdout print = 무인에서 침묵"** — 경고를 print로만 내보내는 곳이 전 주차에 있다. 무인 크론에선 아무도 stdout을 안 읽는다.
- W0: secrets 권한 경고 print (med) · W2: fail-open 경고 print뿐, quiet=True면 그마저 없음 (med) · W4: cadence 오타 경고 print 1회 (med) · W5: 0건 통과 시 아무 신호 없음 (med)
- → W7 관측 평면 전까지 구조적 공백. **notify/health 카운터로 승격할 목록.**

**② "구조로 막는다"는 주장 vs 실체** — 선언은 두 겹인데 실제는 한 겹인 곳들.
- gmail 스코프 주장 허위(H6) · `_assert_no_send` 데드코드(D2) · session.py 최소권한이 환경 의존(H5)
- → "구조적 보장"이라 부르려면 **주장이 아니라 검증 가능한 실체**여야 한다는 W5 교훈 그대로. 지금은 문서가 실체보다 안전하다고 말한다.

**③ 락·동시성의 미완** — 단일 프로세스 전제가 코드 곳곳에 암묵으로 박혀 있다.
- per-job 락 부재(W3 med) · stale reap 레이스(D4) · 스케줄러 이중 기동 방지 부재(W4 med) · notion rate limit 프로세스 로컬(W1 low) · gate `_spent` 프로세스 메모리(W2 low)
- → "크론 + 수동 재실행 겹침"이 현실 시나리오라 W6 배선 전 정리 필요.

**④ 진공 통과 / 0건 성공** — 0을 성공으로 세는 곳들 (규칙 05·07의 사각).
- validate 빈 배열 통과→completed (W5 med) · 검증 임계 40 < 정상 84 — 차트 44개 소실 조용히 통과 (W2 med) · run-budget 브레이크 호출자 0곳 = 항상 False (W2 med)
- → 과거 사고("84차트 유실")와 정확히 같은 계열. **"몇 개면 정상인가"를 명시해야 검증이 검증이다.**

**⑤ falsy 함정** — 파이썬 `or`/truthiness가 정당한 값을 삼키는 곳들.
- `monthly_budget_usd: 0`(동결 의도)이 falsy라 플랫폼 한도로 승격 (W2 low) · `{"results": []}`(정당한 0건)가 통짜 dict로 둔갑 → 부당 halt (W5 med) · 빈 문자열 env가 default로 되살아남 (W0 low) · bool이 int 통과 (W5 med)
- → `is not None` / `isinstance(x, bool)` 선검사 일괄 정리 대상.

---

## 주차별 상세

### W0 — 시크릿·경로 (7건: med 2 · low 5) · 의도 위반 0

**PASS 증거:** git ls-files 크레덴셜 스캔 → 실비밀 0(소스 파일 + 빈 .env.example뿐) · 두 루트 다 리포 밖 + env 오버라이드 · 경로 리터럴 단일 진입점 준수 · .gitignore deny-by-default 완비 · config 3종 비밀값 0.

| 심각도 | 위치 | 문제 |
|---|---|---|
| med | `secrets.py:101,111` | `credential()`이 relpath 무검증 결합 — `../`·절대경로로 `$KV_CONFIG` 밖 탈출 + `mkdir` 부작용 |
| med | `secrets.py:74` | 권한 경고가 stdout print — 무인에서 침묵 + 파이프 파싱 오염 (패턴①) |
| low | `secrets.py:62-67` | `.env` 부재 조용히 통과 + `_loaded` stale 캐시 |
| low | `secrets.py:70-74,102` | 600 검사가 `.env`만 — token.json 미검사, mkdir가 755 생성 (docstring과 불일치) |
| low | `secrets.py:84` | `or default` — 빈 문자열(의도적 비활성)이 default로 되살아남 (패턴⑤) |
| low | `paths.py:39-48` | `data()` 기본 create=True — 조회에 쓰기 부작용, 스킬명 오타가 유령 디렉토리 |
| low | `secrets.py:46,59-67` | `_loaded` 무락 read-then-write (실해 낮음) |

### W1 — 공통 런타임 (8건: high 1 · med 2 · low 5)

**PASS 증거:** `import skills` 0건(단방향) · 스킬명 15히트 중 14건 주석·1건 실분기(H7) · notify.py 규칙06 모범 · notion.py 재시도/rate limit/타임아웃 완비, DB id 하드코딩 0 · clip16 UTF-16 계산 정확.

| 심각도 | 위치 | 문제 |
|---|---|---|
| **high** | `registry.py:73-74` | **H7** 스킬명 분기 — ADR 0001 kill-signal 잠재 |
| med | `notion.py:90-97` | 비멱등 POST를 ReadTimeout에서 무조건 재시도 → 노션 중복 페이지 생성 가능. "타임아웃 재시도=중복 위험" 계약 미명시 |
| med | `notion.py:102-103,129-130` | 429/5xx 재시도 소진 시 최종 실패 로그 없이 응답 반환 — **이 파일이 스스로 기록한 2026-07-11 사고 재현 경로**. 실패 계약 비대칭(네트워크=raise, HTTP=조용한 return) |
| low | `notion.py:101,128` | Retry-After가 HTTP-date 형식이면 ValueError 전파 |
| low | `notion.py:173` | rich_text max_items 초과분(~19,000자 이후) 무로그 절단 |
| low | `notion.py:99,126` | RATE_SLEEP 프로세스 로컬 — 병렬 시 합산 3req/s 미보장 (패턴③) |
| low | `notion.py:136` | clip16 그래핌 경계 미보장 (ZWJ 이모지 반쪽 렌더 — 표시 품질만) |
| low | `gate.py:85-86` | `except Exception: return None` — 실패 원인 영구 소실 (401 vs 네트워크 구분 불가) |

### W2 — 게이트·계층A (8건: med 5 · low 3)

**PASS 증거:** 키 일치 · 3임계 상이(12·16·45→유효29) · min(우리,플랫폼) 정확 구현 · warn 산식 정확(29×80%=23.2) · `--force` 크론 금지 명문화 · 워크플로 `|| true` 0건.

| 심각도 | 위치 | 문제 |
|---|---|---|
| med | `gate.py:199` | **run-budget 브레이크 영구 비활성** — `run_budget_usd` 키가 resources.yaml에 없고 `record/tripped` 호출자 0곳. 실동작 브레이크는 monthly 하나 (패턴④) |
| med | `resources.yaml:24` + 스킬 config | `monthly_budget_usd: 45` 이중 진실원 — 조회실패 폴백이 스킬 값으로 판정. "가드가 있다고 믿지만 없는 구간" 변종 |
| med | `gate.py:130-136` | fail-open 표면화가 stdout뿐 — APIFY_TOKEN 장애 시 월 가드가 수 주 꺼진 채 무감지 (패턴①) |
| med | `appstore-snapshot.yml:86-87` | 검증 임계 40 < 정상 84 — 차트 44개 소실이 조용히 성공 커밋. **과거 사고가 정확히 84개 유실** (패턴④) |
| med | `board.py:110,116,156` | TZ 수정이 워크플로 env에만 — naive `date.today()` 잔존. 리포 안에 날짜 기준 2개(UTC cadence vs KST board) 공존 |
| low | `gate.py:141` | `monthly_budget_usd: 0`(동결)이 falsy → 플랫폼 한도로 승격 (패턴⑤) |
| low | `gate.py:117-162` | 동시 두 런 모두 통과 가능 (스냅샷 기반 check, 손해는 회당 가드로 한정) |
| low | `gate.py:64` | resources.yaml 부재 시 `{}` 조용히 진행 |

### W3 — 잡러너·상태규약 (11건: high 2 · med 4 · low 5)

**PASS 증거(실행):** kill-9 실증 통과 — item-02 처리 중 kill → 재개 시 00·01 스킵, 결과 7개 중복 0, item_done 중복 0 · atomic_write(tmp→fsync→os.replace) 확인 · 재개 근거 = 관측 파일(rule 05) · `--live` 게이팅 확인.

| 심각도 | 위치 | 문제 |
|---|---|---|
| **high** | `job.py:437-439` | **H1** validating에서 죽으면 영구 재개 불가 (실측 재현) |
| **high** | `job.py:142-145` | **H2** torn line 병합줄 → events 영구 판독 불능 (실측 재현) |
| med | `job.py:394-444` | per-job 락 부재 — 크론+수동 겹침 시 이중 부작용 (노션 2행 착지) (패턴③) |
| med | `job.py:281-283` | **D4** stale 락 회수 레이스 + SlotBusy 미변환 경로 |
| med | `job.py:431-444` | run()은 실패 이벤트 없이 전파 — run_pipeline과 비대칭, validating 방치 시 H1 직결 |
| med | `linkedin_job.py:64-65` | 과금 수집($10.5/회)이 잡 상태 확인 밖 — completed여도 재실행마다 재과금 (규칙03) |
| low | `job.py:158-168` | read_events 예외 핸들러 O(n) 재읽기 + TOCTOU |
| low | `job.py:369-389` | done_n(누적) vs skipped(이번 실행) 시간 범위 불일치 + item_skipped 중복 적재 |
| low | `job.py:216-229` | create() 멱등 재사용 시 새 request 조용히 무시 |
| low | `job.py:126` | os.replace 후 디렉토리 fsync 없음 (실피해 낮음) |
| low | `mail_job.py:47-52` | 파일 내 `_assert_no_send` 데드코드 (**D2**) + gmail.modify 주석 부정확 (**D1**) |

### W4 — registry 플랫폼 (14건: high 2 · med 5 · low 7)

**PASS 증거(실행):** registry.load 9 tasks · 다단계 kill-9 실증 통과(collect 스킵·land 멱등·잔류 락 회수) · backfill cap 30 · `--run` 스텁 확인 · resource_slots 정렬 획득 + 역순 해제 · approval dry·라우팅 정확 · cadence UTC.

| 심각도 | 위치 | 문제 |
|---|---|---|
| **high** | `scheduler.py:114-127` | **H3** backfill+last_success 중간 실패 영구 구멍 (실측 재현) |
| **high** | `tasks.yaml:40-50` | **H4** appstore 이중실행 무가드 (규칙 01) |
| med | `scheduler.py:107-128` | 스케줄러 이중 기동 방지 없음 (규칙 08) + 워터마크 무락 RMW + 래퍼 성공 계약 미문서 (패턴③) |
| med | `job.py:280-283` | **D4** (W3와 동일 — stale reap 레이스) |
| med | `cadence.py:38-49`+`registry.py` | cadence 오타가 load에서 안 걸림 — `dayly`→weekly 폴백 = "보수적" 주장과 반대 방향(7배 덜 돎), 경고는 print뿐 (패턴①) |
| med | `approval.py:66-69` | 자동 dedup_key가 초 단위 타임스탬프 — 같은 초 다른 요청 조용히 폐기 / 다른 초 같은 요청 초안 2벌. 멱등 보장 실질 0 |
| med | `gmail.py:6,15` | **D1** (스코프 주장 허위 — H6으로 통합) |
| low | `approval.py:34-36` | **D2** `_assert_no_send` 데드코드 |
| low | `job.py:326-336` | SlotBusy 외 예외 시 획득 락 미해제 |
| low | `job.py:294-308` | stale 판정 시간 임계 없음 — PID 재사용 시 영구 블록 |
| low | `scheduler.py:76-80` | 정확히 30개일 때도 "30+" 경고 오발 (실측) |
| low | `approval.py:67` | dedup_key 무검증 파일명 — `../` 탈출 가능 |
| low | `registry.py:73-74` | **D3** (H7로 통합) + status 값 미검증 |
| low | `approval.py:77-89` | live 전달→기록 순서 — 사이 크래시 시 중복 알림 (가역이라 low) |

### W5 — session·validate WAP (12건: high 2 · med 7 · low 3)

**PASS 증거(프로브):** ANTHROPIC_API_KEY 코드 히트 0 · `_assert_safe_tools(['Bash'])`→SessionError · disallowedTools 무조건 부착 · 전 레코드 검사(규칙07) · halt 발행 전 raise(규칙03) · contracts 3종 정확 + 누락키 KeyError(loud) · `.send(` 호출 0.

| 심각도 | 위치 | 문제 |
|---|---|---|
| **high** | `gmail.py:5,15` | **H6/D1** gmail.modify 스코프 주장 허위 (구글 문서 검증) |
| **high** | `session.py:69-76` | **H5** 최소권한이 구조 아님 — 전역 설정·MCP 상속, `--strict-mcp-config` 격리 부재 |
| med | `validate.py:60-64` | parse_json 괄호 슬라이스가 틀린 부분구조 조용히 반환 — 프로즈 괄호 섞이면 N건 배치가 1건으로 축소 (프로브 실증) |
| med | `validate.py:139` | `{"results": []}`(정당한 0건)가 falsy로 통짜 dict 둔갑 → meeting-mail 부당 halt (패턴⑤, 프로브 실증) |
| med | `validate.py:114-118` | DLQ `write_text` 덮어쓰기 — 회차 간 격리분 유실 (프로브 실증) |
| med | `validate.py:27,85` | bool이 int 서브클래스라 type "int"가 True 통과 (패턴⑤, 프로브 실증) |
| med | `validate.py:103-119` | 빈 배열 진공 통과 → 인증 만료 시 0건으로 completed 완주, 무감지 (패턴④) |
| med | `session.py:69` | prompt가 `-`로 시작하면 CLI 플래그 해석 — `--` 구분자/stdin 부재 |
| med | `gmail.py:18-20` | **D2** `_assert_no_send` 데드코드 — 슬라이스 ④ 검증은 동어반복 |
| low | `session.py:79-83` | timeout 시 직계 child만 kill — MCP 고아 프로세스 가능 |
| low | `_synthetic_slice.py:47-52` | job_id 간 작업공간 공유 — ③이 ①의 산물 위에서 돎, halt 판별력 BAD 1건 국한 |
| low | `validate.py:90` | nonempty가 str만 검사 — 빈 list/dict 통과 (현 계약에선 실해 0) |

---

## W6 게이트 — 무인 스위치 켜기 전 수정 필수 후보

W6은 launchd 실배선 = 처음으로 무인이 실물이 되는 주. 아래는 **배선 전에 고쳐야** 하는 것들:

| 우선순위 | 항목 | 이유 |
|---|---|---|
| 1 | H1·H2 (job.py 재개 구멍 2개) | 무인에서 kill/sleep은 일상 — 재개가 W3~W4의 존재 이유인데 실측으로 뚫림 |
| 2 | H3 (backfill 영구 구멍) + H4 (appstore 이중실행) | W6 첫 결정 지점 그 자체. H4는 로드맵에 이미 등재, H3은 신규 발견 |
| 3 | W4-med 스케줄러 이중 기동 방지 + D4 락 레이스 | launchd wake + 수동 실행 겹침이 배선 즉시 현실화 (패턴③) |
| 4 | H5 (session 격리) | 무인 세션이 도는 첫 주 — `--strict-mcp-config`/`--settings` 한 줄 |
| 5 | W5-med validate 경계 4건 (falsy·DLQ·bool·진공) | validating이 "생명줄"이 되는 첫 주 — 생명줄의 구멍부터 |
| 6 | H6·D2 (주석·데드코드 정정) | 코드 반나절 미만 — 거짓 안전감 제거 |

패턴① (stdout→notify 승격)은 W7 관측 평면과 묶어서 처리하는 게 자연스럽다.

---

## 문서 갭

- **W3·W4는 전용 ADR/레슨이 없다** — 의도가 `BASIC-STRUCTURE.md` step 3·4와 `task-registry-mirror.md` 회고 언급에만 있다. 이번 리뷰의 W3·W4 findings(재개 구멍·스케줄러 의미론)가 정확히 이 갭 위에서 나왔다 — 잠근 의도가 문서화 안 된 곳에서 구현이 갈라졌다. W6 진입 전 **W3-W4 레슨 1장**(상태규약·durable execution에서 배운 것 + 이번 findings 반영)을 쓰는 것을 권고.
- gmail 스코프 주장(H6)은 `ADR 0003`·`gmail.py` docstring·`mail_job.py` 3곳에 반복돼 있어 정정 시 일괄 수정 필요.

## 기타 관찰 (findings 아님)

- `skills/net-sourcing/project/data.orphan-20260804/` (66M) — `data`→`~/kv-data` 심볼릭 링크 전환 시 남은 옛 실데이터 백업. **152개 파일 전부 정본에 100% 중복 확인** → 삭제해도 안전하나 리뷰 산출물이 아니므로 보류. 삭제는 별도 승인.
- 리뷰 프로세스 자체 검증: 6 에이전트 전부 완료 · 코드 변경 0 (git tracked clean 유지) · W3·W4 합성테스트 스크래치 실행 · W5 실 claude 호출 0.
