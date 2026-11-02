"""[0] 외부 제약을 코드로 읽는 단일 진입점.

## 왜 코드에 숫자를 안 박나

제약이 코드에 흩어지면 두 진실원이 생긴다. 옛 시스템에서 실제로:

    월 천장이 resources.yaml $29 vs config.yaml $45 로 갈렸고,
    조회 한 번 실패하면 유효 천장이 조용히 $45 가 됐다.

일반 업무 기준은 `constraints.yaml`에서 읽고 로컬 deployment.yaml 값을 우선한다.
행동·보고·승격 스위치와 메일 허용 목록은 로컬에 명시한 값만 쓰며, 누락 시 닫는다.

## 없는 키는 조용히 넘기지 않는다

일반 제약에 `.get(key, 기본값)` 을 쓰지 않는다. 없으면 **즉시 raise** 한다.
닫힌 기본값이 있는 rollout 스위치만 예외다.
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

# These switches must be explicitly enabled in the local deployment, never inherited.
ROLLOUT_DEFAULTS = {"outbound": "off", "report_slack": "off", "digest_slack": "off",
                    "open_kinds": [], "send_branches": []}


def rollout_default(dotted: str):
    if dotted == "linkedin.promote":
        return False
    if dotted == "mail_observe.open_categories":
        return {}
    return ROLLOUT_DEFAULTS.get(dotted.rsplit(".", 1)[-1])


def validate_rollout(dotted: str, value):
    default = rollout_default(dotted)
    if default is None:
        return value
    if isinstance(default, str):
        allowed = ("off", "slack", "on") if dotted.endswith(".outbound") else ("off", "on")
        valid = isinstance(value, str) and value in allowed
    elif isinstance(default, bool):
        valid = isinstance(value, bool)
    elif isinstance(default, dict):
        valid = isinstance(value, dict) and all(
            isinstance(k, str) and isinstance(v, list) and all(isinstance(c, str) and c for c in v)
            for k, v in value.items())
    else:
        valid = isinstance(value, list) and all(isinstance(v, str) and v for v in value)
    if not valid:
        raise ValueError(f"deployment.yaml: invalid rollout value for {dotted}: {value!r}")
    return value


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
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict) or any(not isinstance(v, dict) for v in data.values()):
        raise ValueError("deployment.yaml must map workflow names to settings")
    for task, settings in data.items():
        for key, value in settings.items():
            validate_rollout(f"{task}.{key}", value)
    return data


def get(dotted: str) -> Any:
    """로컬 설정 우선. 누락 rollout은 닫고, 그 밖의 키는 일반 제약에서 찾는다."""
    node: Any = _deployment()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            node = None
            break
        node = node[part]
    if node is not None:
        return node

    closed = rollout_default(dotted)
    if closed is not None:
        return closed.copy() if isinstance(closed, (dict, list)) else closed

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
