"""Organization and external-system identifiers from ``$KV_CONFIG/connections.yaml``.

The repository ships a synthetic example so imports and offline tests work in a
fresh clone. Real commands should be run only after ``./kv setup`` and
``./kv doctor`` have created and validated the local file outside Git.
"""
from __future__ import annotations

import functools
from typing import Any

import yaml

from . import paths

EXAMPLE = paths.repo_root() / "connections.example.yaml"


def path():
    return paths.config_root() / "connections.yaml"


@functools.cache
def _all() -> dict:
    source = path() if path().exists() else EXAMPLE
    if not source.exists():
        raise FileNotFoundError(f"connection config is missing: {source}")
    return yaml.safe_load(source.read_text(encoding="utf-8")) or {}


def get(dotted: str) -> Any:
    node: Any = _all()
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"missing connection setting: {dotted} ({path()})")
        node = node[part]
    return node


def render(text: str) -> str:
    """Replace organization tokens in prompts and customer-facing templates."""
    name = str(get("organization.name"))
    return (text.replace("{{company_name}}", name)
                .replace("카카오벤처스", name)
                .replace("{{shared_inbox}}", str(get("organization.shared_inbox"))))


def using_example() -> bool:
    return not path().exists()


def clear_cache() -> None:
    _all.cache_clear()
