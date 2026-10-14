"""그날 다시 올린(resurfaced) 사람을 **지금 규칙**으로 다시 판정한다 — 변화 판정·사람 검사 규칙을 고친 뒤에 쓴다. 돈 0(원본 아카이브).

    .venv/bin/python -m tools.linkedin_recheck_reassess 2026-10-14          # 보기만
    .venv/bin/python -m tools.linkedin_recheck_reassess 2026-10-14 --apply  # 아닌 사람은 철회(resurfaced revoked) + 노션 Status 원래대로

발단(10/14): 첫 재확인이 회사·직함 지문 전체를 비교해 390명 중 87명을 '변화'로 올렸다 — 직함 표기·부업·무관한 이직이 대부분이었고,
국적 태그가 없는 옛 Legacy 행의 외국인도 섞였다. 규칙(recheck.moved · _not_target)을 고친 뒤 그날 올린 사람을 같은 원본으로 다시 잰다.
"""
from __future__ import annotations

import glob
import json
import sys

from kvlib import ledger, paths
from skills.linkedin import judge, profile, recheck, run


def _scraped(day: str) -> dict[str, dict]:
    """그날 재확인 원본 — publicIdentifier(소문자) → item."""
    out = {}
    for fn in glob.glob(str(paths.data("linkedin", "raw", "_actor") / f"{recheck.SLUG}_{day}T*.json")):
        for it in json.load(open(fn)).get("items") or []:
            if it.get("publicIdentifier"):
                out[str(it["publicIdentifier"]).lower()] = it
    return out


def main(day: str, apply: bool) -> int:
    g = run._gate()
    rs = [r for r in run._last(run.RESURFACED, "page_id").values() if r["ts"][:10] == day and not r.get("revoked")]
    checked = {r["page_id"]: r for r in ledger.rows(run.TASK, ev=run.CHECKED) if r["ts"][:10] == day}
    posted = run._last(run.POSTED, "page_id")
    raw, got = recheck.raw_by_key(), _scraped(day)
    rows = {r["_page_id"]: r for r in run.notion.query(run.notion.writable_id(run.DB), ["Korea Signal", "Axis", "서비스명"]).require()}
    keep, drop, items = [], [], {}
    for r in rs:
        c = checked.get(r["page_id"]) or {}
        it, base = got.get(str(c.get("pub") or "").lower()), raw.get(r["key"])
        why = recheck.moved(recheck.founder_fp(base) if base else None, recheck.founder_fp(it)) if it else ""
        (keep if why else drop).append((r, why or "창업 쪽 변화 아님"))
        if why:
            items[r["key"]] = (it, ((rows.get(r["page_id"]) or {}).get("Axis") or [""])[0])
    bad = recheck._not_target(items) if items else {}
    for r, why in list(keep):
        if r["key"] in bad:
            keep.remove((r, why))
            drop.append((r, bad[r["key"]]))
    print(f"· {day} 다시 올린 {len(rs)}명 → 유지 {len(keep)} · 철회 {len(drop)}")
    for r, why in keep:
        print(f"  ✓ {r.get('name')}: {why}")
    for r, why in drop[:15]:
        print(f"  ✗ {r.get('name')}: {why}")
    if not apply:
        print("  (보기만 — --apply 면 철회·노션 Status 원래대로)")
        return 0
    for r, why in drop:
        status = "New" if r["page_id"] in posted else run.LEGACY                 # 카드로 나갔던 사람은 원래 New 였다
        props = {"Status": status}
        if why == judge.FOREIGN:
            props["Korea Signal"] = sorted({*((rows.get(r["page_id"]) or {}).get("Korea Signal") or []), judge.FOREIGN})
        f = run.notion.update(r["page_id"], run.DB, props)
        if not f.is_measured:
            print(f"  🔴 {r.get('name')} 노션 되돌리기 실패: {f.reason} — 철회도 안 적는다(다음에 다시)")
            continue
        ledger.append(run.TASK, r["key"], run.RESURFACED, page_id=r["page_id"], revoked=True, why=why, name=r.get("name"))
    for r, why in keep:                                                          # 이유를 새 규칙의 말로 남긴다
        ledger.append(run.TASK, r["key"], run.RESURFACED, page_id=r["page_id"], old=r.get("old"), new=r.get("new"), why=why,
                      tier=r.get("tier"), name=r.get("name"))
    print("· 적용 끝")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], "--apply" in sys.argv))
