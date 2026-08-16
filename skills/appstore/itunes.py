"""iTunes / App Store 통로 — 애플에 닿는 유일한 경로.

## 이 파일이 지키는 [0] 제약

    차트 천장          SSR 무료·유료 200위 · RSS 매출 100위 · 최신출시 110건 하드캡
                      "200 요청했는데 100 왔다"를 결손으로 오판하지 않으려고 안다
    최신출시 genre 무시  chart="new" 는 장르를 무시한다 → 전체 1회만 부른다.
                      어기면 25개 장르 × 같은 응답을 25번 받는다
    빈 배열이 정답      카지노 유료(7006)는 한국 앱스토어에 원래 없다.
                      결손으로 읽으면 "부분 결손" 경보가 매일 뜬다 (실제로 그랬다)
    망 계열만 재시도    15/30/60s. 404·429·5xx 는 상대 서버의 답이지 망 문제가 아니다
    회로 차단기        한 번 끝까지 실패하면 나머지는 즉시 실패.
                      없을 때 76요청 × 105초 = 2시간+ 크론이 잠겼다 (07-19)
    charset 없음       애플 차트 HTML 은 charset 헤더가 없어 requests 가 ISO-8859-1 로
                      오판한다 → 한글이 깨진다. utf-8 을 강제한다

## 반환

전부 `Fetched` 다. **빈 배열은 measured=True** — 애플의 정답이지 결손이 아니다.
망이 죽으면 `unknown` 이고, 그건 "0건"과 완전히 다른 사건이다.
"""
from __future__ import annotations

import html
import json
import re
import time

import requests

from kvlib import Fetched, constraints

# ---------------------------------------------------------------- 상수(제약 아님)

# 애플 원문("사진 및 비디오")이 아니라 **노션 기존 옵션명**에 맞춘다.
# 원문을 그대로 넣으면 기존 옵션과 별개로 중복 옵션이 생겨 multi_select 가 오염된다.
GENRE_NAMES = {
    6014: "게임", 6016: "엔터테인먼트", 6008: "사진비디오", 6007: "생산성", 6017: "교육",
    6005: "소셜", 6015: "금융", 6023: "식음료", 6024: "쇼핑", 6012: "라이프스타일",
    6013: "건강피트니스", 6000: "비즈니스", 6002: "유틸리티", 6011: "음악", 6009: "뉴스",
    6003: "여행", 6018: "도서", 6004: "스포츠", 6006: "참고", 6010: "내비게이션",
    6001: "날씨", 6020: "의료", 6021: "잡지신문", 6026: "개발자도구", 6027: "그래픽디자인",
}
CHART_LABEL = {"top-free": "무료", "top-paid": "유료", "top-grossing": "매출", "new": "최신출시"}
_SSR_CHARTS = ("top-free", "top-paid")
_RSS_FEED = {"top-grossing": "topgrossingapplications", "new": "newapplications"}

_SSR_URL = "https://apps.apple.com/kr/iphone/charts/{genre}?chart={chart}"
_RSS_URL = "https://itunes.apple.com/kr/rss/{feed}/limit=200{genre}/json"
_LOOKUP_URL = "https://itunes.apple.com/lookup"
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
       "(KHTML, like Gecko) Version/17.0 Safari/605.1.15")

# 망 계열만. RequestException 을 통째로 잡으면 MissingSchema 같은 **프로그래밍 오류까지**
# 105초 기다렸다 재시도한다 — 고쳐야 할 것을 숨긴다.
# ChunkedEncodingError 는 ConnectionError 의 서브클래스가 **아니다**(08-13 재발 근거).
_NET_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)

# 회로 차단기 — 망이 진짜로 죽었을 때만 열린다.
#
# 🔴 실측(2026-08-17): 한 번의 일시적 실패에 열리게 했더니, 76요청 중 1개가 삐끗한 순간
#    뒤따르던 8개가 재시도 없이 즉시 실패했다. 개별로 다시 부르면 전부 200 이었다.
#    차단기의 목적은 "망이 죽었는데 76 × 105초 = 2시간 잠기는 것"을 막는 것이지,
#    일시적 실패 하나로 나머지를 포기하는 게 아니다. → **연속** 실패에만 연다.
_CIRCUIT_AFTER = 2      # 연속 완전 실패 이 횟수부터 (2 × 105초 = 210초면 충분히 판단된다)
_circuit_open = False
_consec_fail = 0


def reset_circuit() -> None:
    """새 실행 시작 시 호출. 테스트도 이걸 쓴다."""
    global _circuit_open, _consec_fail
    _circuit_open = False
    _consec_fail = 0


class NetDown(RuntimeError):
    """망 계열로 끝까지 실패했다. '0건'이 아니다."""


# ---------------------------------------------------------------- HTTP

_last_call = 0.0


def _pace() -> None:
    """요청 사이 간격. 애플은 연속 요청에 429 를 준다(08-17 실측 39/76)."""
    global _last_call
    gap = constraints.get("appstore.request_gap_sec")
    wait = gap - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    _last_call = time.monotonic()


def _get(url: str, *, params: dict | None = None, headers: dict | None = None) -> requests.Response:
    """재시도 정책이 **계층별로 다르다.**

        망 계열   15/30/60s   절전 해제 직후 DNS 미기동
        429·5xx  2/5/15s     429 는 Retry-After 우선 ("천천히 하라"는 신호다)
        그 외 4xx 재시도 없음  상대의 확정된 답이다
        그 외 예외 재시도 없음  MissingSchema 같은 프로그래밍 오류를 숨기지 않는다

    회로가 열려 있으면 1회만 시도한다 — 망이 죽었을 때 76 × 105초 = 2시간 잠기는 걸 막는다.
    """
    global _circuit_open, _consec_fail
    net_backoff = constraints.get("http.retry_backoff_sec")
    st_backoff = constraints.get("http.status_backoff_sec")
    retry_status = constraints.get("http.retry_status")
    timeout = constraints.get("http.timeout_sec")
    hdrs = {"User-Agent": _UA, **(headers or {})}

    net_try = 0
    st_try = 0
    last: Exception | None = None
    while True:
        _pace()
        try:
            r = requests.get(url, params=params, headers=hdrs, timeout=timeout)
        except _NET_ERRORS as e:
            last = e
            limit = 0 if _circuit_open else len(net_backoff)
            if net_try < limit:
                w = net_backoff[net_try]
                net_try += 1
                print(f"  · 망 오류 {type(e).__name__} — {w}s 후 재시도 ({net_try}/{limit})")
                time.sleep(w)
                continue
            _consec_fail += 1
            if constraints.get("http.circuit_breaker") and _consec_fail >= _CIRCUIT_AFTER and not _circuit_open:
                _circuit_open = True
                print(f"  🔴 회로 차단기 열림 (연속 실패 {_consec_fail}) — 이후는 재시도 없이 즉시 실패")
            raise NetDown(f"{type(last).__name__}: {url}") from last

        if r.status_code in retry_status and st_try < len(st_backoff):
            w = float(r.headers.get("Retry-After") or st_backoff[st_try])
            st_try += 1
            print(f"  · {r.status_code} — {w:.0f}s 후 재시도 ({st_try}/{len(st_backoff)})")
            time.sleep(w)
            continue

        _consec_fail = 0          # 응답이 왔다 = 망은 살아 있다
        return r


# ---------------------------------------------------------------- 차트

def chart(genre: int | None, chart_name: str) -> Fetched:
    """차트 하나. 반환은 순위 오름차순 앱 목록.

    genre=None 은 전체. chart="new" 는 genre 를 무시한다(제약).
    """
    if chart_name == "new" and genre is not None and constraints.get("appstore.rss_new_ignores_genre"):
        raise ValueError(
            "최신출시 피드는 genre 를 무시한다 — genre=None 으로 1회만 불러라. "
            "장르별로 부르면 같은 응답을 25번 받는다."
        )
    try:
        apps = _ssr(genre, chart_name) if chart_name in _SSR_CHARTS else _rss(genre, chart_name)
    except NetDown as e:
        return Fetched.unknown(f"net:{e}", genre=genre, chart=chart_name)
    except Exception as e:  # 4xx·파싱 실패 등 — 망 문제가 아니다. 숨기지 않는다.
        return Fetched.unknown(f"{type(e).__name__}:{e}", genre=genre, chart=chart_name)

    cap = constraints.get(f"appstore.chart_caps.{chart_name}")
    empty_ok = f"{genre}/{chart_name}" in constraints.get("appstore.empty_is_valid")
    if not apps and not empty_ok:
        # 빈 배열이 정답인 차트가 아닌데 비었다 — 측정은 됐지만 표시해둔다.
        print(f"  ⚠️  {genre or '전체'}/{chart_name} 빈 배열 (선언된 정상 진공 아님)")
    # 천장에 못 미치는 건 결손이 아니다. 애플이 그만큼만 주는 것이다.
    return Fetched.measured(apps, genre=genre, chart=chart_name, cap=cap)


def _ssr(genre: int | None, chart_name: str) -> list[dict]:
    """apps.apple.com 차트 페이지의 serialized-server-data 를 파싱한다(200위, 추가 호출 0)."""
    r = _get(_SSR_URL.format(genre=genre or 36, chart=chart_name),
             headers={"Accept": "text/html", "Accept-Language": "ko-KR"})
    r.raise_for_status()
    # 애플 차트는 charset 헤더가 없어 requests 가 ISO-8859-1 로 오판한다 → 한글 깨짐
    r.encoding = "utf-8"
    m = (re.search(r'id="serialized-server-data"[^>]*>(.*?)</script>', r.text, re.S)
         or re.search(r'<script type="application/json"[^>]*>(.*?)</script>', r.text, re.S))
    if not m:
        raise ValueError("SSR 블록을 못 찾았다 — 애플이 페이지 구조를 바꿨을 수 있다")
    seg = _find_segment(json.loads(html.unescape(m.group(1))), chart_name)

    out: list[dict] = []
    seen: set[str] = set()
    # 1~25위는 shelves(ordinal), 26위 이후는 nextPage.remainingContent(순서)
    head = {str(it["adamId"]): it
            for sh in seg.get("shelves", []) for it in sh.get("items", []) if it.get("adamId")}
    for it in sorted(head.values(), key=lambda i: int(i.get("ordinal", 10 ** 9))):
        aid = str(it["adamId"])
        if aid in seen:
            continue
        seen.add(aid)
        off = it.get("offerDisplayProperties") or {}
        out.append({
            "app_id": aid,
            "name": it.get("title") or "",
            "developer": it.get("developerName") or "",
            "subtitle": it.get("subtitle") or "",
            "rating": it.get("rating"),
            "rating_count": it.get("ratingCount"),
            "price": off.get("price") or off.get("priceText") or "",
        })
    for x in seg.get("nextPage", {}).get("remainingContent", []):
        aid = str(x.get("id") or "")
        if aid and aid not in seen:
            seen.add(aid)
            out.append({"app_id": aid})   # 꼬리는 메타가 없다 → lookup 에서 채운다
    for rank, a in enumerate(out, 1):
        a["rank"] = rank
    return out


def _find_segment(node, chart_name: str):
    """하드 인덱스를 피해 방어적으로 찾는다 — 애플이 구조를 바꿔도 덜 깨진다."""
    if isinstance(node, dict):
        if "segments" in node and "genreId" in node:
            for s in node["segments"]:
                if s.get("chart") == chart_name:
                    return s
        for v in node.values():
            r = _find_segment(v, chart_name)
            if r is not None:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _find_segment(v, chart_name)
            if r is not None:
                return r
    return None


def _rss(genre: int | None, chart_name: str) -> list[dict]:
    """iTunes RSS. 매출 100위 · 최신출시 110건 하드캡."""
    g = f"/genre={genre}" if genre else ""
    r = _get(_RSS_URL.format(feed=_RSS_FEED[chart_name], genre=g))
    r.raise_for_status()
    out = []
    for rank, e in enumerate(r.json().get("feed", {}).get("entry", []), 1):
        out.append({
            "rank": rank,
            "app_id": e["id"]["attributes"]["im:id"],
            "name": e["im:name"]["label"],
            "developer": e.get("im:artist", {}).get("label", ""),
            "url": (e.get("id") or {}).get("label", ""),
        })
    return out


# ---------------------------------------------------------------- 상세

def lookup(app_ids: list[str]) -> Fetched:
    """app_id → 상세(출시일·개발사 도메인·설명…). 50개씩 배치.

    일부 배치가 실패하면 **partial=True** 로 표시한다 — 조용히 적게 돌려주지 않는다.
    """
    got: dict[str, dict] = {}
    failed = 0
    for i in range(0, len(app_ids), 50):
        chunk = app_ids[i:i + 50]
        try:
            r = _get(_LOOKUP_URL, params={"id": ",".join(chunk), "country": "kr"})
            r.raise_for_status()
            for it in r.json().get("results", []):
                aid = str(it.get("trackId") or "")
                if not aid:
                    continue
                got[aid] = {
                    "name": it.get("trackName") or "",
                    "developer": it.get("artistName") or "",
                    "released_at": (it.get("releaseDate") or "")[:10],
                    "seller_url": it.get("sellerUrl") or "",
                    "description": it.get("description") or "",
                    "subtitle": it.get("subtitle") or "",
                    "artwork": it.get("artworkUrl512") or it.get("artworkUrl100") or "",
                    "rating": it.get("averageUserRating"),
                    "review_count": it.get("userRatingCount"),
                    "genres": it.get("genres") or [],
                    "url": it.get("trackViewUrl") or "",
                }
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ⚠️  lookup 배치 실패({type(e).__name__}) — {len(chunk)}개")
    return Fetched.measured(got, partial=failed > 0, failed_batches=failed)
