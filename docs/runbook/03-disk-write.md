# 런북 03 — 디스크풀 · 쓰기 실패

## 증상
- `OSError: [Errno 28] No space left on device`
- 슬랙: `🔴 승인 틱 단계 실패` 또는 `<task>: 워터마크 처리 오류 — 이 task 만 건너뛰고 계속`
- 잡은 도는데 워터마크가 안 오름(다음 틱이 같은 일을 반복)

## 즉시 확인
```bash
df -h ~/kv-data | tail -1
du -sh ~/kv-data/* | sort -h | tail -5        # 대개 net-sourcing/snapshots 가 최대
ls ~/kv-data/net-sourcing/snapshots | wc -l
```

## 원인 후보
1. **스냅샷 누적** — 일 84~160개 파일. 수개월이면 수 GB
2. events.jsonl·DLQ·_alerts/sent 마커 누적(회전 없음 — 알려진 부채)
3. 맥 자체 디스크 부족

## 복구
1. 공간 확보(안전한 순서):
   ```bash
   # 🔴 [2026-08-13 정정] 이 자리엔 원래 이렇게 적혀 있었다:
   #     "90일 이전 스냅샷 — GH data 브랜치에 사본이 있으므로 로컬 삭제 안전"
   #    **그 백업은 2026-08-11 에 은퇴했다**(GH Actions schedule 제거 → 로컬 단독).
   #    지금 이 명령을 실행하면 사본 없는 원본을 지운다. 앱스토어 Δ 는 어제와 비교하는
   #    값이라 한 번 지우면 그 구간이 **영구 손실**이다 — 다시 수집할 방법이 없다.
   #    런북이 "안전하다"고 말하기 때문에 더 위험하다(지시를 믿고 실행한다).
   ls ~/kv-data/net-sourcing/snapshots | wc -l          # 먼저 얼마나 있는지 본다
   # 공간이 정말 급하면 외부로 복사한 뒤 지운다. 자동 삭제 명령은 두지 않는다.
   find ~/kv-data/_alerts/sent -type f -mtime +30 -delete   # 알림 마커는 재생성되므로 안전
   ```
2. **삭제 금지**: `cadence/*.json`(워터마크) · `_approvals/*.json`(감사 기록) · 진행 중 잡의 events
3. 확보 후 `launchctl kickstart gui/$(id -u)/com.kv.scheduler`

## 재발 방지
- 스냅샷 회전 정책 미구현(부채) — 90일 컷을 크론화하는 것이 후보
- 쓰기 실패는 task 단위로 격리돼 있어(W9 A7) 다른 task·다이제스트는 계속 돈다

## MTTR (예상)
**5분** (공간 확보 3분 + 재기동 즉시)

---

## events.jsonl 중간 줄 파손 — 잡이 벽돌이 됐을 때

### 증상
```
json.JSONDecodeError: …/events.jsonl 의 N번째 줄이 깨졌다(마지막 줄이 아니므로 append 규약 위반이다)
```
`rebuild`·`transition`·`current_state` 가 전부 죽는다 = **그 잡은 종단 상태로 못 간다.**
judge 의 attempt-suffix 재시도도 같은 예외로 죽어 그날 잡 전체가 rc≠0 이 된다.

### 왜 raise 하나 (조용히 넘기지 않는 이유)
마지막 줄이 깨지는 것은 크래시로 반쯤 써진 것이라 정상이다(`_repair_torn_tail` 가 처리).
**중간 줄**이 깨졌다면 그건 append+fsync 규약을 깬 코드가 있다는 뜻이다 — 드러나야 한다.

### 복구
```bash
cd ~/kv-automation
.venv/bin/python -c "import sys;sys.path.insert(0,'kv-core');\
from kv_core import job;print(job.quarantine_corrupt_events('<skill>','<job_id>'))"
```
깨진 줄을 `events.jsonl.quarantine-<ts>` 로 **옮기고**(지우지 않는다) 나머지로 재생한다.

🔴 **격리 파일을 지우지 마라.** append 규약을 깬 코드를 찾는 유일한 단서다.
🔴 자동으로 돌지 않는다 — 자동 격리는 "장부가 깨졌다"는 신호 자체를 지운다.

### MTTR
**2분** (격리 즉시 + 다음 스케줄러 틱이 재개)
