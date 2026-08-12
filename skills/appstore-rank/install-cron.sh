#!/bin/bash

# 🔴 [2026-08-13] 이 스크립트는 **은퇴**했다.
#    com.kv.appstore-rank-snapshot(05:10)은 W6 에서 내려갔고, 지금 스냅샷의 유일한 실행 주체는
#    com.kv.scheduler → workers.run_appstore_snapshot 이다. 이걸 다시 설치하면 이중 수집이 된다
#    (불변규칙 01 위반 · 애플 SSR 429 압력 배가 → 정상 실행이 RSS 폴백으로 밀린다).
echo "🔴 이 스크립트는 은퇴했습니다. 스냅샷은 com.kv.scheduler 가 돌립니다."
echo "   상태 확인: python -m kv_core.orrery"
echo "   정말 설치하려면 이 가드(아래 exit)를 지우고 이유를 커밋 메시지에 남기세요."
exit 1

# App Store 랭킹 보드 — 매일 자동 스냅샷(전 카테고리 200위) launchd 등록.
# 이걸 돌려두면 매일 05:10에 스냅샷이 쌓여 '언제 열어도' 전 카테고리 Δ(급상승/급하락)가 확보된다.
# 해제:  launchctl unload -w ~/Library/LaunchAgents/com.kv.appstore-rank-snapshot.plist

set -e
PROJ="$HOME/kv-automation/skills/net-sourcing/project"
VENV="$HOME/kv-automation/.venv/bin/python"
PY="$VENV"
PLIST="$HOME/Library/LaunchAgents/com.kv.appstore-rank-snapshot.plist"
LOG="$PROJ/data/snapshot.log"

mkdir -p "$HOME/Library/LaunchAgents" "$PROJ/data"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.kv.appstore-rank-snapshot</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$PROJ/tools/board.py</string>
    <string>--snapshot-all</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>5</integer><key>Minute</key><integer>10</integer></dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>RunAtLoad</key><false/>
</dict></plist>
PLISTEOF

launchctl unload -w "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"
echo "[등록됨] com.kv.appstore-rank-snapshot — 매일 05:10 전 카테고리 스냅샷"
launchctl list | grep appstore-rank || echo "(list 확인 실패 — 로그인 세션 확인)"
