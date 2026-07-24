"""주기(cadence) 어휘 — **두 채널이 공유하는 단 하나의 정본.**

왜 여기 있나: 링크드인 축(`adapters/linkedin.py::_axis_due`)과 스레드 층
(`adapters/threads.py::_layer_due`), 그리고 추정기(`tools/estimate_cost.py`)가 **같은 표**를 봐야
한다. 각자 복사하면 언젠가 한쪽만 고쳐지고, 그때 "링크드인은 격주인데 스레드는 매주"가 된다.
(`estimate_cost.py`가 `_expand`를 두고 *"여기서 별칭 규칙을 다시 적으면 드리프트가 난다"*고
경고하는 것과 같은 이유다.)

🔴 **조용한 폴백을 만들지 마라 — 이 파일의 존재 이유다.**
   전에는 `_DAYS.get(cadence, 7)`이었다. 표에 없는 이름(오타·신규어)을 만나면 **말없이 weekly로
   강등**되고, 로그는 정상이라 "절감이 왜 안 나지"를 며칠 뒤에야 안다(2026-07-19에 실제로 재현).
   → `interval_elapsed`는 모르는 이름을 만나면 **크게 경고하고** 보수적으로(=자주 도는 쪽으로)
     간다. 돈이 더 나가는 건 되돌릴 수 있지만, 조용히 안 도는 층은 아무도 모른다.
"""
from __future__ import annotations

from datetime import date, timedelta

# 주기 이름 → 최소 실행 간격(일). `always` = 간격 없음(매 실행).
DAYS: dict[str, int] = {
    "always": 0,
    "daily": 1,
    "weekly": 7,
    "biweekly": 14,
    "monthly": 30,
    "quarterly": 90,
}

_warned: set[str] = set()          # 같은 오타로 로그를 도배하지 않는다


def gap_days(cadence: str | None, *, default: str = "weekly", where: str = "") -> int:
    """cadence 이름 → 간격(일). 모르는 이름이면 **크게 경고**하고 default로 간다."""
    name = (cadence or default)
    if name in DAYS:
        return DAYS[name]
    key = f"{where}:{name}"
    if key not in _warned:
        _warned.add(key)
        print(f"🔴 알 수 없는 cadence '{name}'{f' ({where})' if where else ''} — "
              f"'{default}'로 처리한다. core/cadence.py DAYS에 추가하라. "
              f"(가능한 값: {', '.join(DAYS)})")
    return DAYS[default]


def interval_elapsed(cadence: str | None, last_run: str | None, *,
                     today: date | None = None, default: str = "weekly",
                     where: str = "") -> bool:
    """최소 실행 간격이 찼나. **`cadence`는 고정 주기가 아니라 하한이다.**

    수동·비정기 실행에서는 실간격이 `ceil(gap / 실행리듬) × 실행리듬`으로 늘어난다 —
    10일 리듬이면 biweekly 층은 14일이 아니라 **20일**마다 돈다. 손실 추정이 이만큼 낙관 편향된다.

    last_run이 없거나 못 읽으면 **True**(=돈다). 상태 파일이 비면 전 층이 한 번은 도는 게 맞다
    — 모르는 상태에서 안 도는 쪽으로 기울면 층이 조용히 죽는다.
    """
    gap = gap_days(cadence, default=default, where=where)
    if not gap:
        return True                                   # always
    if not last_run:
        return True                                   # 한 번도 안 돌았다
    try:
        last_d = date.fromisoformat(str(last_run)[:10])
    except (ValueError, TypeError):
        return True                                   # 못 읽으면 도는 쪽
    return last_d <= (today or date.today()) - timedelta(days=gap)
