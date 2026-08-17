"""[일회성] 노션 ch_링크드인의 기존 인물을 장부에 넣는다.

## 🔴 이걸 빠뜨려서 무슨 일이 났나

2026-08-17 전 축 실행에서 **595명을 넣었는데 509명(86%)이 이미 노션에 있던 사람**이었다.
장부가 비어 있으니 전부 "처음 보는 사람"으로 보였다.

앱스토어는 같은 seed 를 했고(App ID 2,731개) 그래서 중복이 안 났다.
링크드인만 빠졌다 — 계획서에 앱스토어 것만 적혀 있었다.

**교훈: 장부가 멱등의 근거인데, 그 장부를 기존 상태로 채우는 단계가 스킬마다 필요하다.**

## 키 정규화

같은 사람이 스크래퍼 모드마다 다른 URN 으로 오므로 `profile.normalize_url` 로 접는다.
그냥 URL 로 비교하면 옛 시스템처럼 345행 중 84행이 같은 사람의 두 번째 행이 된다.

    ~/kv/.venv/bin/python tools/seed_linkedin_ledger.py --apply
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kvlib import ledger  # noqa: E402
from kv_transport import notion  # noqa: E402
from skills.linkedin import profile  # noqa: E402

TASK = "linkedin"
DB_ID = "00000000-0000-0000-0000-8702f7cc7f34"


def main(apply: bool) -> int:
    already = ledger.keys(TASK, ledger.LANDED)
    print(f"장부에 이미 있는 키: {len(already)}개")

    f = notion.query(DB_ID, ["LinkedIn URL", "Author Username", "Captured At"])
    if not f.is_measured:
        print(f"🔴 노션을 못 읽었다: {f.reason}")
        return 1
    if f.partial:
        print("🔴 응답이 잘렸다 — seed 는 전량이어야 한다. 중단.")
        return 1
    rows = f.require()

    # URN 키가 없는 옛 행은 URL 정규화로 접는다. 둘 다 장부에 넣어야
    # 다음 수집에서 URN·URL 어느 쪽으로 와도 잡힌다.
    keys: dict[str, str] = {}
    blank = 0
    for r in rows:
        url = str(r.get("LinkedIn URL") or "").strip()
        if not url:
            blank += 1
            continue
        k = profile.normalize_url(url)
        if k and k not in already:
            keys.setdefault(k, url)

    print(f"노션 {len(rows)}행 · URL 없음 {blank} · 장부에 없는 고유 키 {len(keys)}개")
    if not apply:
        print("\n(dry-run) --apply 를 붙이면 장부에 넣는다")
        return 0

    for i, (k, url) in enumerate(keys.items(), 1):
        ledger.append(TASK, k, ledger.LANDED, src="seed:notion", url=url)
        if i % 300 == 0:
            print(f"  · {i}/{len(keys)}")
    print(f"✅ {len(keys)}개 넣었다 · 장부 총 {len(ledger.keys(TASK))}키")
    return 0


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
