# 컷오버 — 원본 스킬 → 하네스 전환 규약

원본(hello-mail·bplan-automation)과 하네스는 코드가 분리됐지만 **인박스·구글시트 A1
커서·노션 Bplan DB를 물리적으로 공유**한다. 이 문서의 절차 없이 병행 실행하면
중복 초안·행 건너뜀이 생긴다 (HARD-GATE #1).

## P3 — 섀도 병행 (지금 단계)

허용: 하네스의 `--dry-run` 전체 실행, `triage`(읽기 전용) 단독 실행.
금지: 하네스의 non-dry `run`/`phase2`/`sync`/`drop`.

통과 기준 (둘 중 하나):
- **A. 라이브 3회**: 며칠 간격으로 원본 triage(plan A) vs 하네스 triage(plan B)를
  (id, category, action, to)로 대조, 3회 연속 diff=0.
- **B. 라이브 1회 + 히스토리 리플레이** (권장 — 커버리지가 더 넓다):
  `tools/replay_fetch.py`로 과거 plan 기록의 메일들을 Gmail에서 재수집(읽기 전용) →
  `tools/replay_run.py <프로젝트> corpus out`을 원본·포크 각각 실행 → 판정 diff=0.
  단, **컷오버 당일 라이브 diff 1회를 추가로** 통과해야 한다(최신 메일 유형 반영).

```bash
# 편의 변수 (2026-07-27: venv는 리포 1벌, 원본은 skills/_archive/ 로 내려갔다)
PROJ="$HOME/kv-automation/skills/mail-harness/project"
VENV="$HOME/kv-automation/.venv/bin/python"
H="$HOME/kv-automation/skills/_archive/hello-mail/project"   # 원본(콜드 스탠바이)
```

```bash
# 리플레이 절차
"$VENV" "$PROJ/tools/replay_fetch.py" /tmp/corpus.json
"$VENV"                    "$PROJ/tools/replay_run.py" "$H"           /tmp/corpus.json /tmp/v_orig.json
"$VENV" "$PROJ/tools/replay_run.py" "$PROJ/triage" /tmp/corpus.json /tmp/v_fork.json
# 두 verdict JSON을 대조 — 한 건이라도 다르면 원인 규명 후 수정, 카운트 리셋
```

**실적 (2026-07-17)**: 라이브 diff #1 = 0 (44건) · 히스토리 리플레이 = 0
(65건 × 8개 카테고리: BPLAN_INTAKE·AD_NOISE·WEBFLOW·RECRUIT·IMPERSONATION·
INBOUND_KO·EVENT_SPEAKER·UNKNOWN). 당시 기록과의 차이 3건은 전부 LLM/사람
재분류 개입분으로 양쪽 동일하게 UNKNOWN → 예상 일치.
→ **기준 B의 리플레이 요건 충족. 남은 것: 컷오버 당일 라이브 diff 1회.**

주의: 원본으로 apply를 실행한 날은 원본 processed.json이 앞서간다 →
라이브 비교 전에 하네스 `triage/data/processed.json`에 재복사해 기준선을 다시 맞춘다.

## P4 — 컷오버 실행일

1. **동결**: 원본 두 스킬의 수동 루틴 사용 중지 선언 (크론이 있다면 해제)
2. **상태 이관**:
   - `hello-mail/project/data/processed.json` → `mail-harness/project/triage/data/` 덮어쓰기
   - bplan 커서는 구글시트 A1에 있으므로 이관 불필요 (이어서 읽음)
   - `bplan/data/*_ledger.json`은 하네스 소유 — 그대로 둠
3. **1회 유인 실행**: `harness.py run` (non-dry 첫 실행) → 다이제스트·초안·라벨 눈으로 검수
4. **크론 등록**: `pipeline.md` §크론. 등록 후 5영업일 관찰(크래시 0·중복 0·다이제스트 매일)
5. 원본 SKILL.md 상단에 "일상 운영은 mail-harness로 이관됨(날짜). 수동 실행 전
   cutover.md 확인" 한 줄 추가 — 원본 코드는 수정하지 않는다.

## 롤백 (하네스 결함 발견 시)

1. launchd 크론 해제 → 하네스 실행 전면 중지
2. `mail-harness/project/triage/data/processed.json` → 원본 hello-mail로 역복사
   (하네스가 처리한 건을 원본이 재처리하지 않도록)
3. 원본 스킬로 수동 운영 복귀 — 원본은 수정된 적이 없으므로 항상 동작한다
4. bplan 커서(A1)·드랍 체크박스는 공유 상태라 그대로 유효. 단 하네스
   `drop_ledger.json`에 "초안 생성 + 체크박스 실패" 잔건이 있는지 확인 —
   있다면 노션에서 수동 체크(원본은 이 복구 경로가 없다)
