"""비플랜 판정 ↔ 사람 판정 **불일치 리포트** — 기준을 깎기 위한 재료.

## 왜 있나

판정 기준은 한 번 만들고 끝나는 게 아니다. **만든 사람의 머릿속 기준과 문서에 적힌 기준이
다르고**, 그 차이는 사람이 판정을 고치는 순간에만 드러난다.

    기계가 Red 라 했는데 사람이 Orange 로 고쳤다
      → 그 한 건이 "룰에 안 적힌 기준"의 증거다

## 그물망 판정 리뷰와 다른 점 — 여기는 정답지가 있다

그물망은 "기계가 쓴 값 ↔ 지금 노션 값"만 비교할 수 있었다. 비플랜은 **판정이 끝난 499건**이
있어서 두 가지를 함께 본다:

    ① 라이브 대조   기계가 판정한 것 ↔ 지금 노션 값 (사람이 고쳤나)
    ② 정답 대조     과거 판정 표본으로 백테스트 (기준선을 넘었나)

②가 있으면 **문서를 고친 뒤 즉시 재측정**할 수 있다. 그물망에서는 고치고 나서 몇 주를
기다려야 효과를 알았고, 그 사이 잘못된 기준으로 348건이 나갔다.

## 조심한 것 — 그물망 리뷰가 틀렸던 지점 두 개

1. **'사람이 지움' 과 '기입 실패' 를 갈랐다.** 판정 파일에는 있는데 노션이 빈칸인 건은
   원인이 둘이다(사람이 거부 / PATCH 실패). 섞으면 일치율 분모가 오염되고 **룰을 잘못된
   방향으로 깎는다.** 근거는 디스크에 있다 — 잡이 배치마다 쓰는 `applied_ids.jsonl`.
2. **사람이 볼 수 없는 값은 일치율에서 뺀다.** 그물망에서는 Cut 이 사람 뷰에 안 떠서
   "동의 99%" 가 나왔는데 그중 87.7% 는 사람이 본 적조차 없었다. 여기서는 To do 가
   `Newly Added` 로 남아 아직 사람 손을 안 탄 건을 분모에서 뺀다.

## 쓰는 법
    python tools/bplan_review.py                 # 최근 14일 불일치
    python tools/bplan_review.py --days 30
    python tools/bplan_review.py --backtest 40   # 정답지 재측정까지

읽기 전용 — 노션에 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(_REPO / "kv-core"))

from kv_core import paths, secrets  # noqa: E402

SKILL = "mail-harness"
PASS = {"Orange", "Green"}


def _machine_verdicts(days: int) -> dict[str, dict]:
    """기계가 내린 판정 — 잡이 남긴 판정 원장에서. page_id → {score, reason, day}.

    노션엔 **최종값만** 남는다. 사람이 고치면 기계가 뭐라고 했는지가 사라지므로,
    잡이 배치마다 원장을 쓴다(`bplan_verdicts_<날짜>_<job_id>_<NN>.json`).
    날짜 형식이 아닌 파일은 **통과가 아니라 배제**한다 — 문자열 비교로 두면
    `..._backup.json` 같은 이름이 항상 통과하고 sorted 에서 뒤에 와 실판정을 덮어쓴다.
    """
    import re
    out: dict[str, dict] = {}
    d = paths.data(SKILL, create=False)
    if not d.exists():
        return out
    cutoff = date.today() - timedelta(days=days)
    skipped = []
    for f in sorted(d.glob("bplan_verdicts_*.json")):
        stamp = f.stem.replace("bplan_verdicts_", "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp):
            skipped.append(f.name)
            continue
        try:
            if date.fromisoformat(stamp) < cutoff:
                continue
            rows = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError, json.JSONDecodeError) as e:
            print(f"⚠️  판정 원장 이상 {f.name} — 건너뜀 ({e!r})")
            continue
        for v in rows if isinstance(rows, list) else []:
            pid = str(v.get("page_id") or "")
            if pid:
                out[pid] = {"score": v.get("score"), "reason": v.get("reason", ""), "day": stamp}
    if skipped:
        print(f"⚠️  날짜 형식이 아닌 원장 {len(skipped)}건 제외 — {', '.join(skipped[:4])}")
    return out


def _live(token: str, db: str) -> dict[str, dict]:
    """노션 라이브 — DB 한 번 스캔. **단건 GET 폭주 금지**(그물망에서 5,600콜을 쳤다)."""
    import requests
    out, payload = {}, {"page_size": 100}
    while True:
        r = requests.post(f"https://api.notion.com/v1/databases/{db}/query",
                          headers={"Authorization": f"Bearer {token}",
                                   "Notion-Version": "2022-06-28",
                                   "Content-Type": "application/json"},
                          json=payload, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"노션 조회 실패 {r.status_code} {r.text[:200]}")
        d = r.json()
        for pg in d.get("results", []):
            pr = pg["properties"]

            def sel(k):
                return ((pr.get(k) or {}).get("select") or {}).get("name")

            title = next((k for k, v in pr.items() if (v or {}).get("type") == "title"), None)
            out[pg["id"]] = {
                "score": sel("점수"), "todo": sel("To do"),
                "name": "".join(x.get("plain_text", "")
                                for x in (pr.get(title) or {}).get("title") or []),
                "reason": "".join(x.get("plain_text", "")
                                  for x in (pr.get("포워드 이유") or {}).get("rich_text") or []),
            }
        if not d.get("has_more"):
            break
        payload["start_cursor"] = d["next_cursor"]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="비플랜 판정 ↔ 사람 판정 불일치 (읽기 전용)")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--backtest", type=int, default=0, help="정답지 재측정 표본 크기(0=생략)")
    args = ap.parse_args(argv)

    secrets.load()
    token, db = os.environ.get("NOTION_TOKEN"), os.environ.get("NOTION_DATABASE_ID")
    if not token or not db:
        print("🔴 NOTION_TOKEN / NOTION_DATABASE_ID 없음")
        return 1

    machine = _machine_verdicts(args.days)
    live = _live(token, db)
    print(f"· 기계 판정 {len(machine)}건 (최근 {args.days}일) · 노션 {len(live)}행")

    # ── ① 라이브 대조 ──────────────────────────────────────────
    agree, changed, untouched, gone = [], [], [], []
    for pid, m in machine.items():
        cur = live.get(pid)
        if cur is None:
            gone.append(pid)
            continue
        # 사람이 아직 안 본 건(To do 가 Newly Added)은 분모에서 뺀다 —
        # "동의"가 아니라 "아직 안 봄"이다. 그물망에서 이걸 섞어 일치율이 거짓말했다.
        if cur["todo"] in (None, "Newly Added"):
            untouched.append((pid, cur))
        elif cur["score"] == m.get("score") or m.get("score") is None:
            agree.append((pid, m, cur))
        else:
            changed.append((pid, m, cur))

    seen = len(agree) + len(changed)
    print()
    if seen:
        print(f"═══ 사람이 본 {seen}건 중 일치 {len(agree)} · 수정 {len(changed)} "
              f"({len(agree) / seen * 100:.0f}%)")
    else:
        print("═══ 사람이 손댄 건 0 — 아직 대조할 게 없다")
    if untouched:
        print(f"    아직 안 본 {len(untouched)}건은 분모 밖 (To do = Newly Added)")
    if gone:
        print(f"    노션에서 사라진 {len(gone)}건")

    if changed:
        print()
        print("── 사람이 고친 것 (룰에 안 적힌 기준의 증거) ──")
        flow = Counter(f"{m.get('score')}→{c['score']}" for _, m, c in changed)
        for k, v in flow.most_common():
            print(f"   {k:20} {v}건")
        print()
        for pid, m, c in changed[:15]:
            print(f"  · [{m.get('score')}→{c['score']}] {c['name'][:30]}")
            if m.get("reason"):
                print(f"      기계 사유: {m['reason'][:110]}")
        print()
        print("  → 같은 방향의 수정이 반복되면 그건 판정 실수가 아니라 **룰의 구멍**이다.")
        print("     references/bplan-criteria.md 를 고치고 --backtest 로 즉시 재측정하라.")

    # ── 현재 분포: 2-상태 퇴화 감시 (GD-1) ──────────────────────
    scored = [v for v in live.values() if v["score"] and v["score"] != "?"]
    dist = Counter(v["score"] for v in scored)
    p = sum(dist[k] for k in PASS) / len(scored) * 100 if scored else 0
    print()
    print(f"── 전체 분포 {dict(dist)} · 통과 {p:.1f}% (기준선 7%)")
    if p == 0:
        print("   🔴 통과 0% — 2-상태 필터로 퇴화했다(GD-1)")

    # ── ② 정답지 재측정 ────────────────────────────────────────
    if args.backtest:
        print()
        print("── 정답지 재측정 (문서를 고쳤으면 여기서 효과가 바로 보인다) ──")
        from tools import bplan_backtest as BT
        raw = BT._rows(token, db)
        sample = BT.build_sample(raw, args.backtest)
        verdicts = BT.judge(sample)
        print()
        print(BT.report(sample, verdicts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
