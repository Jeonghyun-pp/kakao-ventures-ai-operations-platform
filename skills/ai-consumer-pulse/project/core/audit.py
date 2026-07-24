"""3중 놓침 방지 감사 — 다이제스트 Source Health 푸터 + 애그리게이터-only 목록의 원천.

① 수집 감사: cadence_days 대비 침묵 소스 (HTTP 상태 병기 — 어댑터 고장 vs 실제 침묵 구분)
② 분석 감사: 애그리게이터-only 테마 (1차 소스가 놓쳤을 후보 — themes.json에서)
③ LLM 스팟체크는 사람 몫 (Phase D 체크리스트) — 여기서는 대상 샘플만 추린다
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from core.aggregate import signals_of
from core.common import data_root, sources, week_key
from core.state import load_health


def run(week: str | None = None) -> str:
    wk = week or week_key()
    h = load_health()
    now = datetime.now(timezone.utc)
    lines = [f"# Audit — {wk}", "", "## ① Source Health (cadence 대비 침묵)"]
    for src in sources():
        if src.get("adapter") == "manual":
            continue
        e = h.get(src["id"])
        if not e:
            lines.append(f"- 🔵 {src['id']}: 실행 이력 없음")
            continue
        last_ok = e.get("last_ok")
        if last_ok:
            days = (now - datetime.fromisoformat(last_ok.replace("Z", "+00:00"))).days
            if days > int(src.get("cadence_days", 14)):
                lines.append(f"- 🔴 {src['id']}: {days}일째 신규 없음 "
                             f"(cadence {src['cadence_days']}일, HTTP {e.get('http_status')}, "
                             f"연속0건 {e.get('consecutive_zero', 0)}회)"
                             + (f" — {e['note']}" if e.get("note") else ""))
        elif e.get("consecutive_zero", 0) >= 2:
            lines.append(f"- 🟡 {src['id']}: 한 번도 성공 없음, 연속 {e['consecutive_zero']}회 0건 "
                         f"(HTTP {e.get('http_status')})")
    if len(lines) == 3:
        lines.append("- (침묵 소스 없음)")

    # manual 소스 cadence 알림 (연간 리포트 갱신 상기)
    lines += ["", "## 연간 기준선 갱신 알림"]
    for src in sources("manual"):
        lines.append(f"- {src['id']}: cadence {src['cadence_days']}일 — "
                     f"{src.get('notes', '')} ({src['url']})")

    # 애그리게이터-only 테마
    tj = data_root() / "analysis" / wk / "themes.json"
    lines += ["", "## ② 애그리게이터-only 테마 (1차 소스 놓침 후보)"]
    if tj.exists():
        themes = json.loads(tj.read_text(encoding="utf-8"))["themes"]
        agg_only = [t for t, v in themes.items() if v.get("aggregator_only")]
        lines += [f"- {t}" for t in agg_only] or ["- (없음)"]
    else:
        lines.append("- themes.json 없음 — aggregate 먼저")

    # ③ 스팟체크 샘플 (사람 검수 대상)
    lines += ["", "## ③ Phase D 스팟체크 대상 (인용-원문 대조 2~3건)"]
    ij = data_root() / "analysis" / wk / "items.jsonl"
    if ij.exists():
        import random
        items = [json.loads(l) for l in ij.read_text(encoding="utf-8").splitlines() if l.strip()]
        with_claims = [i for i in items if signals_of(i)]
        random.seed(wk)                                  # 주차 고정 시드 — 재실행 동일
        for it in random.sample(with_claims, min(3, len(with_claims))):
            c = signals_of(it)[0]
            lines.append(f"- [{it['source_id']}] \"{c['claim'][:80]}\" ← 원문에서 "
                         f"\"{c.get('evidence_quote', '')[:60]}...\" 확인")
    out = "\n".join(lines)
    an = data_root() / "analysis" / wk
    an.mkdir(parents=True, exist_ok=True)
    (an / "audit.md").write_text(out, encoding="utf-8")
    print(out)
    return out


if __name__ == "__main__":
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else None)
