---
name: appstore-rank
description: App Store 급상승/급하락 랭킹 보드를 바로 띄운다(diandian rank/updown 뷰 복제, 무료). 로컬 미니서버를 기동하고 브라우저를 열어 전체+25개 카테고리(게임은 세부장르 16종)의 무료/유료(SSR 200위)·매출/최신출시(RSS 100~110위) 차트를 급상승·급하락·신규진입·전체순위로 보여준다. "앱스토어 순위", "급상승 보드", "appstore rank", "앱 랭킹", "그물망 앱 랭킹", "updown 보드" 요청 시 사용.
---

# appstore-rank — App Store 급상승/급하락 보드 실행

호출되면 **보드 서버를 띄우고 브라우저로 여는 것**이 목적이다. 설명만 하지 말고 아래를 실제로 실행하라.

## 실행 절차 (이대로 Bash로 수행)

1. 이미 떠 있으면 재기동하지 말고 브라우저만 연다:
   ```bash
   PROJ=~/kv-automation/skills/net-sourcing/project
   if lsof -ti tcp:8000 >/dev/null 2>&1; then
     open "http://localhost:8000/?genre=36"
     echo "이미 실행 중 → 브라우저 오픈"
   else
     cd "$PROJ" && .venv/bin/python tools/board.py --port 8000 --genre 36
     # ↑ 서버는 계속 떠 있어야 하므로 반드시 백그라운드로 실행할 것(run_in_background=true).
     #   기동 시 브라우저가 자동으로 열린다.
   fi
   ```
   - **서버 기동은 Bash 도구의 `run_in_background: true`로 실행**한다(포그라운드로 돌리면 대화가 멈춘다).
   - 기동 후 `curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/`로 200을 확인하고 사용자에게 URL을 알린다.

2. 사용자에게 안내: 브라우저 상단에서 사용 가능한 컨트롤 —
   - **카테고리** 드롭다운(전체+25종). **게임 선택 시** 옆에 **세부장르** 드롭다운(전체 게임+16종) 등장 → 게임 롱테일(합집합 ≈2,266개) 커버.
   - **차트 pill**: 무료·유료(SSR 200위) / 매출·최신출시(RSS 100~110위). *최신출시=최근 출시 앱, 극초기 발굴에 유용.*
   - **보기**: 급상승▲ / 급하락▼ / 신규진입● / 전체순위(Δ 무관 절대순위).
   - **순위밴드**(현재순위 min~max, 예: 101~200으로 발굴 구간만) · **🔄새로고침**(즉시 재수집).

## 실시간성
- **절대순위**는 열 때마다 애플 SSR에서 실시간 수집(캐시 TTL 10분, 🔄새로고침은 즉시 재수집).
- **변동값(Δ)**은 매일 자동 스냅샷(launchd `com.kv.appstore-rank-snapshot`, 매일 05:10)이 전 카테고리 200위를 저장해 확보된다. 스냅샷 job 확인:
  ```bash
  launchctl list | grep appstore-rank
  ```
  없거나 꺼져 있으면 `~/kv-automation/skills/appstore-rank/install-cron.sh` 를 실행해 (재)등록한다.

## 수동 명령(참고)
```bash
cd ~/kv-automation/skills/net-sourcing/project
.venv/bin/python tools/board.py                 # 보드 서버 + 브라우저
.venv/bin/python tools/board.py --genre 6014    # 초기=게임
.venv/bin/python tools/board.py --snapshot-all  # 전 카테고리 스냅샷만(크론이 쓰는 것)
.venv/bin/python tools/rank_lookup.py --genre 6016 --limit 20   # 터미널 표 조회
```

## 구현 위치
- 보드/스냅샷: `~/kv-automation/skills/net-sourcing/project/tools/board.py`
- 조회기: `~/kv-automation/skills/net-sourcing/project/tools/rank_lookup.py`
- 데이터 소스·검증 근거: SSR `serialized-server-data` 파싱(무료·무인증, 카테고리당 200위). 100위 초과는 애플 무료 천장 200위.
