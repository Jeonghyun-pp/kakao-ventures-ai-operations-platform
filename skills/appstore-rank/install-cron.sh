#!/bin/bash
# App Store 랭킹 보드 — 매일 자동 스냅샷(전 카테고리 200위) launchd 등록.
# 이걸 돌려두면 매일 05:10에 스냅샷이 쌓여 '언제 열어도' 전 카테고리 Δ(급상승/급하락)가 확보된다.
# 해제:  launchctl unload -w ~/Library/LaunchAgents/com.kv.appstore-rank-snapshot.plist

set -e
PROJ="$HOME/.claude/skills/net-sourcing/project"
PY="$PROJ/.venv/bin/python"
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
