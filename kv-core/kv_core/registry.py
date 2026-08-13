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
_RUNNERS = {"local", "github-actions", "launchd-direct"}
# [2026-08-13 W12 · E-F9] 아래 둘은 어휘표조차 없어 오타가 조용히 통과했다.
# `cadence` 오타는 fail-loud 인데 **한 필드 옆 `resources` 는 무검증**이었다 —
# 같은 파일 안에서 규율이 비대칭이었고, 하필 조용히 새는 쪽이 무검증이었다.
_STATUS = {"done", "partial", "slot"}
# 자원 이름은 곧 락 파일 이름이다(`_locks/<name>.lock`). 오타 하나면 **별도 락**을 잡아
# 직렬화가 갈라지고, 두 잡이 동시에 claude 세션을 띄운다(ADR 0003 대가 ③).
# 새 자원을 늘릴 땐 config/resources.yaml 과 함께 고친다.
_RESOURCES = {"claude", "notion", "notion-bplan", "apify", "gmail-inbox", "sheet-a1"}
# local=우리 스케줄러가 실행 · github-actions=GH cron · launchd-direct=개별 plist 가 직접 실행(카탈로그 등재만).
# local 이 아닌 task 는 스케줄러가 건드리지 않는다(이중실행 방지, 규칙 01) — 단 shadow=true 면 검증용 병행 실행.


@dataclass
class Task:
    name: str
    skill: str
    kind: str = "routine"
    cadence: str | None = None
    catch_up: str = "coalesce"
    # 🔴 [2026-08-13 W12 · E-F9] 기본값이 `last_attempt` 였다 — **페일오픈 쪽이 기본**이다.
    #    ADR 0004 는 *"의심스러우면 False — 재시도는 싸고 유실은 비싸다"* 인데 반대였다.
    #    실측: watermark 미선언 + weekly task 가 러너 예외로 폭발 → last_attempt 만 전진 →
    #    **다음 주까지 재시도 없음**. 지금 13개 task 는 전부 명시 선언이라 안 물렸고,
    #    새 task 하나가 이 줄을 빠뜨리는 순간 물린다. 보수적인 쪽으로 뒤집는다.
    watermark: str = "last_success"
    resources: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
    origin: str = "system"
    covers: list[str] = field(default_factory=list)
    status: str = "slot"
    notes: str = ""
    runner: str = "local"       # 실행 주체. "local"만 스케줄러가 돌린다 — 카탈로그(무슨 일이 있나)와
                                # 실행 주체(누가 돌리나)의 분리. 외부 러너 이중실행 방지(불변규칙 01).
    freshness_days: int | None = None   # [W9] 신선도 SLO — "이 데이터는 며칠까지 유효한가".
                                        # 미지정이면 주기 기반 기본값(cadence.freshness_limit).
                                        # 관측·알림이 이 선언 하나를 공유한다(이중 진실원 방지).
    shadow: bool = False        # [W6] 외부 러너가 primary 인 task 를 스케줄러가 **검증용으로 병행 실행**.
                                # 컷오버 = runner: local 로 바꾸고 이 플래그 제거(한 줄). 저장소가 분리된
                                # 경우에만 안전(appstore: GH=data 브랜치 vs 로컬=~/kv-data — 충돌 0).

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
        if t.shadow and t.runner == "local":
            raise ValueError(f"task '{t.name}': shadow 는 외부 러너 task 전용 — runner: local 이면 그냥 실행이다")
        if t.freshness_days is not None and int(t.freshness_days) < 1:
            raise ValueError(f"task '{t.name}': freshness_days 는 1 이상 (got {t.freshness_days})")
        if t.scheduled and t.cadence not in cadence.DAYS:
            # 오타(dayly 등)가 런타임 weekly 폴백으로 조용히 굳지 않게 load 시점에 잡는다(fail-loud).
            raise ValueError(f"task '{t.name}': cadence 는 {sorted(cadence.DAYS)} (got '{t.cadence}')")

        # ── [2026-08-13 W12 · 리뷰 E-F9] 아래 넷은 검증이 아예 없었다 ──────────────
        if t.status not in _STATUS:
            raise ValueError(f"task '{t.name}': status 는 {sorted(_STATUS)} (got '{t.status}')")
        bad_res = sorted(set(t.resources or []) - _RESOURCES)
        if bad_res:
            raise ValueError(
                f"task '{t.name}': 알 수 없는 자원 {bad_res} — 오타면 `_locks/<오타>.lock` 이라는 "
                f"**별도 락**을 잡아 직렬화가 조용히 갈라진다. 쓸 수 있는 값: {sorted(_RESOURCES)}\n"
                f"   (새 자원은 registry._RESOURCES 와 config/resources.yaml 을 함께 고쳐라.)")
        if t.kind == "routine" and not t.cadence:
            # `scheduled` 가 False 가 되어 due_tasks 에도, 스케줄러의 unwired loud 스킵
            # 목록에도 안 뜬다. **완전 침묵** — mail-triage 미실행 사고는 최소한 로그가
            # 있었는데 이 변형은 그것조차 없다.
            raise ValueError(
                f"task '{t.name}': kind=routine 인데 cadence 가 없다 — 이 상태는 due 목록에도 "
                f"미배선 목록에도 안 떠서 **아무 로그 없이 사라진다**. "
                f"온디맨드면 kind: ondemand 라고 적어라.")
        if t.status != "slot" and not (paths.repo_root() / "skills" / t.skill).is_dir():
            # status: slot = 아직 안 만든 자리(설계상 정상). 그 외에는 실재해야 한다.
            raise ValueError(f"task '{t.name}': skill '{t.skill}' 디렉토리가 없다 "
                             f"(미구현 자리면 status: slot 으로 적어라)")
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
