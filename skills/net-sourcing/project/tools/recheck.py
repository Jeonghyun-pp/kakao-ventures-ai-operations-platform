#!/usr/bin/env python3
"""팔로업 ② — 알려진 스레드 인물의 bio를 **다시 사서**($0.005/인) 변화를 감지한다.

왜 bio인가: 극초기 창업자는 런칭할 때 bio를 새로 쓴다("직장인" → "🚀 Founder of X").
그 한 줄 전이가 우리가 기다리는 바로 그 신호이고, 글($0.025/인, 낡은 글 68% 내재)보다
5배 싸다. 판정은 하지 않는다 — **텍스트가 달라졌는가**만 본다(공백 정규화).
의미 판단("의미 있는 변화인가")은 judge/LLM의 몫이다(사용자 결정 #8: bio는 LLM이 읽는다).

대상(cadence — config `recheck`):
  · 우선   = 타깃 ✅(사람) 또는 Tier=Catch(judge)  → priority_days(기본 30일)마다
  · 빌더   = 빌더 ✅(기계 라벨, 우선군 제외)        → builder_days(기본 90일)마다
  · 그 외  = 재확인 안 함 — 재조우(패시브 레인)가 커버하고, cut까지 다시 사는 건 낭비다.
  Last Checked가 비었거나 기한이 지난 사람만. `max_people`로 1회 상한(기본 100명=$0.50).

산출(노션 PATCH):
  · 변화       → Author Headline 갱신 + 재검토 ✅ + Last Checked
  · 무변화     → Last Checked 만
  · 새 bio가 빈데 저장본이 있음 → **Headline을 지우지 않는다**(액터 오류/비공개 전환 의심)
                 + Last Checked 만 + 로그. 재검토도 안 켠다(판단 불가를 변화로 치지 않는다).
  🔴 재검토는 **켜기만** 한다 — 확인 후 끄는 건 사람이다(운영 규약).

수동 전용 — 크론/파이프라인에 연결하지 않는다(주기 판단도 사람이 한다).
사용:
  ./.venv/bin/python main.py recheck --dry-run    # 대상·비용만 ($0)
  ./.venv/bin/python main.py recheck              # 실행 (상한 max_people)
  ./.venv/bin/python main.py recheck --limit 2    # 스모크
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent

# backfill_bio와 **삼중 일치 필수** (tools/estimate_cost.py BIO_ACTOR 도 같은 값):
# 이 액터는 런 시작 $0 + $0.005/인 — 재확인 경제성의 근거다.
ACTOR = "apify~threads-profile-api-scraper"


def _load(cfg_path: Path = PROJ / "config.yaml"):
    import yaml
    return yaml.safe_load(cfg_path.read_text(encoding="utf-8"))


def _norm(s: str) -> str:
    """공백 정규화만 — 판정하지 않는다. 개행/연속공백 차이는 변화가 아니다."""
    return " ".join((s or "").split())


def _targets(cfg: dict, token: str) -> list[dict]:
    """재확인 대상 수집 — base + 스레드 채널 DB에서 우선군/빌더군을 뽑아 cadence 판정.

    쿼리 필터는 군(群) 소속만 거르고, cadence(Last Checked 경과)는 **클라이언트에서** 판정한다
    — 노션 date 필터로 '비었거나 N일 지남' OR 조합을 짜는 것보다 단순하고, 대상 수가 수백이라 싸다.
    """
    from core import followup, notion

    rc = cfg.get("recheck") or {}
    cad = rc.get("cadence") or {}
    p_days = int(cad.get("priority_days", 30))
    b_days = int(cad.get("builder_days", 90))
    today = date.today()

    ncfg = cfg.get("notion") or {}
    dbs = [str(ncfg.get("db_id", "")).split("://", 1)[-1]]
    th = (ncfg.get("channel_db_ids") or {}).get("스레드")
    if th:
        dbs.append(str(th).split("://", 1)[-1])

    # ★ [2026-07-20] 워치리스트 폐기로 편입된 명단(recheck.always_include)을 **핸들로** 더한다.
    #   노션 필터에 or 절로 넣으면 명단이 길어질 때 쿼리가 비대해지므로, 군(群) 조회는 그대로 두고
    #   편입 명단은 아래에서 **핸들 매칭**으로 합친다(라벨을 건드리지 않는 게 핵심 — config 주석 참조).
    always = {str(h).strip().lstrip("@").lower()
              for h in (rc.get("always_include") or [])}

    flt = {"or": [
        {"property": "타깃", "checkbox": {"equals": True}},
        {"property": "빌더", "checkbox": {"equals": True}},
        {"property": "Tier", "select": {"equals": "Catch"}},
    ]}
    if always:
        flt["or"].append({"property": "Author Username",
                          "rich_text": {"is_not_empty": True}})   # 편입분은 아래서 핸들로 거른다

    out: list[dict] = []
    seen_handles: set[str] = set()
    for db in dict.fromkeys(d for d in dbs if d):
        url = f"{notion.API}/databases/{db}/query"
        payload: dict = {"filter": flt, "page_size": 100}
        while True:
            r = notion._write("POST", url, token, payload)
            if r.status_code != 200:
                print(f"🔴 recheck: DB {db[:8]}… 조회 실패 {r.status_code}: {r.text[:200]}")
                break
            d = r.json()
            for page in d.get("results", []):
                props = page.get("properties", {})
                if not notion._is_threads_row(props):
                    continue                      # base DB의 링크드인 행 등 배제(핸들이 없다)
                h = notion._plain(props.get("Author Username")).strip().lstrip("@").lower()
                if not h or h in seen_handles:
                    continue
                target = bool((props.get("타깃") or {}).get("checkbox"))
                builder = bool((props.get("빌더") or {}).get("checkbox"))
                tier = ((props.get("Tier") or {}).get("select") or {}).get("name")
                in_group = target or builder or tier == "Catch"
                if not in_group and h not in always:
                    continue                      # 편입 명단도 아니면 대상 아님
                prio = target or tier == "Catch" or h in always
                due_days = p_days if prio else b_days
                lc = ((props.get("Last Checked") or {}).get("date") or {}).get("start")
                if lc:
                    try:
                        checked = date.fromisoformat(lc[:10])
                    except ValueError:
                        checked = None
                    if checked and checked > today - timedelta(days=due_days):
                        continue                  # 아직 기한 안 됨
                seen_handles.add(h)
                out.append({
                    "pid": page["id"], "handle": h, "db": db,   # db = 이 행이 사는 DB(스키마가 다르다)
                    "prio": prio,
                    "last_checked": lc or "",
                    "old_bio": notion._plain(props.get("Author Headline")),
                })
            if not d.get("has_more"):
                break
            payload["start_cursor"] = d["next_cursor"]
    # 우선군 먼저, 그 안에서는 오래 안 본 사람 먼저(빈 Last Checked = 가장 오래됨)
    out.sort(key=lambda x: (not x["prio"], x["last_checked"]))
    return out


def run(dry_run: bool = False, limit: int = 0) -> int:
    import yaml  # noqa: F401  (config 로더가 쓴다)
    from adapters import apify
    from core import notion
    from main import load_env

    load_env()
    cfg = _load()
    apify.configure(cfg)                          # 🔴 누락 시 예산 트립·raw 아카이브가 안 돈다
    token = notion._token()
    if not token:
        print("🔴 NOTION_TOKEN 없음")
        return 1

    rc = cfg.get("recheck") or {}
    cap = int(rc.get("max_people", 100))
    batch = int(rc.get("batch", 50))

    tg = _targets(cfg, token)
    total_due = len(tg)

    # ★ 대상이 사는 DB들의 property 이름을 미리 모은다. 행마다 GET하면 수백 번 친다.
    #   🔴 DB마다 스키마가 다르다(base 49칸 vs 스레드 채널 DB 35칸) — 없는 칸에 쓰면 400이다.
    import requests as _rq
    schemas: dict[str, set] = {}
    for _db in {x["db"] for x in tg}:
        try:
            _r = _rq.get(f"{notion.API}/databases/{_db}", headers=notion._headers(token), timeout=30)
            schemas[_db] = set((_r.json().get("properties") or {})) if _r.status_code == 200 else set()
            if _r.status_code != 200:
                print(f"⚠️  DB {_db[:8]}… 스키마 조회 실패 {_r.status_code} — "
                      f"사유/요약 칸은 건너뛴다(본체는 정상 동작)")
        except Exception as e:                    # noqa: BLE001
            schemas[_db] = set()
            print(f"⚠️  DB {_db[:8]}… 스키마 조회 예외({e}) — 사유/요약 칸 건너뜀")
    if limit:
        tg = tg[:limit]
    elif cap and len(tg) > cap:
        print(f"· 기한 도래 {total_due}명 > 상한 {cap}명 — 앞 {cap}명만(우선군→오래된 순). "
              f"나머지는 다음 실행에.")
        tg = tg[:cap]
    if not tg:
        print("✅ 재확인 기한이 된 사람이 없다 — 할 일 없음")
        return 0

    est = len(tg) * 0.005
    n_prio = sum(1 for t in tg if t["prio"])
    print(f"대상 {len(tg)}명 (우선군 {n_prio} · 빌더군 {len(tg) - n_prio}) · **추정 ${est:.2f}**")
    if dry_run:
        for t in tg[:10]:
            print(f"  · @{t['handle']}  {'[우선]' if t['prio'] else '[빌더]'}"
                  f"  last_checked={t['last_checked'] or '없음'}")
        if len(tg) > 10:
            print(f"  … 외 {len(tg) - 10}명")
        print("(--dry-run — 아무것도 안 샀다)")
        return 0

    before = apify.usage_usd()
    got: dict[str, dict] = {}
    handles = [t["handle"] for t in tg]
    for i in range(0, len(handles), batch):
        chunk = handles[i:i + batch]
        try:
            items = apify.run_actor(ACTOR, {"usernames": chunk}, timeout=600)
        except Exception as e:                    # noqa: BLE001
            print(f"⚠️  배치 {i // batch + 1} 실패({len(chunk)}명): {e} — 건너뛰고 계속")
            continue
        for it in items:
            u = (it.get("username") or "").strip().lstrip("@").lower()
            if u:
                # ★ [2026-07-20] `latestPosts`는 **같은 호출에 이미 딸려 온다** — 여태 받아서
                #   버리고 있었다(실측: 632/639명 보유, 본문 98%). 새 글 감지 추가비용 = $0.
                #   🔴 최신 글은 반드시 followup.newest_post(=max(taken_at))로 고른다.
                #     lp[0]을 쓰면 실측 40.1%(243/606명)에서 틀린다.
                got[u] = {"bio": (it.get("biography") or it.get("bio") or "").strip(),
                          "followers": it.get("follower_count") or it.get("followers_count"),
                          "newest": followup.newest_post(it.get("latestPosts"))}
        print(f"· 배치 {i // batch + 1}: {len(chunk)}명 → {len(items)}건 "
              f"(누적 추정 ${apify.spent_usd():.2f})")

    today = date.today().isoformat()
    changed = same = vanished = miss = fail = 0
    newposts = baselined = 0
    fstate = followup.load()
    for t in tg:
        p = got.get(t["handle"])
        if not p:
            miss += 1                             # 미수신 — 마킹 안 함, 다음 실행에 재시도
            continue
        props: dict = {"Last Checked": {"date": {"start": today}}}
        fkey = followup.key_threads(t["handle"])
        frec = fstate.get(fkey) or {}
        newest = p.get("newest")
        reasons: list[str] = []
        summary: list[str] = []

        old, new = _norm(t["old_bio"]), _norm(p["bio"])
        if new and new != old:
            # 변화(빈 → 참, 참 → 다른 참 둘 다) — 갱신하고 사람에게 표시.
            props["Author Headline"] = {"rich_text": [{"text": {"content": p["bio"][:1900]}}]}
            props["재검토"] = {"checkbox": True}
            changed += 1
            reasons.append("프로필변화")
            summary.append(f"bio: 「{old[:60] or '(비어있음)'}」 → 「{new[:60]}」")
            print(f"  ✦ 변화 @{t['handle']}: 「{old[:40] or '(비어있음)'}」 → 「{new[:40]}」")
            followup.record("bio_change", fkey, {"old": old[:200], "new": new[:200]})

        # ★ 새 글 — bio 변화와 **독립**이다. 둘 다면 사유가 둘 다 붙는다.
        if followup.is_new_post(frec, newest):
            props["재검토"] = {"checkbox": True}
            newposts += 1
            reasons.append("새글")
            age = followup.post_age_days(newest)
            summary.append(f"새 글({age:.0f}일 전): {newest['text'][:120]}")
            print(f"  ✦ 새 글 @{t['handle']} ({age:.0f}일 전): {newest['text'][:50]}")
            followup.record("new_post", fkey,
                            {"post_id": newest["id"], "code": newest["code"],
                             "text": newest["text"][:300], "likes": newest.get("likes")})
        elif newest and not frec.get("last_post_ts"):
            baselined += 1                        # 첫 관찰 = 기준선 수립(신호 아님)
        elif not new and old:
            # 저장본이 있는데 새 bio가 비었다 — 지우지 않는다(비공개 전환/액터 오류 의심).
            vanished += 1
        else:
            same += 1
        if p.get("followers") is not None:
            props["Followers"] = {"number": int(p["followers"])}
        # ★ 사람의 주의력이 병목이다 — "재검토 ✅"만 보면 뭐가 바뀐지 행을 열어봐야 안다.
        #   사유(무엇이)와 요약(어떻게)을 칸으로 준다. 🔴 스키마에 없으면 _write가 400을 내므로
        #   **존재하는 필드만** 넣는다(CRITICAL #4와 같은 원칙).
        schema = schemas.get(t["db"], set())
        if reasons and "재검토 사유" in schema:
            props["재검토 사유"] = {"select": {"name": reasons[0]}}
        if summary and "변화 요약" in schema:
            props["변화 요약"] = {"rich_text": [{"text": {"content": " / ".join(summary)[:1900]}}]}
        r = notion._write("PATCH", f"{notion.API}/pages/{t['pid']}", token, {"properties": props})
        if r.status_code != 200:
            fail += 1
            print(f"⚠️  PATCH 실패 @{t['handle']}: {r.status_code} {r.text[:120]}")
            continue                              # 노션에 못 썼으면 상태도 전진시키지 않는다
        # 🔴 노션 PATCH가 성공한 뒤에만 상태를 전진시킨다. 먼저 찍으면 착지에 실패한 사람이
        #   '봤다'로 기록돼 다음 주기까지 안 보인다(백필 커서·층 cadence와 같은 규율).
        followup.mark_checked(fstate, fkey,
                              last_post_ts=(newest or {}).get("ts"),
                              last_post_id=(newest or {}).get("id"))

    after = apify.usage_usd()
    real = (after - before) if (before is not None and after is not None) else None
    followup.save(fstate)
    print(f"\n🎉 recheck 완료: bio변화 {changed} · **새 글 {newposts}**(둘 다 재검토 ✅) · "
          f"무변화 {same} · bio소실의심 {vanished}(보존) · 미수신 {miss} · PATCH실패 {fail}")
    if baselined:
        print(f"   · 첫 관찰 {baselined}명은 기준선만 잡았다(다음 실행부터 새 글이 잡힌다)")
    print(f"💰 실제 과금 ${real:.2f} / 추정 ${apify.spent_usd():.2f}" if real is not None
          else f"💰 추정 ${apify.spent_usd():.2f}")
    return 0 if not fail else 1


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    raise SystemExit(run(dry_run=a.dry_run, limit=a.limit))
