"""노션 통로 — 노션에 닿는 유일한 경로.

## 이 파일이 지키는 [0] 제약

    2,000 UTF-16 코드유닛   파이썬 len 이 아니다. 이모지 3개로 2000자가 2003유닛 되어
                           495명 중 7명이 통째로 착지 실패했다 (07-27)
    잘렸으면 잘렸다고       값만으로는 "원래 그 길이"와 "잘렸다"가 구별이 안 된다
    3 req/s                성공해도 잔다
    200 ≠ 저장됨           모르는 select 옵션을 **조용히 버리면서 200** 을 준다
                           → 쓴 뒤 되읽어 확인한다
    옵션 정본은 라이브 스키마 코드 상수를 믿으면 사람이 추가한 옵션을 되돌린다 (07-19 실제 발생)
    망 예외 4종            ChunkedEncodingError 는 ConnectionError 의 서브클래스가 아니다.
                           헤더는 왔는데 본문 도중 끊긴 경우로, query 응답에서 가장 흔하다
    절전 해제 직후 DNS      수집 10,112건 성공 → 착지 직전에 죽어 229건 유실 (07-18)
    403 은 공유 설정        토큰 문제로 오진하고 재발급하며 시간을 버린다

## 두 가지를 새로 한다 (옛 시스템에 없던 것)

    되읽기        옛 코드는 status_code == 200 만 보고 성공으로 쳤다.
                 "노션이 조용히 버린다"는 걸 주석으로 알면서도 확인은 안 했다.
    칸 지정 읽기   filter_properties 를 쓰는 곳이 옛 리포에 0곳이었다.
                 행 하나를 읽으면 246칸이 통째로 온다 → 느리고, 응답이 커서 잘 끊긴다.
"""
from __future__ import annotations

import functools
import time

import requests

from kvlib import Fetched, constraints, secrets

_API = "https://api.notion.com/v1"
_VERSION = "2022-06-28"

# 🔴 쓰기 허용 DB — **스킬이 등록한다.** 통로가 명단을 들고 있지 않다.
#
#    통로가 하드코딩하면 스킬이 늘 때마다 여기를 고쳐야 하고, 그게 두 진실원이 된다
#    (스킬은 쓰는데 명단에 없거나, 명단엔 있는데 스킬이 없거나).
#    통로가 지키는 건 하나다 — **등록 안 된 DB 에는 안 쓴다.**
#
#    옛 시스템은 매핑 없는 채널을 base DB 로 조용히 흘려보냈고, 그 DB엔 그 채널 칸이 없어
#    값 대부분이 버려진 채 "오염된 대조원"이 됐다.
_WRITABLE: dict[str, str] = {}

# 🔴 READ_ONLY 를 지웠다 (2026-08-18).
#
#    세 개를 선언해두고 **참조가 0건**이었다. 그 자체로 "선언은 있는데 발동 안 하는" 상태고,
#    더 나쁜 건 `dedup_극초기` 라는 이름이 **극초기 DB 를 중복 대조원으로 쓴다고 말하는데
#    실제로는 아무도 안 읽었다**는 점이다. 그래서 08-18 조사에서 "극초기가 오염되면
#    중복 판정이 어긋난다"는 잘못된 걱정이 나왔다 — 어긋날 코드가 없었다.
#
#    읽기는 아무 DB 나 되므로 명단이 필요 없다. 읽는 곳이 id 를 직접 들고 있으면 된다.
#    (쓰기만 등록제다 — 그건 "등록 안 된 DB 에 안 쓴다"를 강제하기 위해서다.)


def register_writable(key: str, db_id: str) -> None:
    """스킬이 자기 착지 DB 를 등록한다. 같은 키를 다른 id 로 덮으면 raise."""
    prev = _WRITABLE.get(key)
    if prev and prev != db_id:
        raise NotionError(f"'{key}' 가 이미 {prev} 로 등록돼 있다 (새로: {db_id})")
    _WRITABLE[key] = db_id


def writable_id(key: str) -> str:
    return _writable_id(key)

_NET_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)


class NotionError(RuntimeError):
    pass


class NotWritable(RuntimeError):
    """쓰기 허용 명단에 없는 DB 다."""


# ---------------------------------------------------------------- HTTP

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {secrets.get('NOTION_TOKEN')}",
        "Notion-Version": _VERSION,
        "Content-Type": "application/json",
    }


def _req(method: str, path: str, *, json_body: dict | None = None, params: dict | None = None) -> dict:
    """노션 API 한 번. 성공해도 rate limit 만큼 잔다.

    재시도 정책이 계층별로 갈린다:
      망 계열   15/30/60s (절전 해제 직후 DNS 미기동)
      429       Retry-After 우선
      5xx       1s 기반
      4xx       재시도 없음 — 상대의 답이지 망 문제가 아니다
    """
    net_backoff = constraints.get("http.retry_backoff_sec")
    sleep_after = 1.0 / constraints.get("notion.rate_limit_rps")
    url = f"{_API}{path}"
    net_try = 0
    other_try = 0

    while True:
        try:
            r = requests.request(method, url, headers=_headers(), json=json_body,
                                 params=params, timeout=constraints.get("http.timeout_sec"))
        except _NET_ERRORS as e:
            if net_try < len(net_backoff):
                w = net_backoff[net_try]
                net_try += 1
                print(f"  · 노션 망 오류 {type(e).__name__} — {w}s 후 재시도 ({net_try}/{len(net_backoff)})")
                time.sleep(w)
                continue
            raise NotionError(f"망 계열로 끝까지 실패: {type(e).__name__} {path}") from e

        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After") or 1)
            other_try += 1
            if other_try > 4:
                raise NotionError(f"429 가 계속된다: {path}")
            print(f"  · 429 — Retry-After {wait}s")
            time.sleep(wait)
            continue
        if 500 <= r.status_code < 600:
            other_try += 1
            if other_try > 4:
                raise NotionError(f"{r.status_code} 가 계속된다: {path}")
            time.sleep(2 ** other_try)
            continue
        if r.status_code == 403:
            raise NotionError(
                f"403 {path} — 토큰이 아니라 **공유 설정**을 먼저 의심하라. "
                f"integration 을 대상 DB에 연결했는지 확인."
            )
        if r.status_code >= 400:
            raise NotionError(f"{r.status_code} {path}: {r.text[:300]}")

        time.sleep(sleep_after)
        return r.json()


# ---------------------------------------------------------------- 스키마

@functools.cache
def schema(db_id: str) -> dict:
    """라이브 스키마. **옵션 목록의 정본은 항상 여기다.**"""
    return _req("GET", f"/databases/{db_id}").get("properties", {})


def allowed_options(db_id: str, prop: str) -> list[str]:
    """select / multi_select 의 허용값을 라이브에서 읽는다."""
    p = schema(db_id).get(prop)
    if not p:
        raise NotionError(f"'{prop}' 칸이 그 DB 에 없다")
    kind = p.get("type")
    if kind not in ("select", "multi_select", "status"):
        raise NotionError(f"'{prop}' 은 {kind} 라 옵션이 없다")
    return [o["name"] for o in p.get(kind, {}).get("options", [])]


def _prop_ids(db_id: str, props: list[str]) -> list[str]:
    """filter_properties 는 이름이 아니라 property id 를 받는다."""
    sch = schema(db_id)
    ids = []
    for name in props:
        if name not in sch:
            raise NotionError(f"'{name}' 칸이 그 DB 에 없다 — 읽으려는 칸을 계약이 모른다")
        ids.append(sch[name]["id"])
    return ids


# ---------------------------------------------------------------- 절단

def sanitize(text: str) -> str:
    """노션이 조용히 제거하는 문자를 **보내기 전에** 개행으로 바꾼다.

    🔴 실측(08-17): 앱 설명 864자를 보냈는데 862자가 저장됐다 — U+2028 두 개가 사라졌다.
    되읽기가 이걸 잡았다. 옛 시스템은 되읽기가 없어서 이런 손실을 영영 몰랐다.

    미리 바꿔두면 저장된 값 = 우리가 의도한 값이 되고, 되읽기 검사를 **느슨하게 하지 않아도**
    된다. 검사를 느슨하게 하면 다음에 노션이 다른 걸 지울 때 그것도 같이 묻힌다.
    """
    for ch in constraints.get("notion.strips_chars"):
        text = text.replace(ch, "\n")
    # 제로폭(U+200B 등)은 개행이 아니라 제거다 — 폭이 0이라 사람 눈엔 원래 없던 글자다 (09-16 실측)
    for ch in constraints.get("notion.strips_zero_width"):
        text = text.replace(ch, "")
    if constraints.get("notion.strips_control_chars"):
        # C0 제어문자 제거 (\n·\t 는 남긴다). 하나씩 잡으면 계속 나온다 — 부류로 막는다.
        text = "".join(c for c in text if c >= " " or c in "\n\t")
    return text


def clip16(text: str, *, limit: int | None = None, mark: str | None = None) -> str:
    """UTF-16 코드유닛 기준 절단. **잘렸을 때만** 표식을 붙인다.

    파이썬 len 으로 자르면 이모지에서 틀린다 — 2,000자가 2,003유닛이 되어 400 이 났다.
    표식 길이만큼 예산을 줄여 자르므로 결과는 **언제나 한계 이하**다.
    """
    limit = limit or constraints.get("notion.rich_text_limit")
    mark = constraints.get("notion.truncation_mark") if mark is None else mark
    if _u16(text) <= limit:
        return text
    budget = limit - _u16(mark)
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _u16(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + mark


def _u16(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


# ---------------------------------------------------------------- 읽기

def query(db_id: str, props: list[str], *, page_size: int = 100, max_pages: int = 200) -> Fetched:
    """행을 읽는다. **props 는 필수** — 전량 읽기를 통로가 제공하지 않는다.

    옛 시스템은 filter_properties 를 안 써서 행마다 246칸을 통째로 받았다.
    응답이 수백 KB 라 ChunkedEncodingError 가 가장 흔한 자리이기도 했다.
    """
    if not props:
        raise ValueError(
            "읽을 칸을 명시하라. 전량 읽기는 제공하지 않는다 — "
            "246칸을 통째로 받으면 느리고, 응답이 커서 중간에 잘 끊긴다."
        )
    ids = _prop_ids(db_id, props)
    out: list[dict] = []
    cursor = None
    pages = 0
    try:
        while True:
            body = {"page_size": page_size}
            if cursor:
                body["start_cursor"] = cursor
            data = _req("POST", f"/databases/{db_id}/query", json_body=body,
                        params=[("filter_properties", i) for i in ids])
            for pg in data.get("results", []):
                out.append({"_page_id": pg["id"], "_edited": pg.get("last_edited_time"), **{k: _plain(v) for k, v in pg.get("properties", {}).items()}})
            pages += 1
            cursor = data.get("next_cursor")
            if not data.get("has_more"):
                return Fetched.measured(out, rows=len(out), pages=pages)
            if pages >= max_pages:
                # 잘렸다는 사실을 반환한다 — 조용히 적게 주지 않는다
                return Fetched.measured(out, partial=True, rows=len(out), pages=pages)
    except NotionError as e:
        return Fetched.unknown(f"notion:{e}")


def _plain(v: dict):
    """노션 property 값 → 파이썬 값. 모르는 타입은 None (조용히 빈 문자열로 만들지 않는다)."""
    t = v.get("type")
    if t == "title" or t == "rich_text":
        return "".join(x.get("plain_text", "") for x in v.get(t, []))
    if t == "select":
        return (v.get("select") or {}).get("name")
    if t == "multi_select":
        return [o["name"] for o in v.get("multi_select", [])]
    if t in ("number", "checkbox", "url", "email", "phone_number"):
        return v.get(t)
    if t == "date":
        return (v.get("date") or {}).get("start")
    if t in ("created_time", "last_edited_time"):
        return v.get(t)
    if t == "formula":
        # 🔴 formula 는 값이 한 겹 더 싸여 있다. 안 풀면 **값이 있는데 None 으로 읽힌다**
        #    (실측 08-17: `회사` 칸이 0% 로 보였는데 실제로는 차 있었다 — 파서가 못 읽은 것).
        fx = v.get("formula") or {}
        return fx.get(fx.get("type", ""))
    if t == "rollup":
        rl = v.get("rollup") or {}
        return rl.get(rl.get("type", ""))
    return None


# ---------------------------------------------------------------- 쓰기

class _Clear:
    """칸을 **비운다**. None 은 "건드리지 않는다"(만들 때 빈 칸을 안 보내려고)라, 갱신에서 값이 없어진 걸 지우려면 이걸 쓴다.
    발단(10/14): 재확인에서 현직을 그만둔 사람의 재직 개월이 None 이 되자 옛 값(13개월)이 그대로 남아 카드 기준을 잘못 탔다."""
    def __repr__(self) -> str:
        return "CLEAR"


CLEAR = _Clear()
_EMPTY = {"number": None, "url": None, "date": None, "select": None, "rich_text": [], "multi_select": [], "checkbox": False}


def create(db_key: str, props: dict) -> Fetched:
    """행 하나 생성 → **되읽어 확인**까지 하고 나서 성공이라고 한다.

    db_key 는 WRITABLE 의 키다. id 를 직접 받지 않는다 — 그래야 명단 밖 DB 에
    쓰는 코드가 표현 불가능해진다.
    """
    db_id = _writable_id(db_key)
    payload = _build(db_id, props)
    try:
        page = _req("POST", "/pages", json_body={"parent": {"database_id": db_id}, "properties": payload})
    except NotionError as e:
        return Fetched.unknown(f"create:{e}")

    page_id = page["id"]
    bad = _verify(db_id, page_id, props)
    if bad:
        return Fetched.unknown(f"readback_mismatch:{bad}", page_id=page_id)
    return Fetched.measured(page_id)


def update(page_id: str, db_key: str, props: dict) -> Fetched:
    db_id = _writable_id(db_key)
    payload = _build(db_id, props)
    try:
        _req("PATCH", f"/pages/{page_id}", json_body={"properties": payload})
    except NotionError as e:
        return Fetched.unknown(f"update:{e}")
    bad = _verify(db_id, page_id, props)
    if bad:
        return Fetched.unknown(f"readback_mismatch:{bad}", page_id=page_id)
    return Fetched.measured(page_id)


def archive(page_id: str) -> Fetched:
    """행을 휴지통으로 보낸다. **지우는 게 아니라 아카이브**라 노션에서 되살릴 수 있다.

    쓰기와 같은 규율을 지킨다 — 보내고 나서 **되읽어** 실제로 archived 인지 확인한다.
    200 은 "요청을 받았다"이지 "반영됐다"가 아니다.
    """
    try:
        _req("PATCH", f"/pages/{page_id}", json_body={"archived": True})
        if not _req("GET", f"/pages/{page_id}").get("archived"):            # 쓴 것은 되읽는다 — 200 은 저장을 뜻하지 않는다
            return Fetched.unknown(f"notion:archive 되읽기 실패 {page_id}")
    except NotionError as e:
        # 🔴 이미 아카이브된 페이지에 PATCH 하면 400 이 난다. 그건 실패가 아니라
        #    **이미 원하는 상태**다. 멱등이어야 재실행이 안전하다.
        #    (실측 08-17: 같은 작업을 두 번 띄웠더니 193건이 "실패"로 잡혔는데 전부 이 경우였다)
        if "archived" not in str(e):
            return Fetched.unknown(f"archive:{e}")
    try:
        page = _req("GET", f"/pages/{page_id}")
    except NotionError as e:
        return Fetched.unknown(f"archive_readback:{e}")
    if not page.get("archived") and not page.get("in_trash"):
        return Fetched.unknown("archive_not_applied", page_id=page_id)
    return Fetched.measured(page_id)


def _writable_id(db_key: str) -> str:
    if db_key not in _WRITABLE:
        raise NotWritable(
            f"'{db_key}' 가 등록돼 있지 않다. 등록된 것: {sorted(_WRITABLE)}. "
            f"스킬이 notion.register_writable('{db_key}', '<db_id>') 를 먼저 불러야 한다."
        )
    return _WRITABLE[db_key]


def _build(db_id: str, props: dict) -> dict:
    """이름→값 dict 를 노션 payload 로. 스키마에 없는 칸은 **조용히 버리지 않고** raise.

    select 값은 쓰기 **전에** 라이브 허용값과 대조한다 — 노션은 모르는 값을 조용히
    버리면서 200 을 주므로, 보내고 나서 알면 늦다.
    """
    sch = schema(db_id)
    out: dict = {}
    for name, val in props.items():
        if name not in sch:
            raise NotionError(f"'{name}' 칸이 DB 에 없다 — 오타이거나 스키마가 바뀌었다")
        if val is None:
            continue
        t = sch[name]["type"]
        if val is CLEAR:
            if t not in _EMPTY:
                raise NotionError(f"'{name}' 타입 {t} 은 비울 수 없다")
            out[name] = {t: _EMPTY[t]}
            continue
        if t == "title":
            out[name] = {"title": [{"text": {"content": clip16(sanitize(str(val)))}}]}
        elif t == "rich_text":
            out[name] = {"rich_text": [{"text": {"content": clip16(sanitize(str(val)))}}]}
        elif t == "select":
            _check_option(db_id, name, str(val))
            out[name] = {"select": {"name": str(val)}}
        elif t == "multi_select":
            vals = val if isinstance(val, list) else [val]
            for v in vals:
                _check_option(db_id, name, str(v))
            out[name] = {"multi_select": [{"name": str(v)} for v in vals]}
        elif t == "number":
            out[name] = {"number": val}
        elif t == "checkbox":
            out[name] = {"checkbox": bool(val)}
        elif t == "url":
            out[name] = {"url": str(val) or None}
        elif t == "date":
            out[name] = {"date": {"start": str(val)}}
        else:
            raise NotionError(f"'{name}' 타입 {t} 은 아직 안 다룬다 — 필요하면 명시적으로 추가하라")
    return out


def _check_option(db_id: str, prop: str, value: str) -> None:
    allowed = allowed_options(db_id, prop)
    if value not in allowed:
        raise NotionError(
            f"'{prop}' 에 없는 옵션 '{value}'. 라이브 허용값: {allowed}. "
            f"노션은 이런 값을 조용히 버리면서 200 을 준다 — 보내기 전에 막는다."
        )


def _verify(db_id: str, page_id: str, props: dict) -> str:
    """되읽어 대조. 안 맞으면 어떤 칸이 틀렸는지 돌려준다.

    🔴 이게 이 통로의 존재 이유 중 하나다. 200 은 "요청을 받았다"이지
    "값이 들어갔다"가 아니다.
    """
    try:
        page = _req("GET", f"/pages/{page_id}")
    except NotionError as e:
        return f"readback_failed:{e}"
    live = {k: _plain(v) for k, v in page.get("properties", {}).items()}
    bad = []
    for name, want in props.items():
        if want is None:
            continue
        got = live.get(name)
        if want is CLEAR:
            if got not in (None, "", [], False):
                bad.append(name)
            continue
        t = schema(db_id)[name]["type"]
        if t in ("title", "rich_text"):
            if str(got or "") != clip16(sanitize(str(want))):
                bad.append(name)
        elif t == "multi_select":
            wants = want if isinstance(want, list) else [want]
            if set(got or []) != {str(w) for w in wants}:
                bad.append(name)
        elif t == "number":
            if got != want:
                bad.append(name)
        elif t == "checkbox":
            if bool(got) != bool(want):
                bad.append(name)
        else:
            if str(got or "") != str(want):
                bad.append(name)
    return ",".join(bad)


# ---------------------------------------------------------------- 페이지 본문 (2026-09-26, 투심 레인)

def find_page(query: str, *, page_size: int = 5) -> Fetched:
    """search API. `[{id, title, parent}]` — parent 는 database id 또는 page id. 제목만 보고 고르는 건 호출부 몫이다."""
    try:
        data = _req("POST", "/search", json_body={"query": query, "page_size": page_size,
                                                  "filter": {"value": "page", "property": "object"}})
    except NotionError as e:
        return Fetched.unknown(f"notion:{e}")
    out = []
    for pg in data.get("results", []):
        title = next((_plain(v) for v in pg.get("properties", {}).values() if v.get("type") == "title"), "")
        par = pg.get("parent") or {}
        out.append({"id": pg["id"], "title": title or "", "parent": par.get(par.get("type", ""), "")})
    return Fetched.measured(out)


def page_text(page_id: str, *, max_blocks: int = 3000, depth: int = 6) -> Fetched:
    """페이지 **본문**을 md 로. 속성(값 있는 것만) → 블록(토글·헤딩·불릿·표·자식 페이지 제목)을 자식까지 재귀로.

    🔴 `query()` 는 DB 행의 칸만 준다. 미팅록은 페이지 본문의 토글 안에 있어 이 경로가 아니면 안 읽힌다.
       모르는 블록 타입은 `[타입]` 으로 남긴다 — 조용히 빈 줄로 만들지 않는다."""
    lines: list[str] = []
    n = 0

    def rich(b: dict) -> str:
        t = b.get("type", "")
        return "".join(x.get("plain_text", "") for x in (b.get(t) or {}).get("rich_text", []))

    def walk(bid: str, level: int) -> None:
        nonlocal n
        cursor = None
        while True:
            data = _req("GET", f"/blocks/{bid}/children", params={"page_size": 100, **({"start_cursor": cursor} if cursor else {})})
            for b in data.get("results", []):
                n += 1
                if n > max_blocks:
                    lines.append(f"[잘림 — 블록 {max_blocks}개 초과]")
                    return
                t, ind, txt = b.get("type", ""), "  " * level, rich(b)
                if t in ("heading_1", "heading_2", "heading_3"):
                    lines.append(f"{'#' * int(t[-1])} {txt}")
                elif t == "toggle":
                    lines.append(f"{ind}▸ {txt}")
                elif t == "to_do":
                    lines.append(f"{ind}- [{'x' if b['to_do'].get('checked') else ' '}] {txt}")
                elif t in ("bulleted_list_item", "numbered_list_item", "quote", "callout"):
                    lines.append(f"{ind}- {txt}")
                elif t == "paragraph":
                    if txt.strip():
                        lines.append(f"{ind}{txt}")
                elif t == "table_row":
                    lines.append(ind + " | ".join("".join(x.get("plain_text", "") for x in cell) for cell in b["table_row"].get("cells", [])))
                elif t in ("child_page", "child_database"):
                    lines.append(f"{ind}[{t}] {(b.get(t) or {}).get('title', '')}")
                elif t in ("divider", "table_of_contents", "column_list", "column", "table"):
                    pass
                else:
                    lines.append(f"{ind}[{t}]")
                if b.get("has_children") and level < depth:
                    walk(b["id"], level + (0 if t in ("table", "column_list", "column") else 1))
            cursor = data.get("next_cursor")
            if not data.get("has_more"):
                return

    try:
        page = _req("GET", f"/pages/{page_id}")
        props = {k: _plain(v) for k, v in page.get("properties", {}).items()}
        title = next((v for k, v in props.items() if page["properties"][k].get("type") == "title"), "")
        lines.append(f"# {title}")
        for k, v in props.items():
            if v not in (None, "", [], False) and page["properties"][k].get("type") != "title":
                lines.append(f"- {k}: {v}")
        lines.append("")
        walk(page_id, 0)
    except NotionError as e:
        return Fetched.unknown(f"notion:{e}")
    return Fetched.measured("\n".join(lines), blocks=n, page_id=page_id)


# ---------------------------------------------------------------- 2026-09-26 펀드제안서 레인 — 페이지 본문을 **구조 그대로**

def _md(rich: list[dict]) -> str:
    """rich_text → 글자. 볼드만 `**…**` 로 남긴다 — 제안서 이관 규칙 '볼드는 노션과 동일하게'. 다른 꾸밈은 서식이 아니라 버린다."""
    return "".join(f"**{x['plain_text']}**" if x.get("annotations", {}).get("bold") and x["plain_text"].strip() else x["plain_text"] for x in rich)


def page_blocks(page_id: str, *, max_blocks: int = 4000, depth: int = 8) -> Fetched:
    """페이지 본문 블록을 평평한 목록으로 — `{type, level, text}` + 표(`cells`·`has_header`) + 그림(`url`·`caption`).
    `page_text()` 는 미팅록용 md 라 볼드·표 경계·중첩 깊이를 버린다. 제안서는 그 셋이 곧 문서 구조라 따로 둔다.
    토글도 재귀한다 — 무엇을 옮기지 않을지는 부르는 쪽(skills/fund/convert)이 정한다. 모르는 타입은 `type` 그대로 남겨 조용히 사라지지 않게."""
    out: list[dict] = []

    def walk(bid: str, level: int) -> None:
        cursor = None
        while True:
            data = _req("GET", f"/blocks/{bid}/children", params={"page_size": 100, **({"start_cursor": cursor} if cursor else {})})
            for b in data.get("results", []):
                if len(out) >= max_blocks:
                    out.append({"type": "truncated", "level": 0, "text": f"블록 {max_blocks}개 초과"}); return
                t, body = b.get("type", ""), b.get(b.get("type", "")) or {}
                row = {"type": t, "level": level, "text": _md(body.get("rich_text") or [])}
                if t == "table":
                    row.update(has_header=bool(body.get("has_column_header")), cols=body.get("table_width"))
                elif t == "table_row":
                    row["cells"] = [_md(c) for c in body.get("cells", [])]
                elif t == "image":
                    row.update(url=(body.get(body.get("type", "")) or {}).get("url", ""), caption=_md(body.get("caption") or []))
                elif t == "to_do":
                    row["checked"] = bool(body.get("checked"))
                elif t in ("child_page", "child_database"):
                    row["text"] = body.get("title", "")
                out.append(row)
                if b.get("has_children") and level < depth:
                    walk(b["id"], level + (0 if t == "table" else 1))
            cursor = data.get("next_cursor")
            if not data.get("has_more"):
                return

    try:
        walk(page_id, 0)
    except NotionError as e:
        return Fetched.unknown(f"notion:{e}")
    return Fetched.measured(out, blocks=len(out), page_id=page_id)


def download(url: str, dest) -> Fetched:
    """노션 그림 블록의 파일(서명된 S3 URL, 한 시간 유효). 노션이 준 주소만 받는다 — 이 통로 밖 주소를 받는 데 쓰지 않는다."""
    from pathlib import Path
    dest = Path(dest)
    try:
        r = requests.get(url, timeout=constraints.get("http.timeout_sec"))
        r.raise_for_status()
    except requests.RequestException as e:
        return Fetched.unknown(f"notion-file:{e}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    return Fetched.measured(dest, bytes=len(r.content))
