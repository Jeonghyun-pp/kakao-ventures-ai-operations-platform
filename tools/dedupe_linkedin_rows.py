"""[일회성] 오늘 만든 중복 행을 휴지통으로 보낸다.

## 왜 생겼나

2026-08-17 전 축 실행에서 장부 seed 를 빠뜨린 채 595명을 넣었고,
그중 **509명이 이미 노션에 있던 사람**이었다. (원인은 커밋 41165de 참조)

## 어느 쪽을 남기나

**옛 행을 남기고 오늘 행을 보낸다.** 옛 행에는 사람 손이 닿았을 수 있고(Status·타깃 체크박스)
이력이 붙어 있다. 우리 판정은 장부에 남아 있으니 필요하면 나중에 옛 행에 반영할 수 있다.

## 안전

노션 archive 는 **삭제가 아니라 휴지통**이다. 노션 UI 에서 되살릴 수 있다.
보내고 나서 되읽어 실제로 반영됐는지 확인한다.

    ~/kv/.venv/bin/python tools/dedupe_linkedin_rows.py           # dry-run
    ~/kv/.venv/bin/python tools/dedupe_linkedin_rows.py --apply
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kvlib import ledger  # noqa: E402
from kv_transport import notion  # noqa: E402
from skills.linkedin import profile  # noqa: E402

DB_ID = "00000000-0000-0000-0000-8702f7cc7f34"
TODAY = "2026-08-17"


def main(apply: bool) -> int:
    f = notion.query(DB_ID, ["LinkedIn URL", "Captured At", "서비스명"])
    if not f.is_measured:
        print(f"🔴 노션을 못 읽었다: {f.reason}")
        return 1
    if f.partial:
        print("🔴 응답이 잘렸다 — 전량이어야 안전하다. 중단.")
        return 1
    rows = f.require()

    old_keys = {profile.normalize_url(str(r.get("LinkedIn URL") or ""))
                for r in rows if str(r.get("Captured At") or "") != TODAY}
    old_keys.discard("")

    victims, seen_today = [], set()
    for r in rows:
        if str(r.get("Captured At") or "") != TODAY:
            continue
        k = profile.normalize_url(str(r.get("LinkedIn URL") or ""))
        if not k:
            continue
        if k in old_keys or k in seen_today:      # 옛 행과 겹치거나, 오늘 안에서도 겹치면
            victims.append((r["_page_id"], k, r.get("서비스명")))
        else:
            seen_today.add(k)

    print(f"노션 {len(rows)}행 · 옛 행의 고유 키 {len(old_keys)}개")
    print(f"휴지통으로 보낼 오늘 행: {len(victims)}개")
    for _, k, name in victims[:5]:
        print(f"    {str(name)[:30]:30} {k}")
    if not apply:
        print("\n(dry-run) --apply 를 붙이면 실제로 보낸다. 노션 휴지통에서 되살릴 수 있다")
        return 0

    ok = fail = 0
    for pid, k, _name in victims:
        res = notion.archive(pid)
        if res.is_measured:
            ok += 1
            ledger.append("linkedin", k, "dropped_dup", page_id=pid, why="seed 누락으로 생긴 중복")
        else:
            fail += 1
            print(f"  ✗ {pid}: {res.reason}")
        if ok % 100 == 0 and ok:
            print(f"  · {ok}/{len(victims)}")
    print(f"✅ 휴지통으로 {ok}개 · 실패 {fail}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
