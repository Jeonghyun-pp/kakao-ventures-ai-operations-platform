"""재고 Tier 다시 매기기 — criteria.md 를 바꾼 뒤 한 번. 아직 안 나간 재고를 **지금 기준**으로 판정해 노션 Tier 를 덮어쓴다.

    .venv/bin/python -m tools.linkedin_retier          # 보기만 (claude 는 부른다 — 무엇이 바뀌는지 보여 주려고)
    .venv/bin/python -m tools.linkedin_retier --apply  # 노션 Tier·AI Reason 덮어쓰기 + judgments(live) 저장

왜: 기준을 바꿔도 이미 착지한 재고의 Tier 는 산 날 판정 그대로라, 카드 순서가 다음 collect 로 산 사람부터만 바뀐다.
재료: 다시 올라온 사람은 그날 재확인 원본(최신), 나머지는 산 날 원본(검색 액터). 원본이 없으면 건너뛴다(지어내지 않는다).
대상: 카드가 볼 재고 — Status New(또는 빈 값) · 아직 안 나감(또는 다시 올라옴) · 사람이 손 안 댐(타깃·DB로 업데이트).
"""
from __future__ import annotations

import collections
import glob
import json
import sys

from kvlib import judgments, ledger, paths
from kv_transport import notion
from skills.linkedin import judge, profile, recheck, run


def _latest_scraped() -> dict[str, dict]:
    """재확인 원본 — publicIdentifier(소문자) → 가장 최근 item."""
    out = {}
    for fn in sorted(glob.glob(str(paths.data("linkedin", "raw", "_actor") / f"{recheck.SLUG}_*.json"))):
        for it in json.load(open(fn)).get("items") or []:
            if it.get("publicIdentifier"):
                out[str(it["publicIdentifier"]).lower()] = it
    return out


def main(apply: bool) -> int:
    rows = notion.query(notion.writable_id(run.DB), ["LinkedIn URL", "Tier", "서비스명", "Status", "타깃", "DB로 업데이트", "Axis"]).require()
    reopen = run._resurfaced_open()
    posted = {r["page_id"] for r in ledger.rows(run.TASK, ev=run.POSTED) if r.get("page_id")} - set(reopen)
    key_of_page = {r["page_id"]: r["key"] for r in ledger.rows(run.TASK, ev=ledger.LANDED) if r.get("page_id")}
    checked = run._last(run.CHECKED, "page_id")
    raw, scraped = recheck.raw_by_key(), _latest_scraped()
    todo, miss = [], 0
    for live in rows:
        pid = live["_page_id"]
        if live.get("Status") not in ("New", None) or pid in posted or live.get("타깃") or live.get("DB로 업데이트"):
            continue
        url = profile.normalize_url(str(live.get("LinkedIn URL") or ""))
        k = key_of_page.get(pid) or url
        it = (scraped.get(str((checked.get(pid) or {}).get("pub") or "").lower()) if pid in reopen else None) or raw.get(k) or raw.get(url)
        if not it:
            miss += 1
            continue
        s = run._summary({**it, "_axis": ((live.get("Axis") or [""])[0])})
        s["key"] = k
        todo.append((live, s))
    print(f"· 재고 {len(todo)}명 (원본 없음 {miss})")
    if not todo:
        return 0
    crit = judge.criteria_text()
    lines = {s["key"]: judge._line(s) for _, s in todo}
    f = judge.judge_lines(list(lines.values()), list(lines), crit)
    if not f.is_measured:
        print(f"🔴 판정 실패: {f.reason}")
        return 1
    vmap = {v["key"]: v for v in f.require()}
    moves = collections.Counter((live.get("Tier"), vmap[s["key"]]["verdict"]) for live, s in todo if s["key"] in vmap)
    print("· 바뀜(옛 → 새): " + " · ".join(f"{a}→{b} {n}" for (a, b), n in sorted(moves.items(), key=lambda x: -x[1])))
    if not apply:
        print("  (보기만 — --apply 면 노션 덮어쓰기)")
        return 0
    g = run._gate()
    judgments.save(g.con, judgments.LIVE, judge.sha(crit), lines, f.require())
    bad = 0
    for live, s in todo:
        v = vmap.get(s["key"])
        if not v:
            continue
        judgments.set_page(g.con, s["key"], live["_page_id"])
        if v["verdict"] == live.get("Tier"):
            continue
        r = notion.update(live["_page_id"], run.DB, {"Tier": v["verdict"], "AI Reason": v["reason"]})
        bad += not r.is_measured
    g.con.commit()
    print(f"· 노션 Tier 덮어쓰기 끝 (실패 {bad})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
