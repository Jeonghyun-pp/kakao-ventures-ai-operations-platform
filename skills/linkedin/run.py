"""링크드인 스킬 — 진입점 셋.

    collect  월 07:30  6축 중 차례인 것 수집 → 판정 → 전량 노션 착지
    land     매일 09:00  (수집일 외에는 할 일 없음 — collect 가 전량 착지한다)
    card     매일 10:00  🔴 노션에서 Tier 를 다시 읽어, 아직 안 나간 사람 좋은 순 5명

앱스토어와 구조가 같다. 다른 건 수집 주기(매일 vs 월요일)와 판정 어휘(2단 vs 4단)뿐이다.

## 지키는 규율

    착지 먼저, 장부 나중    순서가 반대면 착지 실패분이 "처리됨"으로 남아 영영 안 잡힌다
    전량 착지              심사역이 노션을 직접 열 수 있어야 한다. 슬랙엔 그중 하루 5명
    커서는 장부에          축별 next_page 를 별도 파일로 만들지 않는다
    보고는 장부에서        claude 기억이 아니라 줄 수를 센다
"""
from __future__ import annotations

import argparse
import sys
import uuid
from datetime import date, datetime, timedelta, timezone

from kvlib import ledger, lock
from kv_transport import apify, notion, slack

from . import axes, judge, profile

TASK = "linkedin"
DB = "ch_링크드인"
AXIS_EV = "axis"        # 커서·주기 기록
JUDGED = "judged"
CARDED = "carded"
MEETING = "meeting_candidate"

# 🔴 이 스킬이 쓰는 DB 를 **스킬이 등록한다.**
notion.register_writable(DB, "00000000-0000-0000-0000-8702f7cc7f34")

TIERS = ("Catch", "Trend", "Wait", "Cut")

# 🔴 재고 규칙 — 아직 슬랙에 안 나간 사람이 이만큼 남아 있으면 **더 사지 않는다.**
#    고정 페이지로 정하면 재고가 쌓이는 걸 못 본다. 실제로 첫 실행에 590명을 넣어
#    117일치가 쌓였고, 그 상태에서 또 사는 건 의미가 없었다.
#    페이지 숫자를 감으로 정하는 대신 **재고가 스스로 조절하게** 한다.
BACKLOG_CAP = 100
_ORDER = {t: i for i, t in enumerate(TIERS)}      # 좋은 순


class Degraded(RuntimeError):
    pass


# ---------------------------------------------------------------- 축 상태

def _axis_state(axis: str) -> dict:
    """장부에서 축의 마지막 상태를 접는다. 별도 파일을 만들지 않는다."""
    rows = [r for r in ledger.rows(TASK, ev=AXIS_EV) if r.get("key") == axis]
    return rows[-1] if rows else {}


def _due(axis: str, today: str) -> bool:
    st = _axis_state(axis)
    last = st.get("ran_on")
    if not last:
        return True
    gap = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days
    return gap >= axes.AXES[axis]["every_days"]


def _next_page(axis: str) -> int:
    if axes.AXES[axis]["rewind"]:
        return 1              # 전량 재구매 축은 매번 1페이지부터
    return int(_axis_state(axis).get("next_page") or 1)


# ---------------------------------------------------------------- collect

def collect(*, only_axis: str | None = None, pages: int | None = None,
            dry_run: bool = False) -> int:
    run_id = f"{date.today():%Y-%m-%d}-{uuid.uuid4().hex[:6]}"
    today = ledger.today()
    print(f"▶ collect run={run_id}")

    backlog = _backlog()
    if not only_axis and backlog >= BACKLOG_CAP:
        print(f"· 아직 안 나간 사람이 {backlog}명 (임계 {BACKLOG_CAP}) — 더 사지 않는다")
        print(f"  하루 5명이면 {backlog // 5}일치다. 재고가 줄면 그때 산다")
        return 0

    targets = [only_axis] if only_axis else [a for a in axes.AXES if _due(a, today)]
    if not targets:
        print("· 이번 주 차례인 축이 없다")
        return 0

    plan = []
    for a in targets:
        n = pages if pages is not None else axes.AXES[a]["pages"]
        plan.append((a, n, _next_page(a)))
    cost = sum(n for _, n, _ in plan) * axes.USD_PER_PAGE
    print(f"· 축 {len(plan)}개 · {sum(n for _, n, _ in plan)}페이지 · 예상 ${cost:.2f}")
    for a, n, sp in plan:
        print(f"    {a:22} {n}p  (p{sp}부터)")
    if dry_run:
        print("  (dry-run — 돈을 쓰지 않았다)")
        return 0

    # 🔴 키 하나로 세면 안 된다 — URN 으로 온 사람과 URL 로 저장된 사람이 안 만난다
    seen = ledger.all_keys(TASK, ledger.LANDED)
    print(f"· 장부에 이미 있는 키 {len(seen)}개")

    fresh: dict[str, dict] = {}
    bought = 0
    unknowns: list[tuple[str, str]] = []
    for a, n, start in plan:
        for i in range(n):
            f = apify.run_actor(axes.SLUG, axes.payload(a, start + i),
                                skill=TASK, est_usd=axes.USD_PER_PAGE)
            if not f.is_measured:
                unknowns.append((f"{a}/p{start+i}", f.reason))
                break                       # 이 축은 중단, 다음 축으로
            items = f.require()
            bought += len(items)
            for it in items:
                ks = profile.match_keys(it)
                if not ks or any(x in seen for x in ks) or any(x in fresh for x in ks):
                    continue
                fresh[ks[0]] = {**it, "_axis": a, "_keys": ks}
                seen.update(ks)          # 같은 실행 안에서도 두 번 안 잡히게
        # 커서 전진 — 착지 성공 여부와 무관하게 "여기까지 샀다"는 사실이다
        ledger.append(TASK, a, AXIS_EV, ran_on=today,
                      next_page=(1 if axes.AXES[a]["rewind"] else start + n), run=run_id)

    if unknowns:
        print(f"🔴 미상 {len(unknowns)}건")
        for t, why in unknowns[:5]:
            print(f"    {t}: {why[:80]}")
    print(f"· 산 사람 {bought}명 · 처음 보는 사람 {len(fresh)}명 (오늘 지출 ${apify.spent_today():.2f})")
    if not fresh:
        _report()
        return 2 if unknowns else 0

    # ---- 판정 (배치 1회)
    cards = [_summary(v) for v in fresh.values()]
    verdicts = judge.judge_people(cards)
    if not verdicts.is_measured:
        print(f"🔴 판정 실패: {verdicts.reason} — 산 사람은 아카이브에 있으니 다시 판정할 수 있다")
        return 1
    vmap = {v["key"]: v for v in verdicts.require()}
    # 🔴 판정을 못 받은 사람을 **조용히 버리지 않는다.** 돈 주고 산 사람이다.
    unjudged = [k for k in fresh if k not in vmap]
    print(f"· 판정 {len(vmap)}/{len(fresh)}")
    if unjudged:
        print(f"🔴 판정 못 받은 사람 {len(unjudged)}명 — 착지하지 않는다. "
              f"원본은 아카이브에 있으니 다시 판정할 수 있다")
        ledger.append(TASK, "-", "unjudged", n=len(unjudged), keys=unjudged[:50], run=run_id)

    # ---- 착지 → 장부
    landed = failed = 0
    for k, item in fresh.items():
        v = vmap.get(k)
        if not v:
            continue        # 위에서 시끄럽게 셌다
        res = notion.create(DB, _props(item, v, run_id))
        if not res.is_measured:
            failed += 1
            print(f"  ✗ {k} 착지 실패: {res.reason}")
            continue
        page_id = res.require()
        ledger.append(TASK, k, ledger.LANDED, page_id=page_id, run=run_id,
                      keys=item.get("_keys") or [k],      # 별칭 전부 — 다음에 어느 쪽으로 와도 잡힌다
                      axis=item["_axis"], tier=v["tier"], name=_summary(item)["name"])
        if v["tier"] == "Catch":
            ledger.append(TASK, k, MEETING, page_id=page_id, axis=item["_axis"])
        landed += 1

    print(f"· 착지 {landed} · 실패 {failed}")
    _report()
    if unknowns or failed or unjudged:
        raise Degraded(f"미상 {len(unknowns)} · 착지실패 {failed} · 판정누락 {len(unjudged)}")
    return 0


def _summary(item: dict) -> dict:
    s = profile.summarize(item)
    s.update({
        "axis": item.get("_axis", ""),
        "tenure": profile.tenure_months(item),
        "founding": profile.founding_months(item),
        "gap": profile.gap_months(item),
        "ft_gap": profile.fulltime_gap_months(item),
        "kr": profile.korea_signals(item),
        "company_url": profile.company_url(item),
        "company": " · ".join(dict.fromkeys(p["company"] for p in s["positions"] if p["company"])),
        "title": " · ".join(dict.fromkeys(p["title"] for p in s["positions"] if p["title"])),
    })
    return s


def _props(item: dict, v: dict, run_id: str) -> dict:
    s = _summary(item)
    iso = date.today().isocalendar()
    return {
        # 🔴 제목은 **사람 이름**이다. 링크드인은 사람을 소싱하는 채널이고,
        #    스텔스 축에서 오는 사람은 회사명이 "Stealth Mode Startup" 같은 것이라
        #    제목으로 쓰면 목록에서 서로 구별이 안 된다.
        #    회사는 One Liner 에 직함과 함께 싣는다.
        "서비스명": s["name"] or s["key"],
        "LinkedIn URL": s["url"] or None,
        "Author Username": s["public_id"],
        "Author Display Name": s["name"],
        "Author Headline": s["headline"],
        "One Liner": " · ".join(x for x in (s["company"], s["title"]) if x),
        "Raw Text": " · ".join(x for x in (s["headline"], s["company"], s["about"]) if x),
        "Tier": v["tier"],
        "AI Reason": v["reason"],
        "Axis": [s["axis"]] if s["axis"] in notion.allowed_options(
            notion.writable_id(DB), "Axis") else [],
        "Tenure Months": s["tenure"],
        "창업 개월": s["founding"],
        "공백 개월": s["gap"],
        # 🔴 본업 공백은 공백 개월과 **다른 값**이다. 파트타임 자문만 걸어둔 사람은
        #    공백 0 인데 본업 공백 6 이 나온다 — EIR 처럼 창업 준비 중인 사람이 그 모양이다
        "본업 공백": s["ft_gap"],
        "Korea Signal": [x for x in s["kr"] if x in notion.allowed_options(
            notion.writable_id(DB), "Korea Signal")],
        "Homepage": s["company_url"] or None,      # 노션 `회사` formula 가 이걸 파싱한다
        "Post URL": s["url"] or None,
        "Sourcing Method": "keyword_search",
        "Status": "New",
        "Match Reason": f"{s['axis']} · 재직 {s['tenure']}개월 · 창업 {s['founding']}개월",
        "Captured At": ledger.today(),
        "Run ID": run_id,
        "주차": f"{iso.year}-W{iso.week:02d}",
    }


# ---------------------------------------------------------------- card

def card(*, n: int = 5, dry_run: bool = False) -> int:
    """🔴 노션에서 Tier 를 다시 읽는다 — 사람이 고친 게 여기서 반영된다."""
    landed = {r["key"]: r for r in ledger.rows(TASK, ev=ledger.LANDED)}
    already = ledger.keys(TASK, CARDED)
    todo = {k: r for k, r in landed.items() if k not in already}
    if not todo:
        print("· 아직 안 나간 사람이 없다")
        return 0

    f = notion.query(notion.writable_id(DB),
                     ["LinkedIn URL", "Tier", "서비스명", "One Liner", "Author Headline"])
    if not f.is_measured:
        print(f"🔴 노션을 못 읽었다: {f.reason}")
        return 1
    by_page = {r["_page_id"]: r for r in f.require()}

    pool = []
    for k, r in todo.items():
        live = by_page.get(r.get("page_id"))
        if not live:
            continue
        tier = live.get("Tier")
        if tier in ("Catch", "Trend"):        # Wait·Cut 은 안 올린다
            pool.append((_ORDER.get(tier, 9), k, live))
    pool.sort()
    pick = pool[:n]
    print(f"▶ card — 대기 {len(todo)} · 올릴 수 있는 것 {len(pool)} · 오늘 {len(pick)}")
    if not pick:
        return 0

    lines = [f"*오늘의 링크드인 {len(pick)}명*"]
    for _, k, live in pick:
        # 제목이 사람 이름, One Liner 가 "회사 · 직함" 이다
        lines.append(
            f"\n• *{live.get('서비스명')}*  `{live.get('Tier')}`\n"
            f"  {(live.get('One Liner') or '')[:80]}\n"
            f"  {(live.get('Author Headline') or '')[:100]}\n"
            f"  <{live.get('LinkedIn URL')}|링크드인> · "
            f"<https://www.notion.so/{str(live['_page_id']).replace('-', '')}|노션>"
        )
    ok = slack.send_text("\n".join(lines), dry_run=dry_run)
    if ok and not dry_run:
        for _, k, _live in pick:
            ledger.append(TASK, k, CARDED)
    return 0 if ok or dry_run else 1


def _backlog() -> int:
    """아직 슬랙에 안 나간 **사람** 수.

    🔴 seed 로 넣은 옛 노션 행은 빼야 한다. 그건 과거 데이터지 나갈 후보가 아니다.
       (실측: 안 빼면 1,819명으로 잡혀 "363일치"라는 헛소리가 나온다)
    🔴 키가 아니라 사람을 센다 — 한 사람이 URN·URL 두 키를 갖는다.
    """
    carded = ledger.all_keys(TASK, CARDED)
    n = 0
    for r in ledger.rows(TASK, ev=ledger.LANDED):
        if str(r.get("src", "")).startswith("seed"):
            continue
        ks = {str(r.get("key"))} | {str(k) for k in (r.get("keys") or [])}
        if not (ks & carded):
            n += 1
    return n


def _report() -> None:
    s = ledger.summary(TASK)
    print(f"· 장부: 오늘 {s['total_today']}줄 {s['counts']} · 총 {s['ledger_lines']}줄")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="linkedin")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--axis", choices=list(axes.AXES)); c.add_argument("--pages", type=int)
    c.add_argument("--dry-run", action="store_true")
    d = sub.add_parser("card"); d.add_argument("-n", type=int, default=5)
    d.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    a = p.parse_args(argv)
    try:
        if a.cmd == "collect":
            with lock.hold("linkedin-collect"):
                return collect(only_axis=a.axis, pages=a.pages, dry_run=a.dry_run)
        if a.cmd == "card":
            return card(n=a.n, dry_run=a.dry_run)
        if a.cmd == "status":
            print(ledger.summary(TASK))
            print(f"  재고(아직 안 나간 사람) {_backlog()}명 / 임계 {BACKLOG_CAP}")
            for ax in axes.AXES:
                st = _axis_state(ax)
                print(f"  {ax:22} 마지막 {st.get('ran_on','-')} · 다음 p{_next_page(ax)}")
            return 0
    except lock.Busy as e:
        print(f"🔴 {e}")
        return 3
    except Degraded as e:
        print(f"🔴 온전하지 않다: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
