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
    _WIRED: set | None = set(workers.WIRED)
except Exception as _e:                 # noqa: BLE001
    # [중간점검 D3] 침묵하면 배선된 task 가 "미배선(정상)" 🔕 로 위장된다 — loud + 미상(None) 구분.
    print(f"⚠️  orrery: workers 로드 실패 — 배선 상태 미상으로 표시한다: {_e!r}")
    _WIRED = None


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _ts_to_utc_date(ts: str) -> date:
    """이종 원천 ts 정규화 — naive(오프셋 없음)면 **KST 로 간주**해 UTC 로 변환.

    [중간점검 D4] 헬스 파일 writer 는 naive KST 를 쓰고 orrery 는 UTC now 와 빼서
    🔴 경보가 하루 늦었다(TZ 경계 사고 계열 3번째). schema-on-read 는 읽는 쪽이 정규화한다.
    """
    from datetime import timedelta
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt - timedelta(hours=9)            # KST → UTC
        return dt.date()
    return dt.astimezone(timezone.utc).date()


# ─────────────────────────── 조립 (gather — 순수 읽기) ───────────────────────────

def _task_rows(now: date) -> list[dict]:
    """스케줄 task 별: 약속(cadence) vs 실제(워터마크) → 신선도 판정."""
    rows: list[dict] = []
    for t in registry.load():
        if not t.scheduled:
            continue
        row = {"name": t.name, "cadence": t.cadence, "runner": t.runner,
               "shadow": t.shadow,
               "wired": (t.name in _WIRED) if _WIRED is not None else None}
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
        if _WIRED is None:                       # [D3] workers 로드 실패 — 미상을 정상으로 위장 금지
            return {"status": "⚠️", "note": "배선 상태 미상(workers 로드 실패) — 로그 확인"}
        if t.name not in _WIRED and t.runner == "local":
            return {"status": "🔕", "note": "러너 미배선(워터마크 없음이 정상)"}
        return {"status": "⚠️", "note": "가동 이력 없음"}
    try:
        last = date.fromisoformat(last_success[:10])
    except ValueError:                           # [D2] 파손 1개가 브리핑 전체를 못 죽인다
        return {"status": "⚠️", "note": f"워터마크 파손(last_success={last_success!r}) — 파일 확인"}
    late = (now - last).days
    # [D9] 유예: daily 는 0(한 주기만 밀려도 🔴 — 하루가 곧 결손), 그 외는 +1(수면·보조틱 흡수)
    grace = 0 if gap <= 1 else 1
    if late > gap + grace:
        return {"status": "🔴", "note": f"{late}일 밀림 (주기 {gap}일 + 유예 {grace}일 초과)"}
    if late > gap:
        return {"status": "⚠️", "note": f"{late}일 경과 (주기 {gap}일)"}
    return {"status": "✅", "note": f"{late}일 전 성공"}


def _launchd_direct_health(t, now: date) -> dict:
    p = paths.data_root() / t.skill / "last_run_health.json"
    if not p.exists():
        return {"status": "⚠️", "note": "헬스 파일 없음(관측 불가)", "last_success": None}
    try:
        h = json.loads(p.read_text(encoding="utf-8"))
        raw_ts = str(h.get("ts", ""))
        if not raw_ts:                            # [D10] 매직넘버 99 대신 정직한 부재 표기
            return {"status": "⚠️", "note": "헬스 ts 없음(관측 불가)", "last_success": None}
        d_utc = _ts_to_utc_date(raw_ts)           # [D4] naive KST → UTC 정규화
        late = (now - d_utc).days
        ok = bool(h.get("ok"))
        gap = cadence.gap_days(t.cadence, where=t.name)   # [D10] cadence 선언 사용(하드코딩 제거)
        grace = 0 if gap <= 1 else 1
        if not ok:
            status = "⚠️" if late <= gap else "🔴"
        else:
            status = "✅" if late <= gap else ("⚠️" if late <= gap + max(grace, 1) else "🔴")
        note = f"{late}일 전 {'정상' if ok else '저하(degraded)'} (plist 직접 실행)"
        return {"status": status, "note": note, "last_success": d_utc.isoformat() if ok else None}
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
                            "ts": st.get("updated", ""), "broken": False})
        except (json.JSONDecodeError, OSError):
            # [D8] 파손은 ts="" 로 정렬 맨 뒤 — '파손' 문자열이 유니코드 정렬로 최신 자리를
            # 무기한 점유하던 것 정정. 표기는 렌더러가 broken 플래그로 한다.
            out.append({"skill": sp.parts[-4], "job": sp.parts[-2], "ts": "", "broken": True})
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
    """[D6] 정본 필터 재사용 — approval.pending() 은 sent 를 거른다. 전 파일 나열은
    감사 기록(영구 보존)까지 '대기'로 세어 단조 증가하는 거짓 백로그였다."""
    if not (paths.data_root() / "_approvals").exists():
        return []                                # 큐 자체가 없으면 pending() 을 안 부른다 —
                                                 # pending()의 _queue_dir 가 mkdir 부작용을 가짐(read-only 보호)
    try:
        from . import approval
        return [{"key": r.get("dedup_key", "?"), "kind": r.get("kind", "?"),
                 "task": r.get("task", "?"), "status": r.get("status", "?")}
                for r in approval.pending()]
    except Exception as e:                       # noqa: BLE001
        print(f"⚠️  orrery: 승인 큐 조회 실패 — {e!r}")
        return []


def _cost(*, with_network: bool = True) -> dict:
    rc = (gate.config().get("claude") or {})
    claude = {"calls": gate.calls_today("claude"),
              "warn": rc.get("daily_calls_warn"), "block": rc.get("daily_calls_block")}
    apify: dict = {"available": False}
    if with_network:
        try:
            v = gate.check("apify", quiet=True)
            apify = {"available": v.used_usd is not None, "used": v.used_usd,
                     "ceiling": v.ceiling_usd}
        except Exception as e:         # noqa: BLE001  브리핑은 네트워크 장애에도 죽지 않는다
            print(f"⚠️  orrery: apify 조회 실패 — {e!r}")   # [D12] 삼킨 예외도 한 줄은 남긴다
    return {"claude": claude, "apify": apify}


def gather(now: date | None = None, *, with_network: bool = True) -> dict:
    now = now or _utc_today()
    log = paths.data_root() / "_scheduler" / "launchd.log"
    try:                               # [D11] exists→stat 사이 삭제 내성
        last_touch = (datetime.fromtimestamp(log.stat().st_mtime, tz=timezone.utc)
                      .isoformat(timespec="minutes"))
    except OSError:
        last_touch = None
    return {"now": now.isoformat(), "log_last_touch": last_touch,
            "tasks": _task_rows(now), "failures": _recent_failures(),
            "dlq": _dlq_summary(), "approvals": _approvals(),
            "cost": _cost(with_network=with_network)}


# ─────────────────────────── 렌더러 (조립과 분리 — 표면 추가는 여기만) ───────────────────────────

def render_text(d: dict) -> str:
    L: list[str] = []
    # [D11] mtime 은 "발화 성공"이 아니라 "로그가 마지막으로 자란 시각"(실패·수동 kickstart 포함)
    L.append(f"╔═ KV-ORRERY 재진입 브리핑 · {d['now']} (UTC) "
             f"· 로그 마지막 갱신 {d['log_last_touch'] or '기록 없음'}")
    L.append("║")
    L.append("║ ── 약속 vs 실제 (안 벌어진 일 감지) ──")
    for r in d["tasks"]:
        # [T7] shadow 는 로컬 검증 신호일 뿐 — primary(GH) 건강이 아님을 표기에 박는다
        tag = " [shadow-local]" if r.get("shadow") else ""
        note = r["note"] + (" · primary(GH) 관측 없음" if r.get("shadow") else "")
        tail = f"(last_success={r.get('last_success')})" if r.get("last_success") else ""
        L.append(f"║  {r['status']} {r['name']:18} {r['cadence']:9}{tag:15} {note} {tail}")
    L.append("║")
    fails = d["failures"]
    L.append(f"║ ── 실패 잡 (최근 7일, {len(fails)}건) ──")
    for f in fails[:5]:
        ts = "state 파손" if f.get("broken") else str(f["ts"])[:16]
        L.append(f"║  🔴 {f['skill']}/{f['job']}  ({ts})")
    if not fails:
        L.append("║  (없음)")
    # [D5] 파손(-1)은 합산에서 제외하고 별도 표기 — 센티널을 산술에 섞지 않는다
    dlq_ok = [q for q in d["dlq"] if q["count"] > 0]
    dlq_broken = [q for q in d["dlq"] if q["count"] < 0]
    L.append(f"║ ── DLQ 격리 ({sum(q['count'] for q in dlq_ok)}건"
             + (f" · 파손 파일 {len(dlq_broken)}개" if dlq_broken else "") + ") ──")
    for q in dlq_ok:
        L.append(f"║  ⚠️ {q['skill']}/{q['file']}: {q['count']}건")
    for q in dlq_broken:
        L.append(f"║  🔴 {q['skill']}/{q['file']}: 읽기 실패(파손)")
    if not dlq_ok and not dlq_broken:
        L.append("║  (없음)")
    ap = d["approvals"]
    L.append(f"║ ── 승인 대기 ({len(ap)}건) ──")
    for a in ap[:5]:
        L.append(f"║  ⏳ [{a['kind']}] {a['task']} · {a['key']} ({a['status']})")
    if not ap:
        L.append("║  (없음)")
    c = d["cost"]
    cl = c["claude"]
    warn = "미설정" if cl["warn"] is None else cl["warn"]
    block = "미설정" if cl["block"] is None else cl["block"]
    L.append("║ ── 비용·쿼터 ──")
    # [D12] 창 기준 명시 — "오늘"은 UTC 일 단위(KST 09시에 리셋)
    L.append(f"║  claude: 오늘 {cl['calls']}콜 (경고 {warn} · 차단 {block}) — UTC 일 단위·KST 09시 리셋")
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
        tag = "[shadow-local] " if r.get("shadow") else ""     # [T7] 컷오버 판단 오염 방지
        L.append(f"🔴 {tag}{r['name']}: {r['note']}")
    for r in warn:
        tag = "[shadow-local] " if r.get("shadow") else ""
        L.append(f"⚠️ {tag}{r['name']}: {r['note']}")
    if fails:
        recent = next((f for f in fails if not f.get("broken")), fails[0])
        L.append(f"🔴 실패 잡 {len(fails)}건 (최근: {recent['skill']}/{recent['job']})")
    dlq_broken = [q for q in d["dlq"] if q["count"] < 0]
    if dlq_n:
        L.append(f"⚠️ DLQ 격리 {dlq_n}건")
    if dlq_broken:                                             # [D5] 슬랙에서도 파손 은닉 금지
        L.append(f"🔴 DLQ 파일 파손 {len(dlq_broken)}개")
    if ap:
        L.append(f"⏳ 승인 대기 {len(ap)}건")
    block = "미설정" if cl["block"] is None else cl["block"]
    L.append(f"claude {cl['calls']}콜/일 (차단 {block}, UTC 기준)")
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
