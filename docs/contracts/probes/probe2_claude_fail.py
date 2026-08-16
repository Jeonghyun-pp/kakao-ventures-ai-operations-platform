#!/usr/bin/env python3
"""미검증 ② claude 실패 기록 — `_usage` 에 `err` 가 어떻게 적히는지 확보한다.

프로덕션 `_usage/claude/*.jsonl` 124줄이 **전부 `ok:true`** 이고 `err` 는 0건이다.
즉 인증 만료·timeout 이 실제로 어떤 모양으로 남는지 본 적이 없다.
그런데 `health._claude_auth` → `alerts._from_auth` 가 그 필드를 읽어 알림을 낸다.

W9 B-F1 이 고친 게 정확히 이 자리다 — *"`ok=(rc==0)` 이라 인증 만료가 `ok:true` 로
기록돼 auth-suspect 알림이 자기 목적 시나리오에서 영원히 미발동했다."*
**고친 뒤 한 번도 발동한 적이 없다.** 여기서 기록 → 판정 → 알림 사슬을 끝까지 태운다.

안전: KV_DATA 스크래치 강제 · subprocess 스텁(claude 미호출) · 네트워크 0.
"""
from __future__ import annotations

import json
import os
import subprocess as _sp
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path.home() / "kv-automation"
sys.path.insert(0, str(REPO / "kv-core"))

_KV = os.environ.get("KV_DATA")
if not _KV:
    raise SystemExit("🔴 KV_DATA 스크래치 필수")
if Path(_KV).resolve() == (Path.home() / "kv-data").resolve():
    raise SystemExit("🔴 운영 KV_DATA 금지 — 이 프로브는 쿼터 장부에 쓴다")
os.environ.setdefault("KV_NO_SEND", "1")

from kv_core import alerts, gate, health, session  # noqa: E402

OUT: dict = {}
UD = Path(_KV) / "_usage" / "claude"


class _Res:
    def __init__(self, rc, out, err):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _usage_lines():
    p = UD / f"{gate._utc_today_str()}.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _case(label, fake):
    """subprocess.run 을 갈아끼우고 run_session 을 한 번 태운다."""
    before = len(_usage_lines())
    session.subprocess.run = fake
    err_msg = None
    try:
        session.run_session("합성 프롬프트", timeout=1)
    except session.SessionError as e:
        err_msg = str(e)[:120]
    new = _usage_lines()[before:]
    OUT[label] = {"raised": err_msg, "usage_appended": new}
    print(f"  {label:16} → 장부 {len(new)}줄 {new if new else '(기록 없음)'}")
    print(f"                    SessionError: {err_msg}")
    return new


print("=" * 72)
print("  ② claude 실패 기록 — 기록 → 판정 → 알림 사슬")
print("=" * 72)
print("\n[1] 네 가지 실패 모양이 장부에 어떻게 남나\n")

_case("timeout", lambda *a, **k: (_ for _ in ()).throw(
    _sp.TimeoutExpired(cmd="claude", timeout=1)))
_case("empty_output", lambda *a, **k: _Res(0, "   \n", ""))          # rc0 + 빈 출력 = 인증 만료
_case("rc_nonzero", lambda *a, **k: _Res(1, "", "Invalid API key · please run /login"))
_case("not_found", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("claude")))
_case("success", lambda *a, **k: _Res(0, '[{"ok":1}]', ""))

# ── [2] health 가 그 장부를 어떻게 읽나 ─────────────────────────────
print("\n[2] health._claude_auth 판정\n")
auth = health._claude_auth(date.today())
OUT["health_auth"] = auth
for k, v in auth.items():
    print(f"  {k:22} {v}")

# ── [3] alerts 가 알림을 내나 ───────────────────────────────────────
print("\n[3] alerts._from_auth 알림 후보\n")


class _S:
    def __init__(self, a):
        self.auth = a


cands = alerts._from_auth(_S(auth))
OUT["alerts_from_auth"] = cands
for ident, text in cands:
    print(f"  🔔 {ident}\n     {text[:100]}")
if not cands:
    print("  (없음)")

# ── [4] 오늘 발생분이 없으면 침묵하는가 (B-F7 규약) ──────────────────
print("\n[4] 어제 실패 + 오늘 성공 → 침묵해야 한다 (B-F7)\n")
yday = (date.today() - timedelta(days=1)).isoformat()
(UD / f"{yday}.jsonl").write_text(
    "\n".join(json.dumps({"ts": f"{yday}T00:00:00+00:00", "ok": False, "err": "timeout"})
              for _ in range(5)) + "\n", encoding="utf-8")
auth2 = health._claude_auth(date.today())
c2 = alerts._from_auth(_S(auth2))
OUT["yesterday_only"] = {"auth": auth2, "alerts": c2}
print(f"  consecutive_failures={auth2['consecutive_failures']} "
      f"failure_today={auth2['failure_today']} → 알림 {len(c2)}건")

dst = Path(__file__).resolve().parent / "probe2_result.json"
dst.write_text(json.dumps(OUT, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n→ {dst}")
