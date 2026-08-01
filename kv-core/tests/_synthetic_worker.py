"""합성 워커 — 진짜 수집/메일이 아니라, kill -9 재개를 실증하기 위한 통제된 더미 잡.

부작용은 '고정 파일명에 쓰기'(= 멱등 upsert 의 대역)다. 실 노션/메일/Apify 를 안 건드린다.
process() 는 호출될 때마다 invocations.log 에 START/END 를 남겨, 재개로 몇 번 불렸는지 센다.
멱등성 증거: 재개로 두 번 불려도 results/<key> 는 한 번만 생긴다.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import job  # noqa: E402

SKILL = "synthetic"
N = int(os.environ.get("W3_N", "8"))
SLEEP = float(os.environ.get("W3_SLEEP", "0.5"))
FAIL_IDX = int(os.environ.get("W3_FAIL_IDX", "5"))   # 이 항목은 부작용 전에 실패 → 건너뜀
RESULTS = Path(os.environ["KV_DATA"]) / SKILL / "results"
INVOC = Path(os.environ["KV_DATA"]) / SKILL / "invocations.log"


def _log(line: str) -> None:
    INVOC.parent.mkdir(parents=True, exist_ok=True)
    with open(INVOC, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def process(item: dict) -> None:
    k = item["key"]
    if item["idx"] == FAIL_IDX:
        _log(f"FAIL {k}")
        raise RuntimeError(f"합성 실패: {k}")
    _log(f"START {k}")
    time.sleep(SLEEP)                       # 이 구간에서 kill -9 가 떨어지도록 느리게
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{k}.json").write_text('{"ok":true}', encoding="utf-8")  # 멱등: 고정 파일명
    _log(f"END {k}")


def main() -> int:
    items = [{"idx": i, "key": f"item-{i:02d}"} for i in range(N)]
    jid = job.create(SKILL, {"channel": "synthetic", "n": N}, job_id="synthetic-run")
    job.run(
        SKILL, jid, items,
        key=lambda it: it["key"],
        process=process,
        validate=lambda done, skipped: done > 0,   # 불변규칙 06
        heavy=False,
    )
    print(f"[worker] 종료 상태: {job.current_state(SKILL, jid)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
