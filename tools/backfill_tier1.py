"""1티어 백필 — **이미 산 데이터로** 노션의 기존 행에 1티어 표시를 채운다. 돈 0.

## 왜 필요한가

`collect` 는 **장부 중복제거를 사후 필터보다 먼저** 한다. 그래서 2026-08-19 의 1티어 전량
구매(357명) 중 **265명은 "이미 노션에 있음"으로 즉시 건너뛰어져 조건 검사를 아예 안 거쳤다.**
그 사람들은 노션에서 여전히 옛 축(stealth-kr 등)으로만 보이고, 학부 연도도 안 적혀 있다.
→ "1티어를 다 봤다"가 노션에서 참이 아니다.

원본은 `~/kv-data/linkedin/raw/_actor/` 에 남아 있으므로 **추가 구매 없이** 채울 수 있다.

    ~/kv/.venv/bin/python tools/backfill_tier1.py [--live]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kvlib import ledger                        # noqa: E402
from kv_transport import notion                 # noqa: E402
from skills.linkedin import axes, profile, run  # noqa: E402

RAW = Path.home() / "kv-data" / "linkedin" / "raw" / "_actor"
MARK = "1티어"          # 멱등 표식 — 이미 적혀 있으면 건드리지 않는다
TIER_EV = "tier1"

# 축 판별: payload 의 schools 길이로 가른다(7 = 국내 상위대 / 16 = 미국 상위대)
BY_SCHOOLS = {len(axes.KR_ELITE): "elite-kr", len(axes.US_ELITE): "elite-us-kr"}


def load_elite() -> dict[str, dict]:
    """오늘 산 1티어 원본 → {대표키: item}. 같은 사람이 여러 페이지에 오면 하나로."""
    out: dict[str, dict] = {}
    for p in sorted(RAW.glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        pl = d.get("payload") or {}
        axis = BY_SCHOOLS.get(len(pl.get("schools") or []))
        if not axis or not pl.get("searchQuery"):
            continue
        for it in d.get("items") or []:
            ks = profile.match_keys(it)
            if ks and ks[0] not in out:
                out[ks[0]] = {**it, "_axis": axis, "_keys": ks}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="실제로 노션에 쓴다 (없으면 dry-run)")
    a = ap.parse_args()

    people = load_elite()
    print(f"▶ 1티어 원본 {len(people)}명 (오늘 산 것)")

    done = {r["key"] for r in ledger.rows("linkedin", ev=TIER_EV)}

    db_id = notion.writable_id(run.DB)
    opts = notion.allowed_options(db_id, "Axis")
    axis_ok = {x: (x in opts) for x in ("elite-kr", "elite-us-kr")}
    if not all(axis_ok.values()):
        print(f"  ⚠️  노션 Axis 옵션에 없는 축 {[k for k, v in axis_ok.items() if not v]}"
              f" — 이번엔 Match Reason 만 채운다(옵션을 추가하면 다시 돌려서 Axis 도 채운다)")

    f = notion.query(db_id, ["Match Reason", "Axis", "서비스명", "LinkedIn URL"])
    if not f.is_measured:
        print(f"🔴 노션을 못 읽었다: {f.reason}")
        return 1
    live = {r["_page_id"]: r for r in f.require()}

    # 🔴 page_id 의 정본은 **노션**이지 장부가 아니다. 장부 landed 1,857행 중 1,234행은
    #    옛 시스템에서 시드된 행이라 page_id 가 없다(키만 있다). 장부로 매칭하면
    #    그 1,234명이 통째로 "노션에 없음"으로 빠진다 — 실제로는 노션에 있는 사람들이다.
    #    → 노션의 `LinkedIn URL` 을 같은 정규화 함수에 통과시켜 키 공간을 맞춘다.
    # 🔴 지도를 **둘로 나눠서** 갖는다. 하나로 합치면 URN 키가 장부의 옛 page_id 를 먼저
    #    맞혀서, 살아 있는 행이 있는데도 **아카이브된 행**을 고치려 든다(실측 50건 400).
    #    살아 있는 노션 행이 항상 이긴다 — 키 하나가 아니라 **키 전부**를 먼저 훑는다.
    live_of: dict[str, str] = {}
    for pid, row in live.items():
        k = profile.normalize_url(str(row.get("LinkedIn URL") or ""))
        if k:
            live_of.setdefault(k, pid)
    # 노션 URL 이 URN 형태로 적힌 사람은 vanity 키와 안 만난다 → 장부를 보조로 쓴다.
    ledger_of: dict[str, str] = {}
    for r in ledger.rows("linkedin", ev=ledger.LANDED):
        if r.get("page_id"):
            for k in (r.get("keys") or [r.get("key")]):
                if k:
                    ledger_of.setdefault(k, r["page_id"])

    def find_page(keys: list[str]) -> str | None:
        for src in (live_of, ledger_of):
            for k in keys:
                if k in src:
                    return src[k]
        return None

    todo, cut, missing, already = [], {}, 0, 0
    for key, item in people.items():
        pid = find_page(item["_keys"])
        if not pid:
            missing += 1               # 오늘 새로 착지한 사람 = 이미 라벨이 붙어 있다
            continue
        why = axes.reject(item["_axis"], item)
        if why:
            cut[why] = cut.get(why, 0) + 1
            continue
        # 🔴 멱등은 **칸별로** 판단한다. "이미 표시됨"을 사람 단위로 보면,
        #    Match Reason 만 채워진 상태에서 Axis 를 영영 못 채운다(실제로 그랬다).
        row = live.get(pid) or {}
        mr = str(row.get("Match Reason") or "")
        axis = item["_axis"]
        # 🔴 표식은 **모든 1티어 행에** 있어야 한다. 오늘 착지분은 Match Reason 이 축 이름으로
        #    시작한다는 이유로 건너뛰었더니, 텍스트로 찾으면 96명 · Axis 로 찾으면 115명이
        #    나오는 상태가 됐다. 세는 방법에 따라 답이 다르면 둘 다 못 믿는다.
        need_mr = MARK not in mr
        # 🔴 `notion.query` 는 multi_select 를 **리스트**로 준다(`_plain`). 문자열로 다루면
        #    `str(list)` 이 "['stealth-kr']" 이 되어 합집합이 통째로 깨지고, 그대로 PATCH 하면
        #    **기존 축을 지운다.** 되읽기는 '보낸 값'만 대조하므로 이걸 못 잡는다 —
        #    실측 2026-08-19: 이 한 줄로 115행의 옛 Axis 가 날아갔다.
        have_axis = list(row.get("Axis") or [])
        need_axis = axis_ok[axis] and axis not in have_axis
        if not need_mr and not need_axis:
            already += 1
            continue
        todo.append((key, pid, item, need_mr, need_axis, have_axis))

    print(f"· 노션에 이미 있던 사람 중 조건 통과 {len(todo)}명 · 조건 미달 {sum(cut.values())}명 "
          f"· 이미 표시됨 {already} · 장부에 없음 {missing}")
    for k, v in sorted(cut.items(), key=lambda x: -x[1]):
        print(f"    미달 사유 {k:22} {v}")
    if not a.live:
        for key, pid, item, *_ in todo[:5]:
            s = profile.summarize(item)
            y = profile.undergrad_start_year(item)
            print(f"    예: {s['name'][:24]:26} {item['_axis']:12} 학부 {y or '미상'}")
        print("  (dry-run — 아무것도 쓰지 않았다. --live 로 실행)")
        return 0

    ok = fail = 0
    for key, pid, item, need_mr, need_axis, have_axis in todo:
        axis = item["_axis"]
        y = profile.undergrad_start_year(item)
        cur = live.get(pid) or {}
        props: dict = {}
        if need_mr:
            tail = f" · {MARK} {axis} · 학부 {y if y else '미상'}"
            props["Match Reason"] = (str(cur.get("Match Reason") or "").strip() + tail).strip(" ·")
        if need_axis:
            # 🔴 multi_select 는 **덮어쓰기**다. 기존 축을 지우지 않게 합집합으로 보낸다.
            keep = [x for x in have_axis if x in opts]
            if len(keep) != len(have_axis):
                # 옵션 목록에 없는 기존 값이 있으면 **덮어쓰지 않는다.** 지우느니 안 적는다.
                print(f"  ⚠️  {key}: 알 수 없는 기존 축 {set(have_axis) - set(keep)} — Axis 를 건드리지 않는다")
            else:
                props["Axis"] = sorted(set(keep) | {axis})
        res = notion.update(pid, run.DB, props)
        if not res.is_measured:
            fail += 1
            why = ("아카이브된 행이다 — 살아 있는 행을 못 찾았다"
                   if "archived" in res.reason else res.reason[:120])
            print(f"  ✗ {key}: {why}")
            continue
        ledger.append("linkedin", key, TIER_EV, page_id=pid, axis=axis, undergrad=y)
        ok += 1
    print(f"· 표시 완료 {ok}명 · 실패 {fail}")
    return 2 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
