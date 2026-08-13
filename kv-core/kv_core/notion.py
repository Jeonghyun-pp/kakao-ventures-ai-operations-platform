"""노션 API를 **안전하게 부르는 법** — 인증 · 재시도 · UTF-16 절단 · 스키마 조회.

## 경계 — 여기까지가 공통이다

    여기 있는 것    인증 · rate limit · 재시도/백오프 · 길이 제한 · 스키마 조회 · 원시 CRUD
                   = 어느 스킬이 어떤 DB를 쓰든 똑같이 필요한 것

    스킬에 남는 것  DB 라우팅 · 착지 키 · 도메인 프로퍼티 매핑 · 중복 판정
                   = DB마다 스키마가 다르므로 공통화할 수 없는 것

DB를 다루는 법은 DB마다 다르다. 그래서 **그 앞 단계까지만** 공통으로 올린다.

## 이 모듈의 함수들은 전부 실사고에서 나왔다

주석의 날짜와 숫자를 지우지 마라. 그게 이 코드가 지금 모양인 이유이고,
같은 사고를 다시 만났을 때 알아보게 해주는 유일한 단서다.

## 쓰는 법

    from kv_core import notion
    tok    = notion.token()
    schema = notion.fetch_schema(db_id, tok)
    r      = notion.write("POST", f"{notion.API}/pages", tok, payload)
    text   = notion.clip16(bio)                  # rich_text/title에 넣기 전 **반드시**
"""
from __future__ import annotations

import time

import requests

from . import secrets

API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"

# 노션 공식 한도 = **평균 3 req/s**. 2026-07-11엔 415행을 무지연 순차 POST 했고, retry/backoff
# 코드가 리포 전체에 0줄이었다. 429가 나면 그 행은 print만 되고 사라지는데, seen-store는 그와
# 무관하게 '봤음'으로 확정돼 **다시는 안 잡힌다**(조용한 영구 유실).
RATE_SLEEP = 0.34            # ≈3 req/s
MAX_RETRY = 4

# 🔴 [2026-07-21] 네트워크 오류(DNS·연결 불가) 백오프는 429/5xx와 **분리한다**.
# 07-18 크론: 수집(10,112건)은 성공했는데 착지 직전 노션 쓰기가 `gaierror`로 죽어 **229건 유실**.
# 원인은 절전 해제 직후 DNS가 아직 안 올라온 상태인데, 당시 백오프가 1/2/4s = **7초**뿐이라
# 망이 붙기 전에 재시도를 다 소진했다. 수집 경로는 같은 날 15/30/60s = 105초로 강화됐는데
# **쓰기 경로만 안 고쳐져** 파이프라인 뒷단에서 똑같이 죽었다.
# → 두 경로의 정책을 맞춘다. 429는 Retry-After가 오므로 짧게(1s 기반) 유지.
NET_BACKOFF = 15.0           # 연결/DNS 실패 전용: 15 → 30 → 60s (총 105초)

MAX_CHILDREN = 100           # 노션: children 배열 1회 최대 100개
MAX_TEXT = 1900              # 노션: rich_text 항목당 content 2,000자 (여유 100)

_RETRIABLE = (429, 500, 502, 503, 504)


def token() -> str:
    """노션 integration 토큰.

    integration을 대상 DB에 **연결(공유)** 해야 200이 뜬다 — integration 토큰은
    공유된 리소스만 접근한다. 403이 나면 토큰이 아니라 공유 설정을 먼저 의심하라.
    """
    return secrets.get("NOTION_TOKEN")


def headers(tok: str) -> dict:
    return {
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_VERSION,
    }


# 🔴 [2026-08-13 W12 · 리뷰 B-F6] `ChunkedEncodingError` 는 **`ConnectionError` 의 서브클래스가
#    아니다** — 응답 헤더는 왔는데 **본문 도중 연결이 끊긴** 경우다. 노션 query 응답
#    (100행 × 수십 속성 = 수백 KB)에서 가장 흔한 형태인데 두 구현 모두 안 잡고 있었다.
#
#    2026-07-13 에 "`requests.ReadTimeout` 한 번에 904행 착지가 통째로 죽었다(예외가 land 밖으로
#    튐)"를 고쳤는데, 그때 넓힌 것은 `Timeout`·`ConnectionError` 둘뿐이었다. **모양이 같고
#    예외 클래스만 다른 사고가 그대로 남아 있었다.**
#
#    ⚠️ `RequestException` 을 통째로 잡으면 안 된다 — `MissingSchema`·`InvalidURL` 같은
#       **프로그래밍 오류**까지 105초를 기다렸다 재시도한다(고쳐야 할 것을 숨긴다).
#       망 계열만 재시도하고 나머지는 즉시 올린다.
_NET_ERRORS = (
    requests.Timeout,
    requests.ConnectionError,           # SSLError·ProxyError 포함
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)


def write(method: str, url: str, tok: str, payload: dict):
    """노션 쓰기(POST/PATCH) — 429/5xx **와 네트워크 타임아웃**을 백오프 재시도.

    🔴 [2026-07-13] 타임아웃 재시도가 없어서, `requests.ReadTimeout` **한 번**에 904행 착지가
       통째로 죽었다(예외가 호출부 밖으로 튀어나가 프로세스 종료). 상태코드만 보고 있었는데,
       진짜로 자주 터지는 건 **응답이 아예 안 오는 경우**다.

    ⚠️ 200을 받았다고 값이 들어간 것은 아니다. 노션은 **알 수 없는 select 옵션 등을 조용히
       버리면서 200을 준다.** 착지 후 되읽어 확인하는 것은 호출부의 책임이다(불변 규칙 05).
    """
    delay = 1.0
    net_delay = NET_BACKOFF
    r = None
    for attempt in range(MAX_RETRY):
        try:
            r = requests.request(method, url, headers=headers(tok), json=payload, timeout=30)
        except _NET_ERRORS as e:
            if attempt == MAX_RETRY - 1:
                raise
            print(f"  · 노션 네트워크 오류({type(e).__name__}) — {net_delay:.1f}s 후 재시도 "
                  f"({attempt + 1}/{MAX_RETRY - 1}) · 절전 해제 직후면 망이 곧 올라온다")
            time.sleep(net_delay)
            net_delay *= 2
            continue
        if r.status_code not in _RETRIABLE:
            time.sleep(RATE_SLEEP)               # 성공해도 한도를 지킨다
            return r
        wait = float(r.headers.get("Retry-After") or delay)
        if attempt == MAX_RETRY - 1:
            return r
        print(f"  · 노션 {r.status_code} — {wait:.1f}s 후 재시도 ({attempt + 1}/{MAX_RETRY - 1})")
        time.sleep(wait)
        delay *= 2
    return r


def read(url: str, tok: str, params: dict | None = None):
    """노션 읽기(GET) — 429/5xx **와 네트워크 타임아웃** 백오프(쓰기와 같은 이유 → `write` 참조)."""
    delay = 1.0
    net_delay = NET_BACKOFF
    r = None
    for attempt in range(MAX_RETRY):
        try:
            r = requests.get(url, headers=headers(tok), params=params, timeout=30)
        except _NET_ERRORS as e:
            if attempt == MAX_RETRY - 1:
                raise
            print(f"  · 노션 네트워크 오류({type(e).__name__}) — {net_delay:.1f}s 후 재시도")
            time.sleep(net_delay)
            net_delay *= 2
            continue
        if r.status_code not in _RETRIABLE:
            time.sleep(RATE_SLEEP)
            return r
        wait = float(r.headers.get("Retry-After") or delay)
        if attempt == MAX_RETRY - 1:
            return r
        time.sleep(wait)
        delay *= 2
    return r


def clip16(text: str, limit: int = 2000) -> str:
    """노션 길이 제한에 맞춰 자른다. **파이썬 문자 수가 아니라 UTF-16 코드유닛 기준**이다.

    🔴 [2026-07-27 실측] `text[:2000]`으로 잘랐는데 노션이 400을 냈다:
         "content.length should be ≤ 2000, instead was 2003"
       원인: 노션(JS)은 **UTF-16 코드유닛**을 센다. 이모지 같은 보조평면 문자는 **2로 계산**된다.
       한 프로필의 bio에 이모지 3개가 있어 2000자가 2003유닛이 됐다.
       → 링크드인 495건 중 **7건이 통째로 착지 실패**했다(이모지 쓰는 사람이 정확히 우리가 찾는
         인구다 — 게임·디자인·인디 개발자). 로그엔 400이 찍혔지만 조용히 넘어갔으면 못 봤다.

    ⚠️ 새로 rich_text/title에 값을 넣을 땐 **반드시 이걸 통과시켜라.** `[:N]`은 안전하지 않다.
    """
    s = text or ""
    if len(s.encode("utf-16-le")) // 2 <= limit:
        return s
    # 유닛 기준으로 뒤에서 줄인다(문자 경계는 파이썬 슬라이스가 보장한다)
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(s[:mid].encode("utf-16-le")) // 2 <= limit:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo]


def rich_text(text: str, max_items: int = 10) -> list[dict]:
    """긴 본문을 rich_text 배열로 쪼갠다. 안 쪼개면 2,000자 초과 시 400이 난다.

    🔴 청크도 **UTF-16 유닛**으로 잘라야 한다 — 1,900 파이썬 문자가 이모지 때문에
       2,000유닛을 넘으면 그 블록만 400이 나고 본문이 통째로 안 붙는다.
    """
    s = (text or "").strip()
    if not s:
        return []
    out: list[dict] = []
    rest = s
    while rest and len(out) < max_items:
        piece = clip16(rest, MAX_TEXT)
        if not piece:
            break
        out.append({"type": "text", "text": {"content": piece}})
        rest = rest[len(piece):]
    return out


def fetch_schema(db_id: str, tok: str) -> dict:
    """`GET /databases/{id}` → `{property_name: {"type": ...}}`.

    프로퍼티 타입을 코드에 하드코딩하지 않고 **실제 스키마를 읽어** payload를 만든다.
    문서의 "url/text" 모호성과 필드 부재에 안전해진다. **스키마가 정본이고 문서는 참고다.**
    """
    r = read(f"{API}/databases/{db_id}", tok)
    if r.status_code != 200:
        raise RuntimeError(
            f"Notion DB 조회 실패 status={r.status_code} body={r.text[:300]}\n"
            "→ integration을 대상 DB에 연결(공유)했는지 확인하라"
        )
    return r.json().get("properties", {})


def allowed_options(schema: dict, prop: str) -> set[str]:
    """select/multi_select 프로퍼티의 **허용값을 라이브 스키마에서** 읽는다.

    🔴 [2026-07-27] 문서에만 적힌 상수와 실제 스키마가 갈라지면 조용한 사고가 난다.
       07-19에 신설한 판정 옵션이 코드 상수에 없어서, 마이그레이션이 옵션을 되돌리려 했다.
       dry-run에서 잡았다. **옵션 목록의 정본은 항상 라이브 스키마다.**
    """
    p = schema.get(prop) or {}
    body = p.get(p.get("type") or "", {})
    return {o.get("name") for o in (body.get("options") or []) if o.get("name")}
