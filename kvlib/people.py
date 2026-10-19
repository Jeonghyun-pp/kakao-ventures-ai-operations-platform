"""심사역 명부 `people.yaml`(리포 루트) — 스킬 공용. 칸 뜻은 그 파일 머리에."""
from __future__ import annotations

import re

import yaml

from . import paths


def load() -> list[dict]:
    return yaml.safe_load((paths.repo_root() / "people.yaml").read_text(encoding="utf-8")) or []


def requesters(extra: list[str] | None = None) -> set[str]:
    """요청을 받아 주는 슬랙 멤버 — 명부의 slack_id + 스킬별 extra. 비어 있으면 아무 요청도 안 받는다(기본 거부). 투심·펀드제안서 레인 공용."""
    return {p.get("slack_id") for p in load() if p.get("slack_id")} | set(extra or [])


def named(text: str) -> list[dict]:
    """본문에서 **지목된** 심사역(명부 행). 메일 슬랙 핑과 심사역 포워드가 같은 규칙을 쓴다.

    🔴 호칭이 붙어야 지명이다('ch«reviewer-three»l' 오탐 · 2026-08-16 오탐 4건. 부분일치로 되돌리지 마라). 메일 주소는 호칭이 필요 없다.
    """
    out = []
    for p in load():
        names = "|".join(re.escape(n) for n in p.get("names") or [])
        if (p.get("email") and p["email"].lower() in (text or "").lower()) or (
                names and re.search(rf"(?<![가-힣A-Za-z0-9])(?:{names})\s?(?:님|씨|께|심사역|대표|부대표|이사|파트너|수석)", text or "")):
            out.append(p)
    return out
