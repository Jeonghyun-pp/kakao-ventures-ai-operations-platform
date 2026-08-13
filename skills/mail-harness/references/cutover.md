# 컷오버 — 원본 스킬 → 하네스 전환 규약

원본(hello-mail·bplan-automation)과 하네스는 코드가 분리됐지만 **인박스·구글시트 A1
커서·노션 Bplan DB를 물리적으로 공유**한다. 이 문서의 절차 없이 병행 실행하면
중복 초안·행 건너뜀이 생긴다 (HARD-GATE #1).

## P3 — 섀도 병행 (지금 단계)

허용: 하네스의 `--dry-run` 전체 실행, `triage`(읽기 전용) 단독 실행.
금지: 하네스의 non-dry `run`/`phase2`, `jobs/bplan_sync_job.py --apply`, `jobs/bplan_drop_job.py --apply`.
(드랍 잡은 잡 자신도 `--synthetic` 외 진입점을 SystemExit 로 거부한다 — 이중 잠금.)

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

## 🔴 2026-08-13 재빌드로 이 문서의 절반이 낡았다 — 읽기 전에

W12 감사가 `bplan` sync/drop 을 **멱등 단위가 자원과 안 맞는다**는 이유로 재빌드했다
(근원 ②). 아래 세 가지가 바뀌었으니 P4 는 옛 절차대로 하면 안 된다.

| | 옛 세계 (이 문서가 쓰인 시점) | 지금 |
|---|---|---|
| 코드 | `bplan/main.py` (783줄, sync+drop 한 파일) | `jobs/bplan_sync_job.py` · `jobs/bplan_drop_job.py` (`_archive/mail-harness-bplan-legacy/`) |
| sync 커서 | 구글시트 `Setting!A1` 서수 | **커서 없음.** 노션이 진실 + `bplan_sync_floor.json` 단조 바닥 |
| drop 멱등 단위 | 노션 `page_id` (같은 사람이 5통 받았다) | **이메일**(정규화) · 유효기간 1주일 |
| task 선언 | `bplan` 하나 (stages 3개, 워터마크 0개) | `bplan-sync` · `bplan-drop` 둘로 분리 |

**이미 끝난 것**: 2026-08-13 에 42건을 사람 승인 하에 수동 sync 했다(노션 519→561행,
멱등 재실행 0건). 그건 배선이 아니라 1회 수동 실행이다 — 컷오버는 여전히 안 됐다.

## P4 — 컷오버 실행일 (재빌드 반영판)

> 아래 5·6·7 은 **한 커밋에 함께** 넣는다. 셋 중 하나만 하면 검사 O8 이 실패하고,
> 그게 이 절차의 설계다 — 반쪽 컷오버(가드만 열고 배선을 잊음)를 기계가 막는다.

1. **동결**: 원본 두 스킬(`_archive/hello-mail`·`_archive/bplan-automation`)의 수동
   루틴 사용 중지 선언. 크론은 원래 없다(2026-08-13 `launchctl list` 확인 — 설치된
   kv 잡은 approvals·appstore·scheduler 셋뿐).
2. **라이브 diff 1회**: 기준 B 의 남은 요건. 원본 triage vs 하네스 triage 를
   (id, category, action, to)로 대조 → diff=0.
3. **상태 이관**: `hello-mail/project/data/processed.json` →
   `mail-harness/project/triage/data/processed.json` 덮어쓰기.
   - bplan 은 이관할 커서가 **없다**(노션이 진실). 옛 A1 절차는 폐기됐다.
   - `bplan_sync_floor.json`·drop 장부(jsonl)는 하네스 소유 — 그대로 둔다.
4. **드랍 dry 로 눈 검수**: `python jobs/bplan_drop_job.py`(기본이 dry) →
   후보·초안 문안·수신자를 사람이 본다. **Gmail·노션 쓰기 0.**
5. **드랍 잡의 가드 개방**: `jobs/bplan_drop_job.py` `main()` 의
   `raise SystemExit("🔴 컷오버 전에는…")` 제거.
6. **배선**: `kv-core/kv_core/workers.py` 에 러너 2개를 쓰고 `WIRED` 에 등록한다.
   ```python
   "bplan-sync": run_bplan_sync,   # jobs/bplan_sync_job.py --apply --limit 0
   "bplan-drop": run_bplan_drop,   # jobs/bplan_drop_job.py  --apply --limit 3
   ```
   `--limit` 은 처음엔 작게 — 하루치가 다 나가는 걸 눈으로 본 뒤 늘린다.
7. **게이트 검사의 허용 목록 확장**: `kv-core/tests/_synthetic_outbound.py`
   - O5 `_GATED_SCRIPTS`/`_GATED_FLAGS` 에서 두 잡을 뺀다(**`triage/main.py run` 은
     별도 판단이다** — 아카이빙은 이 항목과 함께 결정한다).
   - O8 은 손대지 않는다. 5·6 을 하면 자동으로 "게이트 열림" 가지로 넘어가고,
     그 가지가 배선을 요구한다.
   - `SKILL.md` HARD-GATE #1 에 해제 날짜와 근거를 한 줄 남긴다. 게이트를 **지우지는
     않는다** — 왜 있었는지가 사라지면 다음 사람이 같은 사고를 다시 만든다.
8. **관찰 5영업일**: 크래시 0 · 같은 이메일 중복 초안 0 · 다이제스트 매일.
9. 원본 SKILL.md 상단에 "일상 운영은 mail-harness 로 이관됨(날짜)" 한 줄 —
   원본 코드는 수정하지 않는다.

## 롤백 (하네스 결함 발견 시)

1. `WIRED` 에서 두 줄 제거 → 스케줄러가 즉시 안 부른다(가장 빠른 정지).
   전면 정지가 필요하면 `launchctl bootout gui/$(id -u)/com.kv.scheduler`.
2. `triage/data/processed.json` → 원본 hello-mail 로 역복사
   (하네스가 처리한 건을 원본이 재처리하지 않도록)
3. 원본 스킬로 수동 운영 복귀 — 원본은 수정된 적이 없으므로 항상 동작한다
4. **비플랜 잔건 확인** — 재빌드로 확인 위치가 바뀌었다:
   - drop 장부는 append-only jsonl 이다(옛 `drop_ledger.json` 500건 절단 결함 해소).
     `checkbox_done: false` 잔건 = 초안은 만들었는데 노션 체크가 실패한 것 → 노션에서 수동 체크.
   - sync 는 **되돌릴 게 없다**. 노션 행 생성은 접수키로 멱등이고, 지우고 싶으면
     노션에서 지우면 된다(`bplan_sync_floor.json` 은 단조라 과거를 되살리지 않으므로,
     되살리려면 그 파일의 값을 사람이 낮춰야 한다 — 우연히는 안 일어난다).
