"""통로의 유일한 반환 타입 — 값과 '측정 여부'를 분리한다.

## 왜 필요한가

[0] 조사에서 **"0이 두 뜻"인 자리가 최소 6곳** 나왔다. 전부 같은 부류다:

    카지노 유료 차트 빈 배열 21본   애플의 정답이다 (결손이 아니다)
    DNS 죽어서 0건                 못 쟀다 (0건이 아니다)
    Slack 반응 조회 실패 vs 반응 0개
    Gmail 목록이 잘림 vs 진짜로 없음
    claude 빈 출력 (인증 만료)      rc 는 0이다
    DLQ 읽기 실패 vs 정상 0건

이걸 통로마다 따로 고치면 인스턴스를 고치는 것이다. 타입 하나로 **부류를 막는다.**

## 쓰는 법

    f = itunes.chart(genre=6014, chart="top-free")
    apps = f.require()              # 미상이면 raise — 기본 접근자
    apps = f.or_default([])         # 미상을 명시적으로 삼킬 때만

`.value` 를 두지 않는 이유: 미상을 **모른 척 지나가는 코드가 짜기 어려워야** 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class Unknown(RuntimeError):
    """측정하지 못한 값을 require() 했다."""


@dataclass(frozen=True)
class Fetched:
    """통로가 답하는 방식.

    is_measured=True   실제로 재서 얻은 값. **[] 와 0 도 측정된 결과다**
    is_measured=False  미상. 값이 없는 게 아니라 '못 쟀다'
    partial=True       받긴 받았는데 잘렸다 — 측정이 무효일 수 있다
    """

    _value: Any
    is_measured: bool
    reason: str = ""
    partial: bool = False
    meta: dict = field(default_factory=dict)

    # ---------- 생성 ----------
    @classmethod
    def measured(cls, value: Any, *, partial: bool = False, **meta: Any) -> "Fetched":
        return cls(_value=value, is_measured=True, partial=partial, meta=meta)

    @classmethod
    def unknown(cls, reason: str, **meta: Any) -> "Fetched":
        if not reason:
            raise ValueError("unknown 은 이유가 있어야 한다 — 조용한 미상을 만들지 않는다")
        return cls(_value=None, is_measured=False, reason=reason, meta=meta)

    # ---------- 접근 ----------
    def require(self) -> Any:
        """측정된 값을 꺼낸다. 미상이면 raise."""
        if not self.is_measured:
            raise Unknown(self.reason)
        return self._value

    def or_default(self, default: Any) -> Any:
        """미상이면 default. **명시적으로 삼킬 때만 쓴다.**"""
        return self._value if self.is_measured else default

    # ---------- 표시 ----------
    def __str__(self) -> str:
        if not self.is_measured:
            return f"미상({self.reason})"
        n = len(self._value) if hasattr(self._value, "__len__") else "-"
        return f"측정됨({n}{' · 잘림' if self.partial else ''})"
