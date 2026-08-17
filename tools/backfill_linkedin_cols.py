"""[일회성] 오늘 착지한 행의 누락 4칸을 아카이브에서 채운다.

## 왜 비어 있나

스킬을 처음 지을 때 이 넷을 빠뜨렸다. 옛 시스템은 90% 넘게 채우던 칸이라,
우리 행부터 갑자기 비면 옛 행과 새 행이 다른 모양이 된다.
`회사` formula 는 Homepage 를 읽으므로 그것까지 같이 죽는다.

    Post URL      프로필 링크
    본업 공백       부업 제외 공백 — 공백 개월과 **다른 값**이다
    Korea Signal  KR-거주 · KR-학교
    Homepage      회사 링크드인 URL (노션 `회사` formula 의 입력)

## 돈이 안 든다

원본이 아카이브에 있어서 다시 사지 않는다.

    ~/kv/.venv/bin/python tools/backfill_linkedin_cols.py --apply
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kvlib import ledger, paths  # noqa: E402
from kv_transport import notion  # noqa: E402
from skills.linkedin import profile, run  # noqa: E402

ARCHIVE = paths.data_root() / "linkedin" / "raw" / "_actor"


def main(apply: bool) -> int:
    # 아카이브에서 키 → 원본
    by_key: dict[str, dict] = {}
    for f in sorted(ARCHIVE.glob("*.json")):
        for it in json.loads(f.read_text(encoding="utf-8")).get("items", []):
            for k in profile.match_keys(it):
                by_key.setdefault(k, it)

    # 🔴 휴지통으로 보낸 페이지는 뺀다. 장부에 dropped_dup 로 남겨뒀다.
    #    아카이브된 페이지에 PATCH 하면 400 이 나므로, 511건이 통째로 실패로 잡힌다.
    dropped = {r.get("page_id") for r in ledger.rows(run.TASK, ev="dropped_dup")}

    # 장부에서 오늘 착지한 것 → page_id
    todo = []
    for r in ledger.rows(run.TASK, ev=ledger.LANDED):
        if str(r.get("src", "")).startswith("seed") or not r.get("page_id"):
            continue
        if r["page_id"] in dropped:
            continue
        ks = [str(r.get("key"))] + [str(x) for x in (r.get("keys") or [])]
        it = next((by_key[k] for k in ks if k in by_key), None)
        if it:
            todo.append((r["page_id"], it))

    print(f"채울 행: {len(todo)}개 (아카이브에서 원본을 찾은 것)")
    if not apply:
        for pid, it in todo[:4]:
            print(f"    {profile.summarize(it)['name'][:20]:20} "
                  f"본업공백={profile.fulltime_gap_months(it)} "
                  f"KR={profile.korea_signals(it)} 회사={profile.company_url(it)[:40]}")
        print("\n(dry-run) --apply 를 붙이면 채운다")
        return 0

    allowed = notion.allowed_options(notion.writable_id(run.DB), "Korea Signal")
    ok = fail = 0
    for pid, it in todo:
        props = {
            "Post URL": str(it.get("linkedinUrl") or "") or None,
            "본업 공백": profile.fulltime_gap_months(it),
            "Korea Signal": [x for x in profile.korea_signals(it) if x in allowed],
            "Homepage": profile.company_url(it) or None,
        }
        res = notion.update(pid, run.DB, props)
        if res.is_measured:
            ok += 1
        else:
            fail += 1
            print(f"  ✗ {pid}: {res.reason}")
    print(f"✅ {ok}개 채움 · 실패 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
