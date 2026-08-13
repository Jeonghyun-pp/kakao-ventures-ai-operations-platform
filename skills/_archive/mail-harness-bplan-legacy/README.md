# 은퇴: 구 bplan sync/drop (2026-08-13, W12 재빌드 9-4)

`skills/mail-harness/project/bplan/` 전체를 여기로 옮겼다. **삭제가 아니라 이관**이다 —
몇 주 뒤 "옛날엔 어떻게 했더라"를 볼 일이 생기고, 검사 도구들은 `_archive` 를 제외하므로
부채·게이트 목록에서는 빠진다.

## 무엇이 대체했나

| 구 | 신 |
|---|---|
| `bplan/main.py sync` | `jobs/bplan_sync_job.py` |
| `bplan/main.py drop` | `jobs/bplan_drop_job.py` |
| `bplan/reauth.py` | 그대로 남김(아래) |

## 왜 재빌드였나 — 고쳐 쓸 수 없었던 이유

이 파일은 플랫폼(`kv_core.job`)이 생기기 전에 쓰였고, 그 뒤로 플랫폼이 해결한 문제를
**전부 자기 방식으로 다시 풀었다.** 그 방식이 하나같이 틀렸다:

| 축 | 구 | 신 |
|---|---|---|
| 멱등 키 | 시트 **행 서수** `'5483'` | 접수키 `이메일\|타임스탬프` |
| 커서 | 시트 `Setting!A1`(리포 밖·락 없음·사람이 편집) | 바닥선 + 노션 upsert |
| 장부 | json 덮어쓰기 · **500건 절삭** · fsync 없음 | `events.jsonl` append-only |
| 원자적 쓰기 | 고정 tmp 이름(동시 writer 400회에 예외 21회) | `job.atomic_write` |
| 락 | **0개**(`threading.Lock` 은 프로세스 간 무효) | `resources=[…]` |
| 드랍 멱등 단위 | 노션 `page_id` | **이메일 + 1주일** |

## 실측된 피해 (2026-08-13)

- 같은 이메일 **23명 / 54행**이 중복 → 그중 **18명이 드랍 메일을 2통 이상 받았다**(최다 6통)
- 같은 접수를 가리키는 노션 행 **4쌍** (생성 200 직후 장부 쓰기 전 크래시)
- 회사명 공란이면 Gmail 질의가 **19건**에 매칭 → 남의 스레드에 초안이 붙을 수 있었다

## 남긴 것

`reauth.py` 는 **은퇴 대상이 아니다** — Gmail OAuth 토큰 재발급용이고 대체물이 없다.
`skills/mail-harness/project/bplan_reauth.py` 로 옮겼다.
