"""judge 판정 ↔ 사람 판정 **불일치 리포트** — 판단 기준을 계속 깎기 위한 재료.

## 왜 있나

judge 는 한 번 만들고 끝나는 것이 아니다. 기준은 시장이 변하면 따라 변해야 하고(2021년
라벨과 2026년 라벨의 판단 축이 다르다는 것을 극초기 DB 831건에서 실측했다), 무엇보다
**만든 사람의 머릿속 기준과 문서에 적힌 기준이 다르다**. 그 차이는 사람이 판정을 고치는
순간에만 드러난다.

    judge 가 Catch 라 했는데 사람이 Trend 로 고쳤다
      → 그 한 건이 "룰에 안 적힌 기준"의 증거다

이 도구는 그 증거를 모은다. **판정을 고치지 않는다** — 읽고 비교해서 보여줄 뿐이다.

## 어디서 진실을 가져오나

  judge 의 판정   `$KV_DATA/net-sourcing/judge_verdicts_*.json` (judge_job 이 배치마다 남긴다)
  현재 판정        노션 라이브 값

둘을 page_id 로 맞춰 다르면 불일치다. **AI Reason 유무로 사람/기계를 가르지 않는다** —
judge 가 쓴 AI Reason 은 사람이 Tier 만 고쳐도 그대로 남기 때문이다(그게 이 도구가 필요한 이유).

## 쓰는 법
    python tools/judge_review.py            # 최근 14일 불일치
    python tools/judge_review.py --days 30
    python tools/judge_review.py --json out.json

읽기 전용 — 노션에 쓰지 않는다.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(PROJ.parents[2] / "kv-core"))

from kv_core import paths, secrets  # noqa: E402

SKILL = "net-sourcing"


def _verdict_files(days: int) -> list[Path]:
    d = paths.data(SKILL, create=False)
    if not d.exists():
        return []
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    out = []
    for p in sorted(d.glob("judge_verdicts_*.json")):
        # 파일명: judge_verdicts_YYYY-MM-DD_NN.json
        stamp = p.stem.replace("judge_verdicts_", "")[:10]
        if stamp >= cutoff:
            out.append(p)
    return out


def _judged(days: int) -> dict[str, dict]:
    """page_id → judge 가 내린 판정(가장 최근 것)."""
    out: dict[str, dict] = {}
    for p in _verdict_files(days):
        try:
            rows = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️  판정 파일 파손 {p.name} — 건너뜀 ({e!r})")
            continue
        stamp = p.stem.replace("judge_verdicts_", "")[:10]
        for r in rows if isinstance(rows, list) else []:
            pid = str(r.get("page_id") or "")
            if pid:
                out[pid] = {"verdict": r.get("verdict"), "reason": r.get("reason", ""),
                            "one_liner": r.get("one_liner", ""), "judged_on": stamp}
    return out


def _live(page_ids: list[str], token: str) -> dict[str, dict]:
    """노션 라이브 값 — page 단건 조회(대상만 읽는다)."""
    import requests
    from core import notion
    H = {"Authorization": f"Bearer {token}", "Notion-Version": "2022-06-28",
         "Content-Type": "application/json"}
    out: dict[str, dict] = {}
    for pid in page_ids:
        try:
            r = requests.get(f"{notion.API}/pages/{pid}", headers=H, timeout=30)
        except Exception as e:                    # noqa: BLE001  한 건 실패가 리포트를 죽이지 않는다
            print(f"⚠️  조회 실패 {pid[:8]}: {e!r}")
            continue
        if r.status_code != 200:
            print(f"⚠️  조회 실패 {pid[:8]}: {r.status_code}")
            continue
        pr = (r.json().get("properties") or {})

        def val(k):
            v = pr.get(k) or {}
            t = v.get("type")
            if t in ("select", "status"):
                return ((v.get(t)) or {}).get("name")
            if t in ("rich_text", "title"):
                return "".join(x.get("plain_text", "") for x in v.get(t) or [])
            return None

        # 제목 칸 이름은 DB 마다 다르다(법인(서비스)·Name·이름…). **타입으로 찾는다** —
        # 이름을 추측해 나열하면 DB 가 하나 늘 때마다 조용히 빈칸이 된다(스키마-온-리드).
        title = next((k for k, v in pr.items() if (v or {}).get("type") == "title"), None)
        out[pid] = {"verdict": val("Tier") or val("판정"),
                    "name": (val(title) if title else "") or "",
                    "ai_reason": val("AI Reason") or ""}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="judge ↔ 사람 판정 불일치 리포트 (읽기 전용)")
    ap.add_argument("--days", type=int, default=14, help="최근 N일 판정분 (기본 14)")
    ap.add_argument("--json", default="", help="결과를 JSON 으로 저장할 경로")
    args = ap.parse_args(argv)

    secrets.load()
    from core import notion
    token = notion._token()
    if not token:
        print("🔴 NOTION_TOKEN 없음")
        return 1

    judged = _judged(args.days)
    if not judged:
        print(f"· 최근 {args.days}일 judge 판정 기록 없음 — 아직 돌지 않았거나 기간을 늘려야 한다")
        return 0
    print(f"· judge 판정 {len(judged)}건 (최근 {args.days}일)")

    live = _live(list(judged), token)
    print(f"· 노션 라이브 조회 {len(live)}건")

    agree, changed, cleared = [], [], []
    for pid, j in judged.items():
        cur = live.get(pid)
        if cur is None:
            continue
        now = cur["verdict"]
        if not now:
            cleared.append((pid, j, cur))          # 사람이 지웠다 = 판정 자체를 거부
        elif now == j["verdict"]:
            agree.append((pid, j, cur))
        else:
            changed.append((pid, j, cur))

    n = len(agree) + len(changed) + len(cleared)
    if not n:
        print("· 대조 가능한 건 0")
        return 0

    print()
    print(f"═══ 일치율 {100 * len(agree) // n}%  (동의 {len(agree)} · 수정 {len(changed)} · 삭제 {len(cleared)})")
    print()

    if changed:
        print("── 사람이 고친 것 (룰에 안 적힌 기준의 증거) ──")
        flow = Counter(f"{j['verdict']}→{c['verdict']}" for _, j, c in changed)
        for k, v in flow.most_common():
            print(f"   {k:16} {v}건")
        print()
        for pid, j, c in changed[:15]:
            print(f"  · [{j['verdict']}→{c['verdict']}] {c['name'][:28]}")
            print(f"      judge 사유: {j['reason'][:110]}")
    if cleared:
        print()
        print("── 사람이 판정을 지운 것 (판정 자체를 거부) ──")
        for pid, j, c in cleared[:10]:
            print(f"  · [{j['verdict']}→(빈칸)] {c['name'][:28]} — {j['reason'][:90]}")

    print()
    print("→ 위 '고친 것'을 references/judge-criteria.md 에 반영한다.")
    print("  ⚠️ '삭제'는 사람이 지운 것일 수도, apply 가 실패해 애초에 안 써진 것일 수도 있다")
    print("     (dry 파일럿 기록이 섞여 있으면 전량 삭제로 보인다). 판정 파일 출처를 먼저 확인하라.")
    print("  같은 방향의 수정이 반복되면 그건 룰의 구멍이지 판정의 실수가 아니다.")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "days": args.days, "agree": len(agree), "changed": len(changed),
             "cleared": len(cleared),
             "rows": [{"page_id": p, "judge": j, "current": c}
                      for p, j, c in changed + cleared]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  저장: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
