"""하루 보고와 꺼짐 알림 — 문(`outbound`)과 **별개의 스위치**다. 보고서만 먼저 연다.

    constraints.yaml  <task>.report_slack: on|off · <task>.report_channel
    post(task, text, send)        send(ch, text) 는 스킬이 준다(통로는 kvlib 이 모른다)
    health(task, dir, send, ...)  잡은 자기가 안 돌았다고 말할 수 없다. 다른 시각의 잡이 오늘 보고서 파일이 있는지 본다
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from . import constraints, ledger

CHUNK = 3500                                                     # 슬랙 한 글 상한 근처. 요약은 이 안에 들어야 한다


def post(task: str, text: str, send: Callable[[str, str], object]) -> str:
    if constraints.get(f"{task}.report_slack") != "on":
        return "꺼짐"
    try:
        send(constraints.get(f"{task}.report_channel"), text[:CHUNK])
        return "게시함"
    except Exception as e:                                       # noqa: BLE001 — 보고 실패가 잡을 죽이지 않는다. 파일은 남아 있다
        return f"실패({type(e).__name__}: {e})"


def health(task: str, reports_dir: Path, send: Callable[[str, str], object], *, label: str,
           weekdays_only: bool = False) -> int:
    today = ledger.today()
    if weekdays_only and date.fromisoformat(today).weekday() >= 5:
        print(f"· 주말 — {label} 이 안 나가는 날이다")
        return 0
    f = reports_dir / f"{today}.md"
    if f.exists():
        print(f"✅ 오늘 보고서 있음 · {f.name}")
        return 0
    print("🔴 " + post(task, f"🔴 *오늘({today}) {label} 이 안 돌았습니다* — 보고서 파일이 없습니다. "
                        "맥이 잠들었거나 launchd 작업이 내려갔을 수 있습니다: `launchctl list | grep kv.`", send))
    return 2
