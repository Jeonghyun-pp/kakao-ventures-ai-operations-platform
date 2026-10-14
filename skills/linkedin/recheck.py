"""재확인 — 이미 아는 사람의 프로필을 30일마다 다시 긁어, 회사·직함이 바뀐 사람을 다시 재고로 올린다 [사용자 결정 2026-10-14].

## 왜 (없으면 무엇을 잃나)

노션 행의 재직·창업·공백 개월은 **산 날 값**이고, 장부에 landed 라 다시 사지도 않는다. 그래서
- 카드 기준에 안 맞아 Legacy 로 간 사람(1,444명)은 그 뒤 창업해도 영영 안 보인다.
- ❌("이번엔 관심 없음")·무반응으로 끝난 사람도 다시 안 나온다 — ❌ 는 영구가 아니다.
우리가 찾는 건 **변화**다: 회사명 `Stealth` → `주식회사 XX`, 직함 `PM` → `Founder`. 링크드인에서 얻을 수 있는 가장 이른 신호다.
옛 시스템에 같은 레인이 있었다(kv-automation `legacy-final-2026-09-26` tools/recheck_linkedin.py, 7/21 스모크 5명 $0.02) — 09-26 정리 때 같이 지워졌다.

## 대상 — 원칙

**사람 자체가 대상이 아닌 경우(`axes.legacy_reason`: 국적·나이)만 빼고, "그때 안 맞았던" 사람은 전부 본다.**
사유별 목록(리크루팅·컨설팅 빼기 등)으로 줄이지 않는다 — 그 사람이 나중에 창업하면 목록 밖이라 조용히 샌다.
빼는 것: ✅ 받은 사람(승격됨) · 사람이 손댄 행(타깃·DB로 업데이트·극초기·Status 가 New/Legacy 밖) · 아직 결론 안 난 카드 · 이미 다시 올라와 카드를 기다리는 사람 · 아직 안 나간 재고(card 가 볼 사람).

## 변화 = 회사·직함 지문(profile.fingerprint)이 다르다

헤드라인·소개글은 자주 바뀌어 잡음이다 — 기록만 하고 다시 올리지 않는다.
기준 지문 = 마지막 checked → 없으면 원본 아카이브의 산 날 item(검색 액터) → 그것도 없으면 이번 값을 기준으로만 남긴다(변화로 안 친다).
바뀌면: 노션 행 갱신(재직·창업·공백·헤드라인·Tier·변화 요약·Last Checked · Status=New) → 장부 resurfaced. card 가 그 사람을 다시 후보로 보고
창업 판정·카드 기준을 새로 건다(안 맞으면 다시 Legacy — 감시 목록엔 남는다).
"""
from __future__ import annotations

import glob
import json
import re
from datetime import date, timedelta

from kvlib import judgments, ledger, paths
from kvlib.gate import Gate

from . import axes, judge, profile
from . import run as R

SLUG = "harvestapi~linkedin-profile-scraper"
# 🔴 모드 문자열은 **액터가 받는 정확한 값**이다. 오타 나면 조용히 다른 요금제로 돈다(옛 레인 주석).
MODE = "Profile details no email ($4 per 1k)"
USD_PER_PROFILE = 0.004
# 다시 쓰는 노션 칸 — 재직·창업·공백·헤드라인은 바뀐 직장에서 다시 잰 값, Tier 는 새 판정. 국적·나이는 직장이 바뀌어도 안 바뀐다
REFRESH = ("Author Headline", "One Liner", "Raw Text", "Tier", "AI Reason", "Tenure Months", "창업 개월",
           "공백 개월", "본업 공백", "Homepage", "Match Reason")


def _days(since: str) -> int:
    return (date.fromisoformat(ledger.today()) - date.fromisoformat(since[:10])).days


def _search_files() -> list[str]:
    """검색 액터 원본 — kv 수집분 + 옛 시스템 보관분(07~09월 산 사람). 재확인 원본(profile-scraper)은 기준이 아니라 뺀다."""
    kv = glob.glob(str(paths.data("linkedin", "raw", "_actor") / f"{axes.SLUG}_*.json"))
    old = glob.glob(str(paths.data_root() / "_archive" / "*" / "*" / "raw" / "_actor" / "*linkedin-profile-search_*.json"))
    return sorted(old) + sorted(kv)


def raw_by_key() -> dict[str, dict]:
    """사람 키(URN·URL 전부, 프로필 URL 포함) → 처음 산 날 item."""
    out: dict[str, dict] = {}
    for fn in _search_files():
        try:
            items = json.load(open(fn)).get("items") or []
        except Exception:                                                    # noqa: BLE001 — 깨진 파일 하나로 멈추지 않는다
            continue
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            for k in [*profile.match_keys(it), profile.normalize_url(str(it.get("linkedinUrl") or ""))]:
                if k:
                    out.setdefault(k, it)
    return out


_SLUG = re.compile(r"^[A-Za-z0-9\-_%.]+$")


def public_id(t: dict, raw: dict[str, dict], checked: dict[str, dict]) -> str:
    """액터가 받는 publicIdentifier. 🔴 노션 `LinkedIn URL` 은 대부분 `/in/ACwAA…`(URN)이고, 옛 행의 `Author Username` 엔
    표시 이름("Minsoo Kim")이 들어 있다 — 그걸 넘기면 전원 404 다(10/14 스모크 5/5). 순서:
    지난 재확인이 받은 값 → 산 날 원본의 publicIdentifier(10/14 실측 1,036/1,049) → 슬러그 꼴인 Username. 못 찾으면 빈 값(이번엔 건너뛴다)."""
    live = t["live"]
    url = profile.normalize_url(str(live.get("LinkedIn URL") or ""))
    for v in ((checked.get(t["page_id"]) or {}).get("pub"),
              (raw.get(t["key"]) or raw.get(url) or {}).get("publicIdentifier"),
              str(live.get("Author Username") or "").strip()):
        if v and _SLUG.match(v) and not v.startswith("ACwAA") and not v.startswith("ACoAA"):
            return v
    return ""


def targets(rows: list[dict], early: set[str], raw: dict[str, dict] | None = None) -> list[dict]:
    """감시 목록 중 기한(`recheck_days`)이 된 사람 — 기준일이 오래된 순. 원칙은 모듈 docstring."""
    res, posted = R._last(R.RESOLVED), R._last(R.POSTED, "page_id")
    checked, reopen = R._last(R.CHECKED, "page_id"), R._resurfaced_open()
    key_of_page = {r["page_id"]: r["key"] for r in ledger.rows(R.TASK, ev=ledger.LANDED) if r.get("page_id")}
    due_d = R._cfg("recheck_days")
    out = []
    for live in rows:
        pid = live["_page_id"]
        url_key = profile.normalize_url(str(live.get("LinkedIn URL") or ""))
        if (live.get("타깃") or live.get("DB로 업데이트") or (url_key and url_key in early)
                or live.get("Status") not in ("New", R.LEGACY, None) or axes.legacy_reason(live) or pid in reopen):
            continue
        k = (posted.get(pid) or {}).get("key") or key_of_page.get(pid) or url_key
        if pid in posted:                                                    # 카드로 나간 사람 — ❌·무반응으로 끝났을 때만
            r = res.get(k)
            if not r or r["ts"] < posted[pid]["ts"] or r.get("by") == "reaction_yes":
                continue
            # 09-25 전 장부 줄은 `ts` 칸에 슬랙 ts(숫자)가 있다 — 그땐 게시일로
            base = r["ts"] if r["ts"][:2] == "20" else str(posted[pid].get("day") or "2000-01-01")
        elif live.get("Status") == R.LEGACY:                                 # 카드 기준에 안 맞았던 사람
            base = str(live.get("Captured At") or "2000-01-01")
        else:
            continue                                                         # 아직 안 나간 재고 — card 가 본다
        base = max(base[:10], (checked.get(pid) or {}).get("ts", "")[:10])
        if _days(base) >= due_d:
            t = {"key": k, "page_id": pid, "base": base, "live": live, "axis": ((live.get("Axis") or [""])[0])}
            t["public_id"] = public_id(t, raw or {}, checked)
            out.append(t)
    out.sort(key=lambda t: t["base"])
    return out


def _fp_text(fp: str) -> str:
    """`v2|회사|직함` → `회사 · 직함` (사람이 읽는 꼴)."""
    parts = fp.split("|")
    return " · ".join(x for x in parts[1:] if x) or "(현직 없음)"


def run(*, dry_run: bool = False, limit: int | None = None, gate: Gate | None = None) -> int:
    g = gate or R._gate(dry_run)
    today = ledger.today()
    print(f"▶ recheck {today}")
    f = R.notion.query(R.notion.writable_id(R.DB),
                       ["LinkedIn URL", "Author Username", "Tier", "서비스명", "Status", "타깃", "DB로 업데이트", "Captured At",
                        "Korea Signal", "추정 나이", "Axis"])
    fe = R.notion.query(R.notion.writable_id(R.EARLY), ["링크"])
    if not f.is_measured or not fe.is_measured:
        print(f"🔴 노션을 못 읽었다: {f.reason if not f.is_measured else fe.reason}")
        return 1
    early = {profile.normalize_url(str(r.get("링크") or "")) for r in fe.require()} - {""}
    raw = raw_by_key()
    due_all = targets(f.require(), early, raw)
    due = [t for t in due_all if t["public_id"]]
    cap = limit or R._cfg("recheck_max")
    todo = due[:cap]
    print(f"· 기한 된 사람 {len(due_all)}명 (ID 못 찾음 {len(due_all) - len(due)}) · 이번 {len(todo)}명 (상한 {cap})"
          f" · 추정 ${len(todo) * USD_PER_PROFILE:.2f}")
    if not todo:
        return 0

    # ---- 구매 — 문(buy)을 지난다. 원본은 통로가 아카이브한다
    batch, got_items, unknown = R._cfg("recheck_batch"), {}, []
    for i in range(0, len(todo), batch):
        chunk, box = todo[i:i + batch], {}

        def buy(chunk=chunk, box=box):
            box["f"] = R.apify.run_actor(SLUG, {"publicIdentifiers": [t["public_id"] for t in chunk], "profileScraperMode": MODE},
                                         skill=R.TASK, est_usd=round(len(chunk) * USD_PER_PROFILE, 3),
                                         timeout=max(R.constraints.get("apify.run_timeout_sec"), len(chunk) * 6))   # 100명이면 1페이지보다 오래 걸린다 — 인원에 비례해 늘린다
            return len(box["f"].require())

        # 문 키 = 그날 + 배치 내용(첫 사람·인원). 순번으로 잡으면 앞선 시험 실행이 그날 첫 배치를 '이미 샀다'로 막는다(10/14)
        st = g.out(f"recheck/{today}/{chunk[0]['public_id']}+{len(chunk)}", "buy", {"n": len(chunk), "est_usd": len(chunk) * USD_PER_PROFILE}, buy)
        if "f" not in box:
            print(f"  · 배치 {i // batch + 1} 구매 {st} — 문이 {g.level()} 이다" if st == "dry" else f"  · 배치 {i // batch + 1} 오늘 이미 샀다")
            continue
        if st != "done":
            unknown.append(f"배치 {i // batch + 1}: {box['f'].reason}")
            continue
        for it in box["f"].require():
            pid_ = str(it.get("publicIdentifier") or "").strip().lower()
            if pid_:
                got_items[pid_] = it
            elif it.get("error"):
                print(f"  · 액터 응답 오류: {it.get('error')} ({it.get('status')})")
    if g.level() == "off":
        return 0

    # ---- 비교
    checked = R._last(R.CHECKED, "page_id")
    changed, same, miss = [], 0, 0
    for t in todo:
        it = got_items.get(t["public_id"].lower())
        if not it:
            miss += 1                                                        # 못 받은 사람 — 기록 안 함, 다음 주에 다시
            continue
        new = profile.fingerprint(it)
        old = (checked.get(t["page_id"]) or {}).get("fp") or (profile.fingerprint(raw[t["key"]]) if t["key"] in raw else "")
        ch = bool(old) and old != new
        ledger.append(R.TASK, t["key"], R.CHECKED, page_id=t["page_id"], fp=new, pub=t["public_id"], headline=(it.get("headline") or "")[:200],
                      changed=ch, baseline=not old)
        if ch:
            changed.append((t, it, old, new))
        else:
            same += 1
    print(f"· 받음 {len(todo) - miss}/{len(todo)} · 변화 {len(changed)} · 그대로 {same}" + (f" · 🔴 {'; '.join(unknown)}" if unknown else ""))

    # ---- 바뀐 사람 — 새 기준으로 판정 → 노션 갱신(refresh 문) → resurfaced
    if changed:
        sums = []
        for t, it, _, _ in changed:
            s = R._summary({**it, "_axis": t["axis"]})
            s["key"] = t["key"]
            sums.append(s)
        crit = judge.criteria_text()
        v = judge.judge_people(sums, criteria=crit)
        judgments.save(g.con, judgments.LIVE, judge.sha(crit), {s["key"]: judge._line(s) for s in sums},
                       v.require() if v.is_measured else [])
        vmap = {x["key"]: x for x in (v.require() if v.is_measured else [])}
        for (t, it, old, new), s in zip(changed, sums):
            judgments.set_page(g.con, t["key"], t["page_id"])
            vv = vmap.get(t["key"])
            if not vv:
                print(f"  ⚠️  {t['live'].get('서비스명')} 판정 못 받음 — 다음 주에 다시(checked 를 되돌리지 않는다: 변화는 기록됐다)")
                continue
            full = R._props({**it, "_axis": t["axis"]}, vv, f"recheck-{today}")
            props = {k: full[k] for k in REFRESH if k in full}
            summ = f"{today} {_fp_text(old)} → {_fp_text(new)}"
            props.update({"Status": "New", "Last Checked": today, "변화 요약": summ})
            st = g.out(f"{t['page_id']}@{today}", "refresh", {"name": t["live"].get("서비스명"), "change": summ},
                       lambda props=props, t=t: R.notion.update(t["page_id"], R.DB, props).require())
            if st != "done":
                print(f"  🔴 {t['live'].get('서비스명')} 노션 갱신 {st}: {(g.row(t['page_id'] + '@' + today, 'refresh') or {}).get('error', '')}")
                continue
            ledger.append(R.TASK, t["key"], R.RESURFACED, page_id=t["page_id"], old=_fp_text(old), new=_fp_text(new),
                          tier=vv["verdict"], name=t["live"].get("서비스명"))
            print(f"  ✦ {t['live'].get('서비스명')}: {_fp_text(old)} → {_fp_text(new)} ({vv['verdict']})")
    return 2 if unknown else 0
