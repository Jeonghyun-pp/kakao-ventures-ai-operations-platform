"""VTT 자동자막 정제 — 롤링 중복 제거로 원본의 1/3~1/4 크기 목표.

YouTube ASR 자막의 구조 (실측: Lightcone 421KB VTT):
- 인라인 워드 타임스탬프 <00:00:00.360><c> word</c> 가 텍스트를 부풀림
- 같은 문장이 '진행 중' 큐와 '확정' 큐로 2~3번 반복 (롤링)
전략: 인라인 태그 제거 → 큐별 마지막 줄만 취함 → 직전 줄과 같으면 스킵.
"""
from __future__ import annotations

import re
import sys

_INLINE = re.compile(r"<[^>]+>")
_TS_LINE = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3} --> ")


def clean(vtt_text: str) -> str:
    lines_out: list[str] = []
    prev = ""
    for block in vtt_text.split("\n\n"):
        rows = [r for r in block.splitlines() if r.strip()]
        if not rows or not any(_TS_LINE.match(r) for r in rows):
            continue                                    # 헤더/메타 블록
        # 타임스탬프 줄 이후의 텍스트 줄들에서 인라인 태그 제거
        texts = [_INLINE.sub("", r).strip() for r in rows if not _TS_LINE.match(r)]
        texts = [t for t in texts if t]
        if not texts:
            continue
        cand = texts[-1]                                # 롤링 큐의 마지막 줄이 확정 문장
        if cand and cand != prev:
            lines_out.append(cand)
            prev = cand
    return " ".join(lines_out)


if __name__ == "__main__":
    raw = open(sys.argv[1], encoding="utf-8").read()
    out = clean(raw)
    print(out)
    print(f"\n--- {len(raw)} bytes -> {len(out)} bytes ({len(out)*100//max(1,len(raw))}%)",
          file=sys.stderr)
