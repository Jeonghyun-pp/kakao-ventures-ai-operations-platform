#!/bin/bash
# 🟢 앱스토어 **전용** 매일 크론 — 비용 $0/월.
#
# 왜 이건 매일 돌려도 되나 (전체 실행과 뭐가 다른가):
#   · adapters/appstore.py 는 **Apify를 한 줄도 안 부른다.** iTunes RSS + SSR = 애플 무료 API.
#   · land 후 bio 백필(`_enrich_bio`)은 첫 줄이 `if "threads" not in _expand(channel): return`
#     — 앱스토어 단독이면 **유일한 유료 후처리도 안 탄다.**
#   · 앱스토어는 사용자 확정(2026-07-11) **"판단 없는 신규진입 보드"** 라 judge가 필요 없다.
#     사람 손이 안 드니 매일 돌리는 게 맞다.
#
# 왜 **매일이어야** 하나:
#   신규진입은 **diff**다. 안 본 날의 진입은 영영 못 본다(과거 차트를 되돌려 받을 방법이 없다).
#   실측(2026-07-14, 1일치): 무료 227 · 유료 60 · 매출 213건이 새로 진입했다.
#
#   🔴 [2026-07-14] "최신출시(newapplications) 피드가 하루 35건 회전한다"는 옛 근거는 **거짓**이다.
#      07-11~07-14 나흘간 새 app_id **0개** — 애플이 폐기했다(장르 파라미터도 무시되고,
#      신형 Apple Marketing Tools RSS엔 신규출시 피드가 아예 없다: 404).
#      매일의 근거는 **차트 등락**이지 최신출시 피드가 아니다.
#
# 🔴 install-cron.sh (이름이 비슷하다!) 는 **--channel all = 월 $250** 이다. 헷갈리지 마라.
#
# 사용:  ./install-cron-appstore.sh          설치 (매일 07:00)
#        ./install-cron-appstore.sh remove   제거
#        ./install-cron-appstore.sh status   상태 + 최근 로그
#        ./install-cron-appstore.sh now      즉시 1회 실행
set -euo pipefail

LABEL="com.kv.net-sourcing.appstore"
PROJ="$(cd "$(dirname "$0")" && pwd)"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$PROJ/.venv/bin/python"
LOG="$PROJ/data/cron-appstore.log"
HOUR=7
MIN=0

case "${1:-install}" in
  remove)
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    # 🔴 bootout만 하고 파일을 남기면 **다음 로그인에 되살아난다.** 반드시 지운다.
    rm -f "$PLIST"
    echo "🗑️  제거됨: $LABEL (plist 파일까지 삭제)"
    exit 0 ;;
  status)
    echo "--- 등록 상태 ---"
    launchctl list | grep "$LABEL" || echo "  미등록"
    # 🔴 [2026-07-20] **마지막 실행의 건강 상태를 맨 위에 보여준다.**
    #   07-19·07-20 이틀 연속 DNS 실패로 76요청이 전멸했는데 `launchctl list`는 `- 0`(정상)이었고,
    #   로그 수백 줄을 뒤져야 원인이 나왔다. 한 줄로 드러나게 한다.
    echo "--- 마지막 실행 건강 ---"
    if [ -f "$PROJ/data/last_run_health.json" ]; then
      "$PY" - "$PROJ/data/last_run_health.json" <<'PYEOF'
import json, sys
h = json.load(open(sys.argv[1], encoding="utf-8"))
mark = "✅ 정상" if h.get("ok") else "🔴 실패"
print(f"  {mark}  ({h.get('ts','?')} · channel={h.get('channel','?')})")
for k, v in (h.get("counts") or {}).items():
    print(f"    · {k}: {v}건")
for d in (h.get("degraded") or []):
    print(f"    🔴 {d}")
if not h.get("ok"):
    print("    → 원인 확인: 네트워크·쿼터·액터 스키마 변경 순으로 의심")
PYEOF
    else
      echo "  (기록 없음 — 아직 한 번도 안 돌았거나 구버전)"
    fi
    echo "--- 최근 로그 ---"
    tail -30 "$LOG" 2>/dev/null || echo "  (로그 없음 — 아직 한 번도 안 돌았다)"
    exit 0 ;;
  now)
    launchctl kickstart -k "gui/$(id -u)/$LABEL"
    echo "▶️  즉시 실행 트리거됨. 로그: tail -f $LOG"
    exit 0 ;;
esac

[ -x "$PY" ] || { echo "❌ venv 없음: $PY"; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$PROJ/data"

# 🔴 설치 전에 **기계로 확인한다** — 이 채널이 정말 $0인가. 감으로 "무료겠지" 하면 안 된다.
#    estimate_cost.py --channel appstore 가 $0이 아니면 설치를 거부한다.
EST="$("$PY" "$PROJ/tools/estimate_cost.py" --channel appstore)"
if ! grep -q '추정 \$0\.00' <<<"$EST"; then
  echo "🔴 앱스토어 단독 추정이 \$0이 아니다 — 설치를 거부한다. 파이프라인이 바뀐 것 같다:"
  echo "$EST"
  exit 1
fi
echo "✅ 사전 검사: 앱스토어 단독 추정 \$0.00 (Apify 미사용)"

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
    <string>appstore</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MIN</integer>
  </dict>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST_EOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "✅ 설치됨: $LABEL — 매일 $(printf '%02d:%02d' $HOUR $MIN) · 앱스토어만 · \$0"
echo "   로그:      $LOG"
echo "   상태 확인: ./install-cron-appstore.sh status"
echo "   즉시 실행: ./install-cron-appstore.sh now"
echo "   제거:      ./install-cron-appstore.sh remove"
