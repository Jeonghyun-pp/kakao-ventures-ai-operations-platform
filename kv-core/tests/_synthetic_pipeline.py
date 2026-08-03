"""다단계 합성 워커 — run_pipeline 의 kill-9 재개를 실증한다(W4 완료 판정).

W3 의 _synthetic_worker.py 가 단일 스테이지였다면, 이건 순서형 파이프라인이다:
    collect → land(느린 항목 루프, 여기서 kill) → meeting
부작용은 '고정 파일명 쓰기'(멱등 upsert 대역). 실 노션/메일/Apify 안 건드린다.
invocations.log 에 각 호출을 append 해서, 재개로 몇 번 불렸는지 센다.

증거:
- collect 는 run1 에서 stage_done 됐으니 **재개 때 스킵**(COLLECT 1회).
- land 는 kill 순간 처리 중이라 재개로 일부 항목 재실행되지만 결과 파일은 **항목당 딱 1개**(멱등).
- meeting 은 재개 뒤에야 실행(MEETING 1회). 최종 completed.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import job  # noqa: E402

SK = "synthetic"
N = int(os.environ.get("W3_N", "6"))
SLEEP = float(os.environ.get("W3_SLEEP", "0.5"))
DATA = Path(os.environ["KV_DATA"]) / SK
RES = DATA / "results"
LOG = DATA / "invocations.log"


def _log(line: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())


def s_collect(skill: str, job_id: str) -> None:
    _log("COLLECT")
    time.sleep(0.05)


def s_land(skill: str, job_id: str) -> None:
    """느린 항목 루프 — 여기서 kill -9 가 떨어지도록. 항목별 고정 파일(멱등)."""
    def process(it: dict) -> None:
        _log(f"LAND-START {it['k']}")
        time.sleep(SLEEP)
        RES.mkdir(parents=True, exist_ok=True)
        (RES / f"{it['k']}.json").write_text('{"ok":true}', encoding="utf-8")
        _log(f"LAND-END {it['k']}")
    job.process_items(skill, job_id, [{"k": f"item-{i:02d}"} for i in range(N)],
                      key=lambda it: it["k"], process=process, stage="land")


def s_meeting(skill: str, job_id: str) -> None:
    _log("MEETING")


def main() -> int:
    stages = [job.Stage("collect", s_collect),
              job.Stage("land", s_land),
              job.Stage("meeting", s_meeting)]
    jid = job.create(SK, {"channel": "synthpipe", "n": N}, job_id="synthpipe")
    d = job.run_pipeline(SK, jid, stages, resources=["synthetic-res"])
    print(f"[pipe] 종료 상태: {d.state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
