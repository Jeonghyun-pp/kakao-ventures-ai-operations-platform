"""주기(cadence) 어휘 — **플랫폼 전체가 공유하는 단 하나의 정본.**

W4에서 net-sourcing/core/cadence.py를 여기로 승격했다. 링크드인 축·스레드 층·추정기(net-sourcing)에
더해 스케줄러(kv_core.scheduler)와 registry가 **같은 표**를 본다. 각자 복사하면 언젠가 한쪽만
고쳐지고, 그때 "링크드인은 격주인데 스레드는 매주"가 된다.

🔴 **조용한 폴백을 만들지 마라 — 이 파일의 존재 이유다.**
   전에는 `_DAYS.get(cadence, 7)`이었다. 표에 없는 이름(오타·신규어)을 만나면 **말없이 weekly로
   강등**되고, 로그는 정상이라 "절감이 왜 안 나지"를 며칠 뒤에야 안다(2026-07-19에 실제로 재현).
   → `interval_elapsed`는 모르는 이름을 만나면 **크게 경고하고** 보수적으로(=자주 도는 쪽으로) 간다.
     돈이 더 나가는 건 되돌릴 수 있지만, 조용히 안 도는 층은 아무도 모른다.

🕐 **날짜는 UTC로 판정한다.** 2026-08-02 계층 A 사고(러너 로컬 TZ vs KST 불일치로 날짜 경계에서
   예약 실행 전멸)를 잡 스케줄에서 되풀이하지 않는다. `today`를 명시로 넘기지 않으면 UTC 오늘을 쓴다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

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


def freshness_limit(task) -> int:
    """[W9] 이 task 의 데이터가 며칠까지 신선한가 — **선언 우선, 없으면 주기 기반 규칙**.

    `tasks.yaml` 의 `freshness_days` 가 있으면 그 값(SLO 선언). 없으면 주기 + 유예:
    daily 는 유예 0(하루 밀리면 곧 결손), 그 외는 +1(수면·보조틱 흡수).
    관측(orrery)과 알림(alerts)이 **이 함수 하나**를 공유해야 "다이제스트는 ✅인데
    알림은 🔴" 같은 이중 진실원이 안 생긴다.
    """
    declared = getattr(task, "freshness_days", None)
    if declared:
        return int(declared)
    gap = gap_days(getattr(task, "cadence", None), where=getattr(task, "name", ""))
    return gap + (0 if gap <= 1 else 1)


def _utc_today() -> date:
    """UTC 기준 오늘. 로컬 타임존에 의존하지 않는다(계층 A 타임존 사고 방지)."""
    return datetime.now(timezone.utc).date()


def gap_days(cadence: str | None, *, default: str = "weekly", where: str = "") -> int:
    """cadence 이름 → 간격(일). 모르는 이름이면 **크게 경고**하고 default로 간다."""
    name = (cadence or default)
    if name in DAYS:
        return DAYS[name]
    key = f"{where}:{name}"
    if key not in _warned:
        _warned.add(key)
        print(f"🔴 알 수 없는 cadence '{name}'{f' ({where})' if where else ''} — "
              f"'{default}'로 처리한다. kv_core/cadence.py DAYS에 추가하라. "
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

    today를 명시하지 않으면 **UTC 오늘**로 판정한다(로컬 TZ 의존 금지).
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
    return last_d <= (today or _utc_today()) - timedelta(days=gap)
