#!/usr/bin/env python3
"""App Store 카테고리 랭킹 조회 도구 (읽기 전용, 보강 포함).

파이프라인(dedup/judge/sync/Notion)과 분리된 단독 조회기. 부를 때마다 애플의
현재 순위 200위를 SSR(serialized-server-data)로 가져와 lookup으로 상세 보강해
터미널 표 / JSON 으로 출력한다. 스냅샷·판단·적재는 하지 않는다.

    python tools/rank_lookup.py                       # 기본 4장르, top-free, 표
    python tools/rank_lookup.py --genre 6016          # 엔터만
    python tools/rank_lookup.py --chart top-paid --limit 50
    python tools/rank_lookup.py --no-enrich           # SSR 인라인 메타만(빠름)
    python tools/rank_lookup.py --format json --out ranks.json

무료·무인증. 200위가 애플 무료 천장(그 이상은 유료). 검증 근거: 워크플로우 60회 실측.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys

import requests

from adapters import _util

SSR_URL = "https://apps.apple.com/kr/iphone/charts/{genre}?chart={chart}"
RSS_URL = "https://itunes.apple.com/kr/rss/topfreeapplications/limit=100/genre={genre}/json"
LOOKUP_URL = "https://itunes.apple.com/lookup"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/17 Safari/605.1.15")
GENRE_NAMES = {6016: "엔터", 6017: "교육", 6008: "사진/비디오", 6007: "생산성", 6014: "게임"}
DEFAULT_GENRES = [6016, 6017, 6008, 6007]


# ---------- 1) SSR 차트 파싱 (200위, 추가호출 0) ----------
def _walk_chart(o):
    """방어적 탐색: segments+genreId 가진 dict를 찾는다(하드 인덱스 회피)."""
    if isinstance(o, dict):
        if "segments" in o and "genreId" in o:
            return o
        for v in o.values():
            r = _walk_chart(v)
            if r:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _walk_chart(v)
            if r:
                return r
    return None


def fetch_chart(genre: int, chart: str) -> list[dict]:
    """SSR 파싱 → 순위 오름차순 앱 리스트. 실패 시 RSS-100 폴백."""
    try:
        r = _util.get_with_retry(SSR_URL.format(genre=genre, chart=chart),
                         headers={"User-Agent": UA, "Accept": "text/html",
                                  "Accept-Language": "ko-KR"}, timeout=30)
        r.raise_for_status()
        r.encoding = "utf-8"  # 애플 차트는 charset 헤더가 없어 requests가 ISO-8859-1로 오판 → 한글 깨짐 방지
        m = (re.search(r'id="serialized-server-data"[^>]*>(.*?)</script>', r.text, re.S)
             or re.search(r'<script type="application/json"[^>]*>(.*?)</script>', r.text, re.S))
        data = json.loads(html.unescape(m.group(1)))
        ch = _walk_chart(data)
        seg = next(s for s in ch["segments"] if s.get("chart") == chart)

        # rank 1~25: shelves(ordinal), rank 26~200: nextPage.remainingContent(순서)
        head = {}  # adamId -> item (인라인 메타 보유)
        for sh in seg.get("shelves", []):
            for it in sh.get("items", []):
                if it.get("adamId"):
                    head[str(it["adamId"])] = it
        ordered_head = sorted(head.values(), key=lambda i: int(i.get("ordinal", 10 ** 9)))
        tail_ids = [str(x["id"]) for x in seg.get("nextPage", {}).get("remainingContent", []) if x.get("id")]

        out, seen = [], set()
        for it in ordered_head:
            aid = str(it["adamId"])
            if aid in seen:
                continue
            seen.add(aid)
            out.append(_from_ssr_item(it))
        for aid in tail_ids:
            if aid in seen:
                continue
            seen.add(aid)
            out.append({"app_id": aid})  # tail은 메타 없음 → enrich에서 채움
        for rank, a in enumerate(out, 1):
            a["rank"] = rank
        return out
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] genre {genre} SSR 실패({e!r}) → RSS-100 폴백", file=sys.stderr)
        return _rss_fallback(genre)


def _from_ssr_item(it: dict) -> dict:
    off = (it.get("offerDisplayProperties") or {})
    return {
        "app_id": str(it["adamId"]),
        "name": it.get("title") or "",
        "developer": it.get("developerName") or "",
        "subtitle": it.get("subtitle") or "",
        "rating": it.get("rating"),
        "rating_count": it.get("ratingCount"),
        "age": it.get("ageRating") or "",
        "bundle_id": it.get("bundleId") or "",
        "price": off.get("price") or off.get("priceText") or "",
    }


def _rss_fallback(genre: int) -> list[dict]:
    r = _util.get_with_retry(RSS_URL.format(genre=genre), headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    entries = r.json().get("feed", {}).get("entry", [])
    out = []
    for rank, e in enumerate(entries, 1):
        out.append({
            "rank": rank,
            "app_id": e["id"]["attributes"]["im:id"],
            "name": e["im:name"]["label"],
            "developer": e.get("im:artist", {}).get("label", ""),
        })
    return out


# ---------- 2) lookup 배치 보강 ----------
def enrich(apps: list[dict]) -> None:
    """app_id 50개씩 배치 lookup → 상세 병합(제자리 수정)."""
    by_id = {a["app_id"]: a for a in apps}
    ids = list(by_id)
    for i in range(0, len(ids), 50):
        chunk = ids[i:i + 50]
        try:
            r = _util.get_with_retry(LOOKUP_URL, params={"id": ",".join(chunk), "country": "kr"},
                             headers={"User-Agent": UA}, timeout=30)
            r.raise_for_status()
            r.encoding = "utf-8"  # charset 헤더 누락 대비(한글 개발사/설명 깨짐 방지)
            for res in r.json().get("results", []):
                a = by_id.get(str(res.get("trackId")))
                if not a:
                    continue
                a.setdefault("name", res.get("trackName", ""))
                a.setdefault("developer", res.get("sellerName", ""))
                a["released_at"] = (res.get("releaseDate") or "")[:10]
                a["updated_at"] = (res.get("currentVersionReleaseDate") or "")[:10]
                a["version"] = res.get("version", "")
                a["price"] = res.get("formattedPrice", a.get("price", ""))
                a["avg_rating"] = res.get("averageUserRating")
                a["review_count"] = res.get("userRatingCount")
                a["min_os"] = res.get("minimumOsVersion", "")
                a["genres"] = ", ".join(res.get("genres", []))
                a["seller_url"] = res.get("sellerUrl", "")
                # 🔴 이게 없어서 노션 `Artwork`가 **1,471행 중 0행**이었다(2026-07-14).
                #   배선은 살아 있었다(appstore.py → notion.py:897) — **채우는 쪽만 없었다.**
                #   앱 보드에서 아이콘 하나가 텍스트 열 개보다 빠르다. 512px를 쓴다(썸네일은 60/100).
                a["artwork"] = res.get("artworkUrl512") or res.get("artworkUrl100") or ""
                a["description"] = (res.get("description") or "").replace("\n", " ")[:200]
                a["url"] = res.get("trackViewUrl", "")
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] lookup 배치 실패({e!r})", file=sys.stderr)


# ---------- 3) 출력 ----------
def render_table(genre: int, chart: str, apps: list[dict]) -> None:
    title = f"[{GENRE_NAMES.get(genre, genre)}] {chart}  ({len(apps)}개)"
    print("\n" + title)
    print("-" * len(title))
    print(f"{'#':>3}  {'이름':<24}  {'개발사':<20}  {'출시일':<10}  {'평점':>4}  appId")
    for a in apps:
        name = (a.get("name") or "")[:23]
        dev = (a.get("developer") or "")[:19]
        rel = a.get("released_at", "")
        rat = a.get("avg_rating") if a.get("avg_rating") is not None else a.get("rating")
        rat = f"{rat:.1f}" if isinstance(rat, (int, float)) else ""
        print(f"{a['rank']:>3}  {name:<24}  {dev:<20}  {rel:<10}  {rat:>4}  {a['app_id']}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="App Store 카테고리 랭킹 조회(읽기 전용)")
    p.add_argument("--genre", type=int, action="append",
                   help="장르 ID(반복 가능). 미지정 시 기본 4종")
    p.add_argument("--chart", default="top-free", choices=["top-free", "top-paid"])
    p.add_argument("--limit", type=int, default=200, help="장르당 상위 N (최대 200)")
    p.add_argument("--no-enrich", action="store_true", help="SSR 인라인 메타만(빠름)")
    p.add_argument("--format", default="table", choices=["table", "json"])
    p.add_argument("--out", help="JSON 저장 경로(미지정 시 화면 출력만)")
    args = p.parse_args(argv)

    genres = args.genre or DEFAULT_GENRES
    result = {}
    for g in genres:
        apps = fetch_chart(g, args.chart)[:args.limit]
        if not args.no_enrich:
            enrich(apps)
        result[g] = apps
        if args.format == "table":
            render_table(g, args.chart, apps)

    if args.format == "json" or args.out:
        payload = {"chart": args.chart,
                   "genres": {GENRE_NAMES.get(g, str(g)): result[g] for g in genres}}
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"\n[saved] {args.out}")
        else:
            print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
