"""Task registry — 노션 `인턴 업무 자동화` DB 의 코드 미러 (정본: config/tasks.yaml).

## 왜 있나
"무슨 업무가 존재하나"(노션) 와 "그게 자동화됐나"(코드 status) 를 **분리**해 추적한다.
새 업무는 노션에 행 하나로 생기고, 그게 registry 에 빠지면 `reconcile()` 이 **loud 경고**한다
— 침묵 누락은 W2 사고(조용한 강등)와 같은 계열이라 구조로 막는다.

## 무엇이 아닌가
스케줄러(scheduler.py)가 이 목록을 읽어 "지금 돌 task 는?"을 판정하고, run_pipeline 으로 실행한다.
registry 자체는 실행하지 않는다 — 선언만 담는다(결정 2, 선언적).
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from . import cadence, paths

_KINDS = {"routine", "ondemand"}
_CATCHUP = {"coalesce", "backfill"}
_WATERMARK = {"last_success", "last_attempt"}
_ORIGIN = {"system", "partner"}
_RUNNERS = {"local", "github-actions"}   # local=우리 스케줄러 · 그 외=외부 실행 주체(스케줄러는 건드리지 않음)


@dataclass
class Task:
    name: str
    skill: str
    kind: str = "routine"
    cadence: str | None = None
    catch_up: str = "coalesce"
    watermark: str = "last_attempt"
    resources: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
    origin: str = "system"
    covers: list[str] = field(default_factory=list)
    status: str = "slot"
    notes: str = ""
    runner: str = "local"       # 실행 주체. "local"만 스케줄러가 돌린다 — 카탈로그(무슨 일이 있나)와
                                # 실행 주체(누가 돌리나)의 분리. 외부 러너 이중실행 방지(불변규칙 01).

    @property
    def scheduled(self) -> bool:
        """cadence 로 자동 실행되는가(= 스케줄러가 due 판정할 대상)."""
        return self.kind == "routine" and bool(self.cadence)


def tasks_path() -> Path:
    return paths.repo_root() / "config" / "tasks.yaml"


_KNOWN = {f.name for f in fields(Task)}


def load(path: Path | None = None) -> list[Task]:
    """config/tasks.yaml → Task 목록. 잘못된 값은 **조용히 넘기지 않고 예외**(fail-loud)."""
    p = path or tasks_path()
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or []
    out: list[Task] = []
    for d in raw:
        unknown = set(d) - _KNOWN
        if unknown:
            raise ValueError(f"task '{d.get('name','?')}': 알 수 없는 필드 {sorted(unknown)}")
        t = Task(**{k: v for k, v in d.items() if k in _KNOWN})
        if t.kind not in _KINDS:
            raise ValueError(f"task '{t.name}': kind 는 {_KINDS} 중 하나 (got '{t.kind}')")
        if t.catch_up not in _CATCHUP:
            raise ValueError(f"task '{t.name}': catch_up 은 {_CATCHUP} (got '{t.catch_up}')")
        if t.watermark not in _WATERMARK:
            raise ValueError(f"task '{t.name}': watermark 는 {_WATERMARK} (got '{t.watermark}')")
        if t.origin not in _ORIGIN:
            raise ValueError(f"task '{t.name}': origin 은 {_ORIGIN} (got '{t.origin}')")
        if t.runner not in _RUNNERS:
            raise ValueError(f"task '{t.name}': runner 는 {_RUNNERS} (got '{t.runner}')")
        if t.scheduled and t.cadence not in cadence.DAYS:
            # 오타(dayly 등)가 런타임 weekly 폴백으로 조용히 굳지 않게 load 시점에 잡는다(fail-loud).
            raise ValueError(f"task '{t.name}': cadence 는 {sorted(cadence.DAYS)} (got '{t.cadence}')")
        out.append(t)
    _assert_unique_names(out)
    return out


def _assert_unique_names(tasks: list[Task]) -> None:
    seen: set[str] = set()
    for t in tasks:
        if t.name in seen:
            raise ValueError(f"중복 task 이름: '{t.name}' — 이름은 고유해야 한다")
        seen.add(t.name)


def covered_categories(tasks: list[Task]) -> set[str]:
    """registry 가 커버하는 노션 업무 카테고리 합집합."""
    out: set[str] = set()
    for t in tasks:
        out.update(t.covers)
    return out


def reconcile(notion_categories, *, excluded=frozenset(), tasks: list[Task] | None = None) -> list[str]:
    """노션 업무 카테고리 vs registry 커버 대조.

    노션에 있는데 (a) 어떤 task 도 안 커버하고 (b) 명시적 제외(excluded)도 아니면 → **loud 경고**.
    반환: 누락 카테고리 목록(빈 리스트면 완전).
    """
    tasks = tasks if tasks is not None else load()
    covered = covered_categories(tasks)
    missing = sorted(c for c in notion_categories if c not in covered and c not in set(excluded))
    if missing:
        print("🔴 registry 누락 — 노션에 있으나 어떤 task 도 커버하지 않는 업무:")
        for m in missing:
            print(f"   · {m}")
        print("   → config/tasks.yaml 에 task 를 추가하거나 covers 에 매핑하라(누락 침묵 금지).")
    else:
        print("✅ registry 완전 — 노션 업무(제외 제외) 전부 커버됨")
    return missing
