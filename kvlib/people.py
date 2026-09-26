"""심사역 명부 `people.yaml`(리포 루트) — 스킬 공용. 칸 뜻은 그 파일 머리에."""
from __future__ import annotations

import yaml

from . import paths


def load() -> list[dict]:
    return yaml.safe_load((paths.repo_root() / "people.yaml").read_text(encoding="utf-8")) or []
