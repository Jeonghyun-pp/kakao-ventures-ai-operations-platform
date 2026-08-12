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
