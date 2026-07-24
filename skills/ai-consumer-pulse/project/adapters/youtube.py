"""YouTube 어댑터 — 채널/재생목록 RSS로 신규 감지 → yt-dlp 자막만 추출.

- RSS(무인증·무료)가 최신 15개를 반환 — 신규 videoId만 처리
- raw curl로 timedtext 불가(POT 토큰, 실측) → yt-dlp 필수
- shorts_policy: full(쇼츠도 자막 — Garry Tan처럼 쇼츠가 원본 논평)
                titles(제목만 저장 — 클립형, 본편과 이중 카운트 방지. '편집 선택 신호')
- track light(TBPN): 자막 추출 금지 — 제목+설명만 (토큰 폭발 방지, full 승격 금지)
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from core.clean_vtt import clean
from core.common import config, fetch, polite_delay, save_item, sources
from core.state import load_seen, mark_seen, record_health

FEED_CH = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
FEED_PL = "https://www.youtube.com/feeds/videos.xml?playlist_id={}"
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
      "media": "http://search.yahoo.com/mrss/"}


def _playlist_via_ytdlp(pid: str, limit: int = 15) -> list[dict]:
    """재생목록 신규 감지 경로.

    2026-07-20 실측: feeds/videos.xml?playlist_id=... 가 검증한 3개 재생목록 전부 404였다.
    같은 세션에서 채널 RSS는 200을 반환했으므로 재생목록만의 문제로 보이나,
    반복 요청 후 채널 RSS도 404/500으로 돌아선 것을 보면 유튜브 측 레이트리밋이 섞여 있어
    "재생목록 RSS가 영구히 죽었다"고 단정할 수는 없다.
    어느 쪽이든 yt-dlp는 안정적으로 목록을 반환하므로 재생목록은 이 경로를 기본으로 쓴다.

    flat 모드에는 published/description이 없다(NA) — 신규 감지에는 vid만 있으면 충분하고
    본문은 어차피 자막에서 온다.
    """
    try:
        p = subprocess.run(
            ["yt-dlp", "--flat-playlist", "--print", "%(id)s\t%(title)s",
             "--playlist-items", f"1-{limit}",
             f"https://www.youtube.com/playlist?list={pid}"],
            capture_output=True, text=True, timeout=90)
    except Exception:  # noqa: BLE001 — 소스 하나 때문에 수집 전체가 죽으면 안 됨
        return []
    out = []
    for line in (p.stdout or "").splitlines():
        if "\t" not in line:
            continue
        vid, title = line.split("\t", 1)
        vid = vid.strip()
        if vid and vid != "NA":
            out.append({"vid": vid, "title": title.strip(), "published": "", "description": ""})
    return out


def _feed_entries(src: dict) -> tuple[int, list[dict]]:
    if src.get("playlist_id"):
        ents = _playlist_via_ytdlp(src["playlist_id"])
        # RSS가 죽었으므로 yt-dlp 결과가 곧 성공/실패 판정이다
        return (200 if ents else 0), ents
    url = FEED_CH.format(src["channel_id"])
    status, body = fetch(url)
    if status != 200:
        return status, []
    try:
        root = ET.fromstring(body.encode("utf-8"))
    except ET.ParseError:
        return status, []
    out = []
    for e in root.findall("a:entry", NS):
        desc = e.find("media:group/media:description", NS)
        out.append({
            "vid": e.findtext("yt:videoId", "", NS),
            "title": e.findtext("a:title", "", NS),
            "published": e.findtext("a:published", "", NS),
            "description": (desc.text or "")[:1500] if desc is not None else "",
        })
    return status, out


def _probe_duration(vid: str) -> int:
    """영상 길이(초). 실패 시 -1 — 쇼츠 판정 불가면 본편으로 취급."""
    try:
        r = subprocess.run(["yt-dlp", "--print", "%(duration)s", "--skip-download",
                            f"https://www.youtube.com/watch?v={vid}"],
                           capture_output=True, text=True, timeout=60)
        return int(float(r.stdout.strip().splitlines()[-1]))
    except Exception:
        return -1


def _subtitles(vid: str) -> str:
    """자막 VTT 다운로드 → 정제 텍스트. 실패 시 ''."""
    with tempfile.TemporaryDirectory() as td:
        try:
            subprocess.run(["yt-dlp", "--write-auto-subs", "--sub-langs", "en",
                            "--skip-download", "-o", f"{td}/s.%(ext)s",
                            f"https://www.youtube.com/watch?v={vid}"],
                           capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            return ""
        vtts = list(Path(td).glob("*.vtt"))
        if not vtts:
            return ""
        return clean(vtts[0].read_text(encoding="utf-8"))


def collect() -> dict:
    seen = load_seen()
    caps = config()["caps"]
    max_v = int(caps["max_videos_per_channel"])
    shorts_cut = int(caps["shorts_max_seconds"])
    summary = {}
    for src in sources("youtube"):
        status, entries = _feed_entries(src)
        if status != 200:
            record_health(src["id"], 0, status, "피드 HTTP 오류")
            summary[src["id"]] = 0
            continue
        new = 0
        for e in entries:
            if new >= max_v:
                break
            key = f"yt:{e['vid']}"
            if not e["vid"] or key in seen:
                continue
            policy = src.get("shorts_policy", "titles")
            light = src.get("track") == "light"

            if light:
                # TBPN 등: 제목+설명만 — 자막 추출 절대 금지
                # 🔴 [2026-07-20] 재생목록 소스는 yt-dlp flat 모드라 description이 없다(빈 문자열).
                #   light 트랙과 겹치면 본문 0자짜리 항목이 조용히 쌓인다
                #   (yc-main-function 8건이 전부 그랬다). 설명이 없으면 제목으로 폴백한다.
                body = e["description"] or e["title"]
                save_item(src, key, e["title"], f"https://www.youtube.com/watch?v={e['vid']}",
                          e["published"], body,
                          {"kind": "video-light", "desc_missing": not e["description"]})
            else:
                dur = _probe_duration(e["vid"])
                is_short = 0 <= dur < shorts_cut
                if is_short and policy == "titles":
                    # 클립형 쇼츠: 제목만 — '이 펌이 뭘 밀었나' 편집 선택 신호
                    save_item(src, key, e["title"], f"https://www.youtube.com/watch?v={e['vid']}",
                              e["published"], e["title"], {"kind": "short-title-only"})
                else:
                    text = _subtitles(e["vid"])
                    if not text:
                        # 자막 없음 — 설명으로 폴백하되 health note는 남기지 않음(개별 영상 이슈)
                        text = e["description"]
                    save_item(src, key, e["title"], f"https://www.youtube.com/watch?v={e['vid']}",
                              e["published"], text,
                              {"kind": "short-full" if is_short else "video-transcript",
                               "quote_verbatim": False, "duration_s": dur})
            mark_seen(key)
            seen.add(key)
            new += 1
            polite_delay()
        record_health(src["id"], new, status)
        summary[src["id"]] = new
    return summary


if __name__ == "__main__":
    s = collect()
    print(f"[youtube] {sum(s.values())} new / {len(s)} sources: "
          + ", ".join(f"{k}={v}" for k, v in s.items() if v))
