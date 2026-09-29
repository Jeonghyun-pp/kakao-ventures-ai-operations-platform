"""평일 — 주말과 한국 공휴일(constraints `calendar.holidays_kr`)을 건너뛴다.

리마인드처럼 "그날이 쉬는 날이면 다음 평일"을 세는 스킬이 같이 쓴다(링크드인 ✅ 리마인드 · 앱스토어 격주 리마인드).
🔴 공휴일 목록이 끝나면 조용히 "공휴일 없음"이 된다 — `list_ends_soon` 으로 쓰는 쪽이 미리 알린다.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import constraints


def _holidays() -> set[str]:
    return set(constraints.get("calendar.holidays_kr"))


def is_workday(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in _holidays()


def next_workday(d: date) -> date:
    """d 가 평일이면 d, 아니면 그다음 평일."""
    off = _holidays()
    while d.weekday() >= 5 or d.isoformat() in off:
        d += timedelta(days=1)
    return d


def list_ends_soon(today: date, days: int = 30) -> str | None:
    """공휴일 목록이 `days` 안에 끝나면 마지막 날짜를, 아니면 None."""
    last = max(_holidays())
    return last if last < (today + timedelta(days=days)).isoformat() else None
