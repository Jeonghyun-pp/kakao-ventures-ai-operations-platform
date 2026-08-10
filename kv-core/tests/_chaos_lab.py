"""[W9] 장애 주입 실험 (GameDay) — 안전장치가 "있다"가 아니라 "**작동한다**"를 증명.

## 실험 형식
각 실험은 ①정상상태 가설 ②blast radius ③주입 ④관측 ⑤판정 을 명시한다. 합성 테스트가
"내가 상상한 실패"를 검증했다면, 여기는 **실제 병원체**(kill -9·파손·경합·연쇄 실패)를 넣는다.

## blast radius 총괄
발송 경로 부재(AST 상시검증) + Apify 미접촉 + 노션 미접촉(전부 스텁) + 스크래치 KV_DATA.
→ 최악의 결과 = 스크래치 디렉토리 오염. 실 시스템·외부 서비스 영향 0.

실행: KV_DATA=<스크래치> python kv-core/tests/_chaos_lab.py
"""
from __future__ import annotations

import os
os.environ.setdefault("KV_NO_SEND", "1")   # 실발송 킬 스위치

import json
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"
assert Path(os.environ["KV_DATA"]).resolve() != (Path.home() / "kv-data").resolve(), \
    "🔴 운영 KV_DATA 에서 카오스 실험 금지"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import alerts, approval, approvals_tick, gate, job, notify, paths, scheduler  # noqa: E402
from kv_core.registry import Task  # noqa: E402

notify.slack = lambda *a, **k: True
RESULTS: list[dict] = []


def _is_json(s: str) -> bool:
    try:
        json.loads(s)
        return True
    except json.JSONDecodeError:
        return False


def experiment(no: str, name: str, hypothesis: str):
    """실험 데코레이터 — 가설을 명시하고 판정을 기록한다."""
    def deco(fn):
        print(f"\n━━ {no} {name}")
        print(f"   가설: {hypothesis}")
        t0 = time.time()
        try:
            ok, detail = fn()
        except Exception as e:                    # noqa: BLE001
            ok, detail = False, f"실험 중 예외: {e!r}"
        dur = time.time() - t0
        print(f"   {'✅ 가설 유지' if ok else '❌ 가설 반증'} — {detail} ({dur:.1f}s)")
        RESULTS.append({"no": no, "name": name, "ok": ok, "detail": detail, "sec": round(dur, 1)})
        return fn
    return deco


# ── E1 · 노션 429/네트워크 폭주 ─────────────────────────────────────────────
@experiment("E1", "노션 429 폭주", "재시도 소진 후 loud 실패 · 워터마크 미전진(유실 0) · 다음 시도가 재개")
def e1():
    t = Task(name="chaos-e1", skill="_chaos", cadence="daily",
             catch_up="coalesce", watermark="last_success")
    calls = {"n": 0}

    def flaky_runner(task, period):
        calls["n"] += 1
        if calls["n"] <= 2:                        # 첫 2회는 429 상당의 실패
            raise RuntimeError("429 Too Many Requests (주입)")
        return True

    scheduler.run_due([t], flaky_runner, now=date(2026, 8, 7))
    wm1 = scheduler.read_watermark(t)
    if wm1.get("last_success"):
        return False, "실패했는데 last_success 가 전진했다(유실 위험)"
    scheduler.run_due([t], flaky_runner, now=date(2026, 8, 7))   # 2차 실패
    scheduler.run_due([t], flaky_runner, now=date(2026, 8, 7))   # 3차 성공
    wm2 = scheduler.read_watermark(t)
    return (wm2.get("last_success") == "2026-08-07",
            f"2회 실패 후 3회차 복구 · last_attempt={wm1.get('last_attempt')} → last_success={wm2.get('last_success')}")


# ── E2 · 파이프라인 한복판 kill -9 ──────────────────────────────────────────
@experiment("E2", "파이프라인 kill -9", "완료 스테이지 스킵 · 항목 중복 0 · events 무손상 · 재개로 완주")
def e2():
    env = dict(os.environ, KV_DATA=os.environ["KV_DATA"] + "/e2", W3_N="6", W3_SLEEP="0.4")
    Path(env["KV_DATA"]).mkdir(parents=True, exist_ok=True)
    script = str(Path(__file__).resolve().parent / "_synthetic_pipeline.py")
    p = subprocess.Popen([sys.executable, script], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.6)                                # land 스테이지 한복판
    p.kill()
    p.wait()
    r = subprocess.run([sys.executable, script], env=env, capture_output=True, text=True)
    data = Path(env["KV_DATA"]) / "synthetic"
    results = list((data / "results").glob("*.json"))
    log = (data / "invocations.log").read_text(encoding="utf-8").splitlines()
    collects = sum(1 for ln in log if ln == "COLLECT")
    evs = job.read_events("synthetic", "synthpipe")                # 파손이면 여기서 raise
    return ("completed" in r.stdout and len(results) == 6 and collects == 1,
            f"완주={('completed' in r.stdout)} · 결과 {len(results)}/6(중복0) · "
            f"COLLECT {collects}회(재개 시 스킵) · events {len(evs)}줄 판독 OK")


# ── E3 · 동시 이중 실행 ─────────────────────────────────────────────────────
@experiment("E3", "동시 이중 실행", "락이 한쪽을 물린다 · 워터마크 단일 전진 · 부작용 1회")
def e3():
    with job.heavy_slot("chaos-e3"):
        try:
            with job.heavy_slot("chaos-e3"):
                return False, "같은 락을 두 번 잡았다(배타 실패)"
        except job.SlotBusy:
            pass
    t = Task(name="chaos-e3", skill="_chaos", cadence="daily",
             catch_up="coalesce", watermark="last_success")
    runs = {"n": 0}
    scheduler.run_due([t], lambda task, p: (runs.__setitem__("n", runs["n"] + 1), True)[1],
                      now=date(2026, 8, 7))
    scheduler.run_due([t], lambda task, p: (runs.__setitem__("n", runs["n"] + 1), True)[1],
                      now=date(2026, 8, 7))        # 같은 날 재발화 = 멱등 no-op
    return runs["n"] == 1, f"SlotBusy 배타 ✅ · 같은 날 러너 호출 {runs['n']}회(1 기대)"


# ── E4 · 파일 파손 4종 ──────────────────────────────────────────────────────
@experiment("E4", "파일 파손 4종", "각각 강등/복구/loud — **전체 크래시 0**")
def e4():
    from kv_core import orrery
    notes = []

    # (a) events torn tail → append 후에도 판독 가능
    job.create("_chaos", {"k": 1}, "corrupt-ev")
    p = job._events_path("_chaos", "corrupt-ev")
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"ts": "2026-01-01", "kind": "half')      # 개행 없는 torn
    job.append_event("_chaos", "corrupt-ev", "marker")
    evs = job.read_events("_chaos", "corrupt-ev")
    notes.append(f"events torn→복구({len(evs)}줄)")

    # (b) 워터마크 파손 → 브리핑 전체가 아니라 그 task 만 ⚠️
    t = Task(name="chaos-wm", skill="_chaos", cadence="daily",
             catch_up="coalesce", watermark="last_success")
    job.atomic_write(scheduler._wm_path(t), '{"last_success": "not-a-date"}')
    from kv_core import health as _hz      # [W9 Batch B] 판정 정본은 health 하나다
    st = _hz.compute(now=date(2026, 8, 7), tasks=[t]).tasks[0]
    notes.append(f"워터마크 파손→{st.icon}")

    # (c) 승인 큐 레코드 파손 → 건너뛰고 계속
    (approval._queue_dir() / "broken.json").write_text("{ not json", encoding="utf-8")
    n_pending = len(approval.pending())
    notes.append(f"큐 파손→pending {n_pending}건 정상 반환")

    # (d) claude 카운터 torn → 다음 기록이 병합줄을 만들지 않음
    d = gate.usage_dir("claude")
    d.mkdir(parents=True, exist_ok=True)
    cp = d / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl"
    cp.write_text('{"ts":"x","ok":true}\n{"ts":"y","ok":tr', encoding="utf-8")
    gate.record_call("claude", ok=True)
    lines = [ln for ln in cp.read_text(encoding="utf-8").splitlines() if ln.strip()]
    parsed = sum(1 for ln in lines if _is_json(ln))
    notes.append(f"카운터 torn→전 줄 판독 {parsed}/{len(lines)}")

    ok = (len(evs) >= 2 and st.icon == "⚠️" and parsed == len(lines))
    # 브리핑 전체가 살아있는지 최종 확인
    orrery.gather(with_network=False)
    return ok, " · ".join(notes) + " · 브리핑 크래시 0"


# ── E5 · 인증 만료 시뮬 (MTTD 측정) ─────────────────────────────────────────
@experiment("E5", "인증 만료 시뮬", "3연속 실패 → auth-suspect 발동 (MTTD = 다음 틱까지)")
def e5():
    today = datetime.now(timezone.utc).date()
    d = paths.data_root() / "_usage" / "claude"
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"ts": f"{today.isoformat()}T0{i}:00:00+00:00", "ok": False, "err": "empty_output"}
            for i in range(3)]
    (d / f"{today.isoformat()}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    for m in (paths.data_root() / "_alerts" / "sent").glob("auth-suspect-*"):
        m.unlink()
    got = alerts.scan()
    return ("auth-suspect-claude" in got,
            f"발동={got} · MTTD=다음 approvals 틱(≤10분) — 예전엔 rc0 이라 **영원히 미발동**이었다")


# ── E6 · 게이트 차단 (연쇄 관측) ────────────────────────────────────────────
@experiment("E6", "게이트 차단", "차단 · 1일 1회 알림 · 이벤트 장부 기록 · 복구 후 정상")
def e6():
    gate._cfg = {"claude": {"daily_calls_warn": 1, "daily_calls_block": 1}}
    for d, pat in ((paths.data_root() / "_alerts" / "sent", "gate-block-*"),
                   (paths.data_root() / "_alerts" / "policy", "gate-block-*.json")):
        for m in (d.glob(pat) if d.exists() else ()):
            m.unlink()
    sent: list = []
    notify.slack = lambda text, **kw: (sent.append(text), True)[1]
    v = gate.check_calls("claude", quiet=True)      # 이미 카운터에 기록이 있어 초과
    # [W9 Batch C] gate 는 **발송하지 않는다** — 차단 사실만 장부에 남긴다.
    # dedup·백오프는 dispatch 한 곳에 있으므로, 두 번 차단돼도 발송은 1회다.
    gate.notify_blocked(v)
    gate.notify_blocked(v)                          # 같은 날 2회차
    ev = (paths.data_root() / "_alerts" / "events.jsonl").read_text(encoding="utf-8")
    n_ev = sum(1 for ln in ev.splitlines() if "gate_block" in ln)
    alerts.dispatch(); alerts.dispatch()            # 두 틱이 돌아도
    gate._cfg = None
    n_gate = sum(1 for t in sent if "게이트 차단" in t)
    return (not v.allowed and n_gate == 1 and n_ev >= 2,
            f"차단={not v.allowed} · 장부 {n_ev}줄 · 실발송 {n_gate}회(1 기대)")


# ── E7 · 승인 왕복 장애 ─────────────────────────────────────────────────────
@experiment("E7", "승인 왕복 장애", "조회 실패는 반응 0과 구분 · 실행 실패는 보상 후 재시도 · 중복 초안 0")
def e7():
    from kv_core import gmail, slackbot
    drafts: list = []
    slackbot.post = lambda text: ("ts-e7", "C-E7")
    slackbot.message_reactions = lambda ch, ts: None          # 조회 실패
    approvals_tick.slackbot = slackbot
    gmail.create_draft = lambda to, s, b, **k: (drafts.append(to), f"d{len(drafts)}")[1]
    gmail.get_draft = lambda did, **k: {"id": did}

    approval.request("chaos", "mail", {"to": "hello@x", "subject": "s", "body": "b"},
                     partner="hello@x", dedup_key="chaos-e7")
    approvals_tick._announce()
    approvals_tick._reconcile()                              # 조회 실패 → 결정 없음
    if approval.load("chaos-e7").get("decision") is not None:
        return False, "조회 실패인데 결정이 생겼다(반응 0과 혼동)"

    slackbot.message_reactions = lambda ch, ts: [{"name": "white_check_mark", "users": ["U1"]}]
    approvals_tick._reconcile()
    gmail.create_draft = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Gmail 장애 주입"))
    approvals_tick._execute()                                # 실패 → 보상
    r = approval.load("chaos-e7")
    if r.get("decision") != "approved" or not r.get("execute_error"):
        return False, f"보상 실패 — decision={r.get('decision')} err={r.get('execute_error')}"
    gmail.create_draft = lambda to, s, b, **k: (drafts.append(to), f"d{len(drafts)}")[1]
    approvals_tick._execute()                                # 복구 후 재시도
    approvals_tick._execute()                                # 멱등 확인
    return (len(drafts) == 1 and approval.load("chaos-e7").get("draft_id"),
            f"조회실패≠반응0 ✅ · 보상 후 복구 ✅ · 초안 {len(drafts)}개(1 기대·재틱 멱등)")


if __name__ == "__main__":
    print(f"=== 카오스 랩 (KV_DATA={os.environ['KV_DATA']}) ===")
    print("blast radius: 스크래치 디렉토리만 — 외부 서비스·실 시스템 영향 0")
    fails = [r for r in RESULTS if not r["ok"]]
    print(f"\n{'─' * 60}")
    print(f"실험 {len(RESULTS)}종 · 가설 유지 {len(RESULTS) - len(fails)} · 반증 {len(fails)}")
    for r in RESULTS:
        print(f"  {'✅' if r['ok'] else '❌'} {r['no']} {r['name']} ({r['sec']}s)")
    if fails:
        raise SystemExit(1)
    print("\n🎉 전 실험 가설 유지 — 안전장치가 실장애에서 작동한다")
