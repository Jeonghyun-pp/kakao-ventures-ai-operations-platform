"""자격증명의 유일한 출처 — `$KV_CONFIG/.env`.

## 왜 한 곳인가

옛 시스템에서 `google/token.json` 이 스킬마다 복사돼 4벌이 있었는데,
`refresh_token`·`client_id` 가 전부 같았다 — **물리적 복사본일 뿐**이었다.
한 곳만 갱신되고 나머지 3벌이 만료된 채 조용히 실패한다.

## 없으면 조용히 넘기지 않는다

`os.environ.get(k, "")` 를 쓰지 않는다. 없으면 **즉시 raise** 한다.
빈 문자열을 흘리면 그게 인증 실패가 아니라 "권한 없음"으로 나타나서 엉뚱한 데를 뒤진다.
"""
from __future__ import annotations

import functools
import os

from . import paths

# 시크릿이 아닌 것들 — 이건 os.environ 에서 직접 읽어도 된다(검사 예외 목록)
NON_SECRET_ENV = ("KV_DATA", "KV_CONFIG", "KV_NO_SEND")


@functools.cache
def _env() -> dict[str, str]:
    p = paths.config_root() / ".env"
    if not p.exists():
        raise FileNotFoundError(f"자격증명 정본이 없다: {p}")
    mode = p.stat().st_mode & 0o777
    if mode & 0o077:
        print(f"  ⚠️  {p} 권한이 느슨하다({oct(mode)}) — chmod 600 을 권한다")
    out: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def get(key: str, *, required: bool = True) -> str:
    """`$KV_CONFIG/.env` 우선, 없으면 프로세스 환경. required 면 없을 때 raise."""
    v = _env().get(key) or os.environ.get(key) or ""
    if not v and required:
        raise RuntimeError(
            f"자격증명 '{key}' 가 없다. {paths.config_root()}/.env 를 확인하라. "
            f"— 빈 값으로 넘어가지 않는다(빈 값은 권한 오류로 나타나 엉뚱한 데를 뒤지게 만든다)."
        )
    return v


def no_send() -> bool:
    """발송 억제 스위치.

    🔴 **명시 값만** 인정한다. truthy 판정이면 `KV_NO_SEND=0`·`false`·`no` 가
    전부 "켜짐"이 된다 — 직관의 정반대다(옛 시스템에서 실제로 그랬다).
    """
    from . import constraints
    raw = (os.environ.get("KV_NO_SEND") or "").strip().lower()
    return raw in constraints.get("slack.suppress_values")
