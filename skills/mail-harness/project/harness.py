"""mail-harness 오케스트레이터 — hello@ 메일 업무 전체를 한 번에.

  phase1 : selfcheck → bplan sync → triage(읽기 전용)   ← 여기서 멈추고 UNKNOWN을 사람/Claude가 분류
  phase2 : apply → bplan drop → metrics → digest
  run    : phase1 + phase2 (UNKNOWN은 분류 없이 슬랙 폴백 — 안전하지만 시끄러움)

실패 격리: selfcheck 실패만 전체 중단(상태 오염 방지). 나머지 단계 실패는
기록 후 계속 — 한 단계가 죽어도 나머지 업무는 처리되고, 실패는 다이제스트에 남는다.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml
from kv_core import paths, secrets

PROJ = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJ))
secrets.load()          # 자격증명 정본은 $KV_CONFIG/.env 하나뿐이다

from core import digest as digest_mod   # noqa: E402
from core import metrics as metrics_mod  # noqa: E402

# venv도 리포 하나뿐이다 (5벌 → 1벌). 스킬별 .venv 는 더 이상 만들지 않는다.
PY = paths.repo_root() / ".venv" / "bin" / "python"
DATA = paths.data("mail-harness", "harness")
STAGE_TIMEOUT = 300
# 🔴 [2026-08-14] 마커를 여기에 따로 적어두면 잡 쪽과 조용히 갈린다 —
#    실제로 재빌드가 잡 쪽 emit 을 빠뜨렸고 두 단계가 항상 fail 로 찍혔다.
#    생산자·소비자가 **같은 모듈**을 본다(core/result.py 독스트링).
from core import result as result_mod  # noqa: E402
RESULT_RE = result_mod.PATTERN


def cfg() -> dict:
    return yaml.safe_load((PROJ / "config.yaml").read_text())


def _run_stage(name: str, cmd: list[str]) -> dict:
    """서브프로세스 1단계 실행. @@HARNESS_RESULT@@ JSON을 파싱해 반환."""
    print(f"\n━━ [{name}] {' '.join(str(c) for c in cmd[1:])}")
    try:
        p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                           timeout=STAGE_TIMEOUT, cwd=str(PROJ))
    except subprocess.TimeoutExpired:
        return {"name": name, "status": "fail", "note": f"{STAGE_TIMEOUT}s 타임아웃"}
    out = p.stdout + p.stderr
    print(out.strip()[-2000:])
    result = result_mod.parse_last(out)
    if p.returncode != 0 or (result and not result.get("ok", True)) \
            or (result is None and name != "triage" and name != "apply"):
        note = (result or {}).get("halted") or f"rc={p.returncode}"
        return {"name": name, "status": "fail", "note": str(note)[:300], "result": result}
    return {"name": name, "status": "ok", "result": result}


def _latest_plan() -> Path | None:
    plans = sorted((PROJ / "triage" / "data").glob("plan_*.json"))
    return plans[-1] if plans else None


def _plan_summary(c: dict) -> tuple[dict, list[dict]]:
    """triage 계획 파일 → (요약, 에스컬레이션 원문 목록)."""
    path = _latest_plan()
    if not path:
        return {}, []
    plan = json.loads(path.read_text())
    actions: dict[str, int] = {}
    unknown = 0
    esc = []
    for p in plan:
        actions[p["action"]] = actions.get(p["action"], 0) + 1
        if p["category"] == "UNKNOWN":
            unknown += 1
        if p["action"] == "escalate":
            esc.append({k: p.get(k) for k in ("category", "subject", "sender", "note")})
    return {
        "actions": actions,
        "unknown_ratio": (unknown / len(plan)) if plan else 0.0,
        "unknown_alert_ratio": c["autonomy"]["unknown_alert_ratio"],
        "total": len(plan),
    }, esc


def _watch_drafts_from_plan():
    """apply가 만든 초안(reply/forward)을 무수정 발송률 측정 대기열에 등록."""
    path = _latest_plan()
    if not path:
        return 0
    entries = []
    for p in json.loads(path.read_text()):
        if p["action"] in ("reply", "forward") and p.get("body"):
            entries.append({"msg_id": p["id"], "thread_id": p["thread_id"],
                            "category": p["category"], "draft_body": p["body"]})
        if p.get("also_reply"):
            entries.append({"msg_id": p["id"] + ":also", "thread_id": p["thread_id"],
                            "category": "PARTNERSHIP_DECLINE",
                            "draft_body": p["also_reply"]["body"]})
    metrics_mod.watch(entries)
    return len(entries)


def _load_or_new_record() -> dict:
    runs = sorted(DATA.glob("run_*.json"))
    if runs:
        rec = json.loads(runs[-1].read_text())
        if not rec.get("phase2_done"):
            return rec  # phase1만 끝난 기록 이어쓰기
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return {"started": ts, "stages": [], "path": f"run_{ts}.json"}


def _save_record(rec: dict):
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = DATA / (rec["path"] + ".tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2))
    tmp.replace(DATA / rec["path"])


def phase1(c: dict, rec: dict, dry_run: bool) -> bool:
    """selfcheck → sync → triage. selfcheck 실패 시 False(전체 중단)."""
    st = _run_stage("selfcheck", [PY, PROJ / "core" / "selfcheck.py"])
    rec["stages"].append(st)
    if st["status"] == "fail":
        rec["stages"].append({"name": "이후 단계", "status": "skip",
                              "note": "selfcheck 실패 — 상태 오염 방지를 위해 전체 중단"})
        return False

    # 🔴 [2026-08-13 W12 재빌드] 구 `bplan/main.py sync` → 새 `jobs/bplan_sync_job.py`.
    #    바뀐 것: 멱등키가 시트 행 서수 → **접수키(이메일|타임스탬프)** · 커서가 시트 A1 →
    #    바닥선 + 노션 upsert · 장부가 500건 절삭 json → events.jsonl · 락 0개 → resources.
    #    ⚠️ [2026-08-14 정정] 위 재빌드는 **라벨 단계를 잘못 뺐다.** drop 이 `label:"…"` 로
    #       원본을 찾으므로 라벨은 안전 경계가 아니라 **다음 단계의 입력**이었다. 되살렸다 —
    #       `--label` 은 `core/labels.py` 한 곳에서 온다(drop 과 같은 값). 남의 메일을
    #       집던 C-F-B15 는 라벨 제거가 아니라 `pick_thread` 가드로 막는다.
    sync_cmd = [PY, PROJ / "jobs" / "bplan_sync_job.py", "--limit", "0"]
    if not dry_run:
        sync_cmd.append("--apply")
    st = _run_stage("bplan_sync", sync_cmd)
    rec["stages"].append(st)
    rec["bplan_sync"] = st.get("result")

    st = _run_stage("triage", [PY, PROJ / "triage" / "main.py", "triage"])
    rec["stages"].append(st)
    rec["plan_summary"], rec["escalations"] = _plan_summary(c)
    return True


def phase2(c: dict, rec: dict, dry_run: bool):
    """apply → drop → metrics → digest."""
    if dry_run:
        rec["stages"].append({"name": "apply", "status": "skip", "note": "dry-run"})
    else:
        st = _run_stage("apply", [PY, PROJ / "triage" / "main.py", "apply"])
        rec["stages"].append(st)
        if st["status"] == "ok":
            watched = _watch_drafts_from_plan()
            acts = (rec.get("plan_summary") or {}).get("actions", {})
            rec["drafts_created"] = acts.get("reply", 0) + acts.get("forward", 0)
            rec["stages"].append({"name": "metrics_watch", "status": "ok",
                                  "note": f"측정 대기열 등록 {watched}건"})

    # 🔴 [W12 재빌드] 구 `bplan/main.py drop` → 새 `jobs/bplan_drop_job.py`.
    #    바뀐 것: 멱등 단위가 노션 page_id → **이메일 + 1주일**(같은 사람이 여러 번 접수해도
    #    한 통). 실측 23명/54행이 중복이었고 18명이 실제로 2통 이상 받았다.
    #    ⚠️ 이 잡은 컷오버 전 **실행 자체를 거부**한다(SKILL.md HARD-GATE #1).
    #       그 가드는 사람이 명시적으로 연다 — harness 가 우회하지 않는다.
    drop_cmd = [PY, PROJ / "jobs" / "bplan_drop_job.py", "--limit", "0"]
    if not dry_run:
        drop_cmd.append("--apply")
    st = _run_stage("bplan_drop", drop_cmd)
    rec["stages"].append(st)
    rec["bplan_drop"] = st.get("result")

    try:
        m = metrics_mod.measure(c)
        rec["stages"].append({"name": "metrics", "status": "ok",
                              "note": f"측정 {m['measured']}건 · 대기 {m['pending']}건"})
    except Exception as e:
        rec["stages"].append({"name": "metrics", "status": "fail", "note": str(e)[:200]})
    rec["metrics_summary"] = None
    try:
        rec["metrics_summary"] = metrics_mod.summary(c)
    except Exception:
        pass

    rec["dry_run"] = dry_run
    text = digest_mod.build(rec)
    try:
        via = digest_mod.send(text, c)
        rec["stages"].append({"name": "digest", "status": "ok", "note": via})
    except Exception as e:
        rec["stages"].append({"name": "digest", "status": "fail", "note": str(e)[:200]})
    rec["phase2_done"] = True
    print("\n" + "═" * 60 + "\n" + text)


def main():
    ap = argparse.ArgumentParser(prog="mail-harness")
    ap.add_argument("cmd", choices=["run", "phase1", "phase2", "digest"], nargs="?",
                    default="run")
    ap.add_argument("--dry-run", action="store_true",
                    help="쓰기 없는 리허설: apply 생략, bplan --dry-run")
    args = ap.parse_args()
    c = cfg()
    rec = _load_or_new_record()

    if args.cmd == "digest":
        print(digest_mod.build(rec))
        return

    if args.cmd in ("run", "phase1"):
        ok = phase1(c, rec, args.dry_run)
        _save_record(rec)
        if not ok:
            # selfcheck 실패도 다이제스트는 남긴다 — 조용한 죽음이 최악이다.
            rec["dry_run"] = args.dry_run
            try:
                digest_mod.send(digest_mod.build(rec), c)
            except Exception:
                pass
            sys.exit(2)
        if args.cmd == "phase1":
            ps = rec.get("plan_summary", {})
            print(f"\n📋 phase1 완료 — UNKNOWN {int(ps.get('unknown_ratio', 0) * ps.get('total', 0))}건. "
                  f"reclassify 후 phase2를 실행하세요.")
            return

    phase2(c, rec, args.dry_run)
    _save_record(rec)


if __name__ == "__main__":
    main()
