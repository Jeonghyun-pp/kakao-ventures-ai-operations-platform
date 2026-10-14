"""정답을 슬랙 반응으로 옮길 때 한 번 — 지난 카드의 ✅·❌ 를 소급하고, 판정 입력을 원본 아카이브로 되살린다 [사용자 결정 2026-10-14].

    .venv/bin/python -m tools.linkedin_reaction_backfill          # 보기만 (슬랙 읽기만)
    .venv/bin/python -m tools.linkedin_reaction_backfill --apply  # 장부 reaction_snapshot · judgments(live 복원 · 옛 human 삭제)

왜 필요한가:
- 정답이 노션 Tier(AI 값)에서 반응으로 바뀌었다. judgments(human) 의 '노션 Tier' 437행은 AI 가 쓴 값이라 정답이 아니다 — 지운다.
- 판정 입력은 09-29 부터만 저장돼 카드 172명 중 37명뿐이다. 그래서 replay 로 기준을 재도 맞댈 사람이 없다.
  원본 아카이브(raw/_actor)에는 산 날 그대로의 item 이 있다 — 그걸로 collect 와 같은 줄(judge._line)을 만들고, 판정은 그때 낸 Tier.
- 반응은 슬랙에 그대로 남아 있다. 카드 스레드를 다시 읽어 ✅·❌ 를 snapshot 으로 남긴다(문안·승격은 안 한다 — 지난 일이다).
  같은 값이면 다시 쓰지 않는다 — 몇 번 돌려도 된다(Reviewer C님이 지난 스레드에 눌러 주시면 다시 돌린다).
"""
from __future__ import annotations

import glob
import json
import sys

from kvlib import judgments, ledger, paths
from kv_transport import notion, slack
from skills.linkedin import judge, profile, run


def _raw_by_key() -> dict[str, dict]:
    """사람 키(URN·URL 전부) → 산 날 item. 축은 payload 를 몰라 장부 landed 에서 붙인다."""
    out: dict[str, dict] = {}
    for fn in sorted(glob.glob(str(paths.data("linkedin", "raw", "_actor") / "*.json"))):
        try:
            items = json.load(open(fn)).get("items") or []
        except Exception:                                                    # noqa: BLE001 — 깨진 파일 하나로 멈추지 않는다
            continue
        for it in items:
            for k in profile.match_keys(it):
                out.setdefault(k, it)                                        # 처음 산 날 값
    return out


def snapshots(apply: bool) -> tuple[int, int]:
    """카드 스레드를 다시 읽어 반응이 있는 카드를 snapshot 으로. (✅, ❌) 수."""
    posted = run._last(run.POSTED)
    have = {r["key"]: (tuple(r.get("yes_by") or []), tuple(r.get("no_by") or []))
            for r in ledger.rows(run.TASK, ev=run.SNAPSHOT)}
    by_parent: dict[str, list] = {}
    for k, r in posted.items():
        if r.get("parent"):
            by_parent.setdefault(r["parent"], []).append((k, r))
    n_yes = n_no = 0
    for parent, rows in by_parent.items():
        got = slack.replies(rows[0][1].get("ch") or "net", parent)
        if not got.is_measured:
            print(f"  🔴 스레드 {parent} 못 읽음: {got.reason}")
            continue
        react = {m["ts"]: m.get("reactions") or [] for m in got.require()}
        for k, r in rows:
            mine = react.get(r.get("slack_ts") or r.get("ts"), [])
            yes, no = run._pressers(mine, "reaction_yes"), run._pressers(mine, "reaction_no")
            if not (yes or no):
                continue
            n_yes += bool(yes)
            n_no += bool(no) and not yes
            if apply and have.get(k) != (tuple(yes), tuple(no)):
                ledger.append(run.TASK, k, run.SNAPSHOT, yes_by=yes, no_by=no, day=r.get("day"), src="backfill")
    return n_yes, n_no


def restore_inputs(con, apply: bool) -> tuple[int, int]:
    """카드로 나간 사람 중 판정 입력이 없는 사람을 원본으로 되살린다. (복원, 원본 없음)."""
    posted = run._last(run.POSTED)
    live = {k for (k,) in con.execute("SELECT key FROM judgments WHERE run='live' AND ok=1 AND input_text!=''")}
    landed = run._last(ledger.LANDED)
    tier_of = {}
    f = notion.query(notion.writable_id(run.DB), ["Tier"])
    if f.is_measured:
        tier_of = {r["_page_id"]: r.get("Tier") for r in f.require()}
    raw = _raw_by_key()
    lines, verdicts, pages, miss = {}, [], {}, 0
    for k, p in posted.items():
        if k in live:
            continue
        it = raw.get(k)
        tier = (landed.get(k) or {}).get("tier") or tier_of.get(p.get("page_id"))
        if not it or tier not in judge.TIERS:
            miss += 1
            continue
        s = run._summary({**it, "_axis": (landed.get(k) or {}).get("axis", "")})
        s["key"] = k                                                          # 장부 키로 — 정답·page_id 가 이 키에 붙는다
        lines[k] = judge._line(s)
        verdicts.append({"key": k, "verdict": tier, "reason": "backfill: 착지 때 Tier", "raw": ""})
        pages[k] = p.get("page_id")
    if apply and lines:
        judgments.save(con, judgments.LIVE, "backfill", lines, verdicts)
        for k, pid in pages.items():
            if pid:
                judgments.set_page(con, k, pid)
    return len(lines), miss


def main(apply: bool) -> int:
    con = run._gate().con
    old = con.execute("SELECT count(*) FROM judgments WHERE run='human' AND reason='노션 Tier'").fetchone()[0]
    print(f"· 옛 정답(노션 Tier = AI 값) {old}행 — " + ("지운다" if apply else "--apply 면 지운다"))
    if apply:
        con.execute("DELETE FROM judgments WHERE run='human' AND reason='노션 Tier'")
    y, n = snapshots(apply)
    print(f"· 슬랙 반응 소급: ✅ {y}명 · ❌ {n}명" + ("" if apply else " (보기만)"))
    got, miss = restore_inputs(con, apply)
    print(f"· 판정 입력 복원 {got}명 · 원본·Tier 없음 {miss}명" + ("" if apply else " (보기만)"))
    if apply:
        con.commit()
        print(f"· 정답 동기화: 판정과 반응이 갈린 사람 {run._sync_labels(con)}명")
        con.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main("--apply" in sys.argv))
