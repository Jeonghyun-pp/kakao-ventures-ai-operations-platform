#!/bin/bash
# 🔴🔴 이 스크립트는 **전체 실행(--channel all)** 크론이다 — 1회 $8.14 × 매일 = **월 $250**.
#      Apify Starter는 월 $29다. 사용자가 2026-07-13에 **"실행 주기 = 수동"**으로 확정했다.
#      그래서 설치를 막아뒀다. 정말로 켜려면 `--yes-monthly-250-usd` 를 붙여야 한다.
#
#      🟢 앱스토어만 매일 돌리고 싶으면 이게 아니라 **install-cron-appstore.sh** 다.
#         그건 애플 무료 API만 쓴다 = **$0/월**. 이름이 비슷하니 똑바로 봐라.
#
# net-sourcing 매일 실행 크론 (macOS launchd)
#
# 왜 매일인가:
#   · 스레드   — max_posts 캡이 **하루치에서 이미 찬다**. 주 1회면 사실상 일간 스냅샷이었고
#                "주당 진성 창업자 6명"은 실은 "하루치에서 6명"이었다.
#   · 앱스토어 — 차트(무료/유료/매출) 신규진입이 하루 ~490건 돈다. 주 1회면 그만큼 놓친다.
#                ⚠️ [2026-07-14 정정] 예전엔 여기에 "RSS 최신출시가 하루 ~35건 회전"이라고
#                   적혀 있었다. **거짓이었다.** newapplications 피드는 07-11 이후 나흘간
#                   새 app_id가 **0개** — 애플이 폐기했다. 매일의 근거는 차트 등락이지
#                   최신출시 피드가 아니다.
#   · 링크드인 — 매일 돌 필요 없다. 축별 `cadence: weekly`가 알아서 주 1회만 돌린다.
#                (이게 없으면 스톡 축이 매일 6페이지씩 전진해 주간 기준의 7배로 소진된다)
#
# 사용:  ./install-cron.sh          설치
#        ./install-cron.sh remove   제거
#        ./install-cron.sh status   상태 + 최근 로그
#        ./install-cron.sh now      즉시 1회 실행
set -euo pipefail

LABEL="com.kv.net-sourcing.daily"
PROJ="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$PROJ/.venv/bin/python"
HOUR=8
MIN=0

case "${1:-install}" in
  remove)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "🗑️  제거됨: $LABEL"
    exit 0 ;;
  status)
    echo "--- 등록 상태 ---"
    launchctl list | grep "$LABEL" || echo "  미등록"
    echo "--- 최근 로그 ---"
    tail -25 "$PROJ/data/cron.log" 2>/dev/null || echo "  (로그 없음 — 아직 한 번도 안 돌았다)"
    exit 0 ;;
  now)
    launchctl kickstart -k "gui/$(id -u)/$LABEL"
    echo "▶️  즉시 실행 트리거됨. 로그: tail -f $PROJ/data/cron.log"
    exit 0 ;;
esac

# 🔴 안전핀 — 이 아래는 **월 $250짜리 크론**을 설치한다. 실수로 밟는 걸 막는다.
if [[ " $* " != *" --yes-monthly-250-usd "* ]]; then
  cat <<'WARN'
🔴 설치를 거부했다 — 이건 **전체 실행(--channel all)** 크론이다.

   1회 $8.14 × 매일 = 월 $250.  Apify Starter 한도는 월 $29다.
   사용자 확정(2026-07-13): **실행 주기 = 수동.**

   · 앱스토어만 매일 자동으로 돌리려면 →  ./install-cron-appstore.sh   ($0/월)
   · 전체를 수동 1회 돌리려면        →  ./.venv/bin/python main.py run --channel all
   · 그래도 정말 매일 전체를 돌리려면 →  ./install-cron.sh --yes-monthly-250-usd
WARN
  exit 1
fi

[ -x "$PY" ] || { echo "❌ venv 없음: $PY"; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$PROJ/data"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>WorkingDirectory</key><string>$PROJ</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>$PROJ/main.py</string>
    <string>run</string>
    <string>--channel</string>
    <string>all</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MIN</integer>
  </dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$PROJ/data/cron.log</string>
  <key>StandardErrorPath</key><string>$PROJ/data/cron.log</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "✅ 설치됨: $LABEL — 매일 $(printf '%02d:%02d' $HOUR $MIN)"
echo "   로그:      $PROJ/data/cron.log"
echo "   상태 확인: ./install-cron.sh status"
echo "   즉시 실행: ./install-cron.sh now"
echo "   제거:      ./install-cron.sh remove"
