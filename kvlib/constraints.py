"""[0] 외부 제약을 코드로 읽는 단일 진입점.

## 왜 코드에 숫자를 안 박나

제약이 코드에 흩어지면 두 진실원이 생긴다. 옛 시스템에서 실제로:

    월 천장이 resources.yaml $29 vs config.yaml $45 로 갈렸고,
    조회 한 번 실패하면 유효 천장이 조용히 $45 가 됐다.

그래서 정본은 `constraints.yaml` 하나다. 코드는 여기서 읽는다.

## 없는 키는 조용히 넘기지 않는다

`.get(key, 기본값)` 을 쓰지 않는다. 없으면 **즉시 raise** 한다.
옛 시스템에서 `.get(cadence, 7)` 이 모르는 주기를 조용히 weekly 로 강등시킨 사고가 있었다.

    from kvlib import constraints
    cap = constraints.get("appstore.chart_caps.top-free")   # 200
    constraints.get("appstore.없는키")                        # KeyError (조용히 넘기지 않는다)
"""
from __future__ import annotations

import functools
from typing import Any

import yaml

from . import paths

_PATH = paths.repo_root() / "constraints.yaml"


@functools.cache
def _all() -> dict:
    if not _PATH.exists():
        raise FileNotFoundError(f"제약 정본이 없다: {_PATH}")
    with _PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@functools.cache
def _deployment() -> dict:
    p = paths.config_root() / "deployment.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def get(dotted: str) -> Any:
    """`"appstore.chart_caps.top-free"` 처럼 점으로 찾는다. 없으면 raise."""
    node: Any = _deployment()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            node = None
            break
        node = node[part]
    if node is not None:
        return node

    node = _all()
    seen: list[str] = []
    for part in dotted.split("."):
        seen.append(part)
        if not isinstance(node, dict) or part not in node:
            raise KeyError(
                f"제약에 '{dotted}' 가 없다 (여기까지: {'.'.join(seen[:-1]) or '(root)'}). "
                f"constraints.yaml 에 추가하거나 오타를 고쳐라 — 기본값으로 넘어가지 않는다."
            )
        node = node[part]
    return node
