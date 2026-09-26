"""문서 모델 + 서식 프로필 → hwp. 순서는 8월 KIF 빌드(제출됨)와 9월 LP 빌드(사람이 서식 승인)에서 굳은 그대로:
  서식 복사 → 본문 비우기 → op 한 벌(rhwp 전체 직렬화) → 표 글꼴·머리행 → 셀 줄나눔 분할 → 불렛 내어쓰기 → 바이트 패치 → 검사 셋.

서식 프로필(profile.json)은 사람이 손질하는 눈금이다: 서식 파일에서 배운 것(글꼴·머리행 색·셀 테두리·쪽)과 constraints 의 기본값(크기 사다리·불렛)을 합친다.
✅ 시점 제출본을 실측한 값이 다음 공고의 기본값이 된다(run._done).
"""
from __future__ import annotations

import json
import re
import shutil
import struct
from pathlib import Path

from kvlib import constraints
from kv_transport import hwp

from .convert import plain

BOLD = re.compile(r"\*\*(.+?)\*\*")


# ---------------------------------------------------------------- 서식 프로필

def learn_profile(template: Path) -> dict:
    """서식 파일 실측 + constraints 기본값. 실측이 없는 값은 기본값을 쓰고 그 사실을 `learned` 에 남긴다."""
    fmt = hwp.learn_format(template)
    bf = hwp.learn_borders(template)
    f = fmt.or_default({}) or {}
    body = f.get("body") or {}
    table = f.get("table") or {}
    p = {
        "template": template.name,
        "font": body.get("fontKo") or constraints.get("fund.font"),
        "font_note": constraints.get("fund.font_note"),
        "sizes": dict(constraints.get("fund.sizes")),
        "line_spacing": body.get("lineSpacing") or 160,
        "header_fill": (table.get("headerFillColor") or "#EFF3FB").lower(),
        "cell_padding": table.get("cellMarginHwp") or 141,
        "bf": (bf.or_default({}) or {}).get("bf") or {},
        "bullets": [dict(b) for b in constraints.get("fund.bullets")],
        "image_width_mm": constraints.get("fund.image_width_mm"),
        "image_max_height_mm": constraints.get("fund.image_max_height_mm"),
        "blank_before_h1": True,
        "learned": {"format": fmt.is_measured, "borders": bf.is_measured and bool((bf.or_default({}) or {}).get("bf")),
                    "tables_used": (bf.or_default({}) or {}).get("tables_used", 0)},
        "preset": f,                                     # audit_format 이 읽는 원본 프리셋(본문 크기만 사다리 값으로 덮는다)
    }
    p["bullet_prefixes"] = [b["glyph"] + b["gap"] for b in p["bullets"]]
    if p["preset"]:
        p["preset"].setdefault("body", {})["fontPtRaw"] = p["sizes"]["TXT"] * 100
    return p


def profile_text(p: dict) -> str:
    """접수 답글에 붙이는 '이 서식으로 읽었다' 표 — 여기서 틀리면 뒤가 다 틀린다(9/9 실측). 사람이 한 번 훑는다."""
    s = p["sizes"]
    return (f"• 글꼴 {p['font']} · 본문 {s['TXT']}pt · 표 {s['TBL']}pt · 줄간격 {p['line_spacing']}%\n"
            f"• 제목 사다리 {s['SEC']}/{s['ITEM']}/{s['H1']}/{s['H2']}/{s['H3']}pt 볼드 · 표 제목 밑줄 가운데\n"
            f"• 표 머리행 {p['header_fill']} · 테두리 가족 {'배움(표 ' + str(p['learned']['tables_used']) + '개)' if p['learned']['borders'] else '못 배움 — rhwp 기본'}\n"
            f"• 불렛 {' / '.join(b['glyph'] + ' ' + str(b['margin_left']) + '·' + str(b['indent']) for b in p['bullets'])}")


# ---------------------------------------------------------------- op 생성 (8월 kb.py 의 어휘)

def _runs(md: str, size: int, font: str, *, bold=False, ul=False) -> tuple[str, list[dict]]:
    runs, i, text = [], 0, ""
    for m in BOLD.finditer(md):
        if m.start() > i:
            runs.append((md[i:m.start()], False))
        runs.append((m.group(1), True)); i = m.end()
    if i < len(md):
        runs.append((md[i:], False))
    if not runs:
        runs = [("", False)]
    out = []
    for x, b in runs:
        text += x
        r = {"text": x, "fontSize": size, "font_family": font}
        if bold or b:
            r["bold"] = True
        if ul:
            r["underline"] = True
        out.append(r)
    return text, out


def _para(md: str, size: int, p: dict, *, bold=False, ul=False, align=None, font=None) -> dict:
    text, runs = _runs(md, size, font or p["font"], bold=bold, ul=ul)
    o = {"type": "append_paragraph", "text": text, "runs": runs, "line_spacing": p["line_spacing"], "margin_left": 0, "indent": 0}
    if align:
        o["align"] = align
    return o


def _bullet(md: str, lv: int, p: dict) -> dict:
    b = p["bullets"][min(lv, len(p["bullets"]) - 1)]
    text, runs = _runs(md, p["sizes"]["TXT"], p["font"])
    glyph = [{"text": b["glyph"], "fontSize": b.get("glyph_pt", p["sizes"]["TXT"]), "font_family": b.get("glyph_font", p["font"])},
             {"text": b["gap"], "fontSize": p["sizes"]["TXT"], "font_family": p["font"]}]
    return {"type": "append_paragraph", "text": b["glyph"] + b["gap"] + text, "runs": glyph + runs, "line_spacing": p["line_spacing"], "margin_left": 0, "indent": 0}


def _table(rows: list[list[str]], hdr: bool, p: dict) -> dict:
    cells = [[plain(c) for c in r] for r in rows]
    width = max(len(r) for r in cells)
    cells = [r + [""] * (width - len(r)) for r in cells]
    o = {"type": "append_table", "rows": cells[1:] if hdr else cells, "header_fill": p["header_fill"], "cell_padding": p["cell_padding"],
         "header_align": "center", "cell_line_spacing": p["line_spacing"], "cell_font_family": p["font"], "cell_font_pt": p["sizes"]["TBL"]}
    if hdr:
        o["headers"] = cells[0]
    else:
        o["no_header"] = True
    return o


def _png_jpeg_size(path: Path) -> tuple[int, int] | None:
    b = path.read_bytes()
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", b[16:24])
    if b[:2] == b"\xff\xd8":                           # JPEG: SOF 마커에서 높이·너비
        i = 2
        while i < len(b) - 9:
            if b[i] != 0xFF:
                i += 1; continue
            marker, ln = b[i + 1], struct.unpack(">H", b[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", b[i + 5:i + 9]); return w, h
            i += 2 + ln
    return None


def _image(path: Path, p: dict) -> dict:
    """가로는 본문 폭, 세로는 원본 비율. 세로가 상한을 넘으면 세로를 고정하고 가로를 줄인다(이관 규칙 #3 — 8월 그림 6장 왜곡 사고)."""
    w_mm, h_max = float(p["image_width_mm"]), float(p["image_max_height_mm"])
    wh = _png_jpeg_size(path)
    if not wh:
        return {"type": "append_image", "path": str(path), "width_cm": w_mm / 10}
    h_mm = w_mm * wh[1] / wh[0]
    if h_mm > h_max:
        w_mm, h_mm = h_max * wh[0] / wh[1], h_max
    return {"type": "append_image", "path": str(path), "width_cm": round(w_mm / 10, 2), "height_cm": round(h_mm / 10, 2)}


def ops_for(items: list[dict], p: dict) -> list[dict]:
    s, out = p["sizes"], []
    for it in items:
        k = it["k"]
        if k == "PB":
            out.append({"type": "append_page_break"})
        elif k == "BLANK":
            out.append({"type": "append_paragraph", "text": "", "line_spacing": p["line_spacing"], "margin_left": 0, "indent": 0})
        elif k in ("SEC", "ITEM", "H1", "H2", "H3", "H4"):
            out.append(_para(it["t"], s[k], p, bold=True))
        elif k == "TXT":
            out.append(_para(it["t"], s["TXT"], p))
        elif k == "BUL":
            out.append(_bullet(it["t"], it.get("lv", 0), p))
        elif k == "TT":
            out.append(_para(it["t"], s["TT"], p, bold=True, ul=True, align="center"))
        elif k == "UNIT":
            out.append(_para(it["t"], s["UNIT"], p, align="right"))
        elif k == "NOTE":
            out.append(_para(it["t"], s["NOTE"], p, font=p.get("font_note") or p["font"]))
        elif k == "TBL":
            out.append(_table(it["rows"], it.get("hdr", True), p))
        elif k == "IMG" and it.get("img"):
            out.append(_image(Path(it["img"]), p))
    return out


# ---------------------------------------------------------------- 빌드

def build(items: list[dict], p: dict, template: Path, out: Path, work: Path) -> dict:
    """반환 = {ok, steps, tables, bullets_styled, bullets_total, warnings, structure, format}. 어느 단계가 왜 멈췄는지 steps 에 전부 남는다."""
    work.mkdir(parents=True, exist_ok=True)
    prof = work / "profile.json"; prof.write_text(json.dumps(p, ensure_ascii=False, indent=1), encoding="utf-8")
    preset = work / "preset.json"; preset.write_text(json.dumps(p.get("preset") or {}, ensure_ascii=False), encoding="utf-8")
    ops = ops_for(items, p)
    (work / "ops.json").write_text(json.dumps(ops, ensure_ascii=False, indent=1), encoding="utf-8")
    r: dict = {"ok": False, "steps": [], "warnings": [], "ops": len(ops)}

    def step(name, got):
        r["steps"].append((name, got.is_measured, got.reason if not got.is_measured else ""))
        return got

    shutil.copyfile(template, out)
    if not step("truncate", hwp.truncate(out)).is_measured:
        return r
    base = int((hwp.inspect(out).or_default({}) or {}).get("paragraphCount") or 1)
    if not step("create", hwp.create(out, ops)).is_measured:
        return r
    ins = step("inspect", hwp.inspect(out))
    if not ins.is_measured:
        return r
    tables = [t for t in ins.require().get("tables", []) if t.get("para", 0) >= base]
    r["tables"] = len(tables)
    font_ops = [{"type": "set_paragraph_spacing", "spacing_before": 0, "spacing_after": 0}] + [o for t in tables for o in (
        {"type": "set_table_font", "section": t["sec"], "para": t["para"], "control": t["ctrl"], "size": p["sizes"]["TBL"], "font_family": p["font"]},
        {"type": "set_table_font", "section": t["sec"], "para": t["para"], "control": t["ctrl"], "only_row": 0, "bold": True, "align": "center"})]
    if font_ops and not step("table_font", hwp.create(out, font_ops, full=False)).is_measured:
        return r
    split = [{"type": "split_cell_paragraph", "section": t["sec"], "para": t["para"], "control": t["ctrl"], "row": c["row"], "col": c["col"], "delimiter": "\n"}
             for t in tables for c in t.get("cells", []) if "\n" in (c.get("text") or "")]
    if split:
        got = step("split_cells", hwp.create(out, split, full=False))
        if not got.is_measured:
            r["warnings"].append(f"셀 줄나눔 분할 실패: {got.reason[:120]}")
    n_paras = int(ins.require().get("paragraphCount") or 0)
    by_index = n_paras == base + len(ops)                      # op 하나 = 문단 하나면 번호로 집는다(같은 글자의 불렛이 둘이면 target 은 첫 것만 잡는다 — 실측 3건)
    r["bullets_by_index"] = by_index
    bul = [(i, o) for i, o in enumerate(ops) if o["type"] == "append_paragraph" and any(o["text"].startswith(b["glyph"] + b["gap"]) for b in p["bullets"])]
    r["bullets_total"] = len(bul)
    seen, ind_ops = set(), []
    for i, o in bul:
        if not by_index:
            if o["text"] in seen:
                continue
            seen.add(o["text"])
        b = next(b for b in p["bullets"] if o["text"].startswith(b["glyph"] + b["gap"]))
        ind_ops.append({"type": "apply_paragraph_style", **({"index": base + i} if by_index else {"target": o["text"]}), "margin_left": b["margin_left"], "indent": b["indent"]})
    if ind_ops:
        got = step("bullet_indent", hwp.create(out, ind_ops, full=False))
        if got.is_measured:
            edits = got.require().get("edits") or []
            r["bullets_styled"] = sum(1 for e in edits if e.get("paragraphs_styled", 1)) if edits else len(ind_ops)
        else:
            r["warnings"].append(f"불렛 내어쓰기 실패: {got.reason[:120]}")
    pt = step("patch", hwp.patch(out, base, prof))
    if not pt.is_measured:
        return r
    r["patch"] = {k: v for k, v in pt.require().items() if k != "status"}
    if r.get("bullets_total") and pt.require().get("lineseg_trimmed", 0) != r["bullets_total"]:
        r["warnings"].append(f"불렛 {r['bullets_total']}개 중 줄캐시 손질 {pt.require().get('lineseg_trimmed')}개 — 나머지는 내어쓰기가 안 살 수 있다")
    st = step("audit_structure", hwp.audit_structure(out))
    r["structure"] = st.or_default({}).get("ok", False) if st.is_measured else False
    r["structure_report"] = st.or_default({}).get("report", "")[-600:] if st.is_measured else st.reason
    af = step("audit_format", hwp.audit_format(out, preset if p.get("preset") else None))
    rows = (af.or_default({}) or {}).get("rows", []) if af.is_measured else []
    r["format"] = [(x["name"], x["got"], x["sev"]) for x in rows if not x.get("ok")]
    r["ok"] = out.exists() and r["structure"]
    return r
