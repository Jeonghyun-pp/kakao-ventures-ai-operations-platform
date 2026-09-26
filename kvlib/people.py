"""심사역 명부 `people.yaml`(리포 루트) — 스킬 공용. 칸 뜻은 그 파일 머리에."""
from __future__ import annotations

import yaml

from . import paths


def load() -> list[dict]:
    return yaml.safe_load((paths.repo_root() / "people.yaml").read_text(encoding="utf-8")) or []


def requesters(extra: list[str] | None = None) -> set[str]:
    """요청을 받아 주는 슬랙 멤버 — 명부의 slack_id + 스킬별 extra. 비어 있으면 아무 요청도 안 받는다(기본 거부). 투심·펀드제안서 레인 공용."""
    return {p.get("slack_id") for p in load() if p.get("slack_id")} | set(extra or [])
