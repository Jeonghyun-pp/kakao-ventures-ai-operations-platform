#!/usr/bin/env python3
"""이미 착지한 링크드인 행의 `Tenure Months`를 **저장된 액터 원본으로 재계산해 교정**한다. 무과금.

## 왜 필요한가 (2026-07-27)
`_positions()`가 `experience`의 `endDate=Present`를 안 봐서 tenure가 틀리게 착지했다.
코드는 고쳤지만 **그건 앞으로 들어올 행에만 적용된다** — 이미 노션에 있는 행은 틀린 값을 그대로 들고 있고,
링크드인 뷰가 `Tenure Months` 오름차순이라 **틀린 값일수록 위로 올라온다.**

실측: 노션 링크드인 345행 중 원본과 대조 가능한 247행에서 **41행(17%)의 tenure가 틀렸다.**
  예) Example Person F 4 → 26개월 (행정안전부 시간제 자문 5개월이 AIM Intelligence 창업 2년 3개월을 가렸다)
  나머지 98행은 대조할 원본이 없지만 **tenure가 전부 빈칸**이라 틀린 값을 들고 있지는 않다.

## 무엇을 쓰나 (3칸만)
  · Tenure Months  — 재계산값이 **다를 때만**
  · 창업 개월       — 창업 직함 Present 중 가장 긴 것 (신규 칸, 2026-07-27)
  · 과거 스텔스     — 끝난 경력에 stealth (신규 칸)

🔴 **팔로업 필드는 절대 안 건드린다** (`Last Seen`·`Last Checked`·`재검토`) — SKILL.md CRITICAL #11.
   체크박스는 falsy도 직렬화돼서, 같이 실으면 **사람이 꺼둔 재검토를 코드가 되살린다.**

## 되돌리기
원본이 그대로 있으므로 옛 로직으로 재계산해 다시 쓰면 된다(무과금). `data/backfill_tenure_{날짜}.json`에
**바꾸기 전 값**을 남긴다.

사용:
  ./.venv/bin/python tools/backfill_tenure.py            # dry-run (기본)
  ./.venv/bin/python tools/backfill_tenure.py --apply
"""
from __future__ import annotations

import glob
import json
import os
import sys
from datetime import date
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))


def _load_env() -> None:
    # 🔴 [2026-08-16] 인라인 `.env` 파서 → `kv_core.secrets`.
    #    `main.py:28-36` 이 *"자격증명 정본은 `$KV_CONFIG/.env` 하나뿐. 스킬별 `.env` 를
    #    다시 만들지 마라"* 라고 선언하고 옮겼는데 도구들은 옛 파서를 그대로 들고 있었고,
    #    `project/.env` 는 **존재하지 않아** 실행 즉시 `FileNotFoundError` 로 죽었다.
    from kv_core import secrets
    secrets.load()


def main() -> int:
    apply = "--apply" in sys.argv
    _load_env()
    import yaml
    import core.notion as notion
    from adapters.linkedin import (_tenure_months, _founder_since_months, _past_stealth,
                                   _featured_titles, _career_timeline, _current_company_url,
                                   _gap_months, _fulltime_gap_months)

    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    token = os.environ["NOTION_TOKEN"]
    db = cfg["notion"]["channel_db_ids"]["링크드인"]

    # ① 저장된 원본 — 키를 셋 다 걸어둔다(id·publicIdentifier·URL 꼬리). Short 모드 행의 URL은
    #    난독화 URN이라 publicIdentifier로는 안 걸리고 id로만 걸린다.
    prof: dict[str, dict] = {}
    for f in glob.glob(str(PROJ / "data/raw/_actor/harvestapi-linkedin-profile-*.json")):
        d = json.load(open(f, encoding="utf-8"))
        items = d if isinstance(d, list) else d.get("items", d)
        if not isinstance(items, list):
            continue
        for p in items:
            if isinstance(p, dict) and isinstance(p.get("experience"), list):
                for k in (p.get("id"), p.get("publicIdentifier"),
                          (p.get("linkedinUrl") or "").rstrip("/").split("/")[-1]):
                    if k:
                        prof.setdefault(str(k), p)

    # ② 노션 행
    rows, cur = [], None
    while True:
        body: dict = {"page_size": 100}
        if cur:
            body["start_cursor"] = cur
        r = notion._write("POST", f"{notion.API}/databases/{db}/query", token, body)
        if r.status_code != 200:
            print(f"🔴 쿼리 실패 {r.status_code}: {r.text[:200]}")
            return 1
        d = r.json()
        for pg in d.get("results", []):
            pp = pg["properties"]
            rows.append({
                "id": pg["id"],
                "name": notion._plain(pp.get("서비스명")),
                "url": (pp.get("LinkedIn URL") or {}).get("url") or "",
                "tenure": (pp.get("Tenure Months") or {}).get("number"),
                "fs": (pp.get("창업 개월") or {}).get("number"),
                "ps": (pp.get("과거 스텔스") or {}).get("checkbox"),
                "feat": notion._plain(pp.get("대표 콘텐츠")),
                "home": (pp.get("Homepage") or {}).get("url") or "",
                "gap": (pp.get("공백 개월") or {}).get("number"),
                "fgap": (pp.get("본업 공백") or {}).get("number"),
            })
        if not d.get("has_more"):
            break
        cur = d.get("next_cursor")

    plan, unmatched = [], 0
    for row in rows:
        p = prof.get((row["url"] or "").rstrip("/").split("/")[-1])
        if not p:
            unmatched += 1
            continue
        new_t, new_fs, new_ps = _tenure_months(p), _founder_since_months(p), _past_stealth(p)
        props: dict = {}
        if new_t is not None and (row["tenure"] is None or int(row["tenure"]) != int(new_t)):
            props["Tenure Months"] = {"number": new_t}
        if new_fs is not None and row["fs"] != new_fs:
            props["창업 개월"] = {"number": new_fs}
        if bool(row["ps"]) != bool(new_ps):
            props["과거 스텔스"] = {"checkbox": bool(new_ps)}
        new_feat = _featured_titles(p)
        if new_feat and (row.get("feat") or "") != new_feat:
            props["대표 콘텐츠"] = {"rich_text": [{"text": {"content": new_feat[:1900]}}]}
        new_gap = _gap_months(p)
        if new_gap is not None and row.get("gap") != new_gap:
            props["공백 개월"] = {"number": new_gap}
        new_fgap = _fulltime_gap_months(p)
        if new_fgap is not None and row.get("fgap") != new_fgap:
            props["본업 공백"] = {"number": new_fgap}
        new_home = _current_company_url(p)
        if new_home and not row.get("home"):
            props["Homepage"] = {"url": new_home}
        # 경력 본문은 프로퍼티가 아니라 블록이라 따로 들고 간다(append_career_blocks가 멱등).
        row["_career"] = _career_timeline(p)
        if props or row["_career"]:
            plan.append((row, props, new_t))

    ten_fix = sum(1 for _, pr, _ in plan if "Tenure Months" in pr)
    print(f"· 노션 {len(rows)}행 · 원본 매칭 {len(rows)-unmatched} · 대조 불가 {unmatched}(건드리지 않음)")
    print(f"· 바꿀 행 {len(plan)} — Tenure 교정 {ten_fix}행 · "
          f"경력 본문 {sum(1 for r,_,_ in plan if r.get('_career'))}행 · "
          f"Homepage {sum(1 for _,pr,_ in plan if 'Homepage' in pr)}행")
    # 미리보기는 **tenure가 바뀌는 행만** 보여준다(나머지 167행은 빈 칸을 채우는 것뿐이라
    # 사람이 검수할 대상이 아니다). 변화폭이 큰 순으로.
    fixes = [(r, pr, nt) for r, pr, nt in plan if "Tenure Months" in pr]
    for row, pr, new_t in sorted(fixes, key=lambda x: -(x[2] - (x[0]["tenure"] or 0)))[:15]:
        print(f"    {row['name'][:22]:24s} Tenure {str(row['tenure']):>4} → {new_t:>4}"
              f" | 창업 {pr.get('창업 개월',{}).get('number')}"
              f" | 과거스텔스 {pr.get('과거 스텔스',{}).get('checkbox')}")
    print(f"    (그 외 {len(plan)-len(fixes)}행은 tenure는 그대로이고 새 칸 두 개만 채운다)")

    if not apply:
        print(f"\n[dry-run] --apply 를 붙이면 위 {len(plan)}행을 실제로 쓴다. "
              f"(팔로업 필드 Last Seen·Last Checked·재검토는 건드리지 않는다)")
        return 0

    snap = PROJ / "data" / f"backfill_tenure_{date.today().isoformat()}.json"
    snap.write_text(json.dumps(
        [{"page_id": r["id"], "name": r["name"], "before": {"Tenure Months": r["tenure"],
          "창업 개월": r["fs"], "과거 스텔스": r["ps"]}} for r, _, _ in plan],
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n· 변경 전 값 스냅샷: {snap.name}")

    ok = fail = careers = 0
    for row, props, _ in plan:
        if props:
            r = notion._write("PATCH", f"{notion.API}/pages/{row['id']}", token, {"properties": props})
            if r.status_code == 200:
                ok += 1
            else:
                fail += 1
                print(f"  🔴 {row['name'][:20]} {r.status_code}: {r.text[:160]}")
        if row.get("_career") and notion.append_career_blocks(
                token, row["id"], row["_career"], "2026-07-13 수집분"):
            careers += 1
    print(f"\n{'✅' if not fail else '🔴'} 프로퍼티 {ok}행 · 실패 {fail}행 · **경력 본문 {careers}행**")
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
