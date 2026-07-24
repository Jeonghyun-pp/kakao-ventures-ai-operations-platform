"""Pass 0 — 무료 필터. LLM에 넣기 전에 큐를 만든다 (토큰 최대 절감 레버).

- track=light 소스: 경량 큐로 분리 (제목+한줄 — 교차검증 표 없음, 감사 전용)
- Lenny 등 범용 소스: AI 키워드 없으면 스킵 큐로
- 장편 영상이 몰린 주: 큐 크기와 추정 토큰을 보여줘 사람이 스킵을 결정할 수 있게
출력: analysis/<주차>/queue.json {full: [...], light: [...], skipped: [...]}
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from core.common import data_root, week_key

AI_KW = re.compile(r"\b(AI|artificial intelligence|LLM|GPT|agent|chatbot|gen(erative)?\s?AI|"
                   r"claude|gemini|openai|anthropic|copilot|assistant|model)\b", re.I)

# 범용(비AI 전용) 소스 — AI 키워드 필터 적용 대상.
# 🔴 [2026-07-20] 실측으로 확장. Pass 1 시험에서 RevenueCat 아이템이 Google Play 결제
#   기술 문서였고(신호 0건), 수집물 상위가 Shipaton·Gradle Plugin 같은 개발자 공지였다.
#   아래는 sources.yaml 메모에 "키워드 필터 필요"가 명시된 소스들이다:
#     revenuecat  — 개발자·제품 공지가 대부분, 결제 인사이트는 소수
#     technode    — 대부분 EV·파운드리·IPO
#     sixth-tone  — 전체 발행의 85%가 무관
#     geeknews    — 약 65%가 개발·인프라
#     restofworld — 지정학·인프라 기사가 절반
#     semrush     — 블로그 전체가 SEO 콘텐츠, [Study]만 유효
#     the-ken / modern-retail — 정책·브랜드 각도 위주
#   (yc-blog는 2026-07-20 소스에서 제거됐으나 과거 주차 재처리를 위해 남겨둔다)
GENERAL_SOURCES = {"lenny", "yc-blog", "newcomer", "avc", "usv-writing", "spc-blog",
                   "revenuecat", "technode", "sixth-tone", "geeknews", "restofworld",
                   "semrush-study", "the-ken", "modern-retail"}


def run(week: str | None = None) -> dict:
    wk = week or week_key()
    root = data_root() / "raw" / wk
    out = {"full": [], "light": [], "skipped": []}
    if not root.exists():
        print(f"raw/{wk} 없음 — collect 먼저")
        return out
    for p in sorted(root.rglob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        rel = str(p.relative_to(data_root()))
        content = d.get("content", "")
        if d.get("track") == "light" or d.get("kind") in ("short-title-only", "video-light"):
            out["light"].append(rel)
        elif d["source_id"] in GENERAL_SOURCES and not AI_KW.search(
                (d.get("title", "") + " " + content)[:3000]):
            out["skipped"].append({"path": rel, "reason": "AI 키워드 없음 (범용 소스 필터)"})
        elif len(content) < 100:
            out["skipped"].append({"path": rel, "reason": "본문 없음/너무 짧음"})
        else:
            out["full"].append(rel)
    an = data_root() / "analysis" / wk
    an.mkdir(parents=True, exist_ok=True)
    (an / "queue.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    est_chars = sum(len(json.loads((data_root() / f).read_text(encoding="utf-8")).get("content", ""))
                    for f in out["full"])
    print(f"[prefilter {wk}] full={len(out['full'])} light={len(out['light'])} "
          f"skipped={len(out['skipped'])} | full 추정 입력 ~{est_chars // 3 // 1000}k tokens")
    return out


if __name__ == "__main__":
    import sys
    run(sys.argv[1] if len(sys.argv) > 1 else None)
