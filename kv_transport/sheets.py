"""구글 시트 통로 — 시트에 닿는 유일한 경로. **읽기 전용이다.**

## 왜 이 통로가 있나

비플랜 접수는 구글폼 → 시트로 쌓인다. 노션에 착지시키려면 시트를 읽어야 하는데,
현행 `~/kv` 에는 시트 통로가 없었다 — 옛 시스템의 sync 가 gspread 로 읽던 자리다.

## 왜 구글 SDK(gspread·google-auth)를 안 쓰나

지메일 통로와 같은 이유다. `check_intent` 의 A1 은 `requests` 를 보고 통로 밖의 망 접촉을
잡는데, SDK 는 그 목록에 없어 **통로 밖에서 시트를 읽어도 조용히 통과**한다.
SDK 를 피하려고 검사를 만든 게 아니라, SDK 를 쓰면 검사가 무력해진다.

서비스계정 인증은 JWT 하나를 RS256 으로 서명해 토큰 서버에 내는 것이 전부라
`cryptography` 로 서명만 하면 SDK 없이 된다.

## 이 파일이 지키는 [0] 제약

    JWT 는 1시간까지          더 길게 주면 invalid_grant. 만료 60초 전에 갱신한다
    200 ≠ 토큰 받음          응답에 access_token 키가 없으면 실패다
    행 길이가 제각각이다       values.get 은 뒤쪽 빈 칸을 **생략한 채** 준다.
                            인덱스로 읽으면 IndexError — 스킬이 패딩한다
    타임스탬프는 표시 문자열    FORMATTED_VALUE 로 받는다. 옛 시스템(gspread)이 그렇게 받았고,
                            노션에 심어둔 접수키가 그 문자열에서 만들어졌다. 바꾸면 키가 어긋난다
    403 은 공유 설정          서비스계정 이메일을 시트에 공유했는지 먼저 의심한다
"""
from __future__ import annotations

import base64
import json
import re
import time
from urllib.parse import quote

import requests

from kvlib import Fetched, constraints, secrets

_API = "https://sheets.googleapis.com/v4/spreadsheets"
_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"

_NET_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)


class SheetsError(RuntimeError):
    pass


class AuthDead(SheetsError):
    """서비스계정 키가 거부됐다 — 키 파일을 다시 받아야 한다. 자동으로 못 고친다."""


# ---------------------------------------------------------------- 인증

_token: dict = {}          # {scope: {"value": str, "expires_at": float}} — drive 통로가 다른 스코프로 같은 키를 쓴다


def _service_account() -> dict:
    """$KV_CONFIG 의 서비스계정 키. 경로의 정본은 .env 의 GOOGLE_CREDENTIALS_FILE 하나다."""
    path = secrets.get("GOOGLE_CREDENTIALS_FILE")
    try:
        sa = json.loads(open(path, encoding="utf-8").read())
    except FileNotFoundError as e:
        raise AuthDead(f"서비스계정 키 파일이 없다: {path}") from e
    except json.JSONDecodeError as e:
        raise AuthDead(f"서비스계정 키 파일이 깨졌다: {path} — {e}") from e
    if sa.get("type") != "service_account":
        raise AuthDead(f"서비스계정 키가 아니다(type={sa.get('type')!r}): {path}")
    for k in ("client_email", "private_key", "token_uri"):
        if not sa.get(k):
            raise AuthDead(f"서비스계정 키에 {k} 가 없다: {path}")
    return sa


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _assertion(sa: dict, scope: str = _SCOPE) -> str:
    """RS256 JWT. 구글이 받는 건 이 형식 하나다."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64url(json.dumps({
        "iss": sa["client_email"], "scope": scope, "aud": sa["token_uri"],
        "iat": now, "exp": now + constraints.get("sheets.jwt_lifetime_sec"),
    }).encode())
    signing = f"{header}.{claims}".encode("ascii")
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    sig = key.sign(signing, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64url(sig)}"


def _access_token(scope: str = _SCOPE) -> str:
    """서비스계정 JWT → access_token. 만료 60초 전에 미리 바꾼다.

    🔴 상태코드로 성공을 판정하지 않는다. 필요한 건 access_token 문자열이다.
    """
    lead = constraints.get("sheets.token_refresh_lead_sec")
    cur = _token.get(scope)
    if cur and cur["expires_at"] - lead > time.time():
        return cur["value"]

    sa = _service_account()
    try:
        r = requests.post(sa["token_uri"], data={"grant_type": _GRANT, "assertion": _assertion(sa, scope)},
                          timeout=constraints.get("http.timeout_sec"))
    except _NET_ERRORS as e:
        raise SheetsError(f"토큰 서버에 못 닿았다: {type(e).__name__}") from e

    body = {}
    try:
        body = r.json()
    except ValueError:
        pass
    tok = body.get("access_token")
    if not tok:
        err = body.get("error", "")
        if err == "invalid_grant" or r.status_code in (400, 401):
            raise AuthDead(f"서비스계정 키가 거부됐다 ({err or r.status_code}) — "
                           f"키가 폐기됐거나 시계가 어긋났다. 자동으로 못 고친다.")
        raise SheetsError(f"토큰 발급 실패 {r.status_code}: {str(body)[:200]}")
    _token[scope] = {"value": tok, "expires_at": time.time() + float(body.get("expires_in") or 0)}
    return tok


def auth_check() -> Fetched:
    """키가 살아 있나. 시트를 읽기 **전에** 본다. 나중에 401 로 알면 "0행"과 구별이 안 된다."""
    try:
        _access_token()
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except SheetsError as e:
        return Fetched.unknown(f"sheets:{e}")
    return Fetched.measured(True)


# ---------------------------------------------------------------- HTTP

def _get(path: str, *, params: dict | None = None) -> dict:
    """시트 요청 한 번. 재시도 계층은 노션·지메일 통로와 같다."""
    net_backoff = constraints.get("http.retry_backoff_sec")
    url = f"{_API}{path}"
    net_try = 0
    other_try = 0
    while True:
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {_access_token()}"},
                             params=params, timeout=constraints.get("http.timeout_sec"))
        except _NET_ERRORS as e:
            if net_try < len(net_backoff):
                w = net_backoff[net_try]
                net_try += 1
                print(f"  · 시트 망 오류 {type(e).__name__} — {w}s 후 재시도 ({net_try}/{len(net_backoff)})")
                time.sleep(w)
                continue
            raise SheetsError(f"망 계열로 끝까지 실패: {type(e).__name__} {path}") from e

        if r.status_code == 401:
            if other_try == 0:
                other_try += 1
                _token.clear()
                continue
            raise AuthDead(f"401 이 반복된다: {path}")
        if r.status_code == 429 or 500 <= r.status_code < 600:
            other_try += 1
            if other_try > 4:
                raise SheetsError(f"{r.status_code} 가 계속된다: {path}")
            wait = float(r.headers.get("Retry-After") or 2 ** other_try)
            print(f"  · {r.status_code} — {wait}s 후 재시도")
            time.sleep(wait)
            continue
        if r.status_code == 403:
            raise SheetsError(
                f"403 {path} — 키가 아니라 **공유 설정**을 먼저 의심하라. "
                f"서비스계정 이메일을 그 시트에 공유했는지 확인."
            )
        if r.status_code == 404:
            raise SheetsError(f"404 {path} — 시트 id 나 워크시트 이름이 틀렸다")
        if r.status_code >= 400:
            raise SheetsError(f"{r.status_code} {path}: {r.text[:300]}")
        return r.json() if r.content else {}


# ---------------------------------------------------------------- 읽기

_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")


def spreadsheet_id(url_or_id: str) -> str:
    """URL 이면 id 를 뽑고, 이미 id 면 그대로."""
    m = _ID_RE.search(url_or_id or "")
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{20,}", url_or_id or ""):
        return url_or_id
    raise SheetsError(f"시트 id 를 못 읽었다: {url_or_id!r}")


def values(url_or_id: str, worksheet: str) -> Fetched:
    """워크시트 전체 → list[list[str]]. **첫 행이 헤더**다.

    🔴 행 길이가 제각각이다 — 뒤쪽 빈 칸은 생략돼 온다. 스킬이 헤더 길이로 패딩한다.
    🔴 값은 **표시 문자열**이다(FORMATTED_VALUE). 타임스탬프가 시트 로케일 그대로 온다.
    """
    try:
        sid = spreadsheet_id(url_or_id)
        rng = quote(f"'{worksheet}'", safe="")
        res = _get(f"/{sid}/values/{rng}",
                   params={"valueRenderOption": constraints.get("sheets.value_render"),
                           "majorDimension": "ROWS"})
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except SheetsError as e:
        return Fetched.unknown(f"sheets:{e}")
    rows = res.get("values")
    if rows is None:
        # 키가 아예 없으면 워크시트가 비었거나 응답이 이상하다 — 0행과 구별해 미상으로 둔다
        return Fetched.unknown(f"응답에 values 가 없다: {str(res)[:200]}")
    return Fetched.measured([[str(c) for c in r] for r in rows], rows=len(rows), range=res.get("range"))
