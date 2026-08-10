"""KV-ORRERY — 재진입 브리핑. 관측 평면의 첫 표면. [W7 · W9 재설계에서 판정 분리]

## 이 파일이 하는 일 = **렌더링뿐**

W9 이전에는 여기에 판정이 있었다(`_freshness`·`_launchd_direct_health`·`_recent_failures`).
그래서 alerts 와 답이 갈렸다 — **judge 가 4일째 전멸인데 여기선 ⚠️, alerts 는 🔴**.
지금 판정은 전부 `kv_core.health.compute()` 하나에 있고, 이 파일은 그 결과를 **그리기만** 한다.
표면을 추가하려면 렌더러만 늘린다. 판정을 여기 다시 넣으면 이중 진실원이 부활한다.

## 네 가지 질문에 한 판으로 답한다
①뭐 돌았나 ②뭐가 **안** 돌았나(absence) ③뭐 실패했나·격리됐나 ④얼마 썼나(쿼터·$)

## read-only 관측자
아무것도 쓰지 않는다 — 읽기와 조립뿐(유일한 출력은 렌더링·발송이라는 표면).
"""
from __future__ import annotations

from datetime import date

from . import health
from .health import SystemState


def gather(now: date | None = None, *, with_network: bool = True) -> SystemState:
    """상태 수집 — **판정하지 않는다**. health 에 위임하고 결과를 그대로 돌려준다."""
    return health.compute(now=now, with_network=with_network)


# ─────────────────────────── 렌더러 ───────────────────────────

def render_text(s: SystemState) -> str:
    L: list[str] = []
    # [D11] mtime 은 "발화 성공"이 아니라 "로그가 마지막으로 자란 시각"(실패·수동 kickstart 포함)
    L.append(f"╔═ KV-ORRERY 재진입 브리핑 · {s.now.isoformat()} (UTC) "
             f"· 로그 마지막 갱신 {s.log_last_touch or '기록 없음'}")
    L.append("║")
    L.append("║ ── 약속 vs 실제 (안 벌어진 일 감지) ──")
    for t in s.tasks:
        # [T7] shadow 는 로컬 검증 신호일 뿐 — primary(GH) 건강이 아님을 표기에 박는다
        tag = " [shadow-local]" if t.shadow else ""
        note = t.note + (" · primary(GH) 관측 없음" if t.shadow else "")
        tail = f"(last_success={t.last_success})" if t.last_success else ""
        L.append(f"║  {t.icon} {t.name:18} {(t.cadence or '-'):9}{tag:15} {note} {tail}")
    L.append("║")
    # 미해결만 🔴 — 해결분(나중 attempt 성공·이후 last_success)은 요약 1줄로 강등
    un = s.unresolved_failures
    resolved_n = len(s.failures) - len(un)
    L.append(f"║ ── 실패 잡 (최근 7일, 미해결 {len(un)}건"
             + (f" · 해결됨 {resolved_n}건" if resolved_n else "") + ") ──")
    for f in un[:5]:
        ts = "state 파손" if f.broken else str(f.ts)[:16]
        L.append(f"║  🔴 {f.skill}/{f.job}  ({ts})")
    if not un:
        L.append("║  (미해결 없음)")
    # [D5] 파손(-1)은 합산에서 제외하고 별도 표기 — 센티널을 산술에 섞지 않는다
    dlq_ok = [q for q in s.dlq if q["count"] > 0]
    dlq_broken = [q for q in s.dlq if q["count"] < 0]
    L.append(f"║ ── DLQ 격리 ({sum(q['count'] for q in dlq_ok)}건"
             + (f" · 파손 파일 {len(dlq_broken)}개" if dlq_broken else "") + ") ──")
    for q in dlq_ok:
        L.append(f"║  ⚠️ {q['skill']}/{q['file']}: {q['count']}건")
    for q in dlq_broken:
        L.append(f"║  🔴 {q['skill']}/{q['file']}: 읽기 실패(파손)")
    if not dlq_ok and not dlq_broken:
        L.append("║  (없음)")
    # [A-M6] 오늘 벌어진 일을 표면에 남긴다 — 예전엔 pending() 기반이라 거부·실행완료가
    # 관측에서 통째로 사라졌고 `❌거부` 는 어떤 입력에도 0인 죽은 카운터였다.
    ap = s.approvals
    td = ap.get("today", {})
    L.append(f"║ ── 승인 큐 (⏳미결 {ap.get('undecided', 0)}"
             f" · 실행대기 {ap.get('approved_pending_exec', 0)}"
             f" · 오늘 ✅{td.get('approved', 0)} ❌{td.get('rejected', 0)}"
             f" 📤{td.get('executed', 0)} · 누적 {ap.get('total', 0)}) ──")
    for row in ap.get("undecided_rows", []):
        L.append(f"║  ⏳ [{row['kind']}] {row['task']} · {row['key']}")
    if not ap.get("total"):
        L.append("║  (없음)")
    if s.alerts_today:
        L.append(f"║ ── 오늘 즉시 알림 ({len(s.alerts_today)}건) ── "
                 f"{' · '.join(s.alerts_today[:4])}")
    cl = s.costs["claude"]
    warn = "미설정" if cl["warn"] is None else cl["warn"]
    block = "미설정" if cl["block"] is None else cl["block"]
    L.append("║ ── 비용·쿼터 ──")
    # [D12] 창 기준 명시 — "오늘"은 UTC 일 단위(KST 09시에 리셋)
    L.append(f"║  claude: 오늘 {cl['calls']}콜 (경고 {warn} · 차단 {block}) — UTC 일 단위·KST 09시 리셋")
    ap_cost = s.costs["apify"]
    if ap_cost.get("available"):
        L.append(f"║  apify : ${ap_cost['used']:.2f} / ${ap_cost['ceiling']:.0f}")
    else:
        L.append("║  apify : 조회 불가(토큰 없음/오프라인)")
    L.append("╚═")
    return "\n".join(L)


def render_slack(s: SystemState) -> str:
    """슬랙 다이제스트 — 몇 줄 요약. 문제 없으면 짧게, 있으면 문제부터."""
    bad = [t for t in s.tasks if t.icon == "🔴"]
    warn = [t for t in s.tasks if t.icon == "⚠️"]
    ok = [t for t in s.tasks if t.icon == "✅"]
    dlq_n = sum(q["count"] for q in s.dlq if q["count"] > 0)
    cl = s.costs["claude"]

    L = [f"*KV-ORRERY* {s.now.isoformat()} — ✅{len(ok)} ⚠️{len(warn)} 🔴{len(bad)}"]
    for t in bad + warn:
        tag = "[shadow-local] " if t.shadow else ""     # [T7] 컷오버 판단 오염 방지
        L.append(f"{t.icon} {tag}{t.name}: {t.note}")
    un = s.unresolved_failures
    if un:
        recent = next((f for f in un if not f.broken), un[0])
        L.append(f"🔴 미해결 실패 잡 {len(un)}건 (최근: {recent.skill}/{recent.job})")
    dlq_broken = [q for q in s.dlq if q["count"] < 0]
    if dlq_n:
        L.append(f"⚠️ DLQ 격리 {dlq_n}건")
    if dlq_broken:                                      # [D5] 슬랙에서도 파손 은닉 금지
        L.append(f"🔴 DLQ 파일 파손 {len(dlq_broken)}개")
    ap = s.approvals
    if ap.get("undecided"):
        L.append(f"⏳ 승인 대기 {ap['undecided']}건 — ✅/❌ 이모지로 결정")
    td = ap.get("today", {})
    if any(td.values()):
        L.append(f"오늘 승인 ✅{td.get('approved', 0)} ❌{td.get('rejected', 0)} "
                 f"📤{td.get('executed', 0)}")
    block = "미설정" if cl["block"] is None else cl["block"]
    L.append(f"claude {cl['calls']}콜/일 (차단 {block}, UTC 기준)")
    return "\n".join(L)


def push_slack(s: SystemState | None = None) -> bool:
    """다이제스트 발송 — 실패해도 파이프라인을 죽이지 않는다(notify 규약)."""
    from . import notify
    s = s if s is not None else gather()
    return notify.slack(render_slack(s))


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="KV-ORRERY 재진입 브리핑 (read-only)")
    ap.add_argument("--slack", action="store_true", help="터미널 대신 슬랙 다이제스트 발송")
    ap.add_argument("--no-net", action="store_true", help="네트워크 조회(apify) 생략")
    args = ap.parse_args(argv)
    from . import secrets
    secrets.load()                     # APIFY_TOKEN·웹훅 — 읽기용
    s = gather(with_network=not args.no_net)
    if args.slack:
        ok = push_slack(s)
        print("슬랙 발송 " + ("✅" if ok else "실패(웹훅 미설정?) — 아래 텍스트로 대체:"))
        if not ok:
            print(render_text(s))
        return 0
    print(render_text(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
