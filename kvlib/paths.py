"""데이터 루트 기준 경로 — 코드에 경로 리터럴을 두지 않는 단일 진입점.

## 세 루트

    ~/kv/          코드    git 관리
    ~/kv-data/     데이터   git 이 안 닿는다
    ~/kv-config/   시크릿   백업 정책이 다르다 (시크릿은 회전 가능, 데이터는 불가)

## 🔴 리포 안에 데이터 심볼릭 링크를 만들지 않는다

옛 시스템은 `skills/net-sourcing/project/data` → `~/kv-data/net-sourcing` 링크를 뒀다.
그 결과 **합성 테스트가 그 링크로 KV_DATA 를 우회해 넉 달간 프로덕션 데이터에 썼다.**
링크가 gitignore 라 CI 엔 없었고, 그래서 **원리적으로 CI 가 못 보는 결함**이었다.

경로는 이 모듈로만 얻는다.
"""
from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_DATA = Path.home() / "kv-data"
_DEFAULT_CONFIG = Path.home() / "kv-config"


def data_root() -> Path:
    """실데이터 루트. `$KV_DATA` 로 덮어쓸 수 있다(테스트가 이걸 쓴다)."""
    return Path(os.environ.get("KV_DATA") or _DEFAULT_DATA).expanduser()


def config_root() -> Path:
    """자격증명 루트. `$KV_CONFIG` 로 덮어쓸 수 있다."""
    return Path(os.environ.get("KV_CONFIG") or _DEFAULT_CONFIG).expanduser()


def data(*parts: str, create: bool = True) -> Path:
    """`$KV_DATA/<parts...>`"""
    p = data_root().joinpath(*parts)
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def ledger(task: str) -> Path:
    """장부 파일. `$KV_DATA/_ledger/<task>.jsonl`

    옛 시스템 데이터와 섞이지 않게 `_ledger/` 아래에만 쓴다.
    """
    return data("_ledger") / f"{task}.jsonl"


def repo_root() -> Path:
    """코드 루트. `__file__` 에서 역산한다 — 하드코딩하지 않는다."""
    return Path(__file__).resolve().parents[1]
