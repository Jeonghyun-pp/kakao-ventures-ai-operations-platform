"""실데이터 루트 기준 경로 — 코드에 경로 리터럴을 두지 않기 위한 단일 진입점.

## 왜 필요한가

절대경로가 코드에 **하나라도** 남으면 다른 머신으로 옮길 때 통째로 깨진다.
기본구조 1단계의 비가역 결정 3가지 중 하나다.

## 왜 리포 밖인가

`git clean -xdf`는 `.gitignore`된 파일도 지운다(`-x`가 그 뜻). 실측(2026-07-27)으로
확인했다 — 리포 안에 있으면 명령 하나로 사라지고, 심볼릭 링크면 링크만 지워지고
대상은 살아남는다. 그래서 데이터는 리포 밖에 두고 링크로 잇거나 이 모듈로 접근한다.

## 쓰는 법

    from kv_core import paths
    p = paths.data("net-sourcing", "snapshots")     # ~/kv-data/net-sourcing/snapshots/
    f = paths.data("net-sourcing") / "state.json"
"""
from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_DATA = Path.home() / "kv-data"
_DEFAULT_CONFIG = Path.home() / "kv-config"


def data_root() -> Path:
    """실데이터 루트. `$KV_DATA` 로 덮어쓸 수 있다."""
    return Path(os.environ.get("KV_DATA") or _DEFAULT_DATA).expanduser()


def config_root() -> Path:
    """자격증명 루트. `$KV_CONFIG` 로 덮어쓸 수 있다."""
    return Path(os.environ.get("KV_CONFIG") or _DEFAULT_CONFIG).expanduser()


def data(skill: str, *parts: str, create: bool = True) -> Path:
    """`$KV_DATA/<skill>/<parts...>` — 스킬별 데이터 경로.

    코드 루트와 대칭이다: `skills/<name>/` ↔ `kv-data/<name>/`.
    경로만 보고 어느 스킬의 데이터인지 알 수 있고, 스킬을 지울 때 대상이 명확해진다.
    """
    p = data_root().joinpath(skill, *parts)
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def repo_root() -> Path:
    """코드 루트. 이 패키지 위치에서 역산한다 — 하드코딩하지 않는다.

    `<repo>/kv-core/kv_core/paths.py` 이므로 parents[2] 가 리포 루트다.
    (editable 설치라 `__file__` 은 site-packages가 아니라 실제 소스를 가리킨다)
    """
    return Path(__file__).resolve().parents[2]
