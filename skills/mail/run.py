"""hello@ 메일 — 진입점. 일은 `observe.py`(관찰·판정·보고) 와 `flow.py`(실행·슬랙 판단) 에 있다.

    observe   수집 → 한 통 한 번 판정 → (문이 열려 있으면) 실행 → 하루 보고서 → 백업     매일 10:00

밖으로 나가는 문은 `constraints.yaml mail_observe.outbound` 하나다(기본 off). 켜는 건 사람이다.
옛 실행부(plan/apply/followup · judge.py · brief.py)는 2026-09-25 에 지웠다 — 태그 `baseline-2026-09-21` 에 있다.
"""
from __future__ import annotations

import argparse
import sys

from . import observe


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mail")
    sub = p.add_subparsers(dest="cmd", required=True)
    ob = sub.add_parser("observe", help="관찰·판정·기록. 밖으로 나가는 건 mail_observe.outbound 문이 정한다(기본 off)")
    ob.add_argument("--op", default="run",
                    choices=["run", "status", "report", "replay", "diff", "label", "decide", "backup", "restore-test", "reconcile"])
    ob.add_argument("--apply", action="store_true", help="reconcile: 일치한 것을 정답으로 적는다(없으면 보고만)")
    ob.add_argument("--decision", choices=["yes", "no"], default=None)
    ob.add_argument("--days", type=int, default=None, help="이만큼 거슬러 수집 (첫 실행·밀린 날)")
    ob.add_argument("--no-judge", action="store_true")
    ob.add_argument("--criteria", default=None, help="replay 에 쓸 기준 파일 (기본: criteria.md)")
    ob.add_argument("--limit", type=int, default=None)
    ob.add_argument("--a", default="live", help="diff 의 한쪽 run")
    ob.add_argument("--b", default=None, help="diff 의 다른쪽 run (r-<sha>)")
    ob.add_argument("--id", default=None)
    ob.add_argument("--category", default=None)
    ob.add_argument("--why", default=None)
    a = p.parse_args(argv)
    return observe.main(a)


if __name__ == "__main__":
    sys.exit(main())
