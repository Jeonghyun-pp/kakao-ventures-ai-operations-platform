#!/usr/bin/env python3
"""App Store 급상승/급하락 보드 — diandian rank/updown 뷰 복제 (로컬 미니서버 앱).

diandian updown 로직: 시간창 양끝 순위 차분(Δ) + 순위밴드 필터 + 정렬.
"이전 시점" = 디스크의 가장 가까운 과거 스냅샷(없으면 RSS 파이프라인 스냅샷).
새로고침 = 서버가 애플 SSR(200위)을 그 자리에서 다시 받아 diff → 실시간 갱신.

    python tools/board.py                 # 서버 기동 + 브라우저 자동 오픈 (localhost:8000)
    python tools/board.py --port 8080
    python tools/board.py --genre 6014    # 초기 카테고리=게임

npm·빌드 없음. 파이썬 stdlib http.server + requests(기수집)만 사용. Ctrl+C로 종료.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

# 🔴 [2026-07-20 → 07-28] **프로젝트 루트도 넣어야 한다.**
#   tools/ 만 넣었더니 `rank_lookup` 은 찾았는데 그게 import 하는 `adapters._util` 을 못 찾았다.
#   증상: ModuleNotFoundError: No module named 'adapters'.
#   07-20에 rank_lookup.py 가 `from adapters import _util` 을 얻으면서 깨졌고,
#   그날부터 **8일간 보드 스냅샷이 한 건도 안 쌓였다**(앱스토어 Δ는 재수집이 불가능 —
#   그 8일은 영구 손실이다). 로그엔 트레이스백이 찍혔지만 아무도 안 봤다.
#   → 실패 알림이 없으면 조용히 죽은 것을 못 본다는 증거. (불변 규칙 06)
_TOOLS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _TOOLS)
sys.path.insert(0, os.path.dirname(_TOOLS))      # 프로젝트 루트 — adapters/ core/ 가 여기 있다
import rank_lookup as rl  # noqa: E402

PROJ = Path(__file__).resolve().parents[1]
# 실데이터 루트는 $KV_DATA 다(기본 ~/kv-data). 리포 안에 두면 git 명령의 사정권에 들어간다.
# GitHub Actions 러너에서도 같은 코드가 돌아야 하므로 경로를 코드에 박지 않는다.
from kv_core import paths as _paths  # noqa: E402
SNAP_DIR = _paths.data("net-sourcing", "snapshots")
BOARD_PREFIX = "appstore_board"

# 전체(genre 36 = 카테고리 무관 통합 차트) + diandian과 동일한 App Store 25개 카테고리.
# 전부 무인증 실측으로 200위 반환 확인.
CATEGORIES = {
    36: "전체 (모든 카테고리)",
    6014: "게임", 6016: "엔터테인먼트", 6008: "사진 및 비디오", 6007: "생산성", 6017: "교육",
    6005: "소셜 네트워킹", 6015: "금융", 6023: "식음료", 6024: "쇼핑", 6012: "라이프스타일",
    6013: "건강 및 피트니스", 6000: "비즈니스", 6002: "유틸리티", 6011: "음악", 6009: "뉴스",
    6003: "여행", 6018: "도서", 6004: "스포츠", 6006: "참고", 6010: "내비게이션",
    6001: "날씨", 6020: "의료", 6021: "잡지 및 신문", 6026: "개발자 도구", 6027: "그래픽 및 디자인",
}

# 게임 세부장르 — 각자 200위 차트 보유(실측). 커버리지 폭 확장용(6014+서브 합집합 ≈ 2,266개).
# 7007 주사위·7008 교육게임은 실측 0건이라 제외.
GAME_SUB = {
    7001: "액션", 7002: "어드벤처", 7003: "아케이드", 7004: "보드", 7005: "카드", 7006: "카지노",
    7009: "가족", 7011: "음악", 7012: "퍼즐", 7013: "레이싱", 7014: "롤플레잉", 7015: "시뮬레이션",
    7016: "스포츠", 7017: "전략", 7018: "트리비아", 7019: "워드",
}

# 스냅샷·이름 조회는 메인+서브 전체를 대상으로.
ALL_GENRES = {**CATEGORIES, **{g: f"게임·{n}" for g, n in GAME_SUB.items()}}


def genre_name(genre: int) -> str:
    return ALL_GENRES.get(genre, str(genre))


# 차트 종류: 무료/유료는 SSR(200위), 매출/최신출시는 RSS(100~110위). label=UI표기, depth=대략 깊이.
CHART_LABELS = {"top-free": ("무료", 200), "top-paid": ("유료", 200),
                "top-grossing": ("매출", 100), "new": ("최신출시", 110)}
_RSS_FEED = {"top-grossing": "topgrossingapplications", "new": "newapplications"}
SSR_CHARTS = ("top-free", "top-paid")


def _rss_chart(genre: int, feed: str, limit: int = 200) -> list[dict]:
    """RSS 기반 차트(매출/최신출시). 전체(36)는 genre 파라미터 없이."""
    g = "" if genre == 36 else f"/genre={genre}"
    url = f"https://itunes.apple.com/kr/rss/{feed}/limit={limit}{g}/json"
    r = requests.get(url, headers={"User-Agent": rl.UA}, timeout=30)
    r.raise_for_status()
    out = []
    for rank, e in enumerate(r.json().get("feed", {}).get("entry", []), 1):
        out.append({
            "rank": rank, "app_id": e["id"]["attributes"]["im:id"],
            "name": e["im:name"]["label"],
            "developer": e.get("im:artist", {}).get("label", ""),
            "released_at": (e.get("im:releaseDate", {}).get("attributes", {}).get("label", "") or "")[:10],
        })
    return out


def fetch_any(genre: int, chart: str) -> list[dict]:
    """차트 종류에 맞는 소스로 현재 순위를 가져온다(SSR 200위 or RSS 100~110위)."""
    if chart in SSR_CHARTS:
        return rl.fetch_chart(genre, chart)[:200]
    return _rss_chart(genre, _RSS_FEED[chart])


# ---------- 순서보존 스냅샷 ----------
def _save_snapshot(genre: int, chart: str, ids: list[str]) -> None:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    key = f"{BOARD_PREFIX}_{genre}_{chart}_{date.today().isoformat()}.json"
    (SNAP_DIR / key).write_text(json.dumps(ids), encoding="utf-8")


def _load_prev(genre: int, chart: str) -> tuple[list[str], str]:
    """'이전 시점' = 오늘보다 과거 중 가장 최근 스냅샷. board 스냅샷 우선, 없으면 RSS 폴백."""
    today = date.today()

    def _nearest(glob: str) -> tuple[Path | None, date | None]:
        best, best_d = None, None
        for f in SNAP_DIR.glob(glob):
            try:
                d = date.fromisoformat(f.stem.split("_")[-1])
            except ValueError:
                continue
            if d >= today:
                continue
            if best is None or d > best_d:
                best, best_d = f, d
        return best, best_d

    f, d = _nearest(f"{BOARD_PREFIX}_{genre}_{chart}_*.json")
    if f is None and chart == "top-free":
        f, d = _nearest(f"appstore_{genre}_*.json")
        if f:
            ids = json.loads(f.read_text(encoding="utf-8"))
            return ids, f"RSS기준({d}, {len(ids)}위 깊이)"
    if f is None:
        return [], "이전 스냅샷 없음(오늘 기준선 세팅 — 내일부터 Δ)"
    ids = json.loads(f.read_text(encoding="utf-8"))
    return ids, f"board기준({d}, {len(ids)}위)"


def _seen_cumulative(genre: int, chart: str) -> set[str]:
    """**누적 seen-set** — 과거 모든 스냅샷(board + 소싱)의 합집합.

    🔴 [2026-07-14] 왜 이게 따로 필요한가:
      `_load_prev`는 **가장 최근 스냅샷 하나**와만 비교한다(Δ 계산엔 이게 맞다 — 어제 대비 등락).
      그런데 그 기준으로 'NEW'를 판정하면 **195위↔205위를 오가는 앱이 200위 밖으로 나갔다
      들어올 때마다 매번 신규로 잡힌다.** 이건 재진입이지 신규가 아니다.
      소싱 어댑터는 이 버그를 이미 고쳤다(adapters/_util.load_seen_ids = 누적 방식).
      **보드만 옛 방식으로 남아 있었다** → 보드의 '신규진입'과 노션 착지 행이 서로 안 맞았다.

    → Δ/이전순위는 `_load_prev`(최근 1개)를 그대로 쓰고, **'진짜 신규'(first_seen) 판정만**
      이 누적 집합으로 한다. 두 질문이 다르므로 기준선도 둘이다.
    """
    today = date.today()
    seen: set[str] = set()
    for pat in (f"{BOARD_PREFIX}_{genre}_{chart}_*.json",   # 보드가 매일 05:10에 쌓는 것
                f"appstore_{genre}_{chart}_*.json"):        # 소싱 파이프라인이 쌓는 것
        for f in SNAP_DIR.glob(pat):
            try:
                d = date.fromisoformat(f.stem.split("_")[-1])
            except ValueError:
                continue
            if d >= today:                 # 오늘 저장분은 자기 자신이라 제외
                continue
            try:
                seen |= set(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
    return seen


# ---------- 차분(Δ) 계산 ----------
def build_board(genre: int, chart: str) -> dict:
    cur = fetch_any(genre, chart)
    prev_ids, baseline = _load_prev(genre, chart)
    prev_rank = {aid: i + 1 for i, aid in enumerate(prev_ids)}
    cur_ids = {a["app_id"] for a in cur}
    dropped = [{"app_id": aid} for aid in prev_ids if aid not in cur_ids]

    # 진짜 신규(first_seen) 판정용 — Δ용 기준선(prev_ids)과는 **다른 질문**이다. 위 주석 참조.
    seen_cum = _seen_cumulative(genre, chart)

    rl.enrich(cur + dropped)
    _save_snapshot(genre, chart, [a["app_id"] for a in cur])

    rows = []
    for a in cur:
        pr = prev_rank.get(a["app_id"])
        delta = (pr - a["rank"]) if pr else None
        rows.append({
            "rank": a["rank"], "prev_rank": pr, "delta": delta,
            "status": "NEW" if pr is None else ("UP" if delta > 0 else "DOWN" if delta < 0 else "SAME"),
            # 누적 이력이 없으면(첫날) 판정 불가 → False. "모른다"를 "신규"로 부풀리지 않는다.
            "first_seen": bool(seen_cum) and a["app_id"] not in seen_cum,
            "name": a.get("name", ""), "developer": a.get("developer", ""),
            "released_at": a.get("released_at", ""), "rating": a.get("avg_rating") or a.get("rating"),
            "app_id": a["app_id"], "url": a.get("url", ""),
        })
    for a in dropped:
        rows.append({
            "rank": None, "prev_rank": prev_rank.get(a["app_id"]), "delta": None, "status": "OUT",
            "first_seen": False,
            "name": a.get("name", ""), "developer": a.get("developer", ""),
            "released_at": a.get("released_at", ""), "rating": a.get("avg_rating") or a.get("rating"),
            "app_id": a["app_id"], "url": a.get("url", ""),
        })
    return {"genre_id": genre, "genre": genre_name(genre), "chart": chart,
            "baseline": baseline, "seen_days": len(seen_cum), "rows": rows}


# ---------- 매일 스냅샷 배치(모든 카테고리 Δ 실시간성 확보용, enrich 생략해 가볍게) ----------
def snapshot_all(charts=("top-free", "top-paid")) -> None:
    n = 0
    for g in ALL_GENRES:
        for c in charts:
            try:
                ids = [a["app_id"] for a in rl.fetch_chart(g, c)[:200]]
                _save_snapshot(g, c, ids)
                n += 1
            except Exception as e:  # noqa: BLE001
                print(f"  [warn] {genre_name(g)}/{c}: {e!r}", file=sys.stderr)
    print(f"[snapshot] {n}개 차트 저장 완료 ({date.today()}) — 내일부터 전 카테고리 Δ")


# ---------- 서버 ----------
_CACHE: dict[tuple[int, str], tuple[float, dict]] = {}
_TTL = 600  # 초. 서버를 오래 켜둬도 이 시간 지나면 열 때 자동 재수집 → '언제 열어도 최신'


def get_board(genre: int, chart: str, fresh: bool) -> dict:
    key = (genre, chart)
    hit = _CACHE.get(key)
    if not fresh and hit and (time.monotonic() - hit[0]) < _TTL:
        return hit[1]
    data = build_board(genre, chart)
    _CACHE[key] = (time.monotonic(), data)
    return data


def _shell(default_genre: int) -> str:
    # 메인 카테고리(전체+25) — 게임 세부장르는 별도 셀렉트로 분리(게임 선택 시에만 노출)
    main = "".join(
        f'<option value="{g}"{" selected" if g == default_genre else ""}>{n}</option>'
        for g, n in CATEGORIES.items())
    subs = '<option value="6014">전체 게임</option>' + "".join(
        f'<option value="{g}">{n}</option>' for g, n in GAME_SUB.items())
    return (_TEMPLATE.replace("__OPTIONS__", main).replace("__SUBOPTS__", subs)
            .replace("__DEFAULT__", str(default_genre)))


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        u = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            self._send(200, _shell(int(qs.get("genre", [36])[0])).encode("utf-8"),
                       "text/html; charset=utf-8")
        elif u.path == "/api/board":
            g = int(qs.get("genre", ["6016"])[0])
            c = qs.get("chart", ["top-free"])[0]
            try:
                b = get_board(g, c, fresh=("fresh" in qs))
                self._send(200, json.dumps(b, ensure_ascii=False).encode("utf-8"),
                           "application/json; charset=utf-8")
            except Exception as e:  # noqa: BLE001
                self._send(500, json.dumps({"error": repr(e)}).encode("utf-8"),
                           "application/json; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *a) -> None:  # 조용히
        pass


_TEMPLATE = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>App Store 급상승/급하락 보드</title>
<style>
:root{--bg:#0f1115;--card:#171a21;--line:#2a2f3a;--fg:#e8eaed;--mut:#9aa0aa;--up:#2ecc71;--dn:#e74c3c;--new:#f1c40f;--acc:#4f8cff}
@media(prefers-color-scheme:light){:root{--bg:#f5f6f8;--card:#fff;--line:#e3e6ea;--fg:#1a1d22;--mut:#6b7280;--up:#149a54;--dn:#c0392b;--new:#b7860b}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo",sans-serif}
.wrap{max-width:1360px;margin:0 auto;padding:20px}
/* 본문 + 용어 사이드바. 좁은 화면(≤980px)에선 사이드바가 표 아래로 내려간다. */
.cols{display:grid;grid-template-columns:minmax(0,1fr) 268px;gap:18px;align-items:start}
@media(max-width:980px){.cols{grid-template-columns:minmax(0,1fr)}}
.side{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 15px;font-size:12px;position:sticky;top:16px}
.side h3{font-size:12px;margin:0 0 7px;color:var(--fg);letter-spacing:.02em}
.side h3+dl{margin:0 0 14px}
.side dl{margin:0}
.side dt{font-weight:700;margin-top:6px}
.side dd{margin:1px 0 0;color:var(--mut);line-height:1.45}
.side .k{display:inline-block;min-width:52px}
.side hr{border:0;border-top:1px solid var(--line);margin:12px 0}
.side .warn{color:var(--dn);font-weight:600}
h1{font-size:20px;margin:0 0 4px}.sub{color:var(--mut);font-size:12px;margin-bottom:16px}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:12px}
select,input,button{background:var(--card);border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:7px 11px;font-size:13px}
button{cursor:pointer}.tg.on{background:var(--acc);border-color:var(--acc);color:#fff}
.lb{color:var(--mut);font-size:12px;margin-right:2px}
.chips{display:inline-flex;gap:4px;padding:3px;border:1px solid var(--line);border-radius:10px;background:var(--card)}
.ch{border:none;background:transparent;padding:5px 12px;border-radius:7px;font-size:13px;color:var(--mut)}
.ch.on{background:var(--acc);color:#fff}
#refresh{margin-left:auto;border-color:var(--acc);color:var(--acc);font-weight:600}#refresh:hover{background:var(--acc);color:#fff}
.band{margin-left:auto;color:var(--mut);font-size:12px}.band input{width:56px}
.rel{color:var(--mut);font-size:12px}.rel input{width:132px;font-size:12px}
.rl.on{background:var(--acc);color:#fff}
.dead{opacity:.55}.dead:after{content:" ⚠︎"}
.meta{color:var(--mut);font-size:12px;margin:2px 0 12px;display:flex;gap:14px;flex-wrap:wrap}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{padding:9px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--mut);font-weight:600;font-size:12px}tr:last-child td{border-bottom:none}
td.d{font-weight:700}.up{color:var(--up)}.dn{color:var(--dn)}.new{color:var(--new)}.mut{color:var(--mut)}
.name{white-space:normal;max-width:360px}.name a{color:inherit;text-decoration:none}.name a:hover{text-decoration:underline}
.spin{opacity:.5;pointer-events:none}
</style></head><body><div class="wrap">
<h1>App Store 급상승 / 급하락 보드</h1>
<div class="sub">diandian rank/updown 뷰 · 로직: 시간창 양끝 순위 차분(Δ) + 순위밴드 필터 + 정렬</div>
<div class="cols"><div class="main">
<div class="bar">
  <label class="lb">카테고리</label>
  <select id="genre">__OPTIONS__</select>
  <select id="subgenre" style="display:none">__SUBOPTS__</select>
  <span class="chips" id="charts">
    <button class="ch on" data-c="top-free" title="SSR 200위">무료</button>
    <button class="ch" data-c="top-paid" title="SSR 200위">유료</button>
    <button class="ch" data-c="top-grossing" title="RSS 100위">매출</button>
    <button class="ch" data-c="new" title="RSS 110위 · 최근 출시">최신출시</button>
  </span>
  <button id="refresh">🔄 새로고침</button>
</div>
<div class="bar">
  <label class="lb">보기</label>
  <button class="tg on" data-m="up">급상승 ▲</button>
  <button class="tg" data-m="down">급하락 ▼</button>
  <button class="tg" data-m="new">신규진입 ●</button>
  <button class="tg" data-m="all">전체순위</button>
  <label class="lb" title="끄면 '한 번도 차트에 없던 앱'만 — 소싱 파이프라인과 같은 기준. 켜면 200위 밖으로 나갔다 돌아온 앱도 포함(옛 기준)">
    <input type="checkbox" id="reentry" style="vertical-align:-1px"> 재진입 포함</label>
  <span class="band">현재순위 <input id="lo" type="number" placeholder="min"> ~ <input id="hi" type="number" placeholder="max"></span>
</div>
<div class="bar">
  <label class="lb">출시일</label>
  <span class="chips" id="rels">
    <button class="rl on" data-r="all">전체</button>
    <button class="rl" data-r="y"  title="365일">1년</button>
    <button class="rl" data-r="m6" title="180일 — 소싱 파이프라인의 L0 컷과 동일 기준">6개월</button>
    <button class="rl" data-r="m3" title="90일">3개월</button>
    <button class="rl" data-r="m1" title="30일">1개월</button>
  </span>
  <span class="rel">직접 지정 <input id="relFrom" type="date"> ~ <input id="relTo" type="date">
    <button id="relClear" style="padding:5px 9px">지우기</button></span>
</div>
<div class="meta"><span id="baseline"></span><span id="updated"></span><span id="count"></span></div>
<table><thead><tr><th>Δ</th><th>현재</th><th>이전</th><th>앱</th><th>개발사</th><th>출시일</th><th>평점</th></tr></thead>
<tbody id="tb"></tbody></table>
<div class="sub" style="margin-top:14px">Δ = 이전순위 − 현재순위(양수=상승). NEW=이전 차트에 없던 신규진입 · OUT=top-200 밖 이탈. 새로고침 = 애플에서 200위 실시간 재수집 후 재계산. 기준선이 100위 깊이(RSS)면 100위 밖 진입은 NEW로 보일 수 있음(내일부터 200위 기준 축적).<br>
<b>출시일</b> = 앱이 앱스토어에 <b>처음 등록된 날</b>(최근 업데이트일 아님). <b>6개월(180일)</b>은 소싱 파이프라인이 실제로 자르는 L0 컷과 <b>같은 기준</b>이다 — 즉 <b>[신규진입 ● + 6개월]</b>으로 보면 그날 노션에 착지하는 것과 같은 집합이 된다. 출시일 미상 앱은 필터를 켜면 제외되며 개수가 표시된다(소싱 L0는 미상을 통과시킨다).</div>
</div>

<aside class="side">
<h3>📊 차트 4종</h3>
<dl>
  <dt>무료</dt><dd>돈 안 내고 받는 앱의 <b>다운로드</b> 순위. 200위까지.</dd>
  <dt>유료</dt><dd>돈 주고 사는 앱의 <b>구매</b> 순위. 200위까지.</dd>
  <dt>매출</dt><dd>인앱결제·구독까지 포함한 <b>매출</b> 순위. 다운로드는 적어도 돈을 잘 버는 앱이 여기 뜬다. 100위까지.</dd>
  <dt>최신출시</dt><dd>최근 출시된 <b>신규 앱 목록</b>(순위와 무관). 아직 차트에 못 든 갓 나온 앱을 잡는 층.
    <br><span class="warn">⚠️ 2026-07-11부터 애플이 갱신을 멈췄다. 카테고리도 무시된다(뭘 골라도 같은 106개).</span></dd>
</dl>
<hr>
<h3>👁 보기 4종</h3>
<dl>
  <dt>급상승 ▲</dt><dd>순위가 <b>오른</b> 앱 + 새로 들어온 앱. 상승폭 큰 순.</dd>
  <dt>급하락 ▼</dt><dd>순위가 <b>내린</b> 앱 + 차트 밖으로 빠진 앱. 하락폭 큰 순.</dd>
  <dt>신규진입 ●</dt><dd><b>한 번도 차트에 없던</b> 앱만. 나갔다 돌아온 앱(재진입)은 뺀다. 현재 순위 순.
    <br>← <b>극초기 발굴의 핵심 신호</b></dd>
  <dt>전체순위</dt><dd>현재 차트에 있는 것 <b>전부</b>, 절대 순위 순.</dd>
</dl>
<hr>
<h3>🏷 배지 읽는 법</h3>
<dl>
  <dt><span class="new">NEW</span></dt><dd>우리가 관측을 시작한 이래 <b>한 번도 차트에 없던</b> 앱.</dd>
  <dt><span class="mut">재진입</span></dt><dd>과거엔 차트에 있었는데 밖으로 나갔다가 <b>돌아온</b> 앱. 새 앱이 아니다.</dd>
  <dt><span class="up">▲N</span> / <span class="dn">▼N</span></dt><dd>N계단 상승 / 하락.</dd>
  <dt><span class="dn">OUT</span></dt><dd>어제는 있었는데 오늘 차트 밖으로 빠졌다.</dd>
</dl>
<dd style="margin-top:6px">Δ = 이전순위 − 현재순위 (양수 = 상승)</dd>
<hr>
<h3>📅 출시일</h3>
<dl>
  <dt>무슨 날짜?</dt><dd>앱이 앱스토어에 <b>처음 등록된 날</b>. 최근 업데이트일이 <b>아니다</b>.</dd>
  <dt>6개월 = 180일</dt><dd>소싱 파이프라인이 실제로 자르는 기준과 <b>똑같은 값</b>.</dd>
  <dt>노션과 맞추려면</dt><dd><b>신규진입 ● + 6개월</b> → 그게 그날 노션에 착지하는 <b>바로 그 집합</b>.</dd>
</dl>
</aside>
</div><!-- /.cols -->
</div><!-- /.wrap -->
<script>
let DATA={rows:[]},mode="up",chart="top-free",relMode="all";
// 출시일 프리셋(일). m6=180 은 **소싱 L0 컷과 같은 값**이다(core/judge.py::_hardcut_appstore).
// 달력상 '6개월'이 아니라 180일인 이유: 파이프라인이 실제로 자르는 기준과 눈으로 보는 화면이
// 어긋나면, 보드에서 보이는 앱이 노션엔 없는(혹은 그 반대) 일이 생긴다.
const RELDAYS={all:0,y:365,m6:180,m3:90,m1:30};
const $=id=>document.getElementById(id);
function relCutoff(){
  if(relMode=="all")return "";
  const d=new Date();d.setDate(d.getDate()-RELDAYS[relMode]);
  return d.toISOString().slice(0,10);
}
function effGenre(){
  const g=+$("genre").value, sub=$("subgenre");
  sub.style.display=(g===6014)?"":"none";        // 게임 선택 시에만 세부장르 노출
  return (g===6014 && sub.value)?+sub.value:g;
}
async function load(fresh){
  const g=effGenre(),c=chart;
  $("tb").parentElement.classList.add("spin");$("count").textContent=fresh?"실시간 재수집 중…":"불러오는 중…";
  try{const r=await fetch(`/api/board?genre=${g}&chart=${c}${fresh?"&fresh=1":""}`);DATA=await r.json();
    $("baseline").textContent="Δ기준선: "+DATA.baseline+" · 누적이력 "+(DATA.seen_days||0)+"개 앱(신규 판정용)";
    $("updated").textContent="갱신: "+new Date().toLocaleString("ko-KR");
  }catch(e){$("count").textContent="오류: "+e}
  $("tb").parentElement.classList.remove("spin");draw();
}
// NEW = 어제 대비 새로 들어옴. 그중 **누적 이력에도 없던 것만** 진짜 신규(first_seen)다.
// 나머지는 200위 밖으로 나갔다 돌아온 **재진입**이라 구분해서 보여준다(옛 보드는 둘을 뭉갰다).
function dcell(r){
  if(r.status=="NEW")return r.first_seen?['<span class="new">NEW</span>',"new"]
                                        :['<span class="mut">재진입</span>',"mut"];
  if(r.status=="OUT")return['<span class="dn">OUT</span>',"dn"];
  if(r.delta>0)return['<span class="up">▲'+r.delta+'</span>',"up"];
  if(r.delta<0)return['<span class="dn">▼'+(-r.delta)+'</span>',"dn"];
  return['<span class="mut">–</span>',"mut"]}
function draw(){
  let rows=(DATA.rows||[]).slice();
  const lo=+$("lo").value||0,hi=+$("hi").value||9999;
  let reNote="";
  if(mode=="up")rows=rows.filter(r=>r.status=="NEW"||r.delta>0).sort((a,c)=>(c.status=="NEW")-(a.status=="NEW")||(c.delta||0)-(a.delta||0));
  else if(mode=="down")rows=rows.filter(r=>r.status=="OUT"||r.delta<0).sort((a,c)=>(a.status=="OUT")-(c.status=="OUT")||(a.delta||0)-(c.delta||0));
  else if(mode=="new"){
    const loose=$("reentry").checked, all=rows.filter(r=>r.status=="NEW");
    rows=(loose?all:all.filter(r=>r.first_seen)).sort((a,c)=>a.rank-c.rank);
    if(!loose&&all.length>rows.length)reNote=` · 재진입 ${all.length-rows.length}개 제외`;
  }
  else rows=rows.filter(r=>r.rank).sort((a,c)=>a.rank-c.rank);   // 전체순위
  rows=rows.filter(r=>{const k=r.rank||9999;return k>=lo&&k<=hi});
  // ── 출시일 필터 (프리셋 ∧ 직접지정) ──
  // 출시일 **미상**은 필터가 켜져 있으면 **제외하고, 몇 개 뺐는지 화면에 적는다.**
  //   (소싱 L0는 미상을 '통과'시킨다 — recall 우선. 보드는 사람이 보는 화면이라 반대로 잡았다.
  //    다만 조용히 빼면 필터가 고장난 것처럼 보이므로 개수를 반드시 노출한다.)
  const cut=relCutoff(),rf=$("relFrom").value,rt=$("relTo").value;
  let unknown=0;
  if(cut||rf||rt){
    rows=rows.filter(r=>{
      const v=r.released_at||"";
      if(!v){unknown++;return false}
      if(cut&&v<cut)return false;
      if(rf&&v<rf)return false;
      if(rt&&v>rt)return false;
      return true;
    });
  }
  $("count").textContent=rows.length+"개"+reNote+(unknown?` · 출시일 미상 ${unknown}개 제외`:"");
  $("tb").innerHTML=rows.map(r=>{const[dc,cl]=dcell(r);
    const nm=r.url?`<a href="${r.url}" target="_blank">${r.name||r.app_id}</a>`:(r.name||r.app_id);
    return `<tr><td class="d ${cl}">${dc}</td><td>${r.rank||"—"}</td><td class="mut">${r.prev_rank||"—"}</td><td class="name">${nm}</td><td class="mut">${r.developer||""}</td><td class="mut">${r.released_at||""}</td><td class="mut">${r.rating!=null?(+r.rating).toFixed(1):""}</td></tr>`}).join("")
    ||'<tr><td colspan=7 class="mut" style="padding:18px">해당 조건의 앱이 없습니다.</td></tr>';
}
document.querySelectorAll(".tg").forEach(b=>b.onclick=()=>{mode=b.dataset.m;document.querySelectorAll(".tg").forEach(x=>x.classList.remove("on"));b.classList.add("on");draw()});
document.querySelectorAll(".ch").forEach(b=>b.onclick=()=>{chart=b.dataset.c;document.querySelectorAll(".ch").forEach(x=>x.classList.remove("on"));b.classList.add("on");load(false)});
document.querySelectorAll(".rl").forEach(b=>b.onclick=()=>{relMode=b.dataset.r;document.querySelectorAll(".rl").forEach(x=>x.classList.remove("on"));b.classList.add("on");draw()});
$("reentry").onchange=draw;
$("relFrom").oninput=draw;$("relTo").oninput=draw;
$("relClear").onclick=()=>{$("relFrom").value="";$("relTo").value="";relMode="all";
  document.querySelectorAll(".rl").forEach(x=>x.classList.toggle("on",x.dataset.r=="all"));draw()};
$("genre").onchange=()=>{$("subgenre").value="6014";load(false)};
$("subgenre").onchange=()=>load(false);
$("refresh").onclick=()=>load(true);$("lo").oninput=draw;$("hi").oninput=draw;
load(false);
</script></body></html>"""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="App Store 급상승/급하락 보드 (로컬 서버 앱)")
    p.add_argument("--genre", type=int, default=36, help="초기 카테고리 ID(36=전체)")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-open", action="store_true", help="브라우저 자동 오픈 안 함")
    p.add_argument("--snapshot-all", action="store_true",
                   help="서버 없이 전 카테고리 200위 스냅샷만 저장(매일 크론용)")
    args = p.parse_args(argv)

    if args.snapshot_all:
        snapshot_all()
        return 0

    url = f"http://localhost:{args.port}/?genre={args.genre}"
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"▶ 보드 실행 중:  {url}")
    print("  카테고리 25종 · 무료/유료 · 급상승/급하락/신규 · 순위밴드 · 🔄새로고침(실시간 재수집)")
    print("  종료: Ctrl+C")
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
