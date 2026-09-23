"""앱스토어 스킬 — 수집 → 국적 필터 → 노션 착지.

    collect   09:10  25장르 × 4차트 수집 → 6개월 컷 → 국적 필터(규칙+LLM) → 한국 앱만 착지

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

from kvlib import constraints
from kvlib import ledger, lock
from kv_transport import itunes, notion

from . import kr_origin

# 🔴 이 스킬이 쓰는 DB 를 **스킬이 등록한다.** 통로는 명단을 안 들고 있다.
notion.register_writable("ch_앱스토어", "00000000-0000-0000-0000-30deea8edcc5")
notion.register_writable("ch_극초기", "00000000-0000-0000-0000-72b2b8579963")

TASK = "appstore"
DB = "ch_앱스토어"
DB_극초기 = "ch_극초기"

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
    notion_existing = notion.query(notion.writable_id(DB), ["App ID"])
    if notion_existing.is_measured:
        notion_ids = {r["App ID"] for r in notion_existing.require() if r.get("App ID")}
        gap = notion_ids - seen_before
        seen_before |= notion_ids
        print(f"· 기존 앱 {len(seen_before)}개 (장부 {len(seen_before) - len(gap)} + 노션에만 {len(gap)})")
    else:
        print(f"⚠️  노션 기존 앱 조회 실패: {notion_existing.reason} — 장부만으로 중복 판정")
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

    # ---- 국적 필터
    origins: dict[str, tuple[str, str]] = {}
    foreign_by_rule = []
    unknowns_for_llm = []
    for aid in list(fresh):
        d = details[aid]
        verdict, reason = kr_origin.classify(
            d.get("seller_name", ""), d.get("seller_url", ""),
            d.get("name", ""), d.get("description", ""))
        origins[aid] = (verdict, reason)
        if verdict == "foreign":
            foreign_by_rule.append(aid)
        elif verdict == "unknown":
            unknowns_for_llm.append({
                "app_id": aid,
                "seller_name": d.get("seller_name", ""),
                "app_name": d.get("name", ""),
                "description": d.get("description", ""),
            })

    for aid in foreign_by_rule:
        fresh.pop(aid, None)
    print(f"· 국적 필터(규칙): foreign {len(foreign_by_rule)} 제거 · unknown {len(unknowns_for_llm)} → LLM")

    if unknowns_for_llm:
        llm_result = kr_origin.llm_judge_origin(unknowns_for_llm)
        if llm_result.is_measured:
            llm_foreign = 0
            for item in llm_result.require():
                origins[item["app_id"]] = (item["verdict"], item["reason"])
                if item["verdict"] == "foreign":
                    fresh.pop(item["app_id"], None)
                    llm_foreign += 1
            print(f"· 국적 필터(LLM): foreign {llm_foreign} 추가 제거")
        else:
            print(f"  ⚠️  국적 LLM 실패: {llm_result.reason} — unknown 은 그대로 착지")

    health_results = [(v, r) for v, r in origins.values()]
    for warn in kr_origin.check_health(health_results, len(origins)):
        print(f"  ⚠️  {warn}")

    print(f"· 국적 필터 후 후보 {len(fresh)}개")

    if dry_run:
        for aid, v in list(fresh.items())[:10]:
            d = details[aid]
            ov, oreason = origins.get(aid, ("?", ""))
            print(f"    {d['released_at']}  {d['name'][:30]:30} · {ov:7} · {d.get('seller_url','')[:40]}")
        if foreign_by_rule:
            print(f"  제거된 외국 앱 (규칙, 앞 5개):")
            for aid in foreign_by_rule[:5]:
                d = details[aid]
                _, oreason = origins.get(aid, ("", ""))
                print(f"    ✗ {d['name'][:30]:30} · {oreason}")
        print(f"  (dry-run — 착지하지 않았다. 후보 {len(fresh)}개)")
        return 0

    # ---- 착지 → 장부 (순서가 중요하다)
    if limit:
        fresh = dict(list(fresh.items())[:limit])
        print(f"  (--limit {limit} — 앞의 {len(fresh)}개만 착지한다)")
    landed = failed = 0
    for aid, v in fresh.items():
        origin = origins.get(aid, ("unknown", ""))
        props = _props(aid, v, details[aid], run_id, origin=origin)
        res = notion.create(DB, props)
        if not res.is_measured:
            failed += 1
            print(f"  ✗ {aid} 착지 실패: {res.reason}")
            continue
        ledger.append(TASK, aid, ledger.LANDED, page_id=res.require(), run=run_id,
                      name=details[aid]["name"], released=details[aid]["released_at"],
                      origin=origin[0])
        landed += 1

    print(f"· 착지 {landed} · 실패 {failed}")
    _report(run_id, landed=landed, unknowns=len(unknowns), attempts=attempts, failed=failed)

    # 🔴 포팅은 노션 자동화가 한다 (2026-09-23 활성). 체크한 사람이 발굴자로 들어가야 해서
    #    코드는 그 사람을 알 수 없다. 코드 port() 는 수동 명령으로만 남긴다 — 둘 다 돌면 두 줄씩 생긴다.

    if unknowns or failed:
        raise Degraded(f"미상 {len(unknowns)} · 착지실패 {failed}")
    return 0


def _d(s: str) -> date:
    return datetime.strptime(s[:10], "%Y-%m-%d").date()


def _props(aid: str, v: dict, d: dict, run_id: str,
           origin: tuple[str, str] = ("unknown", "")) -> dict:
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
            notion.writable_id(DB), "Genre") else [],
        "Entry Type": entry,
        "Post URL": d.get("url") or f"https://apps.apple.com/kr/app/id{aid}",
        # 한국팀 판별을 정규식으로 하지 않는다 — 개발사 도메인을 그대로 보여주고 사람이 본다
        "Homepage": d.get("seller_url") or None,
        "Artwork": d.get("artwork") or None,
        "Rating": d.get("rating"),
        "Review Count": d.get("review_count"),
        "Match Reason": f"{genre_name} · {chart_label} {v.get('rank')}위 · 국적 {origin[0]}({origin[1][:40]})",
        "Captured At": ledger.today(),   # 로컬(KST) — 사람이 보는 날짜와 맞춘다
        "Run ID": run_id,
        "주차": f"{iso.year}-W{iso.week:02d}",
    }


# ---------------------------------------------------------------- 포팅

_GENRE_MAP = {
    "교육": "교육", "금융": "금융", "식음료": "식음료",
    "비즈니스": "비즈니스", "엔터테인먼트": "엔터테인먼트",
    "지식정보": "지식정보",
}


def port() -> int:
    """앱스토어 DB에서 'DB로 업데이트' 체크된 앱을 극초기 DB로 포팅한다."""
    print("▶ port: 앱스토어 → 극초기 DB")

    checked = notion.query(
        notion.writable_id(DB),
        ["서비스명", "App ID", "One Liner", "Post URL", "Rating", "Genre"],
    )
    if not checked.is_measured:
        print(f"  ⚠️  앱스토어 DB 조회 실패: {checked.reason}")
        return 0

    targets = []
    for row in checked.require():
        targets.append(row)

    if not targets:
        print("· 체크된 앱 0개 — 포팅할 것 없음")
        return 0

    # DB로 업데이트 = true 인 행만 필터 (query 는 전체를 가져오므로)
    # → Notion API query 에 filter 를 써야 하지만, 현재 통로에 filter 지원이 없다.
    #   대신 앱스토어 DB 를 전부 읽은 뒤 SQL 로 필터하는 대신,
    #   MCP 로 이미 확인한 체크 앱 목록을 기준으로 한다.
    #   실제로는 query 결과에서 checkbox 를 읽을 수 없으므로,
    #   별도로 체크 여부를 조회한다.
    checked_with_flag = notion.query(
        notion.writable_id(DB),
        ["서비스명", "App ID", "One Liner", "Post URL", "Rating", "Genre", "DB로 업데이트"],
    )
    if not checked_with_flag.is_measured:
        print(f"  ⚠️  앱스토어 DB 조회 실패: {checked_with_flag.reason}")
        return 0

    targets = [r for r in checked_with_flag.require() if r.get("DB로 업데이트")]

    if not targets:
        print("· DB로 업데이트 체크된 앱 0개 — 포팅할 것 없음")
        return 0
    print(f"· DB로 업데이트 체크 {len(targets)}개")

    existing = notion.query(notion.writable_id(DB_극초기), ["법인(서비스)"])
    if existing.is_measured:
        existing_names = {r["법인(서비스)"] for r in existing.require() if r.get("법인(서비스)")}
    else:
        print(f"  ⚠️  극초기 DB 조회 실패: {existing.reason} — 중복 체크 없이 진행")
        existing_names = set()

    new = [t for t in targets if t.get("서비스명") not in existing_names]
    if not new:
        print(f"· 전부 이미 극초기 DB에 존재 — 포팅할 것 없음")
        return 0
    print(f"· 신규 {len(new)}개 (기존 {len(targets) - len(new)}개 스킵)")

    극초기_allowed_genres = notion.allowed_options(notion.writable_id(DB_극초기), "영역")
    ported = failed = 0
    for row in new:
        genre_list = row.get("Genre") or []
        mapped = [_GENRE_MAP[g] for g in genre_list if g in _GENRE_MAP]
        mapped = [g for g in mapped if g in 극초기_allowed_genres]

        props = {
            "법인(서비스)": row.get("서비스명") or "",
            "한 줄 설명": row.get("One Liner") or "",
            "링크": row.get("Post URL") or "",
            "평점": row.get("Rating"),
            "발굴": "앱스토어",
        }
        if mapped:
            props["영역"] = mapped

        res = notion.create(DB_극초기, props)
        if not res.is_measured:
            failed += 1
            print(f"  ✗ {row.get('서비스명')} 포팅 실패: {res.reason}")
            continue
        ported += 1

    print(f"· 극초기 DB 포팅 완료: {ported}건 성공 · {failed}건 실패")
    return ported


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
    sub.add_parser("port")
    sub.add_parser("status")
    a = p.parse_args(argv)
    try:
        if a.cmd == "collect":
            with lock.hold("appstore-collect"):
                return collect(dry_run=a.dry_run, limit_genres=a.genres, limit=a.limit)
        if a.cmd == "port":
            port()
            return 0
        if a.cmd == "status":
            print(ledger.summary(TASK))
            return 0
    except lock.Busy as e:
        print(f"🔴 {e}")
        return 3
    except Degraded as e:
        print(f"🔴 온전하지 않다: {e}")
        return 2   # 크론이 종료코드만 본다 — "0건 성공"과 가른다
    return 0


if __name__ == "__main__":
    sys.exit(main())
