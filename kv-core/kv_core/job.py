"""잡 러너와 상태 프로토콜 — **골격. 본체는 W3.**

기본구조 3단계. 이 규약 하나가 4·6·7·9·11단계를 전부 가능하게 만든다.

## 디렉토리 규약

    $KV_DATA/<skill>/jobs/<id>/
      request.json    무엇을 요청받았나
      state.json      지금 어느 단계인가
      events.jsonl    무엇을 수행했나 (append-only)
      artifacts/      산출물
      stderr.log

## 상태머신

    requested → queued → running → validating → completed | failed

전이마다 넷을 정의한다 — **누가 바꾸나 · 어디 기록되나 · 중복 전이 가능한가 · 죽으면 남나.**

## 강제 사항 2개

    append-only 로그      덮어쓰지 않는다. 중단돼도 앞부분이 남는다
    원자적 쓰기(tmp→rename)  비원자적 쓰기는 재실행 시 중복 산출물을 만든다 (실제 사고)

## 파일을 쓰는 이유

DB의 싼 대체물이 아니라 **프로세스 간 상태 교환 프로토콜**이기 때문이다.
읽기만 하면 관측이 되고(7단계), 죽음을 판정할 근거가 되고(6단계),
새 기능이 규약만 따르면 관측·복구를 공짜로 얻는다(11단계).
"""
from __future__ import annotations

from pathlib import Path

STATES = ("requested", "queued", "running", "validating", "completed", "failed")


def atomic_write(path: Path, text: str) -> None:
    """tmp → rename. **부분 기록된 파일이 남지 않게 한다.**

    비원자적 쓰기 때문에 재실행 시 중복 산출물이 만들어진 적이 있다.
    이 한 줄이 그걸 없앤다. W3 전에도 쓸 수 있도록 지금 넣어둔다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def create(skill: str, request: dict) -> str:
    """잡 생성 → job_id. **W3에서 구현.**"""
    raise NotImplementedError("W3 — 잡 러너와 상태 프로토콜")


def transition(skill: str, job_id: str, to: str, **meta) -> None:
    """상태 전이 + events.jsonl 기록. **W3에서 구현.**"""
    raise NotImplementedError("W3 — 잡 러너와 상태 프로토콜")
