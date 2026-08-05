"""KV-ORRERY — 재진입 브리핑. 관측 평면(observation plane)의 첫 표면. [W7]

## 네 가지 질문에 한 판으로 답한다
①뭐 돌았나 ②뭐가 **안** 돌았나(absence) ③뭐 실패했나·격리됐나 ④얼마 썼나(쿼터·$)

## read-only 관측자
이 모듈은 **아무것도 쓰지 않는다** — 파일 읽기와 조립뿐. 관측이 상태를 바꾸면 관측이 아니다.
(유일한 출력은 렌더링: 터미널 텍스트 또는 슬랙 다이제스트 발송 — 상태 변경이 아니라 표면.)

## 진실은 저장하고 뷰는 계산한다 (on-demand derived view)
원천 = 이미 존재하는 파일들이다. 새 기록을 만들지 않고, 부를 때마다 조립한다(schema-on-read):
    tasks.yaml                          약속(선언된 cadence·runner)
    $KV_DATA/<skill>/cadence/*.json     실제(워터마크)         → 약속-실제 diff = 안 벌어진 일
    $KV_DATA/<skill>/jobs/*/state.json  잡 상태(failed 수색)
    $KV_DATA/<skill>/dlq/*.jsonl        격리(DLQ)
    $KV_DATA/_approvals/*.json          승인 대기
    $KV_DATA/_usage/claude/*.jsonl      claude 콜 미터링
    ~/kv-data/net-sourcing/last_run_health.json   launchd-direct 잡의 헬스(이종 원천)

## 안 벌어진 일 (absence detection)
"로그가 없다는 로그는 없다" — 부재는 **선언(cadence)과 워터마크의 차이**로만 잡힌다.
임계: 지연 > 주기+1일 → 🔴 (W9 stale 감지의 전신 — 여기선 단순 규칙).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from . import cadence, gate, paths, registry, scheduler

try:
    from . import workers
    _WIRED = set(workers.WIRED)
except Exception:                       # workers 미로드 환경에서도 브리핑은 돈다
    _WIRED = set()


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


# ─────────────────────────── 조립 (gather — 순수 읽기) ───────────────────────────

def _task_rows(now: date) -> list[dict]:
    """스케줄 task 별: 약속(cadence) vs 실제(워터마크) → 신선도 판정."""
    rows: list[dict] = []
    for t in registry.load():
        if not t.scheduled:
            continue
        row = {"name": t.name, "cadence": t.cadence, "runner": t.runner,
               "shadow": t.shadow, "wired": t.name in _WIRED}
        if t.runner == "launchd-direct":
            # 스케줄러 밖에서 돎 — 워터마크가 없다. 헬스 파일(있으면)로 신선도 판정(schema-on-read).
            row.update(_launchd_direct_health(t, now))
        else:
            wm = scheduler.read_watermark(t)
            ls, la = wm.get("last_success"), wm.get("last_attempt")
            row.update({"last_success": ls, "last_attempt": la,
                        **_freshness(t, ls, la, now)})
        rows.append(row)
    return rows


def _freshness(t, last_success: str | None, last_attempt: str | None, now: date) -> dict:
    gap = cadence.gap_days(t.cadence, where=t.name)
    if not last_success:
        if last_attempt:
            return {"status": "⚠️", "note": "시도는 있으나 성공 0 — 로그 확인"}
        if t.name not in _WIRED and t.runner == "local":
            return {"status": "🔕", "note": "러너 미배선(워터마크 없음이 정상)"}
        return {"status": "⚠️", "note": "가동 이력 없음"}
    late = (now - date.fromisoformat(last_success[:10])).days
    if late > gap + 1:
        return {"status": "🔴", "note": f"{late}일 밀림 (주기 {gap}일 + 유예 1일 초과)"}
    if late > gap:
        return {"status": "⚠️", "note": f"{late}일 경과 (주기 {gap}일)"}
    return {"status": "✅", "note": f"{late}일 전 성공"}


def _launchd_direct_health(t, now: date) -> dict:
    p = paths.data_root() / t.skill / "last_run_health.json"
    if not p.exists():
        return {"status": "⚠️", "note": "헬스 파일 없음(관측 불가)", "last_success": None}
    try:
        h = json.loads(p.read_text(encoding="utf-8"))
        ts = str(h.get("ts", ""))[:10]
        late = (now - date.fromisoformat(ts)).days if ts else 99
        ok = bool(h.get("ok"))
        status = "✅" if (ok and late <= 1) else ("⚠️" if late <= 2 else "🔴")
        note = f"{late}일 전 {'정상' if ok else '저하(degraded)'} (plist 직접 실행)"
        return {"status": status, "note": note, "last_success": ts if ok else None}
    except (json.JSONDecodeError, ValueError, OSError):
        return {"status": "⚠️", "note": "헬스 파일 파손", "last_success": None}


def _recent_failures(days: int = 7, cap: int = 10) -> list[dict]:
    """최근 failed 잡 — state.json mtime 기준 최근 N일만 훑는다(전량 스캔 회피)."""
    out: list[dict] = []
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    root = paths.data_root()
    if not root.exists():
        return out
    for sp in sorted(root.glob("*/jobs/*/state.json")):
        try:
            if sp.stat().st_mtime < cutoff:
                continue
            st = json.loads(sp.read_text(encoding="utf-8"))
            if st.get("state") == "failed":
                out.append({"skill": sp.parts[-4], "job": sp.parts[-2],
                            "ts": st.get("updated", "?")})
        except (json.JSONDecodeError, OSError):
            out.append({"skill": sp.parts[-4], "job": sp.parts[-2], "ts": "파손"})
    out.sort(key=lambda r: str(r["ts"]), reverse=True)
    return out[:cap]


def _dlq_summary() -> list[dict]:
    out = []
    for p in sorted(paths.data_root().glob("*/dlq/*.jsonl")):
        try:
            n = sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip())
        except OSError:
            n = -1
        out.append({"skill": p.parts[-3], "file": p.name, "count": n})
    return out


def _approvals() -> list[dict]:
    out = []
    for p in sorted((paths.data_root() / "_approvals").glob("*.json")):
        try:
            a = json.loads(p.read_text(encoding="utf-8"))
            out.append({"key": p.stem, "kind": a.get("kind", "?"),
                        "task": a.get("task", "?"), "status": a.get("status", "?")})
        except (json.JSONDecodeError, OSError):
            out.append({"key": p.stem, "kind": "파손", "task": "?", "status": "?"})
    return out


def _cost(now: date, *, with_network: bool = True) -> dict:
    rc = (gate.config().get("claude") or {})
    claude = {"calls": gate.calls_today("claude"),
              "warn": rc.get("daily_calls_warn"), "block": rc.get("daily_calls_block")}
    apify: dict = {"available": False}
    if with_network:
        try:
            v = gate.check("apify", quiet=True)
            apify = {"available": v.used_usd is not None, "used": v.used_usd,
                     "ceiling": v.ceiling_usd}
        except Exception:
            pass                       # 브리핑은 네트워크 장애에도 죽지 않는다
    return {"claude": claude, "apify": apify}


def gather(now: date | None = None, *, with_network: bool = True) -> dict:
    now = now or _utc_today()
    log = paths.data_root() / "_scheduler" / "launchd.log"
    last_fire = (datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc)
                 .isoformat(timespec="minutes") if log.exists() else None)
    return {"now": now.isoformat(), "last_scheduler_fire": last_fire,
            "tasks": _task_rows(now), "failures": _recent_failures(),
            "dlq": _dlq_summary(), "approvals": _approvals(),
            "cost": _cost(now, with_network=with_network)}


# ─────────────────────────── 렌더러 (조립과 분리 — 표면 추가는 여기만) ───────────────────────────

def render_text(d: dict) -> str:
    L: list[str] = []
    L.append(f"╔═ KV-ORRERY 재진입 브리핑 · {d['now']} (UTC) "
             f"· 마지막 발화 {d['last_scheduler_fire'] or '기록 없음'}")
    L.append("║")
    L.append("║ ── 약속 vs 실제 (안 벌어진 일 감지) ──")
    for r in d["tasks"]:
        tag = " [shadow]" if r.get("shadow") else ""
        tail = f"(last_success={r.get('last_success')})" if r.get("last_success") else ""
        L.append(f"║  {r['status']} {r['name']:18} {r['cadence']:9}{tag:9} {r['note']} {tail}")
    L.append("║")
    fails = d["failures"]
    L.append(f"║ ── 실패 잡 (최근 7일, {len(fails)}건) ──")
    for f in fails[:5]:
        L.append(f"║  🔴 {f['skill']}/{f['job']}  ({str(f['ts'])[:16]})")
    if not fails:
        L.append("║  (없음)")
    dlq = [q for q in d["dlq"] if q["count"]]
    L.append(f"║ ── DLQ 격리 ({sum(q['count'] for q in dlq)}건) ──")
    for q in dlq:
        L.append(f"║  ⚠️ {q['skill']}/{q['file']}: {q['count']}건")
    if not dlq:
        L.append("║  (없음)")
    ap = d["approvals"]
    L.append(f"║ ── 승인 대기 ({len(ap)}건) ──")
    for a in ap[:5]:
        L.append(f"║  ⏳ [{a['kind']}] {a['task']} · {a['key']} ({a['status']})")
    if not ap:
        L.append("║  (없음)")
    c = d["cost"]
    cl = c["claude"]
    L.append("║ ── 비용·쿼터 ──")
    L.append(f"║  claude: 오늘 {cl['calls']}콜 (경고 {cl['warn']} · 차단 {cl['block']})")
    if c["apify"].get("available"):
        L.append(f"║  apify : ${c['apify']['used']:.2f} / ${c['apify']['ceiling']:.0f}")
    else:
        L.append("║  apify : 조회 불가(토큰 없음/오프라인)")
    L.append("╚═")
    return "\n".join(L)


def render_slack(d: dict) -> str:
    """슬랙 다이제스트 — 몇 줄 요약. 문제 없으면 짧게, 있으면 문제부터."""
    bad = [r for r in d["tasks"] if r["status"] == "🔴"]
    warn = [r for r in d["tasks"] if r["status"] == "⚠️"]
    ok = [r for r in d["tasks"] if r["status"] == "✅"]
    fails, ap = d["failures"], d["approvals"]
    dlq_n = sum(q["count"] for q in d["dlq"] if q["count"] > 0)
    cl = d["cost"]["claude"]

    L = [f"*KV-ORRERY* {d['now']} — ✅{len(ok)} ⚠️{len(warn)} 🔴{len(bad)}"]
    for r in bad:
        L.append(f"🔴 {r['name']}: {r['note']}")
    for r in warn:
        L.append(f"⚠️ {r['name']}: {r['note']}")
    if fails:
        L.append(f"🔴 실패 잡 {len(fails)}건 (최근: {fails[0]['skill']}/{fails[0]['job']})")
    if dlq_n:
        L.append(f"⚠️ DLQ 격리 {dlq_n}건")
    if ap:
        L.append(f"⏳ 승인 대기 {len(ap)}건")
    L.append(f"claude {cl['calls']}콜/일 (차단 {cl['block']})")
    return "\n".join(L)


def push_slack(d: dict | None = None) -> bool:
    """다이제스트 발송 — 실패해도 파이프라인을 죽이지 않는다(notify 규약)."""
    from . import notify
    d = d or gather()
    return notify.slack(render_slack(d))


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="KV-ORRERY 재진입 브리핑 (read-only)")
    ap.add_argument("--slack", action="store_true", help="터미널 대신 슬랙 다이제스트 발송")
    ap.add_argument("--no-net", action="store_true", help="네트워크 조회(apify) 생략")
    args = ap.parse_args(argv)
    from . import secrets
    secrets.load()                     # APIFY_TOKEN·웹훅 — 읽기용
    d = gather(with_network=not args.no_net)
    if args.slack:
        ok = push_slack(d)
        print("슬랙 발송 " + ("✅" if ok else "실패(웹훅 미설정?) — 아래 텍스트로 대체:"))
        if not ok:
            print(render_text(d))
        return 0
    print(render_text(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
