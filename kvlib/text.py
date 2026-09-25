"""글 다루기 — 모델에게 넘기기 전에."""
from __future__ import annotations


def clip(s: str, cap: int) -> tuple[str, int]:
    """상한을 넘으면 **머리와 꼬리**를 남긴다. (글, 잘라낸 글자 수).

    요청은 대개 끝에 있다("…검토 부탁드립니다"). 앞에서만 자르면 그걸 버린다.
    """
    if len(s) <= cap:
        return s, 0
    head = cap * 2 // 3
    tail = cap - head
    cut = len(s) - cap
    return f"{s[:head]}\n[…중략 {cut}자…]\n{s[-tail:]}", cut
