"""링크드인 스킬 — collect · card · followup · 보고서. 일정·흐름·규율은 SKILL.md(frontmatter 가 스케줄이다).

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
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

import yaml

from kvlib import Fetched, constraints, judgments, ledger, lock, paths, report, store
from kvlib.gate import Gate
from kv_transport import apify, notion, slack

from . import axes, brief, judge, outreach, profile

TASK = "linkedin"
DB = "ch_링크드인"
AXIS_EV = "axis"        # 커서·주기 기록
CARDED = "carded"
MEETING = "meeting_candidate"
POSTED = "posted"          # 슬랙 스레드 답글로 올림 (사람별 ts)
RESOLVED = "resolved"      # ✅ 를 받아 결론남
OUTREACH = "outreach"      # 콜드 메시지 초안을 스레드에 올림
PROMOTED = "promoted"      # 극초기 DB 로 승격

# 🔴 극초기 DB — 쓰기 등록. **✅ 를 받은 사람만** 올라간다.
#    노션 자동화는 체크 즉시 사람 이름을 `법인(서비스)` 칸에 복사했다(08-18 실측 24건).
#    승격은 행 복사가 아니라 **단위 변환**(사람 → 회사)이라 판단이 필요하다 —
#    claude 가 헤드라인에서 회사명을 찾고, 못 찾으면 정직하게 "(미상)" 으로 둔다.
EARLY = "극초기"
notion.register_writable(EARLY, "00000000-0000-0000-0000-72b2b8579963")

# 🔴 이 스킬이 쓰는 DB 를 **스킬이 등록한다.**
notion.register_writable(DB, "00000000-0000-0000-0000-8702f7cc7f34")
slack.register("net",  "C000AB261A0")       # 그물망 — 봇 초대 필요

# 스위치·채널·상한은 constraints.yaml `linkedin.*` 에 있다 [2026-09-25]. 승격 게이트·카드 티어·재고 규칙의 근거도 그쪽 주석에.
PEOPLE = paths.repo_root() / "people.yaml"                      # 심사역 명부 — 메일과 같은 파일


def _cfg(k: str):
    return constraints.get(f"linkedin.{k}")


# 🔴 밖으로 나가는 다섯(buy·land·card·outreach·promote)은 전부 `Gate.out` 을 지난다 (constraints.yaml linkedin.outbound).
#    `slack` 단계에서 실제로 나가는 종류 — **허용 목록**. 카드·초안은 슬랙 글이고, 구매·착지·승격은 아니다.
SLACK_KINDS = ("card", "outreach")


def db_path():
    return paths.data("linkedin", "db") / "linkedin.sqlite"


def _gate(dry_run: bool = False) -> Gate:
    """--dry-run 은 이 실행만 문을 내리는 것이다(force="off"). 장부(ledger)는 문 밖 — 한 일의 기록이라 dry 면 쓰지 않는다."""
    con = store.connect(db_path(), judgments.SCHEMA)
    judgments.ensure(con)
    return Gate(con, "linkedin", lambda k: k in SLACK_KINDS, force="off" if dry_run else None)


def _reviewers() -> dict[str, str]:
    """슬랙 ID → 성함. ✅ 를 누른 분 이름으로 콜드 메시지를 쓴다 — 개인 이름으로 나가는 글이라 명단 밖이면 비운다."""
    return {p["slack_id"]: p["names"][0] for p in yaml.safe_load(PEOPLE.read_text(encoding="utf-8")) or [] if p.get("slack_id")}


_ORDER = {t: i for i, t in enumerate(judge.TIERS)}      # 좋은 순


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
            dry_run: bool = False, gate: Gate | None = None) -> int:
    g = gate or _gate(dry_run)
    run_id = f"{date.today():%Y-%m-%d}-{uuid.uuid4().hex[:6]}"
    today = ledger.today()
    print(f"▶ collect run={run_id}")

    backlog = _backlog()
    if not only_axis and backlog >= _cfg("backlog_cap"):
        print(f"· 아직 안 나간 사람이 {backlog}명 (임계 {_cfg('backlog_cap')}) — 더 사지 않는다")
        print(f"  하루 5명이면 {backlog // 5}일치다. 재고가 줄면 그때 산다")
        return 0

    # 🔴 `manual` 축은 정기 수집이 안 집는다 — 임시 조건이라 사람이 --axis 로 부를 때만 돈다.
    if only_axis and axes.AXES.get(only_axis, {}).get("parked"):
        # 🔴 주차한 축은 사람이 이름을 대도 안 돈다. 근거를 보여주고 멈춘다.
        print(f"🔴 '{only_axis}' 는 주차된 축이다 — {axes.AXES[only_axis]['parked']}")
        print("   되살리려면 axes.py 의 `parked` 를 지워라. 지우기 전에 저 숫자를 반박하라")
        return 2
    targets = ([only_axis] if only_axis else
               [a for a in axes.AXES if not axes.AXES[a].get("manual") and _due(a, today)])
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
    # 🔴 축 이름이 노션 옵션에 없으면 `_props` 가 **조용히 빈칸으로 흘린다** — 착지는 "성공"인데
    #    Axis 칸만 빈다. 돈을 쓰기 전에 시끄럽게 말한다.
    try:
        opts = notion.allowed_options(notion.writable_id(DB), "Axis")
        miss = [a for a, _, _ in plan if a not in opts]
        if miss:
            print(f"🔴 노션 Axis 옵션에 없는 축 {miss} — 착지해도 Axis 칸이 빈다. "
                  f"노션에서 옵션을 먼저 추가하라 (Match Reason 에는 남는다)")
    except Exception as e:                      # 옵션 조회 실패가 수집을 막지는 않는다
        print(f"  ⚠️  Axis 옵션을 못 읽었다: {e}")

    if g.level() == "off":
        print("  (dry-run — 돈을 쓰지 않았다)")
        return 0

    # 🔴 키 하나로 세면 안 된다 — URN 으로 온 사람과 URL 로 저장된 사람이 안 만난다
    seen = ledger.all_keys(TASK, ledger.LANDED)
    print(f"· 장부에 이미 있는 키 {len(seen)}개")

    fresh: dict[str, dict] = {}
    rejected: dict[str, int] = {}      # 사후 조건에 걸린 수 (축/이유별)
    bought = 0
    unknowns: list[tuple[str, str]] = []
    for a, n, start in plan:
        total = None                       # 이 축의 모수 (첫 페이지가 알려준다)
        for i in range(n):
            page, got = start + i, {}

            def buy(a=a, page=page):
                got["f"] = apify.run_actor(axes.SLUG, axes.payload(a, page), skill=TASK, est_usd=axes.USD_PER_PAGE)
                return len(got["f"].require())                      # 원본은 통로가 아카이브한다 — actions 엔 수만

            st = g.out(f"{a}/p{page}/{today}", "buy", {"axis": a, "page": page, "est_usd": axes.USD_PER_PAGE}, buy)
            if "f" not in got:                                      # dry(문이 slack 이하) 또는 오늘 이미 산 페이지
                if st == "dry":
                    print(f"  · {a}/p{page} 구매 dry — 문이 {g.level()} 이다")
                continue
            if st != "done":
                unknowns.append((f"{a}/p{page}", got["f"].reason))
                break                       # 이 축은 중단, 다음 축으로
            items = got["f"].require()
            # 🔴 모수는 **이미 산 페이지에 실려 온다**(`_meta.pagination`). 이걸 안 남기면
            #    "이 티어를 다 봤나"를 셀 수 없다 — next_page 만으론 분모가 없다.
            if total is None and items:
                pg = (items[0].get("_meta") or {}).get("pagination") or {}
                total = pg.get("totalElements")
            bought += len(items)
            for it in items:
                ks = profile.match_keys(it)
                if not ks or any(x in seen for x in ks) or any(x in fresh for x in ks):
                    continue
                # 🔴 서버에서 못 거는 조건은 여기서 거른다 — **이미 돈이 나간 사람**이다.
                #    조용히 버리지 않고 이유별로 세서 아래에서 장부에 남긴다.
                why = axes.reject(a, it)
                if why:
                    rejected[f"{a}/{why}"] = rejected.get(f"{a}/{why}", 0) + 1
                    seen.update(ks)      # 같은 실행에서 다른 축이 또 사서 또 거르지 않게
                    continue
                fresh[ks[0]] = {**it, "_axis": a, "_keys": ks}
                seen.update(ks)          # 같은 실행 안에서도 두 번 안 잡히게
        # 커서 전진 — 착지 성공 여부와 무관하게 "여기까지 샀다"는 사실이다
        ledger.append(TASK, a, AXIS_EV, ran_on=today,
                      next_page=(1 if axes.AXES[a]["rewind"] else start + n), run=run_id,
                      total=total, seen_pages=start + n - 1)

    if unknowns:
        print(f"🔴 미상 {len(unknowns)}건")
        for t, why in unknowns[:5]:
            print(f"    {t}: {why[:80]}")
    if rejected:
        n_rej = sum(rejected.values())
        print(f"· 사후 조건에 걸린 사람 {n_rej}명 — **이미 낸 돈이다**")
        for k, c in sorted(rejected.items(), key=lambda x: -x[1]):
            print(f"    {k:34} {c}")
        ledger.append(TASK, "-", "post_filtered", n=n_rej, by_reason=rejected, run=run_id)
    print(f"· 산 사람 {bought}명 · 처음 보는 사람 {len(fresh)}명 (오늘 지출 ${apify.spent_today():.2f})")
    if not fresh:
        _report()
        return 2 if unknowns else 0

    # ---- 판정 (배치 1회)
    cards = [_summary(v) for v in fresh.values()]
    crit = judge.criteria_text()
    verdicts = judge.judge_people(cards, criteria=crit)
    # 🔴 사람별로 저장한다 — 모델이 본 줄과 낸 줄. 기준을 고치면 replay 로 다시 잰다 (판정 못 받은 사람도 ok=0 으로 남는다)
    judgments.save(g.con, judgments.LIVE, judge.sha(crit), {c["key"]: judge._line(c) for c in cards},
                   verdicts.require() if verdicts.is_measured else [])
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
        props = _props(item, v, run_id)
        st = g.out(k, "land", {"tier": v["verdict"], "axis": item["_axis"], "name": _summary(item)["name"]},
                   lambda props=props: notion.create(DB, props).require())
        if st != "done":
            if st == "failed":
                failed += 1
                print(f"  ✗ {k} 착지 실패: {g.row(k, 'land')['error']}")
            continue
        page_id = g.row(k, "land")["result"]
        judgments.set_page(g.con, k, page_id)                        # 노션 Tier(사람 정답)와 이어 준다
        ledger.append(TASK, k, ledger.LANDED, page_id=page_id, run=run_id,
                      keys=item.get("_keys") or [k],      # 별칭 전부 — 다음에 어느 쪽으로 와도 잡힌다
                      axis=item["_axis"], tier=v["verdict"], name=_summary(item)["name"])
        if v["verdict"] == "Catch":
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
        "undergrad": profile.undergrad_start_year(item),
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
        "Tier": v["verdict"],
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
        # 🔴 학부 연도를 여기 남긴다. 전용 칸이 없어서 `Match Reason` 에 싣는데,
        #    이게 없으면 "학부 미상이라 통과시킨 사람"과 "조건을 만족한 사람"이 노션에서 구별이 안 된다.
        "Match Reason": (f"{s['axis']} · 재직 {s['tenure']}개월 · 창업 {s['founding']}개월"
                         + (f" · 학부 {s['undergrad']}" if s.get("undergrad") else " · 학부 미상")),
        "Captured At": ledger.today(),
        "Run ID": run_id,
        "주차": f"{iso.year}-W{iso.week:02d}",
    }


# ---------------------------------------------------------------- card

def card(*, n: int = 5, dry_run: bool = False, gate: Gate | None = None) -> int:
    """🔴 노션에서 Tier 를 다시 읽는다 — 사람이 고친 게 여기서 반영된다.

    🔴 후보는 **노션 행**에서 뽑는다(장부가 아니라). 장부 landed 1,857행 중 1,234행은
       옛 시스템에서 시드된 행이라 `page_id` 가 없다 — 장부로 후보를 뽑으면 그 사람들이
       **카드에 영원히 안 나온다**(2026-08-19 실측: 1티어 115명 중 96명이 그 상태였다).
       Tier 도 어차피 노션에서 다시 읽으므로, 후보의 정본도 노션인 게 맞다.
    """
    g = gate or _gate(dry_run)
    if g.level() != "off" and date.today().weekday() >= 5:        # ponytail: 요일은 닫힌 집합(월~금) — 설정으로 빼지 않는다 [사용자 결정 2026-09-25]
        print("· 주말이라 카드를 쏘지 않는다")
        return 0
    # 🔴 여기 없는 칸은 조용히 빈칸으로 흐른다. 08-18 에 `창업 개월`·`회사`·`Raw Text`·
    #    `AI Reason` 을 안 부르고 읽어서 카드에 "창업 ?개월차" 가 나갔다.
    #    문안에 쓸 칸은 전부 여기 적혀 있어야 한다.
    f = notion.query(notion.writable_id(DB),
                     ["LinkedIn URL", "Tier", "서비스명", "One Liner", "Author Headline",
                      "회사", "창업 개월", "Raw Text", "AI Reason", "Homepage", "Axis",
                      "Match Reason", "타깃", "DB로 업데이트",
                      # 카드 필터가 쓰는 칸 — 없으면 조용히 빈칸이라 전원이 통과한다
                      "Tenure Months", "공백 개월", "본업 공백"])
    if not f.is_measured:
        print(f"🔴 노션을 못 읽었다: {f.reason}")
        return 1
    rows = f.require()
    n_human = judgments.label(g.con, rows, "Tier")                           # 노션에서 고쳐진 Tier 가 곧 정답 — 매일 여기서 동기화
    if n_human:
        print(f"· 사람이 노션에서 고친 Tier {n_human}명 — judgments(human) 에 반영")

    # 🔴 **이미 본 사람은 안 쏜다.** 카드는 "새로 볼 사람"을 올리는 자리다.
    #    ① 극초기 DB 에 이미 있는 사람 — `링크` 칸이 링크드인 프로필 URL 이다(803/1,303행)
    #    ② `DB로 업데이트 ✅` — 이미 승격 처리된 사람
    #    ③ `타깃 ✅` — 사람이 이미 보고 찍은 사람
    #    ⚠️ **이름으로는 대조하지 않는다.** 옛 시스템이 `name:` 키로 대조했다가
    #       `Minsu Kim`·`Minsu Lee`·`Example Person G` 같은 흔한 이름이 통째로 오제외됐다.
    #       사람 이름은 dedup 키가 아니다 — URL 만 쓴다.
    fe = notion.query(notion.writable_id(EARLY), ["링크"])
    if not fe.is_measured:
        print(f"🔴 극초기 DB 를 못 읽었다: {fe.reason} — 이미 본 사람을 못 거른다. 멈춘다")
        return 1
    early = {profile.normalize_url(str(r.get("링크") or "")) for r in fe.require()}
    early.discard("")

    # 이미 나간 사람 — page_id(POSTED 가 남긴다)와 키(CARDED) 둘 다로 막는다.
    # 🔴 하나만 쓰면 안 된다: 같은 사람이 URN 키로 카드가 나갔는데 이번엔 URL 키로
    #    잡히면 **두 번 나간다**. 두 키 공간은 원래 안 만난다.
    posted_pages = {r["page_id"] for r in ledger.rows(TASK, ev=POSTED) if r.get("page_id")}
    carded_keys = ledger.keys(TASK, CARDED)
    key_of_page = {r["page_id"]: r["key"] for r in ledger.rows(TASK, ev=ledger.LANDED)
                   if r.get("page_id")}

    pool = []
    seen_before: dict[str, int] = {}     # 이미 본 사람 (중복 방지)
    filtered: dict[str, int] = {}        # 기준에 안 맞는 사람 (판정)
    for live in rows:
        pid = live["_page_id"]
        if pid in posted_pages:
            continue
        if live.get("Tier") not in _cfg("card_tiers"):
            continue
        # 🔴 [사용자 결정 2026-09-01 · 시니어 기준] 수집 축과 같은 기준을 여기서도 건다.
        #    안 걸면 옛 기준으로 사온 사람이 새 기준인 척 나간다.
        bad = axes.card_reject(live)
        if bad:
            filtered[bad] = filtered.get(bad, 0) + 1
            continue
        url_key = profile.normalize_url(str(live.get("LinkedIn URL") or ""))
        why = ("극초기 DB 에 이미 있음" if url_key and url_key in early else
               "DB로 업데이트 ✅" if live.get("DB로 업데이트") else
               "타깃 ✅" if live.get("타깃") else "")
        if why:
            # 조용히 빼지 않는다 — 몇 명이 왜 빠졌는지 화면에 남긴다
            seen_before[why] = seen_before.get(why, 0) + 1
            continue
        k = key_of_page.get(pid) or url_key
        if not k or k in carded_keys:
            continue
        pool.append((_ORDER.get(live.get("Tier"), 9), k, live))
    for why, cnt in sorted(filtered.items(), key=lambda x: -x[1]):
        print(f"· 기준에 안 맞아 뺐다 — {why}: {cnt}명")
    for why, cnt in sorted(seen_before.items(), key=lambda x: -x[1]):
        print(f"· 이미 본 사람이라 뺐다 — {why}: {cnt}명")
    pool.sort()
    warn_d = constraints.get("linkedin.stock_warn_days")
    if not pool:
        print("· 올릴 수 있는 사람이 없다")
        # 🔴 재고 소진은 **정상 완료가 아니다.** 전에는 여기서 return 0 이라 크론엔
        #    매일 성공으로 보였고, 08-27~31 닷새간 카드가 0건인데 아무도 몰랐다.
        #    로그는 "남으면 3일 더 → 2 → 1 → 0" 하고 세어놓고도 그 숫자를 사람에게
        #    보낸 적이 없다. 세는 것과 알리는 것은 다른 일이다.
        if g.level() != "off":                # 문이 닫혀 있으면 알림도 안 낸다
            _alert("card", "올릴 사람이 0명이다 — 카드가 안 나간다.", key="card:empty", head="🟡 *linkedin card* 재고 소진")
        # 🔴 4 = 재고 소진. 2(다 못 나갔다)와 갈라둔다 — 뜻이 다르고, 무엇보다
        #    card() 가 이미 정확한 문구로 알렸으니 바깥에서 또 보내면 두 번이 된다.
        daily_report(g, pool=0, picked=0)
        return 4
    pick = pool[:n]
    left_n = len(pool) - len(pick)
    left_d = (left_n + n - 1) // n
    print(f"▶ card — 올릴 수 있는 것 {len(pool)} · 오늘 {len(pick)} "
          f"(남으면 {left_d}일 더)")
    if left_d <= warn_d and g.level() != "off":
        _alert("card", f"남은 사람 {left_n}명 — {left_d}일치다. 바닥나기 전에 기준을 손봐야 한다.",
               key="card:low", head="🟡 *linkedin card* 재고 부족")

    # 🔴 One Liner 를 그대로 안 쓴다. 실측 2~95자에 '--'·'CEO' 같은 것도 있었고,
    #    사람이 매번 다시 썼다. claude 가 두 문장과 회사명을 한 번에 만든다.
    cards = [{"key": k, "name": live.get("서비스명"), "company": live.get("회사"),
              "one_liner": live.get("One Liner"), "headline": live.get("Author Headline"),
              "about": live.get("Raw Text"), "reason": live.get("AI Reason"),
              "founding": live.get("창업 개월")}
             for _, k, live in pick]
    b = brief.write(cards)
    if not b.is_measured:
        print(f"🔴 문안 생성 실패: {b.reason}")
        return 1
    bmap = {x["key"]: x for x in b.require()}
    missing = [k for _, k, _ in pick if k not in bmap]
    if missing:
        # 조용히 빼지 않는다. 문안을 못 받은 사람은 이번에 안 올리고 다음에 다시 시도한다.
        print(f"  ⚠️  문안을 못 받은 {len(missing)}명은 이번에 안 올린다 (다음 실행에서 재시도)")
        pick = [t for t in pick if t[1] in bmap]
        if not pick:
            return 2

    lines = [f"*오늘의 링크드인 {len(pick)}명*"]
    for _, k, live in pick:
        m = bmap[k]
        co = m["company"] if m["company"] != brief.NO_COMPANY else "스텔스"
        lines.append(
            f"\n*{live.get('서비스명')}*  {co}  ·  창업 {live.get('창업 개월') or '?'}개월차\n"
            f"{m['brief']}\n"
            f"<{live.get('LinkedIn URL')}|링크드인> · "
            f"<https://www.notion.so/{str(live['_page_id']).replace('-', '')}|노션>"
        )
    # 🔴 헤더 하나 + 사람별 스레드 답글.
    #    한 메시지에 5명을 넣으면 ✅ 가 **누구 건지 알 수가 없다** — 반응은 메시지에
    #    달리지 사람에게 달리지 않는다. 스레드로 쪼개야 반응이 사람과 이어진다.
    #    (노션 지침도 이 방식이다: "쓰레드 기능을 활용… 심사역님들이 이모지로 답변")
    if g.level() == "off":
        print("\n".join(lines))
        for _, k, live in pick:
            print(f"  └ {live.get('서비스명')}  {live.get('Tier')}")
        return 0

    ch, parent = _cfg("card_channel"), {}

    def head_ts() -> str:                                            # 헤더는 첫 사람을 올릴 때 한 번
        if "ts" not in parent:
            parent["ts"] = slack.post(ch, lines[0]).require()
        return parent["ts"]

    posted = 0
    for (_, k, live), body in zip(pick, lines[1:]):
        st = g.out(k, "card", {"name": live.get("서비스명"), "tier": live.get("Tier"), "ch": ch},
                   lambda body=body: slack.post(ch, body.strip() + "\n\n_✅ 를 누르면 콜드 메시지 초안을 만들어 올립니다_",
                                                thread_ts=head_ts()).require())
        if st != "done" or "ts" not in parent:
            if st == "failed":
                print(f"  🔴 {live.get('서비스명')} 답글 실패: {g.row(k, 'card')['error']}")
            continue
        # 착지 먼저, 장부 나중. ts 를 못 남기면 반응을 영영 못 찾는다.
        ledger.append(TASK, k, CARDED)
        ledger.append(TASK, k, POSTED, ch=ch, slack_ts=g.row(k, "card")["result"], parent=parent["ts"],
                      day=ledger.today(), page_id=live["_page_id"],
                      name=live.get("서비스명"),
                      # 🔴 claude 가 찾은 회사명. 극초기 승격의 title 이 된다 —
                      #    자동화가 사람 이름을 회사명 칸에 넣던 문제를 여기서 끊는다.
                      company=bmap[k]["company"], brief=bmap[k]["brief"],
                      homepage=live.get("Homepage"), url=live.get("LinkedIn URL"))
        posted += 1
    print(f"· 스레드에 {posted}/{len(pick)}명 올림")
    daily_report(g, pool=len(pool), picked=posted)
    return 0 if posted == len(pick) else 2


def _days_since(day: str) -> int:
    """게시일(KST)에서 오늘까지 며칠. 🔴 만기를 저장하지 않고 매번 센다 —
    절대시각을 저장하면 표준시 실수가 조용히 며칠을 밀어낸다."""
    return (date.fromisoformat(ledger.today()) - date.fromisoformat(day)).days


def followup(*, dry_run: bool = False, gate: Gate | None = None) -> int:
    """✅ 를 걷어 콜드 메시지 초안을 만들어 그 스레드에 올린다.

    🔴 사람이 보낸다. 심사역 개인 링크드인 계정으로 나가는 메시지라 우리가 못 보낸다 —
       글만 써서 올리고 복사해서 쓰시게 한다.
    """
    print("▶ followup")
    g = gate or _gate(dry_run)
    dry = g.level() == "off"
    open_posts = {r["key"]: r for r in ledger.rows(TASK, ev=POSTED)}
    for r in ledger.rows(TASK, ev=RESOLVED):
        open_posts.pop(r["key"], None)
    if not open_posts:
        print("  · 기다리는 건이 없다")
        return 0
    print(f"  · 반응을 기다리는 사람 {len(open_posts)}명")

    yes = set(constraints.get("slack.reaction_yes"))

    # 🔴 스레드별로 한 번만 부른다. 사람마다 부르면 5명이면 5번이다.
    by_parent: dict[str, list] = {}
    for k, r in open_posts.items():
        by_parent.setdefault(r["parent"], []).append((k, r))

    picked, fails, read_ok = [], [], set()
    for parent, rows in by_parent.items():
        got = slack.replies(rows[0][1]["ch"], parent)
        if not got.is_measured:
            fails.append(f"replies {parent}: {got.reason}")
            continue
        read_ok.add(parent)
        react = {m["ts"]: m.get("reactions") or [] for m in got.require()}
        for k, r in rows:
            mine = react.get(r.get("slack_ts") or r.get("ts"), [])     # 09-25 전 행은 슬랙 ts 가 `ts` 칸에 있다(기록 시각을 덮었다)
            names = {x.get("name") for x in mine}
            users = [u for x in mine if x.get("name") in yes
                     for u in x.get("users", [])]
            if names & yes:
                picked.append((k, r, users))

    # 🔴 만기. 없으면 ✅ 를 못 받은 사람이 **영원히** 대기줄에 남는다 —
    #    실측 08-31: 31명이 그렇게 쌓여 followup 로그가 83KB 가 됐고, 10분마다
    #    "✅ 없음 (대기 31명)" 만 찍혔다. 반응 지연 실측(11명)은 중앙값 당일 ·
    #    10명이 3일 안이라, 7일이면 사실상 답이 나온 것이다.
    #    🔴 **읽기에 성공한 스레드만** 만기시킨다. 못 읽은 걸 만기시키면 망 오류
    #       한 번에 사람이 조용히 사라진다 — 메일에서 이미 겪은 자리다.
    due_d = constraints.get("linkedin.followup_days")
    picked_keys = {k for k, _, _ in picked}
    expired = 0
    for k, r in open_posts.items():
        if k in picked_keys or r.get("parent") not in read_ok:
            continue
        if _days_since(r.get("day") or ledger.today()) < due_d:
            continue
        if not dry:
            ledger.append(TASK, k, RESOLVED, by="expiry", name=r.get("name"),
                          slack_ts=r.get("slack_ts") or r.get("ts"), ch=r.get("ch"))
        expired += 1
    if expired:
        print(f"  · ⏰ {due_d}일 무반응 {expired}명 대기 해제 (노션·인박스는 그대로)")

    if not picked:
        print(f"  · ✅ 없음 (대기 {len(open_posts) - expired}명)")
        return 1 if fails else 0

    # 🔴 발신자는 **사람마다** 정한다. 한 배치에 한 명으로 정하면, 같은 10분 안에 두 분이
    #    각각 다른 사람에게 ✅ 를 눌렀을 때 **한쪽 이름으로 남의 글이 나간다.**
    #    (2026-08-20 실측: Reviewer A 2명 → Reviewer B 2명이 우연히 다른 실행에 걸려 맞았다.
    #     같은 실행에 걸렸으면 두 명 중 한 명 이름으로 4건이 다 나갔다.)
    #    개인 계정으로 나가는 글이라 이건 오타 수준의 문제가 아니다.
    def _sender(users: list) -> str:
        # 한 메시지에 여러 명이 눌렀으면 **먼저 누른 분**(슬랙이 반응 순서로 준다).
        # 명단 밖이면 비워두고 사람이 채운다.
        who = _reviewers()
        return next((who[u] for u in users if u in who), "")

    groups: dict[str, list] = {}
    for k, r, us in picked:
        groups.setdefault(_sender(us), []).append((k, r))
    print(f"  · ✅ {len(picked)}명 · 발신자 "
          + " · ".join(f"{w or '🔴 명단 밖'}({len(v)})" for w, v in groups.items()))

    done = 0
    for who, members in groups.items():
        cards = _outreach_cards([r for _, r in members])
        if not cards.is_measured:
            fails.append(f"노션 읽기({who or '미상'}): {cards.reason}")
            continue
        msgs = outreach.write(cards.require(), who or "OOO")
        if not msgs.is_measured:
            fails.append(f"초안({who or '미상'}): {msgs.reason}")
            continue
        mmap = {m["key"]: m for m in msgs.require()}
        for k, r in members:
            m = mmap.get(k)
            if not m:
                print(f"  ⚠️  {r.get('name')} 초안을 못 받았다 — 다음에 다시 시도한다")
                continue
            text = (f"*{r.get('name')}* 콜드 메시지 초안"
                    + (f" — {who} 심사역 이름" if who else " — 🔴 발신자 이름을 채워주세요")
                    + f"\n\n*짧게 (연결 요청용)*\n{m['short']}"
                    f"\n\n*길게 (이미 연결된 경우)*\n{m['long']}")
            if dry:
                print(f"\n--- {r.get('name')} ({who or '발신자 미상'}) ---\n{text}")
                continue
            st = g.out(k, "outreach", {"who": who, "name": r.get("name")},
                       lambda r=r, text=text: slack.post(r["ch"], text, thread_ts=r["parent"]).require())
            if st != "done":
                fails.append(f"post {k}: {(g.row(k, 'outreach') or {}).get('error', st)}")
                continue
            ledger.append(TASK, k, OUTREACH, slack_ts=g.row(k, "outreach")["result"], by=who, name=r.get("name"))
            pr = _promote(k, r, g)
            ledger.append(TASK, k, RESOLVED, by="reaction_yes", who=who, name=r.get("name"),
                          promoted=pr)
            done += 1

    print(f"· 초안 {done}/{len(picked)}명 올림")
    for f in fails:
        print(f"  🔴 {f}")
    return 2 if (fails or done < len(picked)) else 0


# 🔴 회사 홈페이지가 아닌 값. Apify 가 스텔스 창업자에게 붙이는 플레이스홀더다.
#    실측(08-19): 1,268명 중 **192명(15%)** 이 이 값들을 갖는다 —
#    stealth-startup-community 128 · stealthaistartup 45 · steath 12 · stealthmode14 5.
#    이걸 대조 키로 쓰면 서로 다른 회사 190개가 **같은 회사로 잡힌다.**
_FAKE_HOME = ("linkedin.com/company/stealth", "linkedin.com/company/steath")


def _real_home(v) -> str:
    h = str(v or "").strip()
    return "" if any(f in h.lower() for f in _FAKE_HOME) else h


# 🔴 [사용자 결정 2026-08-20] `Status = Reviewing` 을 **"극초기 DB 에 등록됨"** 의 뜻으로 쓴다.
#    새 옵션을 만드는 대신 안 쓰이던 값을 재정의한 것이다(1,268행이 전부 `New` 였다).
#    ⚠️ 그래서 이 값은 "우리가 검토 중"이라는 일반적 뜻이 **아니다.** 사람이 손으로
#       "검토 중"의 의미로 켜면 그 행은 이미 극초기에 올라간 것처럼 보인다.
PROMOTED_STATUS = "Reviewing"

# 🔴 [사용자 결정 2026-08-20] 극초기에 만들 때 `2. 미팅요청` 으로 넣는다.
#    ✅ 는 "연락하자"는 **결정**이라, `1. 미팅여부 결정전` 에 넣으면 이미 내려진 결정을
#    다시 묻는 칸에 쌓인다. 교통정리에서 안 보이는 게 그 이유였다.
#    ⚠️ 다만 우리는 **실제로 보냈는지 모른다** — 심사역 개인 계정으로 사람이 보낸다.
#       이 값은 "요청하기로 했다"까지의 뜻이다.
EARLY_STATUS = "2. 미팅요청"


def _mark_promoted(page_id: str, title: str) -> None:
    """승격된 사실을 **링크드인 행에도** 남긴다.

    🔴 안 남기면 장부에만 있다 — 노션에서 교통정리하는 사람 눈에는 안 보인다.
       "이미 극초기에 올린 사람"과 "아직 안 본 사람"이 화면에서 똑같아 보이는 게 문제다.

    🔴 `DB로 업데이트` 체크박스는 **쓰지 않는다.** 그건 옛 노션 자동화를 발동시키던
       스위치라, 자동화가 정말 꺼졌는지 확인되기 전에 코드가 켜면 이중 생성이 난다.
       (08-18 에 실제로 중복 10행이 생겨 5행을 아카이브했다)
    """
    if not page_id:
        return
    db_id = notion.writable_id(DB)
    props: dict = {}
    try:
        if PROMOTED_STATUS in notion.allowed_options(db_id, "Status"):
            props["Status"] = PROMOTED_STATUS
    except Exception as e:                                  # noqa: BLE001
        print(f"  ⚠️  Status 옵션을 못 읽었다: {e}")
    if not props:
        print(f"  🔴 Status 에 '{PROMOTED_STATUS}' 옵션이 없다 — 표시 못 함 (08-20 결정: 그 옵션이 '극초기에 등록됨'의 뜻)")   # 조용히 넘어가지 않는다
        return
    res = notion.update(page_id, DB, props)
    if not res.is_measured:
        print(f"  ⚠️  {title} 승격 표시 실패: {res.reason[:80]}")


def _promote(key: str, row: dict, gate: Gate | None = None) -> str:
    """✅ 를 받은 사람을 극초기 DB 에 올린다. **회사 단위로 바꿔서** 넣는다.

    🔴 이미 있으면 안 만든다. 대조는 두 가지로 한다 —
       ① Homepage (링크드인 DB 93% 채워짐)  ② 회사명 정규화
       이름 하나로만 대조하면 `SampleProduct`(회사)와 `Example Person B`(사람)이 두 행으로 갈린다.
       실제로 08-18 에 그렇게 갈렸다.
    """
    gate = gate or _gate()
    if not _cfg("promote"):
        # 막혔다는 걸 말한다. 조용히 건너뛰면 "왜 극초기에 안 들어갔지"를 못 답한다.
        print(f"  · 극초기 승격은 꺼져 있다 (linkedin.promote: false) — {row.get('name')}")
        return "gated"
    if ledger.has(TASK, key, PROMOTED):
        return "already"
    co = (row.get("company") or "").strip()
    name = (row.get("name") or "").strip()
    home = _real_home(row.get("homepage"))
    if not home and row.get("page_id"):
        # 🔴 옛 posted 행엔 homepage·url 이 없다. 노션에서 지금 읽는다 —
        #    링크 없이 넣으면 대조 키가 없는 행이 하나 더 생긴다(극초기 505건이 그 상태다).
        g = notion.query(notion.writable_id(DB), ["Homepage", "LinkedIn URL"])
        if g.is_measured:
            live = {r["_page_id"]: r for r in g.require()}.get(row["page_id"], {})
            home = _real_home(live.get("Homepage")) or str(live.get("LinkedIn URL") or "").strip()
    home = home or str(row.get("url") or "").strip()
    # 회사를 못 찾았으면 사람 이름을 쓰되 **모른다는 걸 제목에 남긴다.**
    # 🔴 [사용자 결정 2026-08-20] 이 형태를 유지한다. `(미상)` 으로 바꾸지 마라 —
    #    스텔스는 회사명이 원래 없어서, 목록에서 사람을 가리키는 유일한 식별자가 이름이다.
    #    (옛 노션 자동화가 사람 이름을 넣던 것과 **모양은 같지만 다르다**: 저건 회사명이
    #     있는데도 사람 이름으로 덮었고, 이건 회사명이 없을 때만이고 `(스텔스)` 로 표시한다)
    title = co if co and co != brief.NO_COMPANY else f"{name} (스텔스)"

    f = notion.query(notion.writable_id(EARLY), ["법인(서비스)", "링크"])
    if not f.is_measured:
        print(f"  🔴 극초기를 못 읽었다: {f.reason} — 대조 없이 안 넣는다")
        return "unknown"
    def norm(x):
        return "".join(str(x or "").lower().split()).rstrip("/")
    names = {norm(r.get("법인(서비스)")) for r in f.require()}
    links = {norm(r.get("링크")) for r in f.require() if str(r.get("링크") or "").strip()}
    if norm(title) in names or (home and norm(home) in links):
        if gate.out(key, "promote", {"title": title, "result": "dup"}, lambda: _mark_promoted(row.get("page_id"), title)) == "dry":
            return "dry"
        ledger.append(TASK, key, PROMOTED, result="dup", title=title)     # 이미 올라가 있다는 사실도 보여야 한다
        print(f"  · {title} — 극초기에 이미 있다. 안 넣는다")
        return "dup"

    def do() -> str:
        pid = notion.create(EARLY, {
            "법인(서비스)": notion.clip16(title),
            "링크": home or row.get("url") or None,
            "한 줄 설명": notion.clip16(row.get("brief") or ""),
            "발굴": "링크드인",
            "Status": EARLY_STATUS,
            # 🔴 교통 정리 뷰가 이 칸으로 거른다 — False 면 **Status 를 옳게 넣어도 화면에 안 뜬다.**
            #    (2026-08-20: 4행을 `2. 미팅요청` 으로 넣었는데 보드에 안 보였다. 원인이 이거였다)
            #    우리는 실제로 슬랙 카드를 보내고 나서 승격하므로 True 가 사실이다.
        "슬랙 발송?": True,
        }).require()
        _mark_promoted(row.get("page_id"), title)     # 링크드인 행에도 남긴다 (교통정리용)
        return pid

    st = gate.out(key, "promote", {"title": title, "link": home or row.get("url")}, do)
    if st == "dry":
        return "dry"
    if st != "done":
        print(f"  🔴 극초기 착지 실패 {title}: {gate.row(key, 'promote')['error']}")
        return "fail"
    ledger.append(TASK, key, PROMOTED, result="created", page_id=gate.row(key, "promote")["result"], title=title)
    print(f"  · 극초기 승격: {title}")
    return "created"


def _outreach_cards(rows: list[dict]) -> Fetched:
    """초안에 쓸 재료를 노션에서 다시 읽는다 — 사람이 고쳤을 수 있다."""
    f = notion.query(notion.writable_id(DB),
                     ["서비스명", "One Liner", "Author Headline", "AI Reason", "판단 이유"])
    if not f.is_measured:
        return f
    by_page = {r["_page_id"]: r for r in f.require()}
    out = []
    for r in rows:
        live = by_page.get(r.get("page_id")) or {}
        out.append({"key": r["key"], "name": live.get("서비스명") or r.get("name"),
                    "one_liner": live.get("One Liner"),
                    "headline": live.get("Author Headline"),
                    "reason": live.get("AI Reason") or live.get("판단 이유")})
    return Fetched.measured(out)


def _backlog() -> int:
    """**카드가 실제로 올릴 수 있는** 사람 수.

    🔴 [2026-09-01] 예전엔 "안 나간 사람"을 셌다. 그래서 collect 은 "598명, 119일치"
       라며 안 사고, 같은 날 card 는 "올릴 수 있는 사람이 없다"고 했다. **계기판 두 개가
       서로 다른 창고를 보고 있었고**, 그 사이 닷새간 카드가 0건이었다.
       재고를 세는 자리와 쓰는 자리는 **같은 기준**이어야 한다.

    🔴 그래서 장부가 아니라 노션을 본다 — card() 의 정본이 노션이기 때문이다.
       노션을 못 읽으면 0 이 아니라 **옛 방식으로 세서 돌려준다**(0 은 "재고 없음"으로
       읽혀 즉시 구매를 부른다 — 못 읽은 것과 없는 것은 다르다).
    """
    f = notion.query(notion.writable_id(DB),
                     ["LinkedIn URL", "Tier", "Author Headline", "회사", "One Liner",
                      "Raw Text", "서비스명", "타깃", "DB로 업데이트",
                      "Tenure Months", "공백 개월", "본업 공백"])
    if not f.is_measured:
        print(f"  ⚠️  재고를 노션으로 못 셌다({f.reason}) — 장부로 센다")
        return _backlog_from_ledger()
    posted_pages = {r["page_id"] for r in ledger.rows(TASK, ev=POSTED) if r.get("page_id")}
    n = 0
    for live in f.require():
        if live["_page_id"] in posted_pages:
            continue
        if live.get("타깃") or live.get("DB로 업데이트"):
            continue
        if live.get("Tier") not in _cfg("card_tiers"):
            continue
        if axes.card_reject(live):
            continue
        n += 1
    return n


def _backlog_from_ledger() -> int:
    """옛 방식 — 노션을 못 읽을 때만 쓴다.

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


def _alert(cmd: str, msg: str, *, key: str | None = None, head: str | None = None) -> None:
    """🔴 실패가 로그 파일에만 남으면 아무도 모른다.

    2026-08-19 11:00 에 카드가 죽었는데(claude 실행파일 경로) 슬랙엔 아무 말도 안 갔고,
    사람이 브리핑을 시키다가 우연히 발견했다. 무인으로 도는 잡은 **실패했다는 사실이
    사람에게 도달해야** 무인이다.

    🔴 하루 한 번만 보낸다 — followup 은 10분마다 돈다. 같은 실패를 하루 144번 보내면
       그 채널은 그날로 무시된다(경보가 소음이 되는 표준 경로다).
    """
    today = ledger.today()
    # 🔴 하루 한 번은 **알림 종류마다** 센다. 실패 알림이 하나 나갔다고 재고 알림이
    #    묻히면, 고치는 김에 만든 침묵이 된다.
    key = key or cmd
    if any(r.get("key") == key and r.get("day") == today
           for r in ledger.rows(TASK, ev="alerted")):
        return
    text = (f"{head or f'🔴 *linkedin {cmd}* 실패'} — {msg}\n"
            f"로그: `tail -30 ~/kv-data/_logs/com.kv.linkedin.{cmd}.log`")
    r = report.post("linkedin", text, _send)
    # 알림이 못 나가도 잡의 결과를 바꾸지 않는다. 다만 조용히 넘어가진 않는다.
    if r == "게시함":
        ledger.append(TASK, key, "alerted", day=today, msg=msg[:200])
    else:
        print(f"  🔴 실패 알림도 못 보냈다: {r}")


def _send(ch: str, text: str) -> str:
    return slack.post(ch, text).require()


def daily_report(g: Gate, *, pool: int, picked: int) -> None:
    """하루 한 번(평일 · card 뒤) 파일 + 슬랙 요약. 먼저 볼 것 = **노션에서 사람이 고친 Tier**(정답 ↔ 판정 갈림) — 기준을 깎을 재료다."""
    out = paths.data("linkedin", "reports") / f"{ledger.today()}.md"
    if out.exists():
        return
    today = ledger.today()
    day = {ev: sum(1 for r in ledger.rows(TASK, ev=ev) if str(r.get("ts", "")).startswith(today) or r.get("day") == today)
           for ev in ("landed", "carded", "outreach", "resolved", "promoted")}
    week = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    dist = dict(g.con.execute("SELECT verdict, count(*) FROM judgments WHERE run='live' AND ok=1 AND created>=? GROUP BY verdict", (week,)))
    human = g.con.execute("SELECT h.key, l.verdict, h.verdict FROM judgments h JOIN judgments l ON l.key=h.key AND l.run='live'"
                          " WHERE h.run='human' ORDER BY h.created DESC").fetchall()
    acts = dict(g.con.execute("SELECT kind||':'||status, count(*) FROM actions WHERE created>=? GROUP BY 1", (today,)))
    head = (f"카드 {picked}명 · 재고 {pool}명({pool // 5}일치) · ✅ 초안 {day['outreach']} · 승격 {day['promoted']} · 만기/결론 {day['resolved']}"
            + (f" · 이번 주 판정 {sum(dist.values())}명" if dist else ""))
    md = [f"# 링크드인 하루 보고 — {today}", "", head, "",
          "## 🚩 노션에서 사람이 고친 Tier (정답 ↔ 판정)", ""]
    md += [f"- `{k}` 판정 {a} → 정답 **{b}**" for k, a, b in human] or ["없음 — 갈린 게 없으면 기준이 맞거나 아직 안 봤다"]
    md += ["", "## 이번 주 판정 분포", "", " · ".join(f"{t} {n}" for t, n in sorted(dist.items(), key=lambda x: -x[1])) or "없음",
           "", "## 오늘 문(actions)", "", " · ".join(f"{k} {v}" for k, v in sorted(acts.items())) or "없음",
           "", "## 고치는 법", "", "```bash", "cd ~/kv",
           "# 기준을 고친다: skills/linkedin/criteria.md → 저장된 사람으로 다시 판정 → 무엇이 달라졌나",
           "./.venv/bin/python -m skills.linkedin.run replay [--limit 50]",
           "./.venv/bin/python -m skills.linkedin.run diff --b r-<sha>",
           "# 정답은 노션 Tier 칸을 고치면 된다 — 다음 card 가 human 으로 가져간다", "```", ""]
    out.write_text("\n".join(md), encoding="utf-8")
    summary = f"*링크드인 하루* — {head}" + (f"\n🚩 노션에서 고친 Tier {len(human)}명" if human else "") + f"\n전체: `{out}`"
    print(f"· 보고서 {out} | 슬랙 {report.post('linkedin', summary, _send)}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="linkedin")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--axis", choices=list(axes.AXES)); c.add_argument("--pages", type=int)
    c.add_argument("--dry-run", action="store_true")
    d = sub.add_parser("card"); d.add_argument("-n", type=int, default=5)
    d.add_argument("--dry-run", action="store_true")
    fu = sub.add_parser("followup", help="✅ 를 걷어 콜드 메시지 초안을 스레드에 올린다")
    fu.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    sub.add_parser("health", help="평일에 오늘 보고서가 있나 — 없으면 슬랙 한 줄")
    rp = sub.add_parser("replay", help="고친 기준으로 저장된 사람을 다시 판정 (돈 0 · 노션 0)")
    rp.add_argument("--limit", type=int, default=-1); rp.add_argument("--criteria", default=None)
    df = sub.add_parser("diff"); df.add_argument("--a", default="live"); df.add_argument("--b", required=True)
    sub.add_parser("label", help="노션 Tier 를 읽어 live 와 다른 사람을 human 으로")
    a = p.parse_args(argv)
    try:
        if a.cmd == "collect":
            with lock.hold("linkedin-collect"):
                return collect(only_axis=a.axis, pages=a.pages, dry_run=a.dry_run)
        if a.cmd == "card":
            rc = card(n=a.n, dry_run=a.dry_run)
            # rc 4(재고 소진)는 card() 가 이미 제 문구로 알렸다. 여기서 또 보내지 않는다.
            if rc and rc != 4 and not a.dry_run:
                _alert("card", f"종료코드 {rc} — 카드가 다 못 나갔다")
            return rc
        if a.cmd == "followup":
            with lock.hold("linkedin-followup"):
                rc = followup(dry_run=a.dry_run)
            if rc and not a.dry_run:
                _alert("followup", f"종료코드 {rc} — ✅ 를 걷거나 초안을 올리지 못했다")
            return rc
        if a.cmd == "health":
            return report.health("linkedin", paths.data("linkedin", "reports"), _send, label="linkedin card", weekdays_only=True)
        if a.cmd == "replay":
            crit = Path(a.criteria).read_text(encoding="utf-8") if a.criteria else judge.criteria_text()
            r = judgments.replay(_gate().con, judge.judge_lines, crit, judge.sha(crit), limit=a.limit)
            print(f"replay {r['run']}: 판정 {r['judged']} · 누락 {r['missing']}" + (f" · 🔴 {r['error']}" if r.get("error") else "")
                  + f"\n  다음: linkedin diff --b {r['run']}")
            return 0
        if a.cmd == "diff":
            print(judgments.diff_text(judgments.diff(_gate().con, a.a, a.b), a.a, a.b, unit="명"))
            return 0
        if a.cmd == "label":
            f = notion.query(notion.writable_id(DB), ["Tier"])
            if not f.is_measured:
                print(f"🔴 노션을 못 읽었다: {f.reason}")
                return 1
            print(f"· 노션 Tier 가 live 와 다른 사람 {judgments.label(_gate().con, f.require(), "Tier")}명 → human")
            return 0
        if a.cmd == "status":
            print(ledger.summary(TASK))
            print(f"  재고(아직 안 나간 사람) {_backlog()}명 / 임계 {_cfg('backlog_cap')}")
            for ax in axes.AXES:
                st = _axis_state(ax)
                line = f"  {ax:22} 마지막 {st.get('ran_on', '-'):10} · 다음 p{_next_page(ax)}"
                # 🔴 커버리지 = 이 티어를 다 봤나. 분모(모수)가 없으면 못 세는 값이다.
                tot, seen_p = st.get("total"), st.get("seen_pages")
                if tot and seen_p:
                    done = min(seen_p * axes.PER_PAGE, tot)
                    line += (f" · 모수 {tot:,} 중 {done:,} 봄 ({done / tot:.0%})"
                             + (" ✅ 전량" if done >= tot else ""))
                if axes.AXES[ax].get("parked"):
                    line += "  ⏸ 주차"
                print(line)
            return 0
    except lock.Busy as e:
        print(f"🔴 {e}")
        return 3                                  # 다른 실행이 도는 중 = 실패가 아니다
    except Degraded as e:
        print(f"🔴 온전하지 않다: {e}")
        _alert(a.cmd, str(e))
        return 2
    except Exception as e:                        # noqa: BLE001
        # 🔴 예상 못 한 예외야말로 사람이 알아야 한다 (오늘 죽은 게 정확히 이 종류였다)
        print(f"🔴 {type(e).__name__}: {e}")
        _alert(a.cmd, f"{type(e).__name__}: {e}")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
