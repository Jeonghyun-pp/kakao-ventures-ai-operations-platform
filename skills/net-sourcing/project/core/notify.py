"""실행 후 알림 (옵션) — 슬랙/메일로 "이번주 후보 N건 대기" 요약. [E3]"""
from __future__ import annotations

import json
from pathlib import Path

CAND = Path(__file__).resolve().parent.parent / "data"


def summary(cfg: dict) -> None:
    files = sorted(CAND.glob("candidates_*.json"))
    if not files:
        return
    judged = json.loads(files[-1].read_text(encoding="utf-8"))
    surface = sum(1 for j in judged if j["verdict"] == "surface")
    border = sum(1 for j in judged if j["verdict"] == "borderline")
    msg = f"[그물망] 이번주 후보 준비 완료 — surface {surface} / borderline {border}건. 극초기 DB 확인 요망."
    # TODO 슬랙/메일 전송 (bplan Gmail 클라이언트 재활용 or 슬랙 웹훅)
    print(msg)
