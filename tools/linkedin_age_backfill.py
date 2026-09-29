"""재고에 `추정 나이` 를 채운다 — 2026-09-29 이전에 들어온 사람은 칸이 비어 있다.

    .venv/bin/python -m tools.linkedin_age_backfill          # 보기만
    .venv/bin/python -m tools.linkedin_age_backfill --apply  # 칸이 없으면 만들고 채운다

대상 = 카드로 나갈 수 있는 사람(card() 와 같은 거르기) 중 칸이 빈 사람. 재료는 원본 아카이브다.
"""
from __future__ import annotations

import sys

from kvlib import ledger
from kv_transport import notion
from skills.linkedin import axes, brief, profile, run
from tools.linkedin_nationality_backfill import _raw_by_url

COL = "추정 나이"


def main(apply: bool) -> int:
    db = notion.writable_id(run.DB)
    if COL not in notion.schema(db):
        if not apply:
            print(f"· `{COL}` 칸이 아직 없다 — --apply 가 만든다")
        else:
            notion._req("PATCH", f"/databases/{db}", json_body={"properties": {COL: {"number": {}}}})
            notion.schema.cache_clear()
            print(f"· `{COL}` 칸을 만들었다")
    props = ["LinkedIn URL", "Tier", "서비스명", "Author Headline", "회사", "One Liner", "Raw Text", "타깃",
             "DB로 업데이트", "Tenure Months", "공백 개월", "본업 공백", "Korea Signal"]
    rows = notion.query(db, props + ([COL] if COL in notion.schema(db) else [])).require()
    posted = {r["page_id"] for r in ledger.rows(run.TASK, ev=run.POSTED) if r.get("page_id")}
    scr = run._screened()
    todo = [r for r in rows
            if r["_page_id"] not in posted and not r.get("타깃") and not r.get("DB로 업데이트")
            and r.get("Tier") in run._cfg("card_tiers") and not axes.card_reject(r)
            and scr.get(r["_page_id"], {}).get("verdict") != brief.NOT_FOUNDER and r.get(COL) is None]
    raw = _raw_by_url()
    ages = {}
    for r in todo:
        it = raw.get(profile.normalize_url(str(r.get("LinkedIn URL") or "")))
        a = profile.estimated_age(it) if it else None
        if a is not None:
            ages[r["_page_id"]] = (r, a)
    over = [(r, a) for r, a in ages.values() if a > axes.MAX_AGE]
    print(f"· 대상 {len(todo)}명 · 추정 {len(ages)} · 불가 {len(todo) - len(ages)} (통과) · {axes.MAX_AGE + 1}세 이상 {len(over)}")
    for r, a in sorted(over, key=lambda x: -x[1]):
        print(f"    {a}세  {r.get('서비스명')}  |  {str(r.get('Author Headline') or '')[:50]}")
    if not apply:
        print("  (보기만 했다 — --apply 로 노션에 쓴다)")
        return 0
    bad = 0
    for pid, (r, a) in ages.items():
        f = notion.update(pid, run.DB, {COL: a})
        if not f.is_measured:
            bad += 1
            print(f"  🔴 {r.get('서비스명')}: {f.reason}")
    print(f"· 채움 {len(ages) - bad}/{len(ages)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
