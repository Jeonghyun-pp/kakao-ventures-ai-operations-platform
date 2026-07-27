"""digest — 실행 결과를 슬랙 다이제스트 한 장으로 (stage: digest).

"사람 없이"는 "관찰 없이"가 아니다: 매 실행의 아카이브·초안·에스컬레이션·실패가
여기로 모인다. SLACK_WEBHOOK_URL(.env)이 없으면 파일로만 남긴다(파이프라인은 안 죽는다).

반-요약 가드레일: 🚩 에스컬레이션 항목은 절대 "기타 n건"으로 뭉치지 않는다 —
제목·발신자·사유를 그대로 옮긴다. 뭉치면 사람이 판단할 수 없다.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent

ICON = {"ok": "✅", "fail": "❌", "skip": "⏭️", "warn": "⚠️"}


def build(run_record: dict) -> str:
    lines = [f"*📮 mail-harness 다이제스트* — {run_record.get('started', '?')}"
             + ("  `[dry-run]`" if run_record.get("dry_run") else "")]

    # ── 단계별 상태 ──────────────────────────────────────────────
    lines.append("")
    for st in run_record.get("stages", []):
        icon = ICON.get(st["status"], "?")
        extra = f" — {st['note']}" if st.get("note") else ""
        lines.append(f"{icon} `{st['name']}`{extra}")

    # ── triage 계획 요약 ─────────────────────────────────────────
    plan = run_record.get("plan_summary")
    if plan:
        lines.append("")
        acts = " · ".join(f"{k} {v}건" for k, v in plan.get("actions", {}).items())
        lines.append(f"*트리아지*: {acts or '처리할 새 메일 없음'}")
        ratio = plan.get("unknown_ratio")
        if ratio is not None and ratio > plan.get("unknown_alert_ratio", 0.30):
            lines.append(f"⚠️ *UNKNOWN 비율 {ratio:.0%}* — 분류기 이상 신호. 발송 자동화 폴백 대상.")

    # ── 🚩 에스컬레이션: 축약 금지, 원문 그대로 ──────────────────
    esc = run_record.get("escalations", [])
    if esc:
        lines.append("")
        lines.append(f"*🚩 사람 확인 필요 {len(esc)}건* (인박스에 남아 있음):")
        for e in esc:
            lines.append(f"  • [{e.get('category')}] {e.get('subject')}")
            lines.append(f"    from {e.get('sender')} — {e.get('note')}")

    # ── bplan 결과 ───────────────────────────────────────────────
    for kind, label in (("sync", "비플랜 동기화"), ("drop", "드랍 초안")):
        r = run_record.get(f"bplan_{kind}")
        if not r:
            continue
        lines.append("")
        if kind == "sync":
            lines.append(f"*{label}*: 노션 등록 {len(r.get('created', []))}건"
                         + (f" · ⚠️ 접수메일 미발견 {r['mail_missing']}" if r.get("mail_missing") else ""))
        else:
            lines.append(f"*{label}*: 생성 {len(r.get('drafted', []))}건"
                         + (f" · 복구 {r['recovered']}" if r.get("recovered") else "")
                         + (f" · ⚠️ 원본메일 미발견 {r['not_found']}" if r.get("not_found") else ""))
        if r.get("halted"):
            lines.append(f"  ⛔ 중단: {r['halted']}")
        for f in r.get("failures", []):
            lines.append(f"  ❌ {f}")

    # ── metrics 주간 요약 ────────────────────────────────────────
    m = run_record.get("metrics_summary")
    if m and m.get("categories"):
        lines.append("")
        lines.append("*무수정 발송률* (승격 기준 ≥95%, 최근 "
                     f"{m.get('window_days', 21)}일):")
        for cat, st in sorted(m["categories"].items()):
            mark = "🟢" if st["rate"] >= m.get("threshold", 0.95) and st["n"] >= 5 else "⚪"
            lines.append(f"  {mark} {cat}: {st['rate']:.0%} (n={st['n']})")

    draft_note = run_record.get("drafts_created", 0)
    if draft_note:
        lines.append("")
        lines.append(f"📮 *초안 {draft_note}건이 Gmail 초안함에 있습니다 — 발송은 사람 몫.*")

    return "\n".join(lines)


def send(text: str, cfg: dict) -> str:
    """웹훅 발송 + 파일 백업. 반환: 'slack' | 'file-only'."""
    out = PROJ / cfg["harness"]["digest"]["fallback_file"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + f"\n\n(생성: {datetime.now().isoformat(timespec='seconds')})")

    # 알림 경로는 kv_core.notify 하나뿐이다. 통합 전에는 webhook(여기)과 bot 토큰(bplan)이
    # 공존해서 "왜 안 왔지"를 두 군데서 찾아야 했다.
    from kv_core import notify
    return "slack" if notify.slack(text) else "file-only"
