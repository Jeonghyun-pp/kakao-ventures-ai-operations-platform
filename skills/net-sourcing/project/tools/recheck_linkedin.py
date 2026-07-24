#!/usr/bin/env python3
"""링크드인 **액티브 재확인 레인** — 이미 아는 사람의 프로필 변화를 다시 본다. ($0.004/인)

## 왜 이게 필요한가 (없으면 무엇을 잃나)

링크드인엔 재확인 경로가 **아예 없었다.**
- `recheck.py`는 스레드 전용이다(`_is_threads_row`로 링크드인 행을 배제한다).
- `linkedin_seen.json`은 **조회 목록이 아니라 사후 필터**다 — 재조우는 "검색축이 우연히 다시
  물어줄 때"만 일어난다.
- 그런데 발굴 축엔 **`재직 1년 미만` 서버측 필터**가 걸려 있고, 링크드인 타깃 정답지 19명은
  **전원 tenure<6개월**이다. → 몇 달 뒤 이들이 필터 밖으로 나가면 **영구 이탈**한다.

우리가 찾는 건 **변화**다: 회사명 `Stealth` → `주식회사 XX`, 직함 `PM` → `Founder`.
그게 링크드인에서 얻을 수 있는 가장 이른 신호인데, 한 번 본 사람을 영영 안 보면 놓친다.

## 이탈임박 = tenure **[9, 12)개월** (하한 9는 사용자 확정 2026-07-20)
30일 주기면 9·10·11개월에 **3회 관찰**할 수 있다 → 12개월 필터를 넘기 전에 잡는다.
한 번 실패해도 두 번 남는다는 게 9를 고른 이유다(10이면 2회, 6이면 사실상 전원 상시 플래그).
🔴 상한 12가 있어야 한다 — 없으면 tenure 72·89개월인 사람이 '임박'으로 잡힌다(dry-run에서 발견).

## ✅ 유료 경로 검증됨 (2026-07-21 · --limit 5 스모크 $0.02)
`data/raw/_actor/EXAMPLE_ID_f9fe604cfa09.json` 원본과 대조:
`publicIdentifier` 누락 0/5 · `headline` 5/5 보유 — 코드가 읽는 두 키가 실측 응답에 정확히 존재.
변화 3건 감지 · 노션 PATCH 실패 0 · followup_state 5명 기록 · 이벤트 로그 3건 append 확인.
(그래도 새 액터를 검토할 땐 이 대조를 반복하라 — "액터가 주는 키 이름 추측"이 이 프로젝트의 반복 지뢰다.)

사용:
  ./.venv/bin/python tools/recheck_linkedin.py --dry-run    # 대상·비용만 ($0)
  ./.venv/bin/python tools/recheck_linkedin.py --limit 5    # 스모크 (~$0.02)
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from core import followup, notion                 # noqa: E402
from main import load_env                         # noqa: E402

ACTOR = "harvestapi~linkedin-profile-scraper"     # adapters/linkedin.py::SCRAPER_ACTOR와 같은 값
# 🔴 모드 문자열은 **액터가 받는 정확한 값**이다. 오타 나면 조용히 다른 요금제로 돈다.
SCRAPER_MODE = "Profile details no email ($4 per 1k)"
BATCH = 100                                       # 실측된 배치 크기(linkedin.py:584)
CADENCE_DAYS = 30                                 # 링크드인 지문 diff 주기
# 🔴 이탈임박은 **구간**이지 하한이 아니다. 처음에 `tenure >= 9`로 짰다가 dry-run에서
#   tenure 72·89개월인 사람이 "이탈임박"으로 잡히는 걸 보고 고쳤다 — 그들은 임박이 아니라
#   **이미 한참 전에 필터 밖으로 나간** 사람이다(발굴 축은 `재직 1년 미만`만 본다).
#   창은 [9, 12): 12개월 필터를 넘기 **직전** 3개월. 30일 주기면 9·10·11개월에 3회 본다.
#   12개월을 이미 넘긴 사람은 우선군(타깃/빌더/Catch)일 때만 본다 — 그게 우선군의 역할이다.
EXIT_RISK_MIN_MONTHS = 9
EXIT_RISK_MAX_MONTHS = 12

_IN = re.compile(r"linkedin\.com/in/([^/?#]+)", re.I)


def public_id(url: str) -> str:
    """프로필 URL → publicIdentifier. 액터는 URL이 아니라 이 값을 받는다(실측)."""
    m = _IN.search(url or "")
    return (m.group(1).strip().lower() if m else "")


def _targets(cfg: dict, token: str, limit: int = 0) -> list[dict]:
    """재확인 대상 = (우선군 OR 이탈임박) AND 주기 도래.

    우선군 = 타깃 ✅ / 빌더 ✅ / Tier=Catch — `recheck.py`와 같은 어휘를 쓴다.
    이탈임박 = Tenure Months가 [9, 12) 구간. **이건 우선군이 아니어도 본다** — 12개월을
    넘는 순간 발굴 축에서 사라져 영영 못 보기 때문이다(이 레인의 존재 이유).
    """
    import requests

    ncfg = cfg.get("notion") or {}
    dbs = [str(ncfg.get("db_id", "")).split("://", 1)[-1]]
    li = (ncfg.get("channel_db_ids") or {}).get("링크드인")
    if li:
        dbs.append(str(li).split("://", 1)[-1])

    state = followup.load()
    out: list[dict] = []
    seen: set[str] = set()
    for db in dict.fromkeys(d for d in dbs if d):
        payload: dict = {"page_size": 100}
        while True:
            r = notion._write("POST", f"{notion.API}/databases/{db}/query", token, payload)
            if r.status_code != 200:
                print(f"🔴 DB {db[:8]}… 조회 실패 {r.status_code}: {r.text[:200]}")
                break
            d = r.json()
            for page in d.get("results", []):
                props = page.get("properties", {})
                url = (props.get("LinkedIn URL") or {}).get("url") or ""
                pid = public_id(url)
                if not pid or pid in seen:
                    continue
                tenure = (props.get("Tenure Months") or {}).get("number")
                target = bool((props.get("타깃") or {}).get("checkbox"))
                builder = bool((props.get("빌더") or {}).get("checkbox"))
                tier = ((props.get("Tier") or {}).get("select") or {}).get("name")
                prio = target or builder or tier == "Catch"
                exiting = (isinstance(tenure, (int, float))
                            and EXIT_RISK_MIN_MONTHS <= tenure < EXIT_RISK_MAX_MONTHS)
                if not (prio or exiting):
                    continue
                key = followup.key_linkedin(pid)
                if not followup.is_due(state, key):
                    continue
                seen.add(pid)
                out.append({
                    "pid_page": page["id"], "public_id": pid, "db": db,
                    "prio": prio, "exiting": exiting, "tenure": tenure,
                    "old_headline": notion._plain(props.get("Author Headline")),
                    "name": notion._plain(props.get("서비스명")) or pid,
                })
            if not d.get("has_more"):
                break
            payload["start_cursor"] = d["next_cursor"]

    # 이탈임박 먼저(시간이 없다), 그다음 우선군
    out.sort(key=lambda x: (not x["exiting"], not x["prio"]))
    return out[:limit] if limit else out


def run(dry_run: bool = False, limit: int = 0) -> int:
    import yaml

    from adapters import apify

    load_env()
    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    apify.configure(cfg)                          # 🔴 누락 시 예산 트립·raw 아카이브가 안 돈다
    token = notion._token()
    if not token:
        print("🔴 NOTION_TOKEN 없음")
        return 1

    tg = _targets(cfg, token, limit)
    if not tg:
        print("✅ 재확인 대상 없음 (전원 주기 미도래)")
        return 0

    n_exit = sum(1 for t in tg if t["exiting"])
    est = len(tg) * 0.004
    print(f"대상 {len(tg)}명 (이탈임박 {n_exit} · 우선군 {len(tg) - n_exit}) · **추정 ${est:.2f}**")
    for t in tg[:10]:
        tag = "이탈임박" if t["exiting"] else "우선"
        print(f"  · {t['name'][:20]:<20} [{tag}] tenure={t['tenure']}")
    if len(tg) > 10:
        print(f"  … 외 {len(tg) - 10}명")
    if dry_run:
        print("(--dry-run — 아무것도 안 샀다)")
        return 0

    before = apify.usage_usd()
    got: dict[str, dict] = {}
    ids = [t["public_id"] for t in tg]
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        try:
            rows = apify.run_actor(ACTOR, {"publicIdentifiers": chunk,
                                           "profileScraperMode": SCRAPER_MODE}, timeout=900)
        except Exception as e:                    # noqa: BLE001
            print(f"⚠️  배치 {i // BATCH + 1} 실패({len(chunk)}명): {e} — 건너뛰고 계속")
            continue
        for r in rows:
            k = (r.get("publicIdentifier") or "").strip().lower()
            if k:
                got[k] = r
        print(f"· 배치 {i // BATCH + 1}: {len(chunk)}명 → {len(rows)}건 "
              f"(누적 추정 ${apify.spent_usd():.2f})")

    state = followup.load()
    today = date.today().isoformat()
    changed = same = miss = fail = 0
    for t in tg:
        r = got.get(t["public_id"])
        if not r:
            miss += 1                             # 미수신 — 마킹 안 함, 다음 실행에 재시도
            continue
        head = (r.get("headline") or "").strip()
        old = (t["old_headline"] or "").strip()
        props: dict = {"Last Checked": {"date": {"start": today}}}
        summary = []
        if head and " ".join(head.split()) != " ".join(old.split()):
            props["Author Headline"] = {"rich_text": [{"text": {"content": head[:1900]}}]}
            props["재검토"] = {"checkbox": True}
            props["재검토 사유"] = {"select": {"name": "프로필변화"}}
            summary.append(f"headline: 「{old[:60] or '(비어있음)'}」 → 「{head[:60]}」")
            changed += 1
            print(f"  ✦ 변화 {t['name'][:18]}: 「{old[:35] or '(비어있음)'}」 → 「{head[:35]}」")
            followup.record("li_profile_change", followup.key_linkedin(t["public_id"]),
                            {"old": old[:200], "new": head[:200]})
        elif t["exiting"]:
            # 변화는 없지만 곧 필터 밖으로 나간다 — 사람이 지금 볼 이유가 있다.
            props["재검토"] = {"checkbox": True}
            props["재검토 사유"] = {"select": {"name": "이탈임박"}}
            summary.append(f"재직 {t['tenure']}개월 — 12개월 필터 이탈 임박(발굴 축에서 곧 사라짐)")
            same += 1
        else:
            same += 1
        if summary:
            props["변화 요약"] = {"rich_text": [{"text": {"content": " / ".join(summary)[:1900]}}]}
        resp = notion._write("PATCH", f"{notion.API}/pages/{t['pid_page']}", token,
                             {"properties": props})
        if resp.status_code != 200:
            fail += 1
            print(f"⚠️  PATCH 실패 {t['name'][:18]}: {resp.status_code} {resp.text[:120]}")
            continue                              # 노션에 못 썼으면 상태도 전진시키지 않는다
        followup.mark_checked(state, followup.key_linkedin(t["public_id"]),
                              days=CADENCE_DAYS, headline=head[:200])

    followup.save(state)
    after = apify.usage_usd()
    real = (after - before) if (before is not None and after is not None) else None
    print(f"\n🎉 링크드인 recheck 완료: 변화 {changed}(재검토 ✅) · 무변화 {same} · "
          f"미수신 {miss} · PATCH실패 {fail}")
    print(f"💰 실제 과금 ${real:.2f} / 추정 ${apify.spent_usd():.2f}" if real is not None
          else f"💰 추정 ${apify.spent_usd():.2f}")
    return 0 if not fail else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="대상·비용만 ($0)")
    ap.add_argument("--limit", type=int, default=0, help="앞 N명만 (스모크용)")
    a = ap.parse_args()
    return run(dry_run=a.dry_run, limit=a.limit)


if __name__ == "__main__":
    raise SystemExit(main())
