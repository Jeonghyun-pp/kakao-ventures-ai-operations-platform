"""링크드인 스킬 — collect · card · followup · remind · 보고서. 일정·흐름·규율은 SKILL.md(frontmatter 가 스케줄이다).

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
from urllib.parse import quote

import yaml

from kvlib import Fetched, constraints, judgments, ledger, lock, paths, report, store, workday
from kvlib.gate import Gate
from kv_transport import apify, notion, slack

from . import axes, brief, judge, names, outreach, profile

TASK = "linkedin"
DB = "ch_링크드인"
AXIS_EV = "axis"        # 커서·주기 기록
CARDED = "carded"
MEETING = "meeting_candidate"
POSTED = "posted"          # 슬랙 스레드 답글로 올림 (사람별 ts)
RESOLVED = "resolved"      # 카드의 결론. by = reaction_yes(✅) · reaction_no(❌ 만) · expiry(무반응)
OUTREACH = "outreach"      # 콜드 메시지 초안을 스레드에 올림
REMINDED = "reminded"      # ✅ N일 뒤 누른 분에게 "연락하세요" 핑
PROMOTED = "promoted"      # 극초기 DB 로 승격
SCREENED = "screened"      # 문안 단계 창업 판정 (brief.VERDICTS) — 카드·재고가 같이 본다
SNAPSHOT = "reaction_snapshot"   # 결론과 별도로 다시 읽은 반응(소급·learn). 정답 접기가 본다
SWEPT = "late_swept"       # 늦은 반응 훑기를 한 날 — 하루 한 번
CHECKED = "checked"        # recheck 가 프로필을 다시 긁음 (지문·변화 여부)
RESURFACED = "resurfaced"  # recheck 에서 회사·직함이 바뀌어 다시 재고로 — 이 뒤로는 다시 카드에 오를 수 있다

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


# 🔴 밖으로 나가는 것(buy·land·card·outreach·remind·promote·refresh)은 전부 `Gate.out` 을 지난다 (constraints.yaml linkedin.outbound).
#    `slack` 단계에서 실제로 나가는 종류 — **허용 목록**. 카드·초안은 슬랙 글이고, 구매·착지·승격은 아니다.
SLACK_KINDS = ("card", "outreach", "remind")


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
EXPLORE_FROM = _ORDER["Wait"]
# 카드 안내. ❌ 는 영구가 아니다 — 프로필이 바뀌면 recheck 가 다시 올린다 [사용자 결정 2026-10-14]
CARD_FOOTER = "_✅ 연락하겠다 → 연락 문안 + 2일 뒤 리마인드 · ❌ 이번엔 관심 없음 (프로필이 바뀌면 다시 올라옵니다)_"                            # 탐색 카드는 이 등급부터 아래에서 — 판정 하위가 맞는지 재는 유일한 길


# ---------------------------------------------------------------- 카드의 생애 (장부를 접는다)

def _last(ev: str, field: str = "key") -> dict[str, dict]:
    """field(key·page_id) → 그 이벤트의 마지막 줄."""
    out: dict[str, dict] = {}
    for r in ledger.rows(TASK, ev=ev):
        if r.get(field):
            out[r[field]] = r
    return out


def _resurfaced_open() -> dict[str, dict]:
    """다시 올라와 아직 안 나간 page_id → 그 resurfaced 줄. 마지막 resurfaced 가 마지막 posted 보다 뒤인 것.
    🔴 ❌ 는 영구가 아니다 [사용자 결정 2026-10-14]. 프로필이 바뀌면(recheck) 한 번 나간 사람도 다시 후보다."""
    posted = _last(POSTED, "page_id")
    return {pid: r for pid, r in _last(RESURFACED, "page_id").items()
            if r["ts"] > posted.get(pid, {}).get("ts", "") and not r.get("revoked")}     # revoked = 기준을 고쳐 다시 보니 변화가 아니었다


def _open_posts() -> dict[str, dict]:
    """결론이 안 난 카드 — 키마다 마지막 posted 가 마지막 resolved 보다 뒤.
    🔴 키 차집합(posted − resolved)이면 한 번 결론 난 사람은 다시 올려도 반응을 영영 못 읽는다.
       09-25 전 행은 `ts` 칸에 슬랙 ts(숫자)가 있어 ISO 시각보다 작게 비교된다 — 결론이 있으면 닫힌 쪽으로 간다."""
    res = _last(RESOLVED)
    return {k: r for k, r in _last(POSTED).items() if r["ts"] > res.get(k, {}).get("ts", "")}


def _pressers(reactions: list[dict], vocab: str) -> list[str]:
    """그 어휘(slack.reaction_yes·reaction_no)로 누른 사람들 — 슬랙이 준 순서(먼저 누른 분 먼저)."""
    names_ = set(constraints.get(f"slack.{vocab}"))
    return [u for x in reactions if x.get("name") in names_ for u in x.get("users", [])]


def _reaction_labels() -> dict[str, dict]:
    """키 → {label: yes|no, page_id, ts, explore}. **정답은 심사역의 반응이다** [사용자 결정 2026-10-14].
    노션 Tier 는 AI 가 쓴 값이라 정답이 아니다(판정끼리 맞대면 437/437 일치 — 10/13 보고서).

        yes  ✅ 를 받았다
        no   **안 고름** — 심사역이 그날 카드를 본 날(그 스레드 어느 카드에든 ✅·❌ 가 붙음), 결론 날 때까지 ✅ 를 못 받았다.
             ❌ 도 여기다: ❌ 는 "이번 주엔 관심 없음"이지 "별로인 사람"이 아니라 같은 날 안 고른 카드와 뜻이 같다.
             ❌ 의 몫은 '그날 봤다'는 표시다 — ✅ 없이 ❌ 만 누른 날도 나머지 카드가 정답이 된다.
        —    아무도 안 본 날의 무반응은 정답이 아니다(10/2~13 카드 40장 반응 0 — 바빴다). 아직 결론 안 난 카드도 아니다(✅ 가 올 수 있다).

    결론(resolved)과 따로 다시 읽은 반응(snapshot)을 본다. 다시 올린 카드는 마지막 게시 뒤의 반응만."""
    posted = _last(POSTED)
    yes: dict[str, str] = {}                     # key → ✅ 를 본 시각
    no_press: set[str] = set()
    done: dict[str, str] = {}                    # key → 결론 시각
    for r in sorted(ledger.rows(TASK, ev=RESOLVED) + ledger.rows(TASK, ev=SNAPSHOT), key=lambda r: r["ts"]):
        p = posted.get(r["key"])
        if not p or r["ts"] < p["ts"]:
            continue
        if r.get("by") == "reaction_yes" or r.get("yes_by"):
            yes.setdefault(r["key"], r["ts"])
        if r.get("by") == "reaction_no" or r.get("no_by"):
            no_press.add(r["key"])
        if r["ev"] == RESOLVED:
            done[r["key"]] = r["ts"]
    seen = {posted[k].get("parent") for k in (*yes, *no_press)} - {None}
    out: dict[str, dict] = {}
    for k, p in posted.items():
        if k in yes:
            lab, ts = "yes", yes[k]
        elif k in done and p.get("parent") in seen:
            lab, ts = "no", done[k]
        else:
            continue
        out[k] = {"label": lab, "page_id": p.get("page_id"), "ts": ts, "explore": bool(p.get("explore")), "pressed_no": k in no_press}
    return out


def _bucket(tier: str | None) -> str:
    """판정 → 정답 어휘. Catch = 올림(yes), 나머지 = 아님(no). criteria.md 가 Catch 를 "✅ 받을 사람"으로 정의한다."""
    return "yes" if tier == "Catch" else "no"


def _sync_labels(con) -> int:
    """반응 정답 → judgments(human). card 가 매일 부른다. 돌려주는 값 = 판정과 갈린 수."""
    rows = [{"_page_id": v["page_id"], "반응": v["label"]} for v in _reaction_labels().values() if v["page_id"]]
    return judgments.label(con, rows, "반응", source="슬랙 반응", same=lambda live, human: _bucket(live) == human)


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

    # 🔴 [사용자 결정 2026-09-29] 재고가 많다고 안 사지 않는다. 08-24~09-28 매주 "재고 충분"으로 건너뛰는 사이
    #    카드는 08-17 에 산 사람과 옛 시드만 돌렸고, 재직·창업 개월이 산 날에 멈춘 채 늙어 품질이 떨어졌다.
    #    지출 상한은 축의 주기·페이지 수가 맡는다. 재고 수는 알려주기만 한다.
    print(f"· 카드 재고 {_backlog()}명")

    # 🔴 `manual` 축은 정기 수집이 안 집는다 — 임시 조건이라 사람이 --axis 로 부를 때만 돈다.
    if only_axis and axes.AXES.get(only_axis, {}).get("parked"):
        # 🔴 주차한 축은 사람이 이름을 대도 안 돈다. 근거를 보여주고 멈춘다.
        print(f"🔴 '{only_axis}' 는 주차된 축이다 — {axes.AXES[only_axis]['parked']}")
        print("   되살리려면 axes.py 의 `parked` 를 지워라. 지우기 전에 저 숫자를 반박하라")
        return 2
    targets = ([only_axis] if only_axis else
               [a for a in axes.AXES if not axes.AXES[a].get("manual") and not axes.AXES[a].get("parked")
                and _due(a, today)])
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
        reached = start                    # 실제로 산(또는 오늘 이미 산) 마지막 페이지 다음
        for i in range(n):
            page, got = start + i, {}

            def buy(a=a, page=page):
                got["f"] = apify.run_actor(axes.SLUG, axes.payload(a, page), skill=TASK, est_usd=axes.USD_PER_PAGE)
                return len(got["f"].require())                      # 원본은 통로가 아카이브한다 — actions 엔 수만

            st = g.out(f"{a}/p{page}/{today}", "buy", {"axis": a, "page": page, "est_usd": axes.USD_PER_PAGE}, buy)
            if "f" not in got:                                      # dry(문이 slack 이하) 또는 오늘 이미 산 페이지
                if st == "dry":
                    print(f"  · {a}/p{page} 구매 dry — 문이 {g.level()} 이다")
                else:
                    reached = page + 1
                continue
            if st != "done":
                unknowns.append((f"{a}/p{page}", got["f"].reason))
                break                       # 이 축은 중단, 다음 축으로
            reached = page + 1
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
        # 커서 전진 — 착지 성공 여부와 무관하게 "여기까지 샀다"는 사실이다.
        # 🔴 **실제로 산 데까지만** 민다. 09-29 s-stealth 15페이지 중 7번째에서 502 로 멈췄는데
        #    커서가 start+n 으로 넘어가 못 산 9페이지(모수의 절반)를 영영 건너뛸 뻔했다.
        ledger.append(TASK, a, AXIS_EV, ran_on=today,
                      next_page=(1 if axes.AXES[a]["rewind"] else reached), run=run_id,
                      total=total, seen_pages=reached - 1)

    if unknowns:
        print(f"🔴 미상 {len(unknowns)}건")
        for t, why in unknowns[:5]:
            print(f"    {t}: {why[:80]}")
    # ---- Legacy (2026-09-28·29 · 사용자 결정): 국적·나이로 대상이 아닌 사람은 Tier 판정 없이
    #      Status=Legacy 로 착지한다 — 재고엔 안 들어가고, 장부에 landed 로 남아 다시 처리되지 않는다.
    # 🔴 액터에 국적·나이 필터가 없다 — 사는 것까지는 못 막는다. 대신 판정 호출과 카드로는 안 흘린다.
    legacy: dict[str, tuple[dict, str]] = {}
    for k, v in fresh.items():
        age = profile.estimated_age(v)
        if age is not None and age > axes.MAX_AGE:
            legacy[k] = (v, f"추정 {axes.MAX_AGE + 1}세 이상")
    ask = {k: v for k, v in fresh.items() if k not in legacy and not axes.nationality_exempt(v["_axis"])}
    if ask:
        nf = judge.nationality({k: judge.nationality_line(k, *profile.nationality_input(v)) for k, v in ask.items()})
        nat = nf.require() if nf.is_measured else {}
        if not nf.is_measured:
            print(f"  ⚠️  국적 판정 실패({nf.reason}) — 아무도 안 뺀다. card 에서 걸리지도 않는다(태그가 없다)")
        elif len(nat) < len(ask):
            print(f"  ⚠️  국적 판정 누락 {len(ask) - len(nat)}명 — 한국인으로 둔다")
        for k, v in ask.items():
            if nat.get(k) == judge.FOREIGN:
                legacy[k] = (v, judge.FOREIGN)
    for k, (v, why) in legacy.items():
        fresh.pop(k)
        tag = f"{v['_axis']}/Legacy:{why}"
        rejected[tag] = rejected.get(tag, 0) + 1

    if rejected:
        n_rej = sum(rejected.values())
        print(f"· 사후 조건에 걸린 사람 {n_rej}명 — **이미 낸 돈이다**")
        for k, c in sorted(rejected.items(), key=lambda x: -x[1]):
            print(f"    {k:34} {c}")
        ledger.append(TASK, "-", "post_filtered", n=n_rej, by_reason=rejected, run=run_id)
    print(f"· 산 사람 {bought}명 · 처음 보는 사람 {len(fresh) + len(legacy)}명 · 그중 Legacy {len(legacy)} "
          f"(오늘 지출 ${apify.spent_today():.2f})")
    n_legacy, legacy_failed = _land_legacy(g, legacy, run_id)
    if not fresh:
        _report()
        if legacy_failed:
            raise Degraded(f"Legacy 착지실패 {legacy_failed}")
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

    print(f"· 착지 {landed} · 실패 {failed} · Legacy {n_legacy}")
    _report()
    if unknowns or failed or unjudged or legacy_failed:
        raise Degraded(f"미상 {len(unknowns)} · 착지실패 {failed + legacy_failed} · 판정누락 {len(unjudged)}")
    return 0


LEGACY = "Legacy"       # 노션 Status — 재고가 아니다. 사람이 Status 를 바꾸면 다시 재고가 된다


def _land_legacy(g: Gate, legacy: dict[str, tuple[dict, str]], run_id: str) -> tuple[int, int]:
    """Tier 판정 없이 Status=Legacy 로 착지 → 장부 landed. (착지 수, 실패 수)."""
    ok = bad = 0
    for k, (item, why) in legacy.items():
        props = {**_props(item, {"verdict": None, "reason": f"Legacy: {why}"}, run_id), "Status": LEGACY}
        st = g.out(k, "land", {"tier": None, "axis": item["_axis"], "legacy": why},
                   lambda props=props: notion.create(DB, props).require())
        if st != "done":
            bad += st == "failed"
            continue
        ledger.append(TASK, k, ledger.LANDED, page_id=g.row(k, "land")["result"], run=run_id,
                      keys=item.get("_keys") or [k], axis=item["_axis"], legacy=why, name=_summary(item)["name"])
        ok += 1
    return ok, bad


def _summary(item: dict) -> dict:
    s = profile.summarize(item)
    s.update({
        "axis": item.get("_axis", ""),
        "tenure": profile.tenure_months(item),
        "founding": profile.founding_months(item),
        "gap": profile.gap_months(item),
        "undergrad": profile.undergrad_start_year(item),
        "ft_gap": profile.fulltime_gap_months(item),
        "age": profile.estimated_age(item),
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
        "추정 나이": s["age"],
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

def _to_legacy(items: list[tuple[dict, str]], already: int, *, record: bool) -> None:
    """노션 Status=Legacy 로 바꾼다. dry 면 세기만. 실패한 행은 다음 실행에서 다시 걸린다(같은 판정이라)."""
    by: dict[str, int] = {}
    for _, why in items:
        by[why] = by.get(why, 0) + 1
    print(f"· Legacy 기존 {already}명 · 이번에 보냄 {len(items)}명"
          + (" — " + " · ".join(f"{w} {c}" for w, c in sorted(by.items(), key=lambda x: -x[1])) if by else "")
          + ("" if record or not items else " (dry — 노션 안 씀)"))
    if not record:
        return
    for live, why in items:
        f = notion.update(live["_page_id"], DB, {"Status": LEGACY})
        if not f.is_measured:
            print(f"  🔴 {live.get('서비스명')} Legacy 전환 실패: {f.reason}")


def _screened() -> dict[str, dict]:
    """page_id → 마지막 창업 판정 {verdict, company, brief}.
    🔴 다시 올라온(resurfaced) 사람은 그 뒤의 판정만 — 회사·직함이 바뀌었으니 창업 여부도 문안도 다시 쓴다."""
    resurf = _last(RESURFACED, "page_id")
    return {pid: r for pid, r in _last(SCREENED, "page_id").items() if r["ts"] > resurf.get(pid, {}).get("ts", "")}


def _fresh(live: dict) -> str:
    """정렬 날짜 = 값을 마지막으로 잰 날. 산 날(Captured At)이거나 recheck 로 다시 긁은 날(Last Checked) 중 늦은 쪽."""
    return max(str(live.get("Captured At") or ""), str(live.get("Last Checked") or ""))


def _card_input(k: str, live: dict) -> dict:
    # 🔴 One Liner 를 그대로 안 쓴다. 실측 2~95자에 '--'·'CEO' 같은 것도 있었고,
    #    사람이 매번 다시 썼다. claude 가 두 문장과 회사명을 한 번에 만든다.
    return {"key": k, "name": live.get("서비스명"), "company": live.get("회사"),
            "one_liner": live.get("One Liner"), "headline": live.get("Author Headline"),
            "about": live.get("Raw Text"), "reason": live.get("AI Reason"),
            "founding": live.get("창업 개월")}


def _screen(pool: list, need: int, screened: dict, *, record: bool) -> str:
    """판정 없는 사람을 풀 앞에서부터 문안에 넣는다 — 통과자가 `need` 명 모이거나 풀이 끝날 때까지.
    `screened` 를 제자리에서 채운다. `record` 면 장부에도(dry 면 안 쓴다). 실패하면 이유를, 아니면 "".

    🔴 판정과 문안을 한 호출로 받는다 — 통과자의 문안은 장부에 있어 올릴 때 다시 부르지 않는다.
    """
    ok = sum(1 for t in pool if screened.get(t[2]["_page_id"], {}).get("verdict") in brief.PASS)
    todo = [t for t in pool if t[2]["_page_id"] not in screened]
    step = max(need * 2, 10)
    while ok < need and todo:
        chunk, todo = todo[:step], todo[step:]
        b = brief.write([_card_input(k, live) for _, k, live in chunk])
        if not b.is_measured:
            return b.reason
        by_key = {k: live for _, k, live in chunk}
        for x in b.require():
            pid = by_key[x["key"]]["_page_id"]
            rec = {"verdict": x["verdict"], "company": x["company"], "brief": x["brief"]}
            screened[pid] = rec
            if record:
                ledger.append(TASK, x["key"], SCREENED, page_id=pid, **rec)
            ok += x["verdict"] in brief.PASS
    return ""


def card(*, n: int = 5, dry_run: bool = False, gate: Gate | None = None, screen_only: bool = False) -> int:
    """🔴 노션에서 Tier 를 다시 읽는다 — 사람이 고친 게 여기서 반영된다.

    🔴 후보는 **노션 행**에서 뽑는다(장부가 아니라). 장부 landed 1,857행 중 1,234행은
       옛 시스템에서 시드된 행이라 `page_id` 가 없다 — 장부로 후보를 뽑으면 그 사람들이
       **카드에 영원히 안 나온다**(2026-08-19 실측: 1티어 115명 중 96명이 그 상태였다).
       Tier 도 어차피 노션에서 다시 읽으므로, 후보의 정본도 노션인 게 맞다.
    """
    # screen_only: 판정만 장부에 남기고 올리지 않는다 — 문은 off(슬랙·알림 0), 장부는 쓴다
    g = gate or _gate(dry_run or screen_only)
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
                      "Tenure Months", "공백 개월", "본업 공백", "Korea Signal", "추정 나이", "Status", "Captured At",
                      "Last Checked"])
    if not f.is_measured:
        print(f"🔴 노션을 못 읽었다: {f.reason}")
        return 1
    rows = f.require()
    if not screen_only:
        n_off = _sync_labels(g.con)                                          # 정답 = 슬랙 반응 — 매일 여기서 동기화
        if n_off:
            print(f"· 판정과 반응이 갈린 사람 {n_off}명 — judgments(human) 에 반영")

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
    # 🔴 다시 올라온 사람(recheck — 회사·직함이 바뀜)은 한 번 나갔어도 다시 후보다. 그 카드의 문 키는 따로 둔다(gk).
    reopen = _resurfaced_open()
    posted_pages = {r["page_id"] for r in ledger.rows(TASK, ev=POSTED) if r.get("page_id")} - set(reopen)
    carded_keys = ledger.keys(TASK, CARDED)
    key_of_page = {r["page_id"]: r["key"] for r in ledger.rows(TASK, ev=ledger.LANDED)
                   if r.get("page_id")}

    screened = _screened()
    pool = []
    seen_before: dict[str, int] = {}     # 이미 본 사람 (중복 방지)
    filtered: dict[str, int] = {}        # 기준에 안 맞는 사람 (판정)
    to_legacy: list[tuple[dict, str]] = []   # 이번에 Legacy 로 보낼 사람 (record 일 때만 노션에 쓴다)
    n_legacy = 0
    for live in rows:
        pid = live["_page_id"]
        if pid in posted_pages:
            continue
        if live.get("Status") == LEGACY:
            n_legacy += 1
            continue
        # 사람이 이미 손댄 행은 재고도 Legacy 도 아니다 — 건드리지 않고 센다
        url_key = profile.normalize_url(str(live.get("LinkedIn URL") or ""))
        why = ("극초기 DB 에 이미 있음" if url_key and url_key in early else
               "DB로 업데이트 ✅" if live.get("DB로 업데이트") else
               "타깃 ✅" if live.get("타깃") else
               f"Status {live.get('Status')}" if live.get("Status") not in (None, "New") else "")
        if why:
            # 조용히 빼지 않는다 — 몇 명이 왜 빠졌는지 화면에 남긴다
            seen_before[why] = seen_before.get(why, 0) + 1
            continue
        if live.get("Tier") not in _cfg("card_tiers"):
            continue
        # 🔴 [사용자 결정 2026-09-29] 카드 기준에 안 맞으면 **전부** Legacy 다.
        #    노션 행의 재직·공백·창업 개월은 산 날 값이고 아무도 다시 안 잰다(옛 재검토 시스템은 09-26 제거).
        #    장부에 landed 라 다시 사지도 않는다 → 지금 안 맞으면 영영 안 맞는다. 재고로 세면 거짓 재고다.
        #    기준 자체는 [2026-09-01 시니어 기준] — 수집 축과 같은 기준을 여기서도 건다.
        why = axes.legacy_reason(live) or (
            "비창업(문안 판정)" if screened.get(pid, {}).get("verdict") == brief.NOT_FOUNDER else "") or axes.card_reject(live)
        if why:
            to_legacy.append((live, why))
            filtered[why] = filtered.get(why, 0) + 1
            continue
        k = key_of_page.get(pid) or url_key
        if not k or (k in carded_keys and pid not in reopen):
            continue
        pool.append((_ORDER.get(live.get("Tier"), 9), k, live))
    for why, cnt in sorted(filtered.items(), key=lambda x: -x[1]):
        print(f"· 기준에 안 맞아 뺐다 — {why}: {cnt}명")
    for why, cnt in sorted(seen_before.items(), key=lambda x: -x[1]):
        print(f"· 이미 본 사람이라 뺐다 — {why}: {cnt}명")
    # 등급 순, 같은 등급 안에서는 **최근에 잰 사람부터** — 재직·창업 개월은 잰 날 값이라 오래될수록 틀린다
    pool.sort(key=lambda t: _fresh(t[2]), reverse=True)
    pool.sort(key=lambda t: t[0])
    warn_d = constraints.get("linkedin.stock_warn_days")
    record = screen_only or g.level() != "off"
    # 🔴 [사용자 결정 2026-09-29] 창업자가 아니면 안 올린다. 판정은 문안을 쓰는 claude 가 한다 —
    #    직함 목록(Founder OR "Building something new" OR …)은 다음 표현에서 새고,
    #    "기존 회사 대표 승계"처럼 직함으로는 원리상 안 갈리는 경우가 있다(09-29 Founder B).
    # 🔴 탐색 카드 [사용자 결정 2026-10-14] — 하루 n장 중 1장은 판정 하위(Wait·Cut)에서. 위만 올리면 위만 정답을 받아
    #    "하위 판정이 맞았나"를 영영 모른다. 카드 모양은 같다 — 표시하면 반응이 치우친다. 장부 posted 에만 explore.
    explore = n > 1 and not screen_only and any(t[0] >= EXPLORE_FROM for t in pool)
    if pool:
        why = _screen(pool, n - 1 if explore else n, screened, record=record)
        if not why and explore:
            why = _screen(sorted((t for t in pool if t[0] >= EXPLORE_FROM), key=lambda t: _fresh(t[2]), reverse=True),
                          1, screened, record=record)
        if why:
            print(f"🔴 문안 생성 실패: {why}")
            return 1
        drop = [t for t in pool if screened.get(t[2]["_page_id"], {}).get("verdict") == brief.NOT_FOUNDER]
        if drop:
            print(f"· 오늘 판정에서 창업자 아님 {len(drop)}명 — 뺐다")
            for _, _, live in drop:
                print(f"    {live.get('서비스명')}: {screened[live['_page_id']]['brief'][:70]}")
                to_legacy.append((live, "비창업(문안 판정)"))
            pool = [t for t in pool if t not in drop]
    _to_legacy(to_legacy, n_legacy, record=record)
    if screen_only:
        print(f"· 판정 끝 — 창업·준비 {len(pool)}명이 재고다")
        return 0
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
    # 판정을 못 받은 사람은 오늘 안 올린다 — 다음 실행에서 다시 판정한다
    ready = [t for t in pool if t[2]["_page_id"] in screened]
    pick = ready[:n - 1] if explore else ready[:n]
    low = sorted((t for t in ready if t[0] >= EXPLORE_FROM and t not in pick), key=lambda t: _fresh(t[2]), reverse=True)
    probe = low[0][1] if explore and low else None
    pick = pick + low[:1] if probe else ready[:n]
    if not pick:
        print("  ⚠️  문안을 받은 사람이 없다 (다음 실행에서 재시도)")
        return 2
    left_n = len(pool) - len(pick)
    left_d = (left_n + n - 1) // n
    print(f"▶ card — 올릴 수 있는 것 {len(pool)} · 오늘 {len(pick)} "
          f"(남으면 {left_d}일 더)")
    if left_d <= warn_d and g.level() != "off":
        _alert("card", f"남은 사람 {left_n}명 — {left_d}일치다. 바닥나기 전에 기준을 손봐야 한다.",
               key="card:low", head="🟡 *linkedin card* 재고 부족")
    bmap = {k: screened[live["_page_id"]] for _, k, live in pick}

    lines = [f"*오늘의 링크드인 {len(pick)}명*"]
    for _, k, live in pick:
        m = bmap[k]
        co = m["company"] if m["company"] != brief.NO_COMPANY else "스텔스"
        rs = reopen.get(live["_page_id"])
        lines.append(
            f"\n*{live.get('서비스명')}*  {co}  ·  창업 {live.get('창업 개월') or '?'}개월차\n"
            + (f"🔄 다시 올림 · {rs.get('old') or '?'} → {rs.get('new') or '?'}\n" if rs else "")
            + f"{m['brief']}\n"
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
        # 문(gate)은 (키, 종류)마다 한 번이다 — 다시 올린 카드는 resurfaced 날짜를 붙인 키로 한 번 더 열린다
        rs = reopen.get(live["_page_id"])
        gk = f"{k}@{rs['ts'][:10]}" if rs else k
        st = g.out(gk, "card", {"name": live.get("서비스명"), "tier": live.get("Tier"), "ch": ch},
                   lambda body=body: slack.post(ch, body.strip() + "\n\n" + CARD_FOOTER, thread_ts=head_ts()).require())
        if st != "done" or "ts" not in parent:
            if st == "failed":
                print(f"  🔴 {live.get('서비스명')} 답글 실패: {g.row(gk, 'card')['error']}")
            continue
        # 착지 먼저, 장부 나중. ts 를 못 남기면 반응을 영영 못 찾는다.
        ledger.append(TASK, k, CARDED)
        ledger.append(TASK, k, POSTED, ch=ch, slack_ts=g.row(gk, "card")["result"], parent=parent["ts"],
                      **({"gk": gk} if rs else {}), **({"explore": True} if k == probe else {}),
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


def _swept_today() -> bool:
    return any(r.get("day") == ledger.today() for r in ledger.rows(TASK, ev=SWEPT))


def _late_posts(open_posts: dict) -> dict[str, dict]:
    """결론이 ❌·무반응인데 최근 `learn_weeks` 주 안에 올린 카드 — 늦게 붙은 반응을 찾을 대상. ✅ 로 끝난 건 이미 답이 났다."""
    since = (date.fromisoformat(ledger.today()) - timedelta(weeks=_cfg("learn_weeks"))).isoformat()
    res = _last(RESOLVED)
    return {k: {**r, "_res": res[k]} for k, r in _last(POSTED).items()
            if k not in open_posts and k in res and res[k].get("by") in ("expiry", "reaction_no")
            and (r.get("day") or "") >= since and r.get("parent")}


def followup(*, dry_run: bool = False, gate: Gate | None = None) -> int:
    """✅ 를 걷어 연락 문안(outreach.md, Reviewer C 심사역 실무 문안)을 그 스레드에 올린다.

    🔴 사람이 보낸다. 심사역 개인 링크드인 계정으로 나가는 메시지라 우리가 못 보낸다 —
       글만 써서 올리고 복사해서 쓰시게 한다.
    """
    print("▶ followup")
    g = gate or _gate(dry_run)
    dry = g.level() == "off"
    open_posts = _open_posts()
    # 🔴 늦은 반응 [사용자 결정 2026-10-14] — 심사역이 바빠 7일 뒤에 누르기도 한다. 만기된 카드도 최근 N주는
    #    하루 한 번 다시 읽는다. 늦은 ✅ 는 제때 ✅ 와 똑같이 문안·승격·리마인드, 늦은 ❌ 는 정답만 고친다.
    late = {} if dry or _swept_today() else _late_posts(open_posts)
    if not open_posts and not late:
        print("  · 기다리는 건이 없다")
        return 0
    print(f"  · 반응을 기다리는 사람 {len(open_posts)}명" + (f" · 늦은 반응 훑기 {len(late)}명" if late else ""))

    # 🔴 스레드별로 한 번만 부른다. 사람마다 부르면 5명이면 5번이다.
    by_parent: dict[str, list] = {}
    for k, r in [*open_posts.items(), *late.items()]:
        by_parent.setdefault(r["parent"], []).append((k, r))

    picked, fails, read_ok, no_of = [], [], set(), {}
    for parent, rows in by_parent.items():
        got = slack.replies(rows[0][1]["ch"], parent)
        if not got.is_measured:
            fails.append(f"replies {parent}: {got.reason}")
            continue
        read_ok.add(parent)
        react = {m["ts"]: m.get("reactions") or [] for m in got.require()}
        for k, r in rows:
            mine = react.get(r.get("slack_ts") or r.get("ts"), [])     # 09-25 전 행은 슬랙 ts 가 `ts` 칸에 있다(기록 시각을 덮었다)
            yes_by, no_of[k] = _pressers(mine, "reaction_yes"), _pressers(mine, "reaction_no")
            # ✅ 와 ❌ 가 같이 있으면 ✅ — 연락은 누른 분 개인 계정으로 나가고, 관심 있는 사람을 놓치는 쪽이 더 비싸다
            if yes_by:
                picked.append((k, r, yes_by))

    # 🔴 만기. 없으면 ✅ 를 못 받은 사람이 **영원히** 대기줄에 남는다 —
    #    실측 08-31: 31명이 그렇게 쌓여 followup 로그가 83KB 가 됐고, 10분마다
    #    "✅ 없음 (대기 31명)" 만 찍혔다. 반응 지연 실측(11명)은 중앙값 당일 ·
    #    10명이 3일 안이라, 7일이면 사실상 답이 나온 것이다.
    #    🔴 **읽기에 성공한 스레드만** 만기시킨다. 못 읽은 걸 만기시키면 망 오류
    #       한 번에 사람이 조용히 사라진다 — 메일에서 이미 겪은 자리다.
    #    ❌ 만 있으면 닫지 않고 기다린다(다른 분이 ✅ 를 누를 수 있다). 7일째 결론은 ❌ 가 있으면 reaction_no — 보고 거른 것,
    #    없으면 expiry — 안 본 것. 둘은 다르다: 앞의 것만 정답이다.
    due_d = constraints.get("linkedin.followup_days")
    picked_keys = {k for k, _, _ in picked}
    expired = refused = 0
    for k, r in open_posts.items():
        if k in picked_keys or r.get("parent") not in read_ok:
            continue
        if _days_since(r.get("day") or ledger.today()) < due_d:
            continue
        by = "reaction_no" if no_of.get(k) else "expiry"
        if not dry:
            ledger.append(TASK, k, RESOLVED, by=by, no_by=no_of.get(k) or [], name=r.get("name"),
                          slack_ts=r.get("slack_ts") or r.get("ts"), ch=r.get("ch"))
        refused += by == "reaction_no"
        expired += by == "expiry"
    if expired or refused:
        print(f"  · ⏰ {due_d}일 결론 — ❌ {refused}명 · 무반응 {expired}명 (노션·인박스는 그대로)")
    # 늦은 ❌ — 무반응으로 닫힌 카드에 나중에 ❌ 가 붙었다. 정답만 고친다(보고 거른 것)
    for k, r in late.items():
        if k not in picked_keys and no_of.get(k) and r["_res"].get("by") == "expiry" and r.get("parent") in read_ok:
            ledger.append(TASK, k, RESOLVED, by="reaction_no", no_by=no_of[k], late=True, name=r.get("name"),
                          slack_ts=r.get("slack_ts") or r.get("ts"), ch=r.get("ch"))
    if late and all(r.get("parent") in read_ok for r in late.values()):
        ledger.append(TASK, "-", SWEPT, day=ledger.today(), n=len(late))

    if not picked:
        print(f"  · ✅ 없음 (대기 {len(open_posts) - expired - refused}명)")
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

    def _uid(users: list) -> str:
        # _sender 와 같은 사람(명단 안의 먼저 누른 분). 명단 밖이면 먼저 누른 분 — 핑은 명단 없이도 된다.
        who = _reviewers()
        return next((u for u in users if u in who), users[0] if users else "")

    uids = {k: _uid(us) for k, _, us in picked}
    groups: dict[str, list] = {}
    for k, r, us in picked:
        groups.setdefault(_sender(us), []).append((k, r))
    print(f"  · ✅ {len(picked)}명 · 발신자 "
          + " · ".join(f"{w or '🔴 명단 밖'}({len(v)})" for w, v in groups.items()))

    # 한글 이름 — 문안 인사(성 뺀 이름)와 리마인드(전체)가 같은 값을 쓴다. 장부에 남겨 리마인드가 다시 묻지 않는다
    ko = names.to_korean([{"key": k, "name": r.get("name"), "headline": r.get("brief")} for k, r, _ in picked])
    done = 0
    for who, members in groups.items():
        for k, r in members:
            full, given, sure = ko[k]
            orig = r.get("name") or ""
            text = (f"*{full}* 연락 문안"
                    + (f" — {who} 심사역 이름" if who else " — 🔴 발신자 이름을 채워주세요")
                    + ("" if sure or full == orig else f"  _(원문 {orig} — 이름 확인)_")
                    + f"\n```{outreach.write(given, who or 'OOO')}```")
            if dry:
                print(f"\n--- {r.get('name')} ({who or '발신자 미상'}) ---\n{text}")
                continue
            gk = r.get("gk") or k                                   # 다시 올린 카드는 문 키가 다르다(card 와 같은 값)
            st = g.out(gk, "outreach", {"who": who, "name": r.get("name")},
                       lambda r=r, text=text: slack.post(r["ch"], text, thread_ts=r["parent"]).require())
            if st != "done":
                fails.append(f"post {k}: {(g.row(gk, 'outreach') or {}).get('error', st)}")
                continue
            ledger.append(TASK, k, OUTREACH, slack_ts=g.row(gk, "outreach")["result"], by=who, name=r.get("name"))
            pr = _promote(k, r, g)
            ledger.append(TASK, k, RESOLVED, by="reaction_yes", who=who, name=r.get("name"),
                          promoted=pr, uid=uids.get(k),     # uid: 리마인드가 핑할 사람
                          name_ko=full, name_sure=sure, no_by=no_of.get(k) or [], late=k in late)
            done += 1

    print(f"· 문안 {done}/{len(picked)}명 올림")
    for f in fails:
        print(f"  🔴 {f}")
    return 2 if (fails or done < len(picked)) else 0


def _remind_day(pressed: date) -> date:
    """누른 날 + N일. 주말·공휴일이면 다음 평일 — 목·금에 누른 건 월요일.
    🔴 저장하지 않고 매번 센다(_days_since 와 같은 이유). 공휴일 목록을 고치면 아직 안 보낸 건에 바로 반영된다."""
    return workday.next_workday(pressed + timedelta(days=_cfg("remind_after_days")))


def remind(*, dry_run: bool = False, gate: Gate | None = None) -> int:
    """✅ N일 뒤, 누른 분을 채널에서 핑한다 — "김가람(Aco) 님께 연락 보내셔야 합니다".

    🔴 연락은 심사역 개인 계정으로 나가서 우리는 보냈는지 모른다. 그래서 한 번만, 짧게 민다.
    🔴 기한이 지난 건도 보낸다(`<=`). 맥이 자서 하루를 건너뛰어도 다음 평일에 나간다.
    """
    print("▶ remind")
    today = date.today()
    if last := workday.list_ends_soon(today):
        _alert("remind", f"공휴일 목록(calendar.holidays_kr)이 {last} 에서 끝난다 — 다음 해를 채워야 한다",
               key="remind:holidays", head="🟡 *linkedin remind* 공휴일 목록")
    if not workday.is_workday(today):
        print("  · 쉬는 날 — 보내지 않는다")
        return 0
    g = gate or _gate(dry_run)
    dry = g.level() == "off"
    sent_keys = ledger.keys(TASK, REMINDED)
    posts = {r["key"]: r for r in ledger.rows(TASK, ev=POSTED)}
    due = []
    for r in ledger.rows(TASK, ev=RESOLVED):
        if r.get("by") != "reaction_yes" or r["key"] in sent_keys:
            continue
        pressed = date.fromisoformat(ledger._local_day(r["ts"]))
        if pressed.isoformat() < _cfg("remind_since") or _remind_day(pressed) > today:
            continue
        due.append((r, posts.get(r["key"], {})))
    if not due:
        print("  · 오늘 보낼 사람 없음")
        return 0

    # 문안을 올릴 때 정한 이름을 그대로 쓴다(09-29 이후 행). 없으면 여기서 정한다
    ko = names.to_korean([{"key": r["key"], "name": r.get("name"), "headline": p.get("brief")}
                          for r, p in due if not r.get("name_ko")])
    by_name = {v: k for k, v in _reviewers().items()}           # 09-29 전 ✅ 행엔 uid 가 없다 — 성함으로 찾는다
    ch, fails, sent = _cfg("remind_channel"), [], 0
    for r, p in due:
        k, orig = r["key"], r.get("name") or ""
        if r.get("name_ko"):
            name, sure = r["name_ko"], r.get("name_sure", False)
        else:
            name, _, sure = ko[k]
        uid = r.get("uid") or by_name.get(r.get("who") or "")
        co = p.get("company")
        co = "스텔스" if not co or co == brief.NO_COMPANY else co
        ping = f"<@{uid}>" if uid else "✅ 누르신 분"
        who = f"<{quote(p['url'], safe=':/?=&%#')}|{name}>" if p.get("url") else name    # 한글 주소(/in/김가람)는 인코딩해야 슬랙 링크가 안 깨진다
        text = (f"{ping} {who}({co}) 님께 연락 보내셔야 합니다 :excited:"
                + ("" if sure or name == orig else f"  _(원문 {orig} — 이름 확인)_"))
        if dry:
            print(f"  {text}")
            continue
        st = g.out(k, "remind", {"uid": uid, "name": name, "ch": ch},
                   lambda text=text: slack.post(ch, text).require())
        if st != "done":
            fails.append(f"remind {orig}: {(g.row(k, 'remind') or {}).get('error', st)}")
            continue
        ledger.append(TASK, k, REMINDED, slack_ts=g.row(k, "remind")["result"], uid=uid, name=orig, name_ko=name)
        sent += 1
    print(f"· 리마인드 {sent}/{len(due)}명" + (" (dry)" if dry else ""))
    for f in fails:
        print(f"  🔴 {f}")
    return 2 if fails else 0


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
                      "Tenure Months", "공백 개월", "본업 공백", "Korea Signal", "추정 나이", "Status", "Captured At"])
    if not f.is_measured:
        print(f"  ⚠️  재고를 노션으로 못 셌다({f.reason}) — 장부로 센다")
        return _backlog_from_ledger()
    posted_pages = {r["page_id"] for r in ledger.rows(TASK, ev=POSTED) if r.get("page_id")} - set(_resurfaced_open())
    screened = _screened()
    n = 0
    for live in f.require():
        if live["_page_id"] in posted_pages:
            continue
        if live.get("타깃") or live.get("DB로 업데이트"):
            continue
        if live.get("Tier") not in _cfg("card_tiers"):
            continue
        if live.get("Status") not in (None, "New") or axes.card_reject(live):      # Legacy · 사람이 손댄 Status 는 재고가 아니다
            continue
        if screened.get(live["_page_id"], {}).get("verdict") == brief.NOT_FOUNDER:
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


def _reaction_week() -> dict:
    """지난 7일에 결론 난 카드 — ✅·❌·무반응·본 비율, 탐색 카드, 판정↔반응 갈림. 보고서가 쓴다."""
    since = ledger.now(timedelta(days=7))
    posted = _last(POSTED)
    res = [r for r in _last(RESOLVED).values() if r["ts"] >= since]
    by = {b: sum(1 for r in res if r.get("by") == b) for b in ("reaction_yes", "reaction_no", "expiry")}
    seen = by["reaction_yes"] + by["reaction_no"]
    exp = [r for r in res if posted.get(r["key"], {}).get("explore")]
    return {**by, "n": len(res), "seen_rate": seen / len(res) if res else None,
            "explore": (sum(r.get("by") == "reaction_yes" for r in exp), len(exp)),
            "late": sum(1 for r in res if r.get("late"))}


def daily_report(g: Gate, *, pool: int, picked: int) -> None:
    """하루 한 번(평일 · card 뒤) 파일 + 슬랙 요약. 먼저 볼 것 = **심사역 반응**(정답)과 판정이 갈린 사람 — 기준을 깎을 재료다.
    🔴 노션 Tier 를 정답으로 대던 절(🚩)은 지웠다 — AI 가 쓴 값을 AI 판정과 맞대 437/437 일치만 찍었다(10/13)."""
    out = paths.data("linkedin", "reports") / f"{ledger.today()}.md"
    if out.exists():
        return
    today = ledger.today()
    day = {ev: sum(1 for r in ledger.rows(TASK, ev=ev) if str(r.get("ts", "")).startswith(today) or r.get("day") == today)
           for ev in ("landed", "carded", "outreach", "resolved", "promoted")}
    week = ledger.now(timedelta(days=7))
    dist = dict(g.con.execute("SELECT verdict, count(*) FROM judgments WHERE run='live' AND ok=1 AND created>=? GROUP BY verdict", (week,)))
    rw = _reaction_week()
    names_ = {r["key"]: r.get("name") or r["key"] for r in ledger.rows(TASK, ev=POSTED)}
    off = [(k, a, b) for k, a, b in g.con.execute(
        "SELECT h.key, l.verdict, h.verdict FROM judgments h JOIN judgments l ON l.key=h.key AND l.run='live'"
        " WHERE h.run='human' AND h.reason='슬랙 반응' AND h.created>=? ORDER BY h.created DESC", (week,)) if _bucket(a) != b]
    acts = dict(g.con.execute("SELECT kind||':'||status, count(*) FROM actions WHERE created>=? GROUP BY 1", (today,)))
    seen = f"{rw['seen_rate']:.0%}" if rw["seen_rate"] is not None else "-"
    head = (f"카드 {picked}명 · 재고 {pool}명({pool // 5}일치) · ✅ 초안 {day['outreach']} · 승격 {day['promoted']}"
            + f" · 지난 7일 결론 {rw['n']}명(✅ {rw['reaction_yes']} · ❌ {rw['reaction_no']} · 무반응 {rw['expiry']} · 본 비율 {seen})"
            + (f" · 이번 주 판정 {sum(dist.values())}명" if dist else ""))
    md = [f"# 링크드인 하루 보고 — {today}", "", head, "",
          "## 🚩 판정과 반응이 갈린 사람 (지난 7일)", "",
          "판정 Catch 인데 안 고름(본 날의 ❌·무반응) · 판정 Catch 아닌데 ✅ — 기준이 틀린 자리다. 안 본 날은 여기 없다.", ""]
    md += [f"- {names_.get(k, k)} — 판정 {a} · 반응 **{'✅' if b == 'yes' else '안 고름'}**" for k, a, b in off] or ["없음"]
    md += ["", "## 반응", "",
           f"- 탐색 카드(판정 하위에서 1장): ✅ {rw['explore'][0]} / 결론 {rw['explore'][1]} — 하위 판정이 놓친 비율",
           f"- 7일 뒤 늦게 누른 반응: {rw['late']}건 (늦은 ✅ 는 문안·리마인드가 자동으로 나갔다)",
           "", "## 이번 주 판정 분포", "", " · ".join(f"{t} {n}" for t, n in sorted(dist.items(), key=lambda x: -x[1])) or "없음",
           "", "## 오늘 문(actions)", "", " · ".join(f"{k} {v}" for k, v in sorted(acts.items())) or "없음",
           "", "## 고치는 법", "", "```bash", "cd ~/kv",
           "# 기준을 고친다: skills/linkedin/criteria.md → 저장된 사람으로 다시 판정 → 반응(정답)과 맞대기",
           "./.venv/bin/python -m skills.linkedin.run replay [--limit 50]",
           "./.venv/bin/python -m skills.linkedin.run diff --b r-<sha>",
           "# 정답은 심사역의 ✅·❌ 다 — 사람이 따로 달 것이 없다", "```", ""]
    out.write_text("\n".join(md), encoding="utf-8")
    summary = f"*링크드인 하루* — {head}" + (f"\n🚩 판정과 반응이 갈린 사람 {len(off)}명" if off else "") + f"\n전체: `{out}`"
    print(f"· 보고서 {out} | 슬랙 {report.daily('linkedin', summary, _send)}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="linkedin")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--axis", choices=list(axes.AXES)); c.add_argument("--pages", type=int)
    c.add_argument("--dry-run", action="store_true")
    d = sub.add_parser("card"); d.add_argument("-n", type=int, default=5)
    d.add_argument("--dry-run", action="store_true")
    sub.add_parser("screen", help="재고 전체를 창업 판정해 장부에 남긴다 (슬랙 0 · 노션 0) — 재고 수가 정확해진다")
    fu = sub.add_parser("followup", help="✅ 를 걷어 콜드 메시지 초안을 스레드에 올린다")
    fu.add_argument("--dry-run", action="store_true")
    rm = sub.add_parser("remind", help="✅ N일 뒤 누른 분을 채널에서 핑한다 (주말·공휴일은 다음 평일)")
    rm.add_argument("--dry-run", action="store_true")
    rc_ = sub.add_parser("recheck", help="감시 목록 중 30일 된 사람의 프로필을 다시 긁어 회사·직함이 바뀌면 다시 재고로 (돈 · 노션)")
    rc_.add_argument("--dry-run", action="store_true"); rc_.add_argument("--limit", type=int)
    ln = sub.add_parser("learn", help="주 1회 — 반응(정답)에서 특징 표 → 기준 근거 · 수정안(자동 반영 안 함)")
    ln.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    rp = sub.add_parser("replay", help="고친 기준으로 저장된 사람을 다시 판정 (돈 0 · 노션 0)")
    rp.add_argument("--limit", type=int, default=-1); rp.add_argument("--criteria", default=None)
    df = sub.add_parser("diff"); df.add_argument("--a", default="live"); df.add_argument("--b", required=True)
    sub.add_parser("label", help="슬랙 반응(✅·❌)을 정답(human)으로 — card 가 매일 하지만 손으로도")
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
        if a.cmd == "screen":
            # card 의 거르기를 그대로 쓰고 올리지만 않는다: n = 재고 전부, 문은 off, 장부는 쓴다
            return card(n=10_000, screen_only=True)
        if a.cmd == "followup":
            with lock.hold("linkedin-followup"):
                rc = followup(dry_run=a.dry_run)
            if rc and not a.dry_run:
                _alert("followup", f"종료코드 {rc} — ✅ 를 걷거나 초안을 올리지 못했다")
            return rc
        if a.cmd == "remind":
            with lock.hold("linkedin-remind"):
                rc = remind(dry_run=a.dry_run)
            if rc and not a.dry_run:
                _alert("remind", f"종료코드 {rc} — 리마인드가 다 못 나갔다")
            return rc
        if a.cmd == "recheck":
            from . import recheck
            with lock.hold("linkedin-recheck"):
                return recheck.run(dry_run=a.dry_run, limit=a.limit)
        if a.cmd == "learn":
            from . import learn
            with lock.hold("linkedin-learn"):
                return learn.run(dry_run=a.dry_run)
        if a.cmd == "replay":
            crit = Path(a.criteria).read_text(encoding="utf-8") if a.criteria else judge.criteria_text()
            r = judgments.replay(_gate().con, judge.judge_lines, crit, judge.sha(crit), limit=a.limit)
            print(f"replay {r['run']}: 판정 {r['judged']} · 누락 {r['missing']}" + (f" · 🔴 {r['error']}" if r.get("error") else "")
                  + f"\n  다음: linkedin diff --b {r['run']}")
            return 0
        if a.cmd == "diff":
            # 정답(반응) 어휘에 맞춰 Tier 를 yes/no 로 묶는다 — 갈림·일치는 그 기준. 원래 Tier 는 raw 로
            print(judgments.diff_text(judgments.diff(_gate().con, a.a, a.b, bucket=_bucket), a.a, a.b, unit="명"))
            return 0
        if a.cmd == "label":
            print(f"· 판정과 반응이 갈린 사람 {_sync_labels(_gate().con)}명 → human (정답 = 슬랙 ✅·❌)")
            return 0
        if a.cmd == "status":
            print(ledger.summary(TASK))
            print(f"  재고(아직 안 나간 사람) {_backlog()}명")
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
