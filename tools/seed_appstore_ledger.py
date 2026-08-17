"""[일회성] 노션 ch_앱스토어의 App ID 를 장부에 한 번 넣는다.

## 왜 필요한가

장부는 비어 있는 채로 시작한다. 그대로 돌리면 **첫 실행에 이미 노션에 있는 수천 개가
전부 "처음 보는 것"이 되어 쏟아진다.**

## 왜 스킬 밖에 있나

한 번 쓰고 버리는 도구다. 옛 시스템은 이런 걸 스킬 안에 뒀고, 26개 중 12개가
아무도 안 부르는 채로 남아 부피가 됐다. **일회성은 처음부터 밖에 둔다.**

    ~/kv/.venv/bin/python tools/seed_appstore_ledger.py --apply
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kvlib import ledger  # noqa: E402
from kv_transport import notion  # noqa: E402

TASK = "appstore"


def main(apply: bool) -> int:
    already = ledger.keys(TASK, ledger.LANDED)
    print(f"장부에 이미 있는 키: {len(already)}개")

    f = notion.query("00000000-0000-0000-0000-30deea8edcc5", ["App ID"])
    if not f.is_measured:
        print(f"🔴 노션을 못 읽었다: {f.reason}")
        return 1
    rows = f.require()
    if f.partial:
        print("🔴 응답이 잘렸다 — seed 는 전량이어야 한다. 중단.")
        return 1

    ids = [str(r.get("App ID") or "").strip() for r in rows]
    valid = [i for i in ids if i]
    blank = len(ids) - len(valid)
    new = [i for i in dict.fromkeys(valid) if i not in already]

    print(f"노션 행 {len(rows)}개 · App ID 있음 {len(valid)} · 빈 칸 {blank} · 장부에 없는 것 {len(new)}")
    if not apply:
        print("\n(dry-run) --apply 를 붙이면 장부에 넣는다")
        return 0

    for i, aid in enumerate(new, 1):
        ledger.append(TASK, aid, ledger.LANDED, src="seed:notion")
        if i % 500 == 0:
            print(f"  · {i}/{len(new)}")
    print(f"✅ {len(new)}개 넣었다 · 장부 총 {len(ledger.keys(TASK))}키")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
