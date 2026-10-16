"""링크드인 연락 문안 — outreach.md 의 코드 블록을 원본으로 칸만 채운다. 맞춤 문단은 pitch.py 가 만들어 넘긴다.

[사용자 결정 2026-09-29 회의] claude 가 사람마다 쓰던 초안(짧게·길게)을 Reviewer C 심사역 실무 문안으로 바꿨다.
[사용자 결정 2026-10-16] Reviewer A 심사역 다듬은 문안으로 바꿨다 — 고정 문장 + 프로필 맞춤 문단(틀 고정, 구절만 바꿔 끼움).
🔴 문장은 코드에 두지 않는다 — 승인된 문안이 파일 하나에만 있어야 고칠 때 한 곳만 고친다.
🔴 우리가 보내지 않는다. 심사역 개인 계정으로 나가는 글이라 스레드에 올리고 사람이 복사해 보낸다.
"""
from __future__ import annotations

import re
from pathlib import Path

_MD = Path(__file__).with_name("outreach.md")
SLOTS = ("{이름}", "{발신자}", "{맞춤}", "{궁금}")


def template() -> str:
    """outreach.md 의 첫 코드 블록. 칸이 하나라도 없으면 시끄럽게 실패한다 — 문안이 망가진 채 나가지 않게."""
    m = re.search(r"```\n(.*?)\n```", _MD.read_text(encoding="utf-8"), re.S)
    missing = [s for s in SLOTS if not m or s not in m.group(1)]
    if missing:
        raise ValueError(f"{_MD.name}: 문안 코드 블록에 {'·'.join(missing)} 칸이 없다")
    return m.group(1)


def write(given: str, sender: str, pitch: str, close: str = "") -> str:
    """`pitch` 는 맞춤 문단(첫 문장 이상은 늘 있다), `close` 는 끝맺음 — 비면 문장째(앞 공백까지) 뺀다."""
    t = template().replace(" {궁금}", f" {close}" if close else "").replace("{궁금}", close)
    return t.replace("{이름}", given).replace("{발신자}", sender).replace("{맞춤}", pitch)
