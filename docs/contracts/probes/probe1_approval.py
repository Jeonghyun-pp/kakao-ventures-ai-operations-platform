#!/usr/bin/env python3
"""미검증 ① 승인 결재·실행 경로를 **합성으로 태워** 파일 모양을 확보한다.

프로덕션에 이 경로의 산출물이 0건이라(레코드 14/14 전부 decision=null, decisions/ 디렉토리 없음)
결재 후 레코드가 어떻게 생기는지 **아무도 모른다.** 사람이 슬랙에서 ✅ 를 누르는 순간
한 번도 안 돈 코드가 실 Gmail 초안을 만든다. 그 전에 여기서 태운다.

안전: KV_DATA 스크래치 강제 · Gmail/Slack 전부 스텁 · 네트워크 0 · KV_NO_SEND=1.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path.home() / "kv-automation"
sys.path.insert(0, str(REPO / "kv-core"))

# ── 안전 가드: 운영 KV_DATA 절대 금지 ──────────────────────────────
_KV = os.environ.get("KV_DATA")
if not _KV:
    raise SystemExit("🔴 KV_DATA 스크래치 필수")
if Path(_KV).resolve() == (Path.home() / "kv-data").resolve():
    raise SystemExit("🔴 운영 KV_DATA 금지 — 이 프로브는 승인 큐에 레코드를 만든다")
os.environ.setdefault("KV_NO_SEND", "1")

from kv_core import approval, approvals_tick, gmail, slackbot  # noqa: E402

OUT: dict = {}
_drafts: list[dict] = []


# ── 스텁 ────────────────────────────────────────────────────────────
def _fake_create_draft(to, subject, body, *, svc=None):
    did = f"draft-{len(_drafts)+1:03d}"
    _drafts.append({"id": did, "to": to, "subject": subject})
    return did


def _fake_get_draft(draft_id, *, svc=None):
    return {"id": draft_id}


_REACTIONS: dict = {}


def _fake_post(text):
    return (f"9999.{len(_REACTIONS)+1:06d}", "C_SYNTH")


def _fake_reactions(channel, ts):
    return _REACTIONS.get(ts)


gmail.create_draft = _fake_create_draft
gmail.get_draft = _fake_get_draft
slackbot.post = _fake_post
slackbot.message_reactions = _fake_reactions


def _rec(key):
    return approval.load(key)


def _snapshot(label, key):
    r = _rec(key)
    OUT.setdefault(label, {})["record"] = r
    d = Path(os.environ["KV_DATA"]) / "_approvals" / "decisions"
    OUT[label]["decisions_dir"] = sorted(p.name for p in d.iterdir()) if d.is_dir() else None
    if d.is_dir():
        OUT[label]["marker_contents"] = {p.name: p.read_text() for p in d.iterdir()}
    return r


def _mk(key, **over):
    return approval.request(
        task="meeting-mail", kind="mail", origin="system",
        partner="deals@example.vc", dedup_key=key, live=False,
        payload={"to": "deals@example.vc", "subject": f"[합성] {key}",
                 "body": "본문", "page_id": key, "name": "합성팀",
                 "contact_channel": "linkedin_dm", **over})


print("=" * 72)
print("  ① 승인 결재·실행 — 합성 왕복")
print("=" * 72)

# ── A. 정상 왕복: request → announce → ✅ → execute ──────────────────
print("\n[A] 정상 왕복 (승인 → 실행)")
_mk("syn-approve")
n_ann = approvals_tick._announce()
r = _rec("syn-approve")
_REACTIONS[r["slack_ts"]] = [{"name": "white_check_mark", "users": ["U_HUMAN"]}]
n_dec = approvals_tick._reconcile()
_snapshot("A_after_decide", "syn-approve")
n_exe = approvals_tick._execute()
r = _snapshot("A_after_execute", "syn-approve")
print(f"  announce {n_ann} · decide {n_dec} · execute {n_exe}")
print(f"  decision={r.get('decision')} by={r.get('decided_by')} draft_id={r.get('draft_id')}")
print(f"  status_of()={approval.status_of(r)} · 실행 후 필드: "
      f"{sorted(k for k in r if k not in ('key','task','kind','origin','partner','created','payload'))}")

# ── B. 거부: ❌ 를 누르면 실행되지 않는다 ─────────────────────────────
print("\n[B] 거부 (실행 금지 확인)")
_mk("syn-reject")
approvals_tick._announce()
r = _rec("syn-reject")
_REACTIONS[r["slack_ts"]] = [{"name": "x", "users": ["U_HUMAN"]}]
approvals_tick._reconcile()
before = len(_drafts)
n = approvals_tick._execute()
r = _snapshot("B_rejected", "syn-reject")
print(f"  decision={r.get('decision')} · execute {n} · 초안 생성 {len(_drafts)-before}건 "
      f"(0이어야 정상) · status_of()={approval.status_of(r)}")

# ── C. ✅+❌ 동시 → reject 우선(fail-safe) ───────────────────────────
print("\n[C] ✅+❌ 동시 반응")
_mk("syn-both")
approvals_tick._announce()
r = _rec("syn-both")
_REACTIONS[r["slack_ts"]] = [{"name": "white_check_mark", "users": ["U_A"]},
                             {"name": "x", "users": ["U_B"]}]
approvals_tick._reconcile()
r = _snapshot("C_both", "syn-both")
print(f"  decision={r.get('decision')} (rejected 여야 정상 — 비가역 방향으로 안 기운다)")

# ── D. 경합: 진짜 프로세스 2개가 동시에 다른 결정 ────────────────────
print("\n[D] 경합 — 진짜 프로세스 2개 (first-decision-wins)")
_mk("syn-race")
code = (
    'import os,sys\n'
    f'sys.path.insert(0,{str(REPO / "kv-core")!r})\n'
    'from kv_core import approval\n'
    'approval.decide(sys.argv[1], sys.argv[2], by="p"+sys.argv[3])\n')
tf = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False)
tf.write(code)
tf.close()
ps = [subprocess.Popen([sys.executable, tf.name, "syn-race", d, str(i)],
                       env={**os.environ})
      for i, d in enumerate(("approved", "rejected"))]
for p in ps:
    p.wait()
r = _snapshot("D_race", "syn-race")
mk = OUT["D_race"]["marker_contents"]
race_mk = {k: v for k, v in mk.items() if k.startswith("syn-race")}
print(f"  decision={r.get('decision')} by={r.get('decided_by')}")
print(f"  마커 {len(race_mk)}개 (1개여야 정상 — O_EXCL 선점): {race_mk}")
OUT["D_race"]["marker_count"] = len(race_mk)

# ── E. 실행 중 결정 변경 → 고아 초안 표기 ────────────────────────────
print("\n[E] 초안 생성 중 결정이 바뀌면 (고아 초안)")
_mk("syn-orphan")
approvals_tick._announce()
r = _rec("syn-orphan")
_REACTIONS[r["slack_ts"]] = [{"name": "white_check_mark", "users": ["U_A"]}]
approvals_tick._reconcile()


def _flip_then_draft(to, subject, body, *, svc=None):
    """create_draft 가 도는 **동안** 사람이 거부한 상황 재현."""
    approval.update("syn-orphan", decision="rejected", decided_by="late-human")
    return _fake_create_draft(to, subject, body)


gmail.create_draft = _flip_then_draft
n = approvals_tick._execute()
gmail.create_draft = _fake_create_draft
r = _snapshot("E_orphan", "syn-orphan")
print(f"  execute {n} (0이어야 정상 — 실행완료로 확정 안 함)")
print(f"  decision={r.get('decision')} · draft_id={r.get('draft_id')} "
      f"· orphan_draft_id={r.get('orphan_draft_id')}")
print(f"  status_of()={approval.status_of(r)}")

# ── 저장 ────────────────────────────────────────────────────────────
dst = Path(__file__).resolve().parent / "probe1_result.json"
dst.write_text(json.dumps(OUT, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n→ {dst}")
print(f"생성된 스텁 초안 {len(_drafts)}건 (실 Gmail 아님): {[d['id'] for d in _drafts]}")
