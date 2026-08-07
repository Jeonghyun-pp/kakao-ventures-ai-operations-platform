# 런북 05 — 스케줄러 미발화 (아무 일도 안 일어남)

> 가장 늦게 발견되는 유형. "에러가 없다"와 "아무것도 안 했다"는 구분되지 않는다 —
> 그래서 orrery 의 **absence 감지**(약속 vs 워터마크)가 유일한 탐지 수단이다.

## 증상
- 다이제스트가 며칠째 안 옴 / orrery 에 `🔴 N일 밀림`
- `launchd.log` 의 마지막 발화가 오래됨

## 즉시 확인
```bash
launchctl list | grep com.kv                          # 등록 상태(0 = 정상 종료)
tail -5 ~/kv-data/_scheduler/launchd.log
cat ~/kv-data/net-sourcing/cadence/*.json             # 워터마크가 언제 멈췄나
ls ~/kv-data/_locks/ 2>/dev/null                      # 잔류 락
```

## 원인 후보
1. **맥이 계속 자고 있었다**(가장 흔함) — 정상 동작. 깨면 catch-up 한다
2. plist 가 언로드됨(재부팅·수동 bootout) → `launchctl list` 에 없음
3. **잔류 락** — `_locks/scheduler.lock` 이 죽은 pid 로 남음(W6 이후 rename 회수로 자동 처리되나 확인)
4. venv/파이썬 경로 변경 → plist 의 절대경로가 깨짐

## 복구
```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.kv.scheduler.plist   # 2번
launchctl kickstart gui/$(id -u)/com.kv.scheduler                                 # 강제 1회
rm ~/kv-data/_locks/scheduler.lock                                                # 3번(프로세스 없음 확인 후)
python -m kv_core.scheduler                                                       # dry — 뭐가 due 인지
```

## 재발 방지
- catch-up 설계상 **놓친 발화는 손실이 아니다** — 깨어나면 밀린 일을 한다
- 며칠 이상 미발화는 freshness SLO(`tasks.yaml freshness_days`)가 🔴 로 표면화

## MTTR (예상)
**3분** (bootstrap + kickstart). 인지까지가 문제 — 다이제스트 부재가 유일한 신호.
