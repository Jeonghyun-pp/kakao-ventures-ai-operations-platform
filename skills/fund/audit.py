"""이관 검수 — 노션 원문이 hwp 에 전부 있는가(문장·셀 1:1), 작업용 문구가 남았는가. 8월 gate.sh(check_text·check_tables)의 규칙을 통로 위로 옮겼다.

대조는 '공백 제거 · NFC · 볼드 마커 제거' 뒤 완전일치/포함이다. 글자 하나 다르면 잡힌다 — 그게 목적이다(심사위원이 원문과 대조한다).
"""
from __future__ import annotations

import re
import unicodedata

from .convert import plain

FORBIDDEN = (("📁", "그림 지시 줄"), ("삽입 예정", "자리표시"), ("추가 예정", "자리표시"), ("(미확보", "자리표시"), ("[보완필요]", "메모"))


def norm(s: str) -> str:
    return re.sub(r"[\s​]+", "", unicodedata.normalize("NFC", plain(s or "")))


def units(items: list[dict]) -> list[tuple[str, str]]:
    """대조 단위 = 글자가 있는 항목 전부(제목·본문·불렛·표 제목·주석). 표는 셀 단위로 따로 본다."""
    return [(it["src"], it["t"]) for it in items if it.get("t") and it["k"] in ("SEC", "ITEM", "H1", "H2", "H3", "H4", "TXT", "BUL", "TT", "UNIT", "NOTE")]


def transfer(items: list[dict], body_text: str, tables: list[dict]) -> dict:
    """body_text = hwp.text() · tables = hwp.inspect()['tables']. 반환 {checked, missing, cells_checked, cells_bad, tables_unmatched, forbidden, ok}."""
    hay = norm(body_text + "\n" + "\n".join(c.get("text") or "" for t in tables for c in t.get("cells", [])))
    us = units(items)
    missing = [(src, plain(t)[:90]) for src, t in us if norm(t) and norm(t) not in hay]
    grids = []
    for t in tables:
        g = [[""] * t["colCount"] for _ in range(t["rowCount"])]
        for c in t.get("cells", []):
            if c["row"] < t["rowCount"] and c["col"] < t["colCount"]:
                g[c["row"]][c["col"]] = c.get("text") or ""
        grids.append(g)
    used, cells_bad, unmatched, cells_checked = set(), [], [], 0
    for it in (x for x in items if x["k"] == "TBL"):
        rows = it["rows"]
        want = (len(rows), max(len(r) for r in rows))
        cand = [i for i, g in enumerate(grids) if (len(g), len(g[0]) if g else 0) == want and i not in used]
        if not cand:
            unmatched.append(f"{it['src']} {want[0]}x{want[1]} 표"); continue
        used.add(cand[0]); g = grids[cand[0]]
        for r, row in enumerate(rows):
            for c, val in enumerate(row):
                cells_checked += 1
                if norm(val) != norm(g[r][c]):
                    cells_bad.append((it["src"], r, c, plain(val)[:40], (g[r][c] or "")[:40]))
    forbidden = [(tok, why, hay_count) for tok, why in FORBIDDEN if (hay_count := (body_text.count(tok) + sum((c.get("text") or "").count(tok) for t in tables for c in t.get("cells", []))))]
    return {"checked": len(us), "missing": missing, "cells_checked": cells_checked, "cells_bad": cells_bad, "tables_unmatched": unmatched,
            "forbidden": forbidden, "ok": not (missing or cells_bad or unmatched or forbidden)}


def coverage(items: list[dict], body_text: str, tables: list[dict]) -> dict:
    """✅ 시점 제출본 대조 — 노션 원문 단위 중 제출본에 남은 비율. 빠진 것 = 'hwp 에서만 고친 문장'(노션에 되돌려 적을 것)."""
    t = transfer(items, body_text, tables)
    n = max(1, t["checked"])
    return {"kept": round((n - len(t["missing"])) / n, 2), "changed_in_hwp": [m[1] for m in t["missing"]][:20],
            "cells_changed": len(t["cells_bad"]), "cells_checked": t["cells_checked"]}
