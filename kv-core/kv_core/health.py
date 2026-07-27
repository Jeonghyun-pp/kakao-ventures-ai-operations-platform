"""심박과 헬스 판정 — **골격. 본체는 W6.**

기본구조 6단계. 세 파일이 각각 다른 질문에 답한다.

    health file   → 지금 살아 있는가       자주 갱신 · 덮어쓰기
    event log     → 무엇을 수행했는가      append-only
    state file    → 지금 어느 단계인가     전이 시점에만

**셋을 하나로 합치면 안 된다** — 갱신 주기와 쓰기 패턴이 다르다.

## 🔴 불변 규칙 06 — 0건 산출을 표면화한다

예외를 삼키는 핸들러가 있으면 하위 층이 죽어도 경고 한 줄로 묻힌다.
실제로 한 수집 층이 **엿새간 죽어 있었는데** 로그엔 ⚠️ 한 줄뿐이라 수백 줄 사이에 묻혔다.
0건은 정상이 아니라 **헬스 경고**로 올린다.

## 간헐 가용 환경에서의 헬스

상시 머신이 있으면 헬스는 "지금 살아 있나"지만, 없으면 **"며칠 밀렸나"** 다.
실패는 로그에라도 남지만 **실행되지 않은 것은 아무 흔적이 없다.**
"""
from __future__ import annotations

from pathlib import Path

from . import paths


def heartbeat_path(skill: str, job: str = "default") -> Path:
    """심박 파일 경로. W6 전에도 경로 규약은 고정해둔다."""
    return paths.data(skill, "health") / f"{job}.json"


def beat(skill: str, job: str = "default", **meta) -> None:
    """심박 기록. **W6에서 구현.**"""
    raise NotImplementedError("W6 — 프로세스 감독")


def staleness_days(skill: str, job: str = "default") -> float | None:
    """마지막 실행 이후 경과 일수. 간헐 가용 환경의 핵심 지표. **W6/W9에서 구현.**"""
    raise NotImplementedError("W6 — 프로세스 감독")
