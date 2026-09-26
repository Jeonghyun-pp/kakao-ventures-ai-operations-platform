"""[일회성] 판정을 못 받았거나 착지에 실패한 사람을 **아카이브에서** 다시 처리한다.

## 왜 돈이 안 드나

유료로 받은 원본을 `{slug, payload, items}` 로 무조건 저장해뒀기 때문이다.
파싱·판정 버그를 고치고 나서 **다시 사지 않고** 재처리할 수 있다.
이게 원본 아카이브의 존재 이유다 — 07-13 에 필드명을 잘못 읽어 돈을 다시 쓴 적이 있다.

    ~/kv/.venv/bin/python tools/rejudge_linkedin.py --apply
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kvlib import ledger, paths  # noqa: E402
from kv_transport import notion  # noqa: E402
from skills.linkedin import judge, profile, run  # noqa: E402

ARCHIVE = paths.data_root() / "linkedin" / "raw" / "_actor"


def main(apply: bool) -> int:
    landed = ledger.keys(run.TASK, ledger.LANDED)

    # 아카이브 전량에서 아직 착지 안 된 사람을 모은다 (축 정보를 payload 로 역산)
    pool: dict[str, dict] = {}
    for f in sorted(ARCHIVE.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        axis = _axis_of(d.get("payload") or {})
        for it in d.get("items", []):
            k = profile.seen_key(it)
            if k and k not in landed and k not in pool:
                pool[k] = {**it, "_axis": axis}

    print(f"· 아카이브에서 아직 착지 안 된 사람 {len(pool)}명")
    if not pool or not apply:
        print("\n(dry-run) --apply 를 붙이면 다시 판정하고 착지한다")
        return 0

    cards = [run._summary(v) for v in pool.values()]
    verdicts = judge.judge_people(cards)
    if not verdicts.is_measured:
        print(f"🔴 판정 실패: {verdicts.reason}")
        return 1
    vmap = {v["key"]: v for v in verdicts.require()}
    still = [k for k in pool if k not in vmap]
    print(f"· 판정 {len(vmap)}/{len(pool)}" + (f" · 여전히 못 받은 {len(still)}명" if still else ""))

    run_id = f"rejudge-{ledger.today()}"
    ok = fail = 0
    for k, item in pool.items():
        v = vmap.get(k)
        if not v:
            continue
        res = notion.create(run.DB, run._props(item, v, run_id))
        if not res.is_measured:
            fail += 1
            print(f"  ✗ {k} 착지 실패: {res.reason}")
            continue
        pid = res.require()
        ledger.append(run.TASK, k, ledger.LANDED, page_id=pid, run=run_id,
                      axis=item["_axis"], tier=v["verdict"], name=run._summary(item)["name"])
        if v["verdict"] == "Catch":
            ledger.append(run.TASK, k, run.MEETING, page_id=pid, axis=item["_axis"])
        ok += 1
    print(f"· 착지 {ok} · 실패 {fail}")
    return 0


def _axis_of(payload: dict) -> str:
    """payload 로 어느 축이었는지 되짚는다 — 아카이브엔 축 이름이 안 들어 있다."""
    from skills.linkedin import axes
    for name, a in axes.AXES.items():
        want = a["payload"]
        if all(payload.get(k) == v for k, v in want.items()):
            return name
    return ""


if __name__ == "__main__":
    raise SystemExit(main("--apply" in sys.argv))
