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


_STAMP = __import__("re").compile(r"\d{4}-\d{2}-\d{2}")


def _stamp_of(p: Path) -> str | None:
    """파일명에서 날짜. 형식이 아니면 None — **통과가 아니다**."""
    s = p.stem.replace("judge_verdicts_", "")[:10]
    return s if _STAMP.fullmatch(s) else None


def _verdict_files(days: int) -> list[Path]:
    """최근 N일 판정 파일. **날짜 형식을 검증한다.**

    [적대리뷰 M3] 예전엔 `stamp >= cutoff` 문자열 비교뿐이라 `judge_verdicts_backup.json`
    이 항상 통과했다(`"backup" >= "2026-07-28"` 은 True — 'b' > '2'). 게다가 `sorted()` 에서
    날짜 파일 **뒤**에 와서 last-wins 로 실판정을 덮어썼다. 이 도구가 print 로 경고하던
    "dry 파일럿 기록이 섞이면 전량 삭제로 보인다"가 정확히 그 경로다 — 경고 말고 막는다.
    """
    d = paths.data(SKILL, create=False)
    if not d.exists():
        return []
    cutoff = date.today() - timedelta(days=days)
    out, skipped = [], []
    for p in sorted(d.glob("judge_verdicts_*.json")):
        stamp = _stamp_of(p)
        if stamp is None:
            skipped.append(p.name)
            continue
        try:
            if date.fromisoformat(stamp) >= cutoff:
                out.append(p)
        except ValueError:
            skipped.append(p.name)
    if skipped:
        print(f"⚠️  날짜 형식이 아닌 판정 파일 {len(skipped)}건 제외 — {', '.join(skipped[:5])}")
    return out


def _applied_ids() -> set[str]:
    """judge 가 **노션에 실제로 기입한** page_id 들.

    [적대리뷰 M4] judge_job 은 판정 파일을 쓴 **다음** apply 한다. 그래서 "판정 파일엔
    있는데 노션엔 안 써진" 행이 구조적으로 생긴다(개별 PATCH 실패·배치 중단). 예전엔
    그게 전부 `cleared`(= 사람이 판정을 거부) 로 집계돼 **일치율 분모**에 들어갔다.
    앞으로 하루 400 PATCH 를 치는데 실패분이 "사람이 거부함"으로 보이면 **룰을 잘못된
    방향으로 깎는다** — W9 에서 정한 "한 메시지에 두 기준의 숫자를 섞지 않는다" 위반이다.
    판별 근거는 이미 디스크에 있다: 배치마다 쓰는 `artifacts/applied_ids.jsonl`.
    """
    out: set[str] = set()
    jobs = paths.data(SKILL, "jobs", create=False)
    if not jobs.exists():
        return out
    for ap in jobs.glob("judge-*/artifacts/applied_ids.jsonl"):
        try:
            out |= {ln.strip() for ln in ap.read_text(encoding="utf-8").splitlines() if ln.strip()}
        except OSError:
            continue
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
        stamp = _stamp_of(p) or "?"
        for r in rows if isinstance(rows, list) else []:
            pid = str(r.get("page_id") or "")
            if pid:
                out[pid] = {"verdict": r.get("verdict"), "reason": r.get("reason", ""),
                            "one_liner": r.get("one_liner", ""), "judged_on": stamp}
    return out


def _row(pr: dict) -> dict:
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
    return {"verdict": val("Tier") or val("판정"),
            "name": (val(title) if title else "") or "",
            "ai_reason": val("AI Reason") or ""}


def _live(page_ids: list[str], token: str, cfg: dict) -> dict[str, dict]:
    """노션 라이브 값.

    [적대리뷰 H4] 예전엔 `requests.get` 로 **page 단건 무지연 순차 조회**였다. 기본
    `--days 14` × 하루 400건이면 ~5,600콜을 3 req/s 한도에 그대로 때린다 → 대부분 429 →
    `⚠️ 조회 실패` 수천 줄 → `대조 가능한 건 0`. "기준을 계속 깎는다"는 규약의 **유일한
    계측기가 실제 규모에서 작동하지 않았다.** `core/notion.py` 는 07-11 사고(415행 무지연
    순차 POST) 뒤에 백오프·페이싱을 넣어뒀는데, 신설 도구가 그걸 우회해 되돌린 것이다.

    그래서 ① DB 를 통째로 한 번 스캔해 page_id 맵을 만들고(1,505행 ≈ 16콜)
    ② 거기 없는 것만 단건 조회하되 `notion._read`(백오프 + 0.34s 페이싱)로 간다.
    """
    from core import judge_cli, notion
    want = set(page_ids)
    out: dict[str, dict] = {}

    dbs = [db for _, db in judge_cli._targets(cfg, "", False)]
    for db in dbs:
        try:
            for page in notion._query_pages(cfg, token, db):
                pid = str(page.get("id") or "").replace("-", "")
                if pid in want or (page.get("id") in want):
                    out[page.get("id") if page.get("id") in want else pid] = _row(
                        page.get("properties") or {})
        except Exception as e:                    # noqa: BLE001  한 DB 실패가 리포트를 죽이지 않는다
            print(f"⚠️  DB 스캔 실패 {db[:8]}: {e!r}")

    missing = [p for p in page_ids if p not in out]
    if missing:
        print(f"· DB 스캔에 없던 {len(missing)}건은 단건 조회(백오프·페이싱 경유)")
    for pid in missing:
        try:
            r = notion._read(f"{notion.API}/pages/{pid}", token)
        except Exception as e:                    # noqa: BLE001
            print(f"⚠️  조회 실패 {pid[:8]}: {e!r}")
            continue
        if r.status_code != 200:
            print(f"⚠️  조회 실패 {pid[:8]}: {r.status_code}")
            continue
        out[pid] = _row(r.json().get("properties") or {})
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

    import yaml
    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    live = _live(list(judged), token, cfg)
    print(f"· 노션 라이브 조회 {len(live)}건")

    applied = _applied_ids()
    agree, changed, cleared, unwritten = [], [], [], []
    for pid, j in judged.items():
        cur = live.get(pid)
        if cur is None:
            continue
        now = cur["verdict"]
        if not now:
            # [적대리뷰 M4] 빈칸의 원인이 둘이다 — 갈라야 일치율이 정직해진다.
            (cleared if pid in applied else unwritten).append((pid, j, cur))
        elif now == j["verdict"]:
            agree.append((pid, j, cur))
        else:
            changed.append((pid, j, cur))

    # 분모는 **사람의 판단이 실제로 개입할 수 있었던 건**만. 애초에 안 써진 건은 제외한다.
    n = len(agree) + len(changed) + len(cleared)
    if not n:
        print(f"· 대조 가능한 건 0 (기입 실패 {len(unwritten)}건은 분모 밖)")
        return 0

    print()
    print(f"═══ 일치율 {100 * len(agree) // n}%  "
          f"(동의 {len(agree)} · 수정 {len(changed)} · 사람이 지움 {len(cleared)})")
    if unwritten:
        print(f"    ※ 기입 실패 {len(unwritten)}건은 분모에서 제외 — "
              "노션에 애초에 안 써졌다(apply 실패·배치 중단). 사람의 거부가 아니다.")
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
    print("  같은 방향의 수정이 반복되면 그건 룰의 구멍이지 판정의 실수가 아니다.")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "days": args.days, "agree": len(agree), "changed": len(changed),
             "cleared": len(cleared), "unwritten": len(unwritten),
             "rows": [{"page_id": p, "judge": j, "current": c}
                      for p, j, c in changed + cleared]},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  저장: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
