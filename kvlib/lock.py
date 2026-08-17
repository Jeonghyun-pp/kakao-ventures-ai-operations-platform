"""이중 실행 방지 — flock 하나.

## 왜 필요한가

진입점이 둘이라(사람이 부르는 것 · 크론) 겹칠 수 있다. 겹치면:

    돈       같은 페이지를 두 번 사서 요금이 두 배가 된다
    노션     같은 행을 두 번 만들거나, 한쪽이 지운 걸 다른 쪽이 고치려다 400 이 난다

🔴 실측(2026-08-17): 아카이브 작업의 출력이 버퍼링돼 죽은 줄 알고 같은 작업을 또 띄웠다.
   둘이 같은 페이지를 건드려 193건이 "실패"로 잡혔다(실은 상대가 이미 처리한 것).
   결과는 맞았지만, 돈 쓰는 경로에서 같은 일이 나면 요금이 두 배가 된다.

## 🔴 락은 flock 만 쓰고 **절대 unlink 하지 않는다**

옛 시스템은 pid+mtime 으로 "죽은 락"을 판단해 지웠는데, 그게 A/B/C 동시 점유를
만들었다. 커널이 소유권을 관리하게 두면 프로세스가 죽는 순간 자동으로 풀린다.
파일은 남지만 그건 진단용이고 해가 없다.

    with lock.hold("linkedin"):
        ...
"""
from __future__ import annotations

import contextlib
import fcntl
import os
from datetime import datetime, timezone

from . import paths


class Busy(RuntimeError):
    """다른 실행이 잡고 있다."""


@contextlib.contextmanager
def hold(name: str):
    p = paths.data("_locks") / f"{name}.lock"
    f = p.open("a+")
    try:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.seek(0)
            who = f.read().strip() or "(알 수 없음)"
            raise Busy(f"'{name}' 을 다른 실행이 잡고 있다: {who}") from None
        f.seek(0)
        f.truncate()
        f.write(f"pid={os.getpid()} since={datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ}")
        f.flush()
        yield
    finally:
        f.close()          # 닫으면 락이 풀린다. **파일은 지우지 않는다.**
