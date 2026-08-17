"""[일회성] 링크드인 행의 제목을 회사명 → 사람 이름으로 바꾼다.

처음엔 제목을 회사명으로 넣었는데, 링크드인은 **사람을 소싱하는 채널**이고
스텔스 축에서 오는 사람은 회사명이 "Stealth Mode Startup" 같은 것이라
목록에서 서로 구별이 안 된다.

    제목        사람 이름
    One Liner   회사 · 직함

    ~/kv/.venv/bin/python tools/retitle_linkedin.py --apply
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kv_transport import notion  # noqa: E402
from skills.linkedin import run  # noqa: E402

TODAY = "2026-08-17"


def main(apply: bool) -> int:
    f = notion.query(notion.writable_id(run.DB),
                     ["서비스명", "Author Display Name", "One Liner", "Captured At"])
    if not f.is_measured:
        print(f"🔴 노션을 못 읽었다: {f.reason}")
        return 1
    todo = []
    for r in f.require():
        if str(r.get("Captured At") or "") != TODAY:
            continue
        name = str(r.get("Author Display Name") or "").strip()
        title = str(r.get("서비스명") or "").strip()
        if name and title != name:
            company = title                       # 지금 제목에 들어 있는 게 회사다
            one = str(r.get("One Liner") or "").strip()
            new_one = " · ".join(x for x in (company, one) if x and x not in one.split(" · "))
            todo.append((r["_page_id"], name, new_one or one))

    print(f"제목을 사람 이름으로 바꿀 행: {len(todo)}개")
    for _, n, o in todo[:5]:
        print(f"    {n:24} ← One Liner: {o[:50]}")
    if not apply:
        print("\n(dry-run) --apply 를 붙이면 바꾼다")
        return 0

    ok = fail = 0
    for pid, name, one in todo:
        res = notion.update(pid, run.DB, {"서비스명": name, "One Liner": one})
        if res.is_measured:
            ok += 1
        else:
            fail += 1
            print(f"  ✗ {pid}: {res.reason}")
    print(f"✅ {ok}개 갱신 · 실패 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
