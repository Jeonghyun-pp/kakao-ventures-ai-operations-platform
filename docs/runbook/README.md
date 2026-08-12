# 런북 — 장애가 났을 때 사람이 하는 일

> 자동화 이전에 **절차부터 명문화**한다(runbook-first). 무엇을 자동화할지는 실측 빈도가 정한다.
> 각 문서 형식: 증상 → 즉시 확인(명령) → 원인 후보 → 복구 → 재발 방지 → MTTR.
> MTTR = 사람이 인지한 뒤 복구까지. 예상치는 W9 장애 실험(GameDay) 후 실측으로 갱신한다.

## 이 시스템의 기본 진단 3줄

```bash
python -m kv_core.orrery --no-net          # 한 판: 뭐 돌았나·안 돌았나·실패·비용
tail -40 ~/kv-data/_scheduler/launchd.log  # 스케줄러(4h) 발화 이력
tail -20 ~/kv-data/_approvals/launchd.log  # 승인 리컨실러(10분) 틱 이력
```

## 시나리오

| # | 문서 | 증상 한 줄 |
|---|---|---|
- [00 — 새 맥에서 처음 돌리기(부트스트랩)](00-bootstrap.md) — clone→run 실측. 리포 밖 자산 목록과 부트스트랩 임계 경로(claude 로그인).
| 1 | [claude 인증 만료](01-claude-auth.md) | 무인 전체 정지 — judge·meeting-mail 이 매일 실패 |
| 2 | [노션 429·네트워크](02-notion-network.md) | 파이프라인이 ConnectionError/429 로 죽음 (2026-08-06 실발생) |
| 3 | [디스크풀·쓰기 실패](03-disk-write.md) | 워터마크·장부 기록 실패 — 상태 신뢰 붕괴 |
| 4 | [승인 큐 이상](04-approval-queue.md) | 카드가 안 뜸 / ✅ 눌러도 반영 안 됨 / 초안 중복 |
| 5 | [스케줄러 미발화](05-scheduler-silent.md) | 아무 일도 안 일어남 — 가장 늦게 발견되는 유형 |

## CI 가 지키는 것 (`.github/workflows/ci.yml`)

커밋마다 자동으로 도는 합성 스위트 **14본** + 구조 검사 2종. **테스트를 사람 기억에서 구조로**
옮긴 것이고(2026-08-07 신설 · 2026-08-11 재설계로 2본 추가), 지키는 계약은 다음과 같다:

| 검사 | 지키는 계약 |
|---|---|
| worker·pipeline·chaos_lab | 상태 규약 — kill-9 재개·항목 멱등·events 무손상·장애 내성 7종 |
| review_regressions | 과거 결함 재발 방지(H1·H2·T2·T4 등) + **실발송 킬 스위치 메타 검증** |
| health | **판정의 단일 정본** — resolved 시각/순번 · launchd-direct 선언 · first_attempt 자기치유 · `now` 주입 시간 시뮬 · 순수 읽기 |
| orrery | 관측자 read-only 계약(AST, health 포함) · absence 감지 임계 · 승인 통계 표면 |
| **concurrency_lab** | 검증 사각① — **진짜 프로세스 경합**. lost update · announce↔decide · 실행 중 거부 · first-decision-wins 수렴 |
| **realdata_replay** | 검증 사각② — 결측 필드·구스키마 내성 + 실 `~/kv-data` 읽기전용 리플레이. **관측🔴↔알림 일치**(이중 진실원 회귀 잠금) |
| approval_decide·approvals_tick | **무승인 무실행** · 결정 멱등 · 실행 보상 · 굶주림 방지 |
| alerts·claude_gate | 알림 발동 · dedup · **백오프 30일 시뮬**(끊기지 않는지) · 쿼터 게이트 |
| AST 무발송 | gmail·approvals_tick·slackbot·approval 에 `.send()` 호출 0건 — **W10 이후 유일한 물리적 안전장치** |
| 선언 정합 | registry·contracts 로드 + claude 호출 상한 불변식(최악치 < 경고선) |

시크릿 0 · 외부 접근 0(`KV_NO_SEND=1` + 전면 스텁). 실 claude 를 부르는 `_synthetic_slice.py`
만 제외되며, 그 파일의 핵심(무발송 AST)은 위 별도 스텝이 대신한다.

## 원칙

- **워터마크가 진실**: 로그가 없어도 `~/kv-data/<skill>/cadence/*.json` 이 마지막 성공을 안다.
- **되돌릴 수 있는 것부터**: 판정 기입·초안은 가역. 발송만 비가역이고, 그 경로는 코드에 없다.
- **모르면 멈춘다**: 재개는 언제든 가능하다(멱등). 억지로 진행시켜 상태를 오염시키지 않는다.
