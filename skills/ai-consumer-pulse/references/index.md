# ai-consumer-pulse Reference — Module Index
**Do not read all modules.** Read this index first, then load only what the current phase needs.

## Module Map
| Module | File | Read when... |
|---|---|---|
| 검증된 소스 함정 목록 | `sources-verified.md` | 어댑터가 0건/오류를 낼 때, 소스 추가/수리할 때만 |
| Phase D 검수 체크리스트 | `ops-checklist.md` | Phase D 진입 시 항상 |

## Phase → Module Loading Guide
- Phase A/B (수집): 문제 없으면 아무것도 로드하지 마라. 침묵/0건 발생 시에만 `sources-verified.md`
- Phase C (분석): 모듈 불필요 — 프롬프트는 `$DATA/prompts/`에서 로드
- Phase D (검수): **Always** `ops-checklist.md`
- Phase E (발행): 모듈 불필요
