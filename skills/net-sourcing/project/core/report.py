"""수요일 미팅용 마크다운 다이제스트.

채널별·Tier별 정렬. surface는 상단, borderline은 별도 섹션(보여주되 표시),
cut은 접힌 로그(왜 컷됐는지 감사가능·복원가능).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

CAND = Path(__file__).resolve().parent.parent / "data"


def build(cfg: dict) -> str:
    judged = _latest()
    if not judged:
        return "# 그물망 다이제스트\n\n(후보 없음 — 먼저 collect/screen 실행)"

    surface = [j for j in judged if j["verdict"] == "surface"]
    border = [j for j in judged if j["verdict"] == "borderline"]
    cut = [j for j in judged if j["verdict"] == "cut"]

    lines = [f"# 그물망 다이제스트 — {date.today().isoformat()}",
             f"\n대상 {len(judged)}건 · surface {len(surface)} / borderline {len(border)} / cut {len(cut)}\n"]

    lines.append("## ⭐ 리뷰 롱리스트 (surface)")
    for j in sorted(surface, key=lambda x: _tier_order(x.get("tier_suggestion"))):
        lines.append(_row(j))

    if border:
        lines.append("\n## 🟡 경계 (borderline — 판단해 주세요)")
        for j in border:
            lines.append(_row(j))

    if cut:
        lines.append("\n<details><summary>🗑️ 컷 로그 (%d건)</summary>\n" % len(cut))
        for j in cut:
            c = j["candidate"]
            lines.append(f"- {c.get('name','?')} — {j.get('cut_reason','')}  {c.get('url','')}")
        lines.append("</details>")
    return "\n".join(lines)


def _row(j: dict) -> str:
    c = j["candidate"]
    tier = j.get("tier_suggestion", "?")
    conf = j.get("confidence", "")
    pros = "; ".join(j.get("pros", []))
    cons = "; ".join(j.get("cons", []))
    return (f"\n### [{tier}] {c.get('name','?')} · {c.get('source','')} ({conf})\n"
            f"- {j.get('one_liner') or c.get('one_liner','')}\n"
            f"- 👍 {pros}\n- 👀 {cons}\n- {c.get('url','')}")


def _tier_order(t: str) -> int:
    return {"Catch": 0, "Wait": 1, "Trend": 2}.get(t, 3)


def _latest() -> list[dict]:
    files = sorted(CAND.glob("candidates_*.json"))
    return json.loads(files[-1].read_text(encoding="utf-8")) if files else []
