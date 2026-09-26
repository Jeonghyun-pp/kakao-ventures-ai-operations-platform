"""하루 보고와 꺼짐 알림 — 문(`outbound`)과 **별개의 스위치**다. 보고서만 먼저 연다.

    constraints.yaml  <task>.report_slack: on|off · <task>.report_channel
    post(task, text, send)        send(ch, text) 는 스킬이 준다(통로는 kvlib 이 모른다)
    daily(task, text, send)       하루 요약 — ops.digest_slack 이 on 이면 저녁 ops digest 한 통으로 묶인다
    health 는 skills/ops 가 여섯 스킬을 한 번에 본다(2026-09-26)
"""
from __future__ import annotations

from collections.abc import Callable

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


def daily(task: str, text: str, send: Callable[[str, str], object]) -> str:
    """스킬의 **하루 요약**. `ops.digest_slack` 이 on 이면 여기서는 보내지 않는다 — 저녁에 ops digest 가 여섯 스킬을 한 통으로 보낸다(2026-09-26).
    실패 경보는 이걸 쓰지 않고 `post()` 로 바로 낸다 — 묶으면 늦는다."""
    if constraints.get("ops.digest_slack") == "on":
        return "묶음(ops digest)"
    return post(task, text, send)
