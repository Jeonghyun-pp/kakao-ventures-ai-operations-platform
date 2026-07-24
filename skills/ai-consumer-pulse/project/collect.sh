#!/bin/bash
# Phase A — 무료·무인증 수집 오케스트레이터 (토큰 0, 과금 0)
# apify/gmail은 여기 없음 — 스킬 Phase B에서 승인 게이트 후 별도 실행.
set -uo pipefail
PROJ="$HOME/.claude/skills/ai-consumer-pulse/project"
PY="$PROJ/.venv/bin/python"
cd "$PROJ"
export PYTHONPATH="$PROJ"

echo "=== ai-consumer-pulse collect ($(date '+%Y-%m-%d %H:%M')) ==="
for adapter in rss scrape youtube podcast ycoss hn hfdataset; do
  echo "--- $adapter ---"
  "$PY" -m "adapters.$adapter" || echo "⚠️  adapter $adapter failed (계속 진행)"
done

echo "--- manifest ---"
"$PY" - <<'EOF'
import json
from pathlib import Path
from core.common import data_root, week_key
from core.state import load_health

wk = week_key()
root = data_root() / "raw" / wk
items = list(root.rglob("*.json")) if root.exists() else []
by_src = {}
total_chars = 0
for p in items:
    d = json.loads(p.read_text(encoding="utf-8"))
    by_src[d["source_id"]] = by_src.get(d["source_id"], 0) + 1
    total_chars += len(d.get("content", ""))
print(f"주차 {wk}: 누적 {len(items)}건 / {len(by_src)}개 소스")
print(f"추정 입력 토큰: ~{total_chars // 3 // 1000}k (chars/3)")
# 🔴 [2026-07-20] 침묵 판정은 cadence 기준이어야 한다.
#   이전 로직(consecutive_zero >= 2)은 (a) sources.yaml에서 제거된 소스를 계속 경보하고
#   (b) cadence 60~90일짜리 저빈도 소스를 2주 만에 침묵으로 오인했다.
#   실측: 9건 경보 중 9건이 "W29에 이미 수집됨"인 정상 케이스였다. audit.py와 같은 기준으로 맞춘다.
from datetime import datetime, timezone
from core.common import sources as _srcs
now = datetime.now(timezone.utc)
known = {s["id"]: s for s in _srcs() if s.get("adapter") != "manual"}
health = load_health()
silent, never = [], []
for sid, h in health.items():
    src = known.get(sid)
    if not src:
        continue                                    # 제거된 소스 — 경보 대상 아님
    ok = h.get("last_ok")
    if ok:
        days = (now - datetime.fromisoformat(ok.replace("Z", "+00:00"))).days
        if days > int(src.get("cadence_days", 14)):
            silent.append(f"{sid}({days}일째 신규 없음 / cadence {src.get('cadence_days')}일, HTTP {h.get('http_status')})")
    elif h.get("consecutive_zero", 0) >= 2:
        never.append(f"{sid}(성공 이력 없음, 연속{h['consecutive_zero']}회, HTTP {h.get('http_status')})")
if silent:
    print("🔴 침묵(cadence 초과): " + "; ".join(silent))
if never:
    print("🟡 한 번도 성공 없음: " + "; ".join(never))
if not silent and not never:
    print("침묵 소스 없음")
stale = [k for k in health if k not in known and k not in {s["id"] for s in _srcs()}]
if stale:
    print(f"(health에 남은 제거 소스 {len(stale)}건: {', '.join(sorted(stale))} — state/health.json 정리 대상)")
EOF
echo "=== done ==="
