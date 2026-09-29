"""링크드인 연락 문안 — outreach.md 의 코드 블록을 원본으로 `{이름}`·`{발신자}` 만 채운다. 모델 없음.

[사용자 결정 2026-09-29 회의] claude 가 사람마다 쓰던 초안(짧게·길게)을 Reviewer C 심사역 실무 문안으로 바꿨다.
🔴 문장은 코드에 두지 않는다 — 승인된 문안이 파일 하나에만 있어야 고칠 때 한 곳만 고친다.
🔴 우리가 보내지 않는다. 심사역 개인 계정으로 나가는 글이라 스레드에 올리고 사람이 복사해 보낸다.
"""
from __future__ import annotations

import re
from pathlib import Path

_MD = Path(__file__).with_name("outreach.md")


def template() -> str:
    """outreach.md 의 첫 코드 블록. 칸이 둘 다 없으면 시끄럽게 실패한다 — 문안이 망가진 채 나가지 않게."""
    m = re.search(r"```\n(.*?)\n```", _MD.read_text(encoding="utf-8"), re.S)
    if not m or "{이름}" not in m.group(1) or "{발신자}" not in m.group(1):
        raise ValueError(f"{_MD.name}: 문안 코드 블록에 {{이름}}·{{발신자}} 칸이 없다")
    return m.group(1)


def write(given: str, sender: str) -> str:
    return template().replace("{이름}", given).replace("{발신자}", sender)
