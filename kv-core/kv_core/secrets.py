"""자격증명 단일 저장소 — `.env`와 토큰 파일의 유일한 출처.

## 왜 필요한가 (2026-07-27 실측)

통합 전 상태:

    .env 6개          NOTION_TOKEN 3벌 · APIFY_TOKEN 2벌 복제
    token.json 4벌    그런데 **고유 refresh token은 1개** — 물리적 복사본일 뿐이었다
    스코프            gmail.modify 하나뿐 (주의: 이 스코프는 send 를 허용한다 — 무발송 보장은 스코프가 아니라 "코드에 send 호출 없음"+AST 검사다. 리뷰 H6 정정)

즉 필요한 작업은 "여러 자격증명을 합치기"가 아니라 **"복사본 N개를 한 경로 참조로 바꾸기"**였다.
재인증도 한 곳에서만 하면 된다.

## 왜 Keychain을 쓰지 않는가

머신 종속이라 **이전 자체가 불가능**해진다. 상시 머신이 생겼을 때 디렉토리 하나만 옮기면
되도록 파일로 둔다. 기본구조 1단계의 비가역 결정 3가지 중 하나다.

## 왜 리포 밖인가

`git clean -xdf`는 `.gitignore`된 파일도 지운다. `.env`가 리포 안에 있으면 명령 하나로
사라진다. 그리고 시크릿은 데이터와 **백업 정책이 다르다** — 시크릿은 회전(rotate)이
가능하지만 데이터는 불가능하다. 같은 곳에 두면 같은 정책을 강요받는다.

    ~/kv-config/
    ├── .env                  600
    └── gmail/
        ├── token.json        600   재인증은 여기 하나만
        └── credentials.json  600

## 쓰는 법

    from kv_core import secrets
    tok  = secrets.get("NOTION_TOKEN")              # 없으면 RuntimeError
    hook = secrets.get("SLACK_WEBHOOK_URL", required=False)   # 없으면 None
    p    = secrets.credential("gmail/token.json")   # Path
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

from . import paths

_loaded = False


def env_file() -> Path:
    return paths.config_root() / ".env"


def load(force: bool = False) -> None:
    """`$KV_CONFIG/.env` 를 한 번만 읽어 환경변수로 올린다.

    `override=True` 인 이유 — 스킬이 자기 `.env`를 먼저 읽었더라도 **공통 저장소가 정본**이다.
    두 값이 다르면 조용히 갈라지는 것보다 한쪽으로 통일되는 편이 낫다.
    """
    global _loaded
    if _loaded and not force:
        return
    f = env_file()
    if f.exists():
        _warn_if_loose(f)
        from dotenv import load_dotenv
        load_dotenv(f, override=True)
    _loaded = True


def _warn_if_loose(f: Path) -> None:
    """권한이 600보다 느슨하면 경고한다. 조용히 넘어가면 아무도 모른다."""
    mode = f.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        print(f"  ⚠️  {f} 권한이 느슨하다({oct(mode & 0o777)}) — `chmod 600 {f}`")


def get(name: str, required: bool = True, default: str | None = None) -> str | None:
    """환경변수 조회. `required` 면 없을 때 **즉시 죽는다**.

    조용히 None을 반환하면 한참 뒤 엉뚱한 곳에서 401이 나고, 그때는 원인이 안 보인다.
    잘못된 입력은 즉시 거부한다(실패 모델 표).
    """
    load()
    v = os.environ.get(name) or default
    if v:
        return v
    if required:
        raise RuntimeError(
            f"자격증명 없음: {name}\n"
            f"→ {env_file()} 에 {name}=... 을 추가하라 (권한 600)"
        )
    return None


def credential_path(relpath: str) -> Path:
    """`$KV_CONFIG/<relpath>` — **존재 여부를 묻지 않는다.**

    아직 없는 파일을 쓰려는 경우(최초 인증으로 token.json을 만들 때)에 쓴다.
    읽을 때는 `credential()` 을 써서 없으면 즉시 죽게 하라.
    """
    p = paths.config_root() / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def credential(relpath: str, required: bool = True) -> Path | None:
    """`$KV_CONFIG/<relpath>` — 토큰·인증 파일 경로.

    스킬마다 복사본을 두지 않는다. 재인증이 한 곳에서만 일어나게 하기 위해서다.
    """
    p = paths.config_root() / relpath
    if p.exists():
        return p
    if required:
        raise RuntimeError(
            f"자격증명 파일 없음: {p}\n"
            f"→ 재인증하거나 기존 파일을 이 경로로 옮겨라"
        )
    return None
