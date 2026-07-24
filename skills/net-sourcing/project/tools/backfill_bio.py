#!/usr/bin/env python3
"""노션에 이미 있는 **스레드 인물 행**의 `Author Headline`(=프로필 bio)과 `Followers`를 채운다.

왜 필요한가 — 사람이 DB를 열고 한 말:
  "쓰레기가 많은데? 식당이나 이런것들. 그리고 글 자체를 긁어오지는 않나봐? 정보가 없어서 뭘 하는 곳인지 모르겠네"
  둘 다 **같은 원인**이었다. `Raw Text`는 있었지만(84%) **중앙값 75자**라 정체를 알 수 없고,
  정체를 알려주는 `Author Headline`(bio)은 **635행 중 1행**만 차 있었다.
  · 키워드 액터(igview)는 bio를 **안 준다** — 이게 대부분(317명)이다.
  · meta 액터는 주는데, `core/fold.py`가 첫 후보 신호만 가져가며 **버리고 있었다**(수정 완료 → 24명).
  → 나머지는 프로필 전용 액터로 사 온다. **$0.005/명** (실측).

bio 한 줄의 값어치:
    「불당 22대로92 1층 더덕삼겹살 전문점!」   ← 쓰레기, 즉시 판별
    「🚀 Founder & CEO of ONE」               ← 진성, 즉시 판별
    「사업가 | 컨텐츠 크리에이터 … 워킹맘」     ← judge 큐에서 '근거 0'이던 stub의 정체

⚠️ bio를 **정규식으로 자르지 않는다.** 사용자 결정(2026-07-13): "룰 기반보다 LLM이 읽는 게 낫다."
   근거: 오늘 워치리스트가 망가진 원인이 정확히 그것이었다 — `피드백` 한 단어짜리 룰이 31명을
   오염시켰다. bio는 문장이라 맥락이 필요하다. → bio는 **judge 큐에 실어 LLM이 읽는다.**

사용:
  ./.venv/bin/python tools/backfill_bio.py --dry-run     # 대상만 세고 비용 추정 ($0)
  ./.venv/bin/python tools/backfill_bio.py               # 실행
  ./.venv/bin/python tools/backfill_bio.py --limit 50    # 일부만

**멱등하다** — 이미 `Author Headline`이 찬 행은 건너뛴다. 중간에 죽어도 다시 돌리면 이어서 한다.
[2026-07-17] bio가 진짜 빈 계정도 `Last Checked`로 마킹해 재구매하지 않는다(전엔 매 실행 ~125명 재구매).
그 사람들이 나중에 bio를 채우는 순간은 `main.py recheck`(TTL 재확인)가 잡는다.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

from adapters import apify                       # noqa: E402
from core import notion                          # noqa: E402
from main import load_env                        # noqa: E402  — .env 로더(토큰) 재사용

ACTOR = "apify~threads-profile-api-scraper"
BATCH = 50            # 한 런에 넣을 인원. 5명=28초 → 50명이면 수 분. 너무 키우면 타임아웃 위험.


def _bio_dbs(cfg: dict) -> list[tuple[str, bool]]:
    """(db_id, has_channel_prop) — 스레드 인물이 사는 DB들.

    ★ [2026-07-17] base 하나만 보던 결함 수정 — 채널 DB 분리(07-15) 후 신규 스레드 행은
      스레드 채널 DB에 산다. base만 보면 그 사람들의 bio가 영영 빈다.
    ⚠️ 스레드 채널 DB엔 `Channel` 속성이 **없다**(전 행이 스레드라 불필요) — 필터에 넣으면 400.
    """
    ncfg = cfg.get("notion") or {}
    out = [(str(ncfg.get("db_id", "")).split("://", 1)[-1], True)]
    th = (ncfg.get("channel_db_ids") or {}).get("스레드")
    if th:
        out.append((str(th).split("://", 1)[-1], False))
    return [(d, c) for d, c in out if d]


def _targets(cfg: dict, token: str, limit: int) -> list[tuple[str, str]]:
    """(page_id, handle) — 스레드 행 중 Author Headline이 빈 것. **Last Checked가 비었거나 만료된 것.**

    ★ [2026-07-17] Last Checked 필터가 빈-bio 재구매 구멍의 봉합이다:
      bio가 진짜로 빈 계정(~125명, 19.6%)은 사도 Headline이 안 차서 매 실행 다시 샀다.
      이제 "샀다"는 사실을 Last Checked(date)로 남기므로 두 번 사지 않는다.

    🔴 [2026-07-19] **그 봉합에 구멍이 있었다 — TTL이 실재하지 않았다.**
      위 주석은 "나중에 bio를 채우는 순간은 recheck의 TTL이 잡는다"고 적어놨지만,
      `tools/recheck.py`의 필터는 **타깃/빌더/Tier=Catch 3군뿐**이다. 셋 중 아무것도
      아닌 빈-bio 계정은 recheck 대상에 **들어가지 않는다** → 사실상 영구 제외였다.
      즉 "런칭하면서 bio를 새로 쓰는 극초기 창업자"를 영영 못 봤다. 발굴 대상이
      근거 없이 조용히 사라지는, 버그로그의 '조용히 사라짐' 패턴이다.
      → 만료(`recheck.cadence.empty_bio_days`, 기본 90일)를 **여기** 넣는다.
        제외를 만든 자리가 만료를 책임지는 게 맞다. recheck.py는 타깃/빌더의 bio
        **diff**가 목적이라 의미가 다르고, 거기 얹으면 우선군 상한($0.50)을 잠식한다.
    """
    ttl = int(((cfg.get("recheck") or {}).get("cadence") or {}).get("empty_bio_days", 90))
    cutoff = (date.today() - timedelta(days=ttl)).isoformat()

    out: list[tuple[str, str]] = []
    for db_id, has_channel in _bio_dbs(cfg):
        url = f"{notion.API}/databases/{db_id}/query"
        conds = [{"property": "Author Headline", "rich_text": {"is_empty": True}},
                 # 아직 안 산 사람 **또는** 산 지 ttl일이 지난 사람. 후자가 07-19에 추가된
                 # 만료다 — 빼면 다시 영구 제외로 돌아간다.
                 {"or": [{"property": "Last Checked", "date": {"is_empty": True}},
                         {"property": "Last Checked", "date": {"on_or_before": cutoff}}]}]
        if has_channel:
            conds.insert(0, {"property": "Channel", "select": {"equals": "Threads"}})
        payload: dict = {"filter": {"and": conds}, "page_size": 100}
        while True:
            r = notion._write("POST", url, token, payload)
            if r.status_code != 200:
                print(f"🔴 노션 쿼리 실패({db_id[:8]}…) {r.status_code}: {r.text[:200]}")
                break
            d = r.json()
            for page in d.get("results", []):
                h = notion._plain(page["properties"].get("Author Username"))
                if h:
                    out.append((page["id"], h.strip().lstrip("@")))
                if limit and len(out) >= limit:
                    return out
            if not d.get("has_more"):
                break
            payload["start_cursor"] = d["next_cursor"]
    return out


def _fetch(handles: list[str]) -> dict[str, dict]:
    """handle → {bio, followers, verified}. 실패한 배치는 건너뛴다(부분 성공을 살린다)."""
    got: dict[str, dict] = {}
    for i in range(0, len(handles), BATCH):
        chunk = handles[i:i + BATCH]
        n = i // BATCH + 1
        try:
            items = apify.run_actor(ACTOR, {"usernames": chunk}, timeout=600)
        except Exception as e:                    # noqa: BLE001
            print(f"⚠️  배치 {n} 실패({len(chunk)}명): {e} — 건너뛰고 계속한다")
            continue
        for it in items:
            u = (it.get("username") or "").strip().lstrip("@").lower()
            if not u:
                continue
            got[u] = {
                "bio": (it.get("biography") or it.get("bio") or "").strip(),
                "followers": it.get("follower_count") or it.get("followers_count"),
                "verified": bool(it.get("is_verified")),
            }
        print(f"· 배치 {n}: {len(chunk)}명 요청 → {len(items)}건 수신 "
              f"(누적 추정 ${apify.spent_usd():.2f})")
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="대상만 세고 비용 추정 ($0)")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    load_env()
    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    apify.configure(cfg)
    token = notion._token()
    if not token:
        print("🔴 NOTION_TOKEN 없음")
        return 1

    tg = _targets(cfg, token, a.limit)
    if not tg:
        print("✅ bio가 빈 스레드 행이 없다 — 할 일 없음")
        return 0

    runs = -(-len(tg) // BATCH)
    est = len(tg) * 0.005
    print(f"대상 {len(tg)}명 · 배치 {BATCH}명 × {runs}런 · **추정 ${est:.2f}**")
    if a.dry_run:
        print("(--dry-run — 아무것도 안 샀다)")
        return 0

    before = apify.usage_usd()
    got = _fetch([h for _, h in tg])
    print(f"· 프로필 수신 {len(got)}/{len(tg)}명")

    from datetime import date
    today = date.today().isoformat()
    ok = empty = miss = fail = 0
    for pid, h in tg:
        p = got.get(h.lower())
        if not p:
            miss += 1          # 액터가 안 준 사람(삭제/일시 실패) — 마킹 안 함 → 다음 실행에 재시도
            continue
        # ★ [2026-07-17] 수신했으면 **무조건 Last Checked를 찍는다.** bio가 진짜로 빈 계정도
        #   "샀고 확인했다"를 남겨야 매 실행 재구매(_targets의 Last Checked 필터)를 멈춘다.
        props: dict = {"Last Checked": {"date": {"start": today}}}
        if p["bio"]:
            props["Author Headline"] = {"rich_text": [{"text": {"content": p["bio"][:1900]}}]}
        else:
            empty += 1
        if p["followers"] is not None:
            props["Followers"] = {"number": int(p["followers"])}
        r = notion._write("PATCH", f"{notion.API}/pages/{pid}", token, {"properties": props})
        if r.status_code == 200:
            ok += 1
        else:
            fail += 1
            print(f"⚠️  PATCH 실패 @{h}: {r.status_code} {r.text[:120]}")

    after = apify.usage_usd()
    real = (after - before) if (before is not None and after is not None) else None
    print(f"\n🎉 백필 완료: {ok}행 갱신(그중 빈-bio 확인마킹 {empty}) · 미수신 {miss}(다음에 재시도) · 실패 {fail}")
    print(f"💰 실제 과금 ${real:.2f} / 추정 ${apify.spent_usd():.2f}" if real is not None
          else f"💰 추정 ${apify.spent_usd():.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
