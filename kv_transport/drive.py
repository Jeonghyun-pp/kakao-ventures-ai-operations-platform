"""구글드라이브 — 덱(사업계획서)을 **읽기만** 한다. 쓰기 함수는 없다.

    text(url) -> Fetched[str]   드라이브 링크 → 파일 텍스트. PDF 는 pdftotext(poppler), 구글 슬라이드·문서는 PDF 로 export 뒤 같은 길.
                                텍스트가 거의 없으면(이미지 덱) measured("") + meta(why="텍스트 없음") — "못 읽었다"를 사실로 넘긴다.

인증은 시트 통로의 서비스계정 JWT 를 스코프만 바꿔 쓴다. 🔴 파일은 그 서비스계정에 **공유**돼 있어야 한다 —
폼 업로드 폴더를 한 번 공유하면 그 안의 파일은 전부 읽힌다. 403 은 키가 아니라 공유 문제다.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path

import requests

from kvlib import Fetched, constraints

from . import sheets

_API = "https://www.googleapis.com/drive/v3"
_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
_ID_RE = re.compile(r"(?:[?&]id=|/d/)([A-Za-z0-9_-]{10,})")
_GOOGLE = "application/vnd.google-apps."


class DriveError(RuntimeError):
    pass


def file_id(url: str) -> str:
    m = _ID_RE.search(url or "")
    return m.group(1) if m else ""


def _get(path: str, *, params: dict | None = None) -> requests.Response:
    net_backoff = constraints.get("http.retry_backoff_sec")
    tries = 0
    while True:
        try:
            r = requests.get(f"{_API}{path}", headers={"Authorization": f"Bearer {sheets._access_token(_SCOPE)}"},
                             params=params, timeout=constraints.get("drive.timeout_sec"))
        except sheets._NET_ERRORS as e:
            if tries < len(net_backoff):
                time.sleep(net_backoff[tries]); tries += 1
                continue
            raise DriveError(f"망 계열로 끝까지 실패: {type(e).__name__} {path}") from e
        if r.status_code == 401 and tries == 0:
            tries += 1; sheets._token.pop(_SCOPE, None)
            continue
        if r.status_code == 403:
            raise DriveError(f"403 {path} — 키가 아니라 **공유**를 의심하라. 폼 업로드 폴더를 서비스계정에 공유했는가")
        if r.status_code == 404:
            raise DriveError(f"404 {path} — 파일이 없거나 **공유가 안 됐다**(드라이브는 권한 없는 파일도 404 로 답한다)")
        if r.status_code >= 400:
            raise DriveError(f"{r.status_code} {path}: {r.text[:200]}")
        return r


def _pdf_text(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(data); path = f.name
    try:
        r = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True,
                           timeout=constraints.get("drive.pdftotext_timeout_sec"))
        return r.stdout                                          # rc 가 아니라 출력으로 판정한다(A3)
    finally:
        Path(path).unlink(missing_ok=True)


def text(url: str) -> Fetched:
    """덱 텍스트. 못 읽는 건 이유와 함께 — 판정 입력에 '덱: <이유>' 로 들어간다."""
    fid = file_id(url)
    if not fid:
        return Fetched.measured("", why="드라이브 링크가 아니다")
    try:
        meta = _get(f"/files/{fid}", params={"fields": "name,mimeType,size"}).json()
        mime, size = meta.get("mimeType", ""), int(meta.get("size") or 0)
        if size > constraints.get("drive.max_bytes"):
            return Fetched.measured("", why=f"파일이 너무 크다 {size // 1_000_000}MB", name=meta.get("name"))
        if mime.startswith(_GOOGLE):                            # 슬라이드·문서 → PDF 로 받는다
            data = _get(f"/files/{fid}/export", params={"mimeType": "application/pdf"}).content
        elif mime == "application/pdf":
            data = _get(f"/files/{fid}", params={"alt": "media"}).content
        else:
            return Fetched.measured("", why=f"지원하지 않는 형식 {mime}", name=meta.get("name"))
    except sheets.AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except (sheets.SheetsError, DriveError) as e:
        return Fetched.unknown(f"drive:{e}")
    out = _pdf_text(data).strip()
    if len(out) < constraints.get("drive.min_text_chars"):     # 이미지만 있는 덱 — 글자가 없다
        return Fetched.measured("", why=f"텍스트 없음(이미지 덱, {len(out)}자)", name=meta.get("name"))
    return Fetched.measured(out, name=meta.get("name"), chars=len(out))
