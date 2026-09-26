"""노션 제안서 DB → 문서 모델. 모델은 없다 — 글자는 노션 원문 그대로, 구조만 옮긴다(이관 규칙 #5 '문구는 원문 표기 그대로').

문서 모델 = 항목(item) 목록. 항목 = {"k": 종류, "t": 글자(볼드는 **…**), "lv": 불렛 깊이, "rows": 표 행, "hdr": 머리행 여부, "img": 그림 경로, "src": 어느 페이지}
종류: SEC 대섹션 · ITEM 항목 · H1 가. · H2 1) · H3 (가) · TXT 본문 · BUL 불렛 · TT [표 제목] · UNIT (단위 …) · NOTE 표 주석 · TBL 표 · IMG 그림 · BLANK · PB 쪽나눔

옮기지 않는 것(2026-08 KIF 이관 가이드 §5 + 9/9 실전): 토글과 그 안 전부 · `📁 HWP 삽입 파일:` 줄(그림 지시로만 쓴다) · 삽입 예정 자리표시 · ✅❓[보완필요] 메모 · 콜아웃.
지운 것은 전부 findings 에 남긴다 — 조용히 빠지는 문장이 없게(반-요약 가드레일).
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

IMG_LINE = re.compile(r"📁\s*HWP\s*삽입\s*파일\s*[:：]\s*(.+?)\s*$")
PLACEHOLDER = re.compile(r"(그림|사진|이미지|도식)\s*(삽입|추가)\s*예정|\(미확보[^)]*\)|기재\s*필요\)|작성\s*예정")
MEMO_LINE = re.compile(r"^\s*(✅|❓|❌|💬|⚠️|\[보완필요\]|\[확인\]|TODO)")
TABLE_TITLE = re.compile(r"^\[.+\]$")
UNIT_LINE = re.compile(r"^\(단위\s*[:：]?.*\)$")
NOTE_LINE = re.compile(r"^(\*|※|주\s*\d+\))")
BODY_ITEM = re.compile(r"^\d+-\d+\.")                    # 공고 목차 번호. 이 모양이 아닌 행(PT·실사 제출분·지침)은 본문 밖


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def plain(md: str) -> str:
    """`**볼드**` 마커를 벗긴 글자 — 대조·표시용."""
    return md.replace("**", "")


def _outline(row: dict) -> tuple[str, str, str]:
    """항목명 "1-2. 운용 전략 / 나. 투자·회수 전략" → (대섹션, "1-2. 운용 전략", "나. 투자·회수 전략")."""
    name = nfc(str(row.get("항목명") or "")).strip()
    item, _, sub = (x.strip() for x in name.partition(" / "))
    return nfc(str(row.get("대섹션") or "")).strip(), item, sub


def body_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """본문 행과 밖에 둘 행. 정렬은 항목번호(문자열 두 자리) 순."""
    rows = sorted(rows, key=lambda r: str(r.get("항목번호") or ""))
    inside = [r for r in rows if BODY_ITEM.match(_outline(r)[1])]
    return inside, [r for r in rows if r not in inside]


def convert(rows: list[dict], blocks_of: dict[str, list[dict]], images: Path | None, *, blank_before_h1: bool = True) -> tuple[list[dict], dict]:
    """rows = DB 행(항목번호·항목명·대섹션·_page_id) · blocks_of[page_id] = notion.page_blocks() 결과 · images = 📁 줄이 가리키는 파일이 있는 폴더."""
    items: list[dict] = []
    f = {"skipped": [], "images": [], "images_missing": [], "slash_cells": [], "emdash": [], "outside": [], "unknown": []}
    inside, outside = body_rows(rows)
    f["outside"] = [f"{r.get('항목번호')} {_outline(r)[1] or r.get('항목명')}" for r in outside]
    seen_sec, seen_item = "", ""
    for r in inside:
        sec, item, sub = _outline(r)
        src = str(r.get("항목번호") or "")
        if sec and sec != seen_sec:
            if items:
                items.append({"k": "PB", "src": src})
            items.append({"k": "SEC", "t": sec, "src": src}); seen_sec = sec
        if item and item != seen_item:
            items.append({"k": "ITEM", "t": item, "src": src}); seen_item = item
        page = _page(blocks_of.get(r["_page_id"], []), src, images, f, blank_before_h1)
        first_heading = next((x for x in page if x["k"] in ("H1", "H2", "H3", "H4")), None)
        if sub and not (first_heading and _same(first_heading["t"], sub)):
            page.insert(0, {"k": "H1", "t": sub, "src": src})     # 항목명에만 있는 소제목 — 페이지 안에 없으면 여기서 붙인다
        if blank_before_h1 and page and page[0]["k"] == "H1":
            page.insert(0, {"k": "BLANK", "src": src})
        items.extend(page)
    return items, f


def _same(a: str, b: str) -> bool:
    return re.sub(r"\s+", "", plain(nfc(a))) == re.sub(r"\s+", "", plain(nfc(b)))


def _page(blocks: list[dict], src: str, images: Path | None, f: dict, blank_before_h1: bool) -> list[dict]:
    out: list[dict] = []
    start = next((i + 1 for i, b in enumerate(blocks) if "✏️" in (b.get("text") or "")), 0)     # KIF 식 DB: `## ✏️ 초안` 아래만
    skip_below: int | None = None                                                                  # 토글 아래 전부 건너뜀
    i = start
    while i < len(blocks):
        b = blocks[i]; i += 1
        t, lv, text = b.get("type", ""), int(b.get("level", 0)), nfc(b.get("text") or "")
        if skip_below is not None:
            if lv > skip_below:
                continue
            skip_below = None
        if t == "toggle" or (t.startswith("heading") and b.get("is_toggleable")):
            skip_below = lv; f["skipped"].append(f"{src} 토글 {plain(text)[:40]}"); continue
        if t in ("callout",) or MEMO_LINE.match(plain(text)):
            f["skipped"].append(f"{src} 메모 {plain(text)[:60]}"); continue
        if t == "table":
            rows, i = _table_rows(blocks, i, lv)
            for row in rows:
                for c in row:
                    if " / " in plain(c) and not re.search(r"\d\s*/\s*\d", c):
                        f["slash_cells"].append(f"{src} {plain(c)[:60]}")
            if rows:
                out.append({"k": "TBL", "rows": rows, "hdr": bool(b.get("has_header", True)), "src": src})
            continue
        if t == "image":
            out.append({"k": "IMG", "img": None, "url": b.get("url", ""), "t": b.get("caption", ""), "src": src}); continue
        if t in ("divider", "table_of_contents", "column_list", "column", "breadcrumb", "truncated"):
            if t == "truncated":
                f["unknown"].append(f"{src} {text}")
            continue
        if t not in ("paragraph", "bulleted_list_item", "numbered_list_item", "quote", "to_do", "table_row", "child_page") and not t.startswith("heading_"):
            f["unknown"].append(f"{src} [{t}] {plain(text)[:40]}"); continue
        m = IMG_LINE.search(plain(text))
        if m:
            text = IMG_LINE.sub("", text).rstrip(" :：").rstrip("*").strip()
            p = _image(m.group(1), images)
            (f["images"] if p else f["images_missing"]).append(f"{src} {m.group(1)}")
        if PLACEHOLDER.search(plain(text)):
            f["skipped"].append(f"{src} 자리표시 {plain(text)[:60]}")
            if m and p:
                out.append({"k": "IMG", "img": p, "t": "", "src": src})
            continue
        if "—" in text:
            f["emdash"].append(f"{src} {plain(text)[:50]}")
        if text.strip():
            if t.startswith("heading_"):                                                        # heading_1~4 → H1~H4(가. / 1) / 가) / (1)). 그 아래는 H4
                n = min(int(t[-1]) if t[-1].isdigit() else 4, 4)
                if n == 1 and blank_before_h1 and out and out[-1]["k"] not in ("BLANK", "PB"):
                    out.append({"k": "BLANK", "src": src})
                out.append({"k": f"H{n}", "t": text, "src": src})
            elif t in ("bulleted_list_item", "numbered_list_item", "to_do"):
                out.append({"k": "BUL", "t": text, "lv": lv, "src": src})
            elif TABLE_TITLE.match(plain(text).strip()):
                out.append({"k": "TT", "t": text, "src": src})
            elif UNIT_LINE.match(plain(text).strip()):
                out.append({"k": "UNIT", "t": text, "src": src})
            elif NOTE_LINE.match(plain(text).strip()) and out and out[-1]["k"] in ("TBL", "NOTE"):
                out.append({"k": "NOTE", "t": text, "src": src})
            else:
                out.append({"k": "TXT", "t": text, "lv": lv, "src": src})
        if m and p:
            out.append({"k": "IMG", "img": p, "t": "", "src": src})
    # 불렛 깊이는 페이지의 가장 얕은 불렛을 0 으로
    base = min((x["lv"] for x in out if x["k"] == "BUL"), default=0)
    for x in out:
        if x["k"] == "BUL":
            x["lv"] -= base
    return out


def _table_rows(blocks: list[dict], i: int, lv: int) -> tuple[list[list[str]], int]:
    rows = []
    while i < len(blocks) and blocks[i].get("type") == "table_row":
        rows.append([nfc(c) for c in blocks[i].get("cells", [])]); i += 1
    return rows, i


def _image(ref: str, images: Path | None) -> Path | None:
    """`images_생성/09_조직도.png` → 폴더에서 같은 이름(경로 무시)을 찾는다. 없으면 None — 자리는 비우고 보고한다."""
    if not images or not images.exists():
        return None
    name = nfc(Path(ref.strip()).name)
    hit = next((p for p in images.rglob("*") if nfc(p.name) == name), None)
    return hit
