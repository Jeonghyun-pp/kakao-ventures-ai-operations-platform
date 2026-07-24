# mail-harness Reference — Module Index
**Do not read all modules.** Read this index first, then load only what the current phase needs.

## Module Map
| Module | File | Read when... |
|--------|------|-------------|
| 파이프라인 | `pipeline.md` | 단계 정의·실패 격리·크론 설정을 다룰 때 |
| 라우팅 | `routing.md` | UNKNOWN을 reclassify할 때 (판정 기준) / 라우팅 수정 시 |
| 자율성 | `autonomy.md` | 자동 발송 승격을 판단·구현할 때 (P5~P6) |
| 컷오버 | `cutover.md` | 원본 스킬 → 하네스 전환/롤백을 실행할 때 (P3~P4) |

## Phase → Module Loading Guide
### 일상 실행 (phase1/phase2)
1. **Always read**: 없음 — SKILL.md만으로 충분
2. **Read if UNKNOWN 발생**: `routing.md` (판정 기준 섹션)
3. **Skim**: 다이제스트에 ⚠️가 있으면 `pipeline.md` §실패 대응

### 섀도 병행·컷오버 (P3~P4)
1. **Always read**: `cutover.md`
2. **Read if 크론 설정**: `pipeline.md` §크론

### 발송 승격 (P5~P6)
1. **Always read**: `autonomy.md`
2. **Read if 발송 코드 구현**: SKILL.md HARD-GATE #2 + `autonomy.md` §구현 계약
