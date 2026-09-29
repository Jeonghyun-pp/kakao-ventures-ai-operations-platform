"""재고에 국적 태그를 단다 — 2026-09-28 이전에 들어온 사람은 판정을 안 거쳤다.

    .venv/bin/python -m tools.linkedin_nationality_backfill          # 판정만 보여준다 (노션 안 씀)
    .venv/bin/python -m tools.linkedin_nationality_backfill --apply  # `Korea Signal` 에 `외국인` 을 단다

대상 = 카드로 나갈 수 있는 사람(card() 와 같은 거르기)에서 KAIST 축과 이미 태그 달린 사람을 뺀 것.
판정 재료는 원본 아카이브(거주지·학력)가 있으면 그걸, 없으면 노션 칸을 쓴다.
"""
from __future__ import annotations

import glob
import json
import sys

from kvlib import ledger, paths
from kv_transport import notion
from skills.linkedin import axes, judge, profile, run


def _raw_by_url() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for fn in glob.glob(str(paths.data_root() / "**" / "*.json"), recursive=True):
        if "linkedin" not in fn and "net-sourcing" not in fn:
            continue
        try:
            d = json.load(open(fn))
        except Exception:
            continue
        items = d if isinstance(d, list) else (d.get("items") or []) if isinstance(d, dict) else []
        for it in items if isinstance(items, list) else []:
            if isinstance(it, dict) and it.get("linkedinUrl"):
                out[profile.normalize_url(str(it["linkedinUrl"]))] = it
    return out


def _ensure_option() -> None:
    db = notion.writable_id(run.DB)
    have = notion.allowed_options(db, "Korea Signal")
    if judge.FOREIGN in have:
        return
    notion._req("PATCH", f"/databases/{db}", json_body={"properties": {"Korea Signal": {"multi_select": {
        "options": [{"name": x} for x in have] + [{"name": judge.FOREIGN}]}}}})
    notion.schema.cache_clear() if hasattr(notion.schema, "cache_clear") else None
    print(f"· `Korea Signal` 에 `{judge.FOREIGN}` 옵션을 추가했다")


def main(apply: bool) -> int:
    rows = notion.query(notion.writable_id(run.DB),
                        ["LinkedIn URL", "Tier", "서비스명", "Author Display Name", "Author Headline", "회사",
                         "One Liner", "Raw Text", "Axis", "타깃", "DB로 업데이트", "Tenure Months", "공백 개월",
                         "본업 공백", "Korea Signal"]).require()
    posted = {r["page_id"] for r in ledger.rows(run.TASK, ev=run.POSTED) if r.get("page_id")}
    todo = [r for r in rows
            if r["_page_id"] not in posted and not r.get("타깃") and not r.get("DB로 업데이트")
            and r.get("Tier") in run._cfg("card_tiers") and not axes.card_reject(r)
            and not any(axes.nationality_exempt(a) for a in (r.get("Axis") or []))]
    print(f"· 대상 {len(todo)}명")
    raw = _raw_by_url()
    lines = {}
    for r in todo:
        it = raw.get(profile.normalize_url(str(r.get("LinkedIn URL") or "")))
        if it:
            name, loc, schools, work = profile.nationality_input(it)
        else:
            name, loc, schools = r.get("Author Display Name") or r.get("서비스명"), "", ""
            work = " · ".join(str(r.get(k) or "") for k in ("Author Headline", "One Liner", "Raw Text"))
        lines[r["_page_id"]] = judge.nationality_line(r["_page_id"], name, loc, schools, work)
    print(f"  원본 있음 {sum(1 for r in todo if profile.normalize_url(str(r.get('LinkedIn URL') or '')) in raw)} · 노션 칸만 {len(todo) - sum(1 for r in todo if profile.normalize_url(str(r.get('LinkedIn URL') or '')) in raw)}")
    nat = judge.nationality(lines).require()
    foreign = [r for r in todo if nat.get(r["_page_id"]) == judge.FOREIGN]
    print(f"· 외국인 {len(foreign)} · 한국인 {sum(1 for v in nat.values() if v != judge.FOREIGN)} · 판정 누락 {len(todo) - len(nat)} (한국인으로 둔다)")
    for r in foreign:
        print(f"    {r.get('서비스명')}  |  {lines[r['_page_id']].split(chr(9))[2][:30]}  |  {str(r.get('Author Headline') or '')[:50]}")
    if not apply:
        print("  (보기만 했다 — --apply 로 노션에 쓴다)")
        return 0
    _ensure_option()
    bad = 0
    for r in foreign:
        f = notion.update(r["_page_id"], run.DB, {"Korea Signal": list(r.get("Korea Signal") or []) + [judge.FOREIGN]})
        if not f.is_measured:
            bad += 1
            print(f"  🔴 {r.get('서비스명')} 태그 실패: {f.reason}")
    print(f"· 태그 {len(foreign) - bad}/{len(foreign)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
