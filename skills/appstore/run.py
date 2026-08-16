"""앱스토어 스킬 — 진입점 셋.

    collect   07:00  25장르 × 4차트 수집 → 후보 추출 → 노션 착지 → 장부 기록
    pick      08:30  claude 가 후보 중 2개 선정 → 판정=Pass 표시
    card      10:00  🔴 노션에서 판정=Pass 를 **다시 읽어** 슬랙 카드

사람이 09~10시에 노션에서 판정을 고치면 card 가 그걸 반영한다.
별도 승인 화면을 만들지 않는 이유가 이거다 — 노션이 이미 그 화면이다.

## 지키는 규율

    착지 먼저, 장부 나중    순서가 반대면 착지 실패한 것이 "처리됨"으로 남아 영영 안 잡힌다
    측정 부재 = 결손        시도 0 이면 "0건"과 "전멸"을 구분할 수 없다
    degraded → 종료코드 ≠ 0  크론이 종료코드만 본다
    보고는 장부에서          claude 기억이 아니라 줄 수를 센다
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

from kvlib import Fetched, constraints

from . import claude, itunes, ledger, notion, slack

TASK = "appstore"
DB = "ch_앱스토어"

# 25장르 × 무료·유료·매출 + 최신출시 1회(genre 무시)
_GENRES = list(itunes.GENRE_NAMES)
_CHARTS = ("top-free", "top-paid", "top-grossing")


class Degraded(RuntimeError):
    """수집이 온전하지 않다. '0건'과 다른 사건이라 종료코드로 가른다."""


# ---------------------------------------------------------------- collect

def collect(*, dry_run: bool = False, limit_genres: int | None = None,
            limit: int | None = None) -> int:
    itunes.reset_circuit()
    run_id = f"{date.today():%Y-%m-%d}-{uuid.uuid4().hex[:6]}"
    print(f"▶ collect run={run_id}")

    seen_before = ledger.keys(TASK, ledger.LANDED)
    print(f"· 장부에 이미 있는 앱 {len(seen_before)}개")

    genres = _GENRES[:limit_genres] if limit_genres else _GENRES
    targets = [(g, c) for g in genres for c in _CHARTS] + [(None, "new")]

    found: dict[str, dict] = {}   # app_id → 최고순위 정보
    attempts = 0
    unknowns: list[tuple[str, str]] = []
    for genre, chart_name in targets:
        attempts += 1
        f = itunes.chart(genre, chart_name)
        if not f.is_measured:
            unknowns.append((f"{genre or '전체'}/{chart_name}", f.reason))
            continue
        for a in f.require():
            aid = str(a["app_id"])
            if aid in seen_before:
                continue
            prev = found.get(aid)
            if prev is None or a.get("rank", 999) < prev.get("rank", 999):
                found[aid] = {**a, "genre": genre, "chart": chart_name}

    # 🔴 계측 부재 자체가 결손이다. 시도 0 이면 "0건"인지 "전멸"인지 알 수 없다.
    if attempts == 0:
        raise Degraded("시도 0 — 수집을 아예 안 했다")
    if unknowns:
        # 🔴 이유를 부류별로 센다. "8개 실패"만 적으면 왜인지 모르는 채로 지나간다.
        by_why: dict[str, list[str]] = {}
        for tgt, why in unknowns:
            key = why.split(":")[0] + (":" + why.split(":")[1].split(" ")[0] if ":" in why else "")
            by_why.setdefault(key[:40], []).append(tgt)
        print(f"🔴 미상 {len(unknowns)}/{attempts}")
        for why, tgts in by_why.items():
            print(f"    {why} × {len(tgts)}: {tgts[:4]}{'…' if len(tgts) > 4 else ''}")
    print(f"· 처음 보는 앱 {len(found)}개 (시도 {attempts} · 미상 {len(unknowns)})")

    if not found:
        _report(run_id, landed=0, unknowns=len(unknowns), attempts=attempts)
        if unknowns:
            raise Degraded(f"미상 {len(unknowns)}건")
        return 0

    # ---- 상세 보강. 출시일 없는 건 버린다(검증 못 한 것은 안 내보낸다)
    det = itunes.lookup(list(found))
    details = det.require()
    lost = [a for a in found if not details.get(a, {}).get("released_at")]
    ratio = len(lost) / len(found)
    warn = constraints.get("appstore.unknown_release_date_warn_ratio")
    if ratio > warn:
        print(f"🔴 출시일 미상 {len(lost)}/{len(found)} = {ratio:.1%} (>{warn:.0%}) — lookup 장애 의심")
    for aid in lost:
        found.pop(aid, None)

    # ---- 6개월 컷
    cutoff = (datetime.now(timezone.utc) - timedelta(days=constraints.get("appstore.max_age_days"))).date()
    fresh = {aid: v for aid, v in found.items()
             if details[aid]["released_at"] and _d(details[aid]["released_at"]) >= cutoff}
    print(f"· 출시일 미상 {len(lost)} 제외 · 6개월 초과 {len(found) - len(fresh)} 제외 → 후보 {len(fresh)}개")

    if dry_run:
        for aid, v in list(fresh.items())[:10]:
            d = details[aid]
            print(f"    {d['released_at']}  {d['name'][:30]:30} · {d['developer'][:20]:20} · {d.get('seller_url','')[:40]}")
        print(f"  (dry-run — 착지하지 않았다. 후보 {len(fresh)}개)")
        return 0

    # ---- 착지 → 장부 (순서가 중요하다)
    if limit:
        fresh = dict(list(fresh.items())[:limit])
        print(f"  (--limit {limit} — 앞의 {len(fresh)}개만 착지한다)")
    landed = failed = 0
    for aid, v in fresh.items():
        props = _props(aid, v, details[aid], run_id)
        res = notion.create(DB, props)
        if not res.is_measured:
            failed += 1
            print(f"  ✗ {aid} 착지 실패: {res.reason}")
            continue
        ledger.append(TASK, aid, ledger.LANDED, page_id=res.require(), run=run_id,
                      name=details[aid]["name"], released=details[aid]["released_at"])
        landed += 1

    print(f"· 착지 {landed} · 실패 {failed}")
    _report(run_id, landed=landed, unknowns=len(unknowns), attempts=attempts, failed=failed)
    if unknowns or failed:
        raise Degraded(f"미상 {len(unknowns)} · 착지실패 {failed}")
    return 0


def _d(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _props(aid: str, v: dict, d: dict, run_id: str) -> dict:
    genre_name = itunes.GENRE_NAMES.get(v.get("genre"), "전체")
    chart_label = itunes.CHART_LABEL.get(v["chart"], v["chart"])
    entry = "new_release" if v["chart"] == "new" else "chart_new"
    iso = date.today().isocalendar()
    return {
        "서비스명": d["name"] or aid,
        "App ID": aid,
        "Developer": d["developer"],
        "One Liner": d.get("subtitle") or (d.get("description") or "")[:120],
        "Raw Text": d.get("description") or "",
        "Release Date": d["released_at"],
        "Chart": chart_label,
        "Chart Rank": v.get("rank"),
        "Genre": [genre_name] if genre_name in notion.allowed_options(
            notion.WRITABLE[DB], "Genre") else [],
        "Entry Type": entry,
        "Post URL": d.get("url") or f"https://apps.apple.com/kr/app/id{aid}",
        # 한국팀 판별을 정규식으로 하지 않는다 — 개발사 도메인을 그대로 보여주고 사람이 본다
        "Homepage": d.get("seller_url") or None,
        "Artwork": d.get("artwork") or None,
        "Rating": d.get("rating"),
        "Review Count": d.get("review_count"),
        "Match Reason": f"{genre_name} · {chart_label} {v.get('rank')}위",
        "Captured At": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "Run ID": run_id,
        "주차": f"{iso.year}-W{iso.week:02d}",
    }


# ---------------------------------------------------------------- pick

def pick(*, n: int = 2, dry_run: bool = False) -> int:
    """오늘 착지분 중 claude 가 n 개를 고른다."""
    rows = [r for r in ledger.rows(TASK, ev=ledger.LANDED, day=ledger.today()) if r.get("page_id")]
    if not rows:
        print("· 오늘 착지분이 없다 — 고를 것이 없다")
        return 0
    print(f"▶ pick — 오늘 후보 {len(rows)}개 중 {n}개")

    picked = claude.pick_apps(rows, n=n)
    if not picked.is_measured:
        print(f"🔴 선정 실패: {picked.reason}")
        return 1
    chosen = picked.require()
    if dry_run:
        for c in chosen:
            print(f"    {c['key']}  {c['reason'][:80]}")
        return 0

    for c in chosen:
        row = next((r for r in rows if r["key"] == c["key"]), None)
        if not row:
            continue
        res = notion.update(row["page_id"], DB, {"판정": "Pass"})
        if not res.is_measured:
            print(f"  ✗ {c['key']} 표시 실패: {res.reason}")
            continue
        ledger.append(TASK, c["key"], ledger.PICKED, page_id=row["page_id"], reason=c["reason"])
        print(f"  ✓ {c['key']} · {c['reason'][:60]}")

    slack.notify_owner(f"앱스토어 후보 {len(chosen)}개 골라뒀습니다 — 10시 전에 훑어주세요")
    return 0


# ---------------------------------------------------------------- card

def card(*, dry_run: bool = False) -> int:
    """🔴 노션에서 판정=Pass 를 다시 읽는다 — 사람이 고친 게 여기서 반영된다."""
    todays = {r["key"]: r for r in ledger.rows(TASK, ev=ledger.LANDED, day=ledger.today())}
    if not todays:
        print("· 오늘 착지분이 없다")
        return 0
    f = notion.query(notion.WRITABLE[DB], ["App ID", "판정", "서비스명", "Developer", "One Liner", "Homepage"])
    if not f.is_measured:
        print(f"🔴 노션을 못 읽었다: {f.reason}")
        return 1
    passed = [r for r in f.require()
              if r.get("판정") == "Pass" and str(r.get("App ID") or "") in todays]
    print(f"▶ card — 오늘 착지 {len(todays)} 중 Pass {len(passed)}")
    if not passed:
        print("· Pass 가 없다 — 카드를 안 보낸다")
        return 0
    return 0 if slack.send_card(passed, dry_run=dry_run) else 1


# ---------------------------------------------------------------- 보고

def _report(run_id: str, **counts) -> None:
    """🔴 보고는 장부에서 만든다. claude 나 변수 기억이 아니라 **줄 수를 센다.**"""
    s = ledger.summary(TASK)
    print(f"· 장부: 오늘 {s['total_today']}줄 {s['counts']} · 총 {s['ledger_lines']}줄 · 마지막 {s['last_ts']}")


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="appstore")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect"); c.add_argument("--dry-run", action="store_true")
    c.add_argument("--genres", type=int); c.add_argument("--limit", type=int)
    k = sub.add_parser("pick"); k.add_argument("--dry-run", action="store_true"); k.add_argument("-n", type=int, default=2)
    d = sub.add_parser("card"); d.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    a = p.parse_args(argv)
    try:
        if a.cmd == "collect":
            return collect(dry_run=a.dry_run, limit_genres=a.genres, limit=a.limit)
        if a.cmd == "pick":
            return pick(n=a.n, dry_run=a.dry_run)
        if a.cmd == "card":
            return card(dry_run=a.dry_run)
        if a.cmd == "status":
            print(ledger.summary(TASK))
            return 0
    except Degraded as e:
        print(f"🔴 온전하지 않다: {e}")
        return 2   # 크론이 종료코드만 본다 — "0건 성공"과 가른다
    return 0


if __name__ == "__main__":
    sys.exit(main())
