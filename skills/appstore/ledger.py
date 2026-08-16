"""장부 — 우리의 유일한 진실.

## 왜 이게 전부인가

멱등·재개·워터마크·관측이 전부 여기서 나온다. 파생물(state.json·cursor.json)을
**아예 만들지 않는다** — 파생물이 없으면 갈라질 게 없다.

    멱등    이미 적힌 키는 다시 처리하지 않는다
    재개    죽어도 다음 실행이 남은 것만 한다. 별도 재개 로직이 없다
    워터마크 마지막 줄의 날짜가 곧 "언제 돌았나"
    관측    오늘 줄 수가 곧 "몇 건 했나". **보고를 claude 기억이 아니라 여기서 만든다**

## 이벤트 2종

    landed   노션에 착지했다   ← 멱등의 근거
    picked   claude 가 골랐다

옛 시스템은 상태 전이 5종짜리 상태머신을 뒀는데, 실제로 필요한 건 이 둘이었다.
상태머신은 관측용이지 정확성용이 아니다.

## 🔴 찢긴 마지막 줄

kill -9 나 전원 차단이 나면 마지막 줄이 개행 없이 끊긴다. 장부가 유일한 진실인데
읽다 죽으면 **시스템 전체가 멈춘다.** 그래서:

    쓰기 전   개행으로 안 끝났으면 그 조각을 잘라낸다
    읽는 중   마지막 줄만 깨진 건 버린다
              **중간 줄이 깨졌으면 시끄럽게 실패한다** — 자동 복구는 하지 않는다
              (조용히 넘기면 데이터 손실이 그대로 굳는다)
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kvlib import paths

LANDED = "landed"
PICKED = "picked"


class LedgerCorrupt(RuntimeError):
    """중간 줄이 깨졌다. 사람이 봐야 한다."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _repair_torn_tail(p: Path) -> None:
    """개행 없이 끝난 마지막 조각을 잘라낸다. append 직전에만 부른다."""
    if not p.exists() or p.stat().st_size == 0:
        return
    with p.open("rb+") as f:
        f.seek(-1, os.SEEK_END)
        if f.read(1) == b"\n":
            return
        # 마지막 개행을 찾아 그 뒤를 버린다
        f.seek(0)
        data = f.read()
        cut = data.rfind(b"\n")
        f.truncate(cut + 1 if cut >= 0 else 0)
    print(f"  ⚠️  장부의 찢긴 마지막 줄을 잘라냈다: {p.name}")


# ---------------------------------------------------------------- 쓰기

def append(task: str, key: str, ev: str, **fields) -> None:
    """한 줄 추가. **매 줄 fsync** — 죽어도 적힌 것은 남는다."""
    p = paths.ledger(task)
    _repair_torn_tail(p)
    row = {"ts": _now(), "key": str(key), "ev": ev, **fields}
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------- 읽기

def rows(task: str, *, ev: str | None = None, day: str | None = None) -> list[dict]:
    """장부 전량(또는 필터). 중간 줄이 깨졌으면 raise."""
    p = paths.ledger(task)
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    out = []
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError as e:
            if i == len(lines) - 1:
                print(f"  ⚠️  장부 마지막 줄이 깨져 있다 — 버린다: {p.name}")
                continue
            raise LedgerCorrupt(
                f"{p}:{i + 1} 중간 줄이 깨졌다. 자동 복구하지 않는다 — 사람이 확인하라.\n  {line[:120]}"
            ) from e
        if ev and r.get("ev") != ev:
            continue
        if day and _local_day(str(r.get("ts", ""))) != day:
            continue
        out.append(r)
    return out


def keys(task: str, ev: str = LANDED) -> set[str]:
    """그 이벤트가 적힌 키 집합. 멱등 판정에 쓴다."""
    return {r["key"] for r in rows(task, ev=ev) if "key" in r}


def has(task: str, key: str, ev: str = LANDED) -> bool:
    return str(key) in keys(task, ev)


def last(task: str) -> dict | None:
    """마지막 줄. '언제 돌았나'가 여기서 나온다."""
    rs = rows(task)
    return rs[-1] if rs else None


# 🔴 판정 기준일은 **로컬(KST)** 이다. [0] 제약:
#    "스케줄러의 period 는 UTC 라 00~09시 KST 에 하루 어긋난다. UTC 로 세면 매일 아침 오탐."
#    07:00 KST 는 22:00 UTC(전날)라 UTC 로 날짜를 세면 사람이 보는 날짜와 하루 어긋난다.
#    ts 는 UTC 로 적되(순서 비교용), **하루의 경계는 로컬**로 센다.
_KST = timezone(timedelta(hours=9))


def today() -> str:
    return datetime.now(_KST).strftime("%Y-%m-%d")


def _local_day(ts: str) -> str:
    """UTC ts 를 KST 날짜로 옮긴다."""
    try:
        return (datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
                .replace(tzinfo=timezone.utc).astimezone(_KST).strftime("%Y-%m-%d"))
    except Exception:
        return ts[:10]


def summary(task: str, day: str | None = None) -> dict:
    """오늘 무슨 일이 있었나 — **보고는 여기서 만든다.**

    claude 가 "47건 처리했습니다"라고 쓰면 그게 사실인지 확인할 사람이 없다.
    장부를 세서 쓴다.
    """
    day = day or today()
    # 🔴 seed 로 넣은 건 "오늘 한 일"이 아니다. 섞어 세면 보고가 거짓말을 한다.
    rs = [r for r in rows(task, day=day) if not str(r.get("src", "")).startswith("seed")]
    seeded = len(rows(task, day=day)) - len(rs)
    by_ev: dict[str, int] = {}
    for r in rs:
        by_ev[r.get("ev", "?")] = by_ev.get(r.get("ev", "?"), 0) + 1
    lastrow = last(task)
    return {
        "day": day,
        "counts": by_ev,
        "total_today": len(rs),
        "seeded_today": seeded,
        "last_ts": (lastrow or {}).get("ts"),
        "ledger_lines": len(rows(task)),
    }
