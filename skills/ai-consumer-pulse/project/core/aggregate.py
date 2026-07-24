"""결정적 집계 — LLM은 해석만, 수치는 전부 여기서.

입력: analysis/<주차>/items.jsonl (Pass 1 산출 — 아이템당 JSON 1줄)
출력: trends/mentions.jsonl append + analysis/<주차>/themes.json

핵심 규칙 (signal-system-design.md):
- 독립성 단위 = org (org_groups로 a16z 계열 병합 — 채널 2개가 같은 얘기 = 1표)
- 카운트 단위 = 항목(item) — 한 글이 같은 테마 10번 말해도 1
- tier 3(애그리게이터)는 표 없음 — 귀속(attributed_to) 처리 후 잔여분도 검증 표 0
- 일간 소스(daily cap): org당 테마당 주간 기여 상한 3 (도배 방지)
- 신규 신호 게이트: registry에 없음 AND 3주 내 독립 org 2+ AND 서로 다른 event_ref
- rising: 이번 주 ≥ 4주 이동평균×2 AND 절대 ≥3 AND org distinct ≥2
- cooling: 직전 4주 평균 ≥3이던 테마가 2주 연속 그 50% 미만
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from core.common import data_root, org_groups, week_key

DAILY_CAP = 3


def signals_of(item: dict) -> list[dict]:
    """코드북 v2는 'signals', v1은 'claims' 키를 쓴다. 둘 다 받는다.

    v1 산출(2026-W29 파일럿)을 재분석 없이 계속 집계하기 위한 호환 레이어.
    """
    return item.get("signals") or item.get("claims") or []


def _canon_org(org: str, groups: dict) -> str:
    for gname, members in groups.items():
        if org in members:
            return gname
    return org


def _known_orgs() -> set[str]:
    """sources.yaml에 실재하는 org 집합 (+ org_groups 이름)."""
    from core.common import sources
    out = set()
    for s in sources():
        o = s.get("org")
        if o and not str(o).startswith("_"):
            out.add(o)
        for m in (s.get("handles") or s.get("targets") or {}).values():
            out.add(m)
    out |= set(org_groups().keys())
    return out


def _resolve_org(sig: dict, item_org: str, groups: dict, known: set[str]) -> str:
    """멘션을 귀속시킬 org 판정.

    🔴 [2026-07-20] attributed_to를 무검증으로 신뢰하던 버그를 막는다.
    Pass 1이 이 필드에 자유 문자열을 넣는다 — 실측으로 "Capcom", "Newzoo",
    "판별 불가('someone')", "원출처 미상" 같은 값이 들어왔고, 그것들이 전부
    독립 org로 계산돼 org_distinct가 부풀려졌다(ai-gaming 21곳 중 절반이 이런 값).
    승격 게이트가 '독립 org 2곳+'이므로 이 오염은 곧 허위 승격이다.

    규칙: attributed_to가 **우리 소스 목록에 실재하는 org일 때만** 귀속을 옮긴다.
    본문에 등장한 제3자 회사명은 우리 소스가 아니므로 보도한 소스에 그대로 귀속한다.
    """
    att = (sig.get("attributed_to") or "").strip()
    if att:
        c = _canon_org(att, groups)
        if c in known:
            return c
    return _canon_org(item_org or "?", groups)


def load_items(week: str) -> list[dict]:
    p = data_root() / "analysis" / week / "items.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _mentions_history() -> list[dict]:
    p = data_root() / "trends" / "mentions.jsonl"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _registry_slugs() -> set[str]:
    p = data_root() / "themes" / "registry.jsonl"
    if not p.exists():
        return set()
    return {json.loads(l)["slug"] for l in p.read_text(encoding="utf-8").splitlines() if l.strip()}


def run(week: str | None = None) -> dict:
    wk = week or week_key()
    groups = org_groups()
    items = load_items(wk)

    # ── 이번 주 멘션 원장 구성: (theme, org, item_key, event_ref, tier) ──
    rows = []
    known = _known_orgs()
    for it in items:
        tier = it.get("tier", 1)
        for c in signals_of(it):
            org = _resolve_org(c, it.get("org", "?"), groups, known)
            for theme in c.get("theme_tags", []):
                rows.append({"week": wk, "theme": theme, "org": org,
                             "item": it.get("item_key"), "event_ref": c.get("event_ref"),
                             "tier": tier if not c.get("attributed_to") else 1})

    # 항목 단위 dedupe (같은 아이템이 같은 테마 여러 번 → 1)
    uniq = {(r["theme"], r["org"], r["item"]): r for r in rows}
    rows = list(uniq.values())

    # 일간 소스 캡: org×theme 주간 기여 상한
    capped, cnt = [], defaultdict(int)
    for r in rows:
        k = (r["theme"], r["org"])
        if cnt[k] < DAILY_CAP:
            capped.append(r)
            cnt[k] += 1
    rows = capped

    # mentions.jsonl append (이번 주차 기존분 제거 후 재기록 — 재실행 멱등)
    tdir = data_root() / "trends"
    tdir.mkdir(parents=True, exist_ok=True)
    hist = [m for m in _mentions_history() if m["week"] != wk]
    with (tdir / "mentions.jsonl").open("w", encoding="utf-8") as f:
        for m in hist + rows:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")

    # ── 테마별 집계 ──
    all_weeks = sorted({m["week"] for m in hist + rows})
    prev4 = all_weeks[-5:-1] if len(all_weeks) > 1 else []

    def wcount(theme, w, tier_max=2):
        return len([m for m in hist + rows
                    if m["theme"] == theme and m["week"] == w and m.get("tier", 1) <= tier_max])

    themes = {}
    known = _registry_slugs()
    for theme in {r["theme"] for r in rows}:
        this_rows = [r for r in rows if r["theme"] == theme and r.get("tier", 1) <= 2]
        orgs = sorted({r["org"] for r in this_rows})
        events = {r["event_ref"] for r in this_rows}
        distinct_events = (len(events - {None}) + (1 if None in events else 0))
        avg4 = (sum(wcount(theme, w) for w in prev4) / len(prev4)) if prev4 else 0.0
        n = len(this_rows)
        is_new = theme.replace("NEW:", "") not in known and theme.startswith("NEW:")
        promoted = (len(orgs) >= 2 and (distinct_events >= 2 or None in events))
        themes[theme] = {
            "count": n, "orgs": orgs, "org_distinct": len(orgs),
            "avg4": round(avg4, 1),
            "rising": bool(prev4) and n >= max(3, avg4 * 2) and len(orgs) >= 2,
            "is_new_candidate": is_new,
            "new_signal_promoted": is_new and promoted,
            "watchlist": is_new and not promoted,
            "aggregator_only": all(r.get("tier", 1) == 3 for r in
                                   [x for x in rows if x["theme"] == theme]) if
                                   [x for x in rows if x["theme"] == theme] else False,
        }

    out = {"week": wk, "n_items": len(items), "n_mentions": len(rows), "themes": themes,
           "note_no_trend_weeks": len(prev4) < 4}
    an = data_root() / "analysis" / wk
    an.mkdir(parents=True, exist_ok=True)
    (an / "themes.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[aggregate {wk}] items={len(items)} mentions={len(rows)} themes={len(themes)} "
          f"(rising={sum(1 for t in themes.values() if t['rising'])}, "
          f"promoted={sum(1 for t in themes.values() if t['new_signal_promoted'])})")
    return out


if __name__ == "__main__":
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else None)
