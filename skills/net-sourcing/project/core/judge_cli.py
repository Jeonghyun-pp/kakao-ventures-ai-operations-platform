"""반자동 judge — 주 1회 Claude Code 세션이 판단한다. **API 키 불필요.**

## 왜 반자동인가 (사용자 결정 2026-07-13)
수집은 주 ~1,000명을 착지시킨다. 판단 없이 사람이 다 볼 수 없다(실측 적중률 6%).
그런데 크론이 LLM을 부르려면 ANTHROPIC_API_KEY가 필요하고, 그건 상한 없는 지출이다.
→ 크론은 **수집·착지까지만**. 판단은 주 1회 Claude Code 세션이 한다.

## 절차
  1. `main.py judge-export`            → data/judge_queue_{날짜}.json  (Tier가 빈 행 전부)
  2. Claude가 references/judge-criteria.md 를 읽고 큐를 판단 → judge_verdicts_{날짜}.json
  3. `main.py judge-apply --file …`    → 노션 Tier / AI Reason / One Liner 기록

## 🔴 judge-apply가 반드시 막아야 하는 것
Tier 값이 노션 select 옵션에 없으면 `_known_options_only`가 **조용히 버리고 PATCH는 200을 준다.**
로그엔 "✅ 120건 적용", 노션 Tier는 전부 빈칸, 사람이 볼 뷰는 영영 비어 있다.
→ **쓰기 전에 전량 검증하고, 하나라도 어긋나면 아무것도 쓰지 않는다.**
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from core import notion

PROJ = Path(__file__).resolve().parent.parent
DATA = PROJ / "data"

# config.notion.channel_db_ids 의 키 ↔ 사람이 읽는 채널 라벨(base DB의 Channel select 값).
# 채널 DB에는 **Channel 속성이 없다**(라이브 확인) — 어느 DB에서 읽었는지가 곧 채널이다.
_CHANNEL_BY_KEY = {"앱스토어": "App Store", "스레드": "Threads", "링크드인": "LinkedIn"}
_KEY_BY_CHANNEL = {v.lower(): k for k, v in _CHANNEL_BY_KEY.items()}

_SCHEMA_CACHE: dict[str, dict] = {}


def _schema_of(token: str, db: str) -> dict:
    """DB별 라이브 스키마. judge는 **DB마다 칸이 다르다** — 하나로 가정하면 조용히 깨진다.

    라이브 실측(2026-07-19): base 52칸 · 앱스토어 19칸 · 스레드 38칸 · 링크드인 27칸.
    앱스토어엔 Tier·AI Reason이 **없고** `판정`(Pass/Drop)이 있다. 링크드인엔 Tenure Months가
    있고 Post Count·Matched Keyword가 없다. 공통 필드만 있다고 믿으면 안 된다.
    """
    if db not in _SCHEMA_CACHE:
        r = notion._read(f"{notion.API}/databases/{db}", token)
        if r.status_code != 200:
            raise RuntimeError(f"DB 스키마 조회 실패 {db[:8]} status={r.status_code} {r.text[:200]}")
        _SCHEMA_CACHE[db] = r.json().get("properties", {})
    return _SCHEMA_CACHE[db]


def _verdict_field(schema: dict) -> tuple[str, list[str]]:
    """이 DB에서 판정을 적는 select 칸과 허용 옵션. (이름, 옵션들) 또는 ("", []).

    사람 채널 = `Tier`(Catch/Trend/Wait) · 앱스토어 = `판정`(Pass/Drop) — 결정 #5 유지.
    🔴 **옵션을 코드에 박지 않는다.** 노션은 모르는 select 값을 조용히 버리고 PATCH는 200을 준다.
    """
    for name in ("Tier", "판정"):
        m = schema.get(name) or {}
        if m.get("type") == "select":
            return name, [o["name"] for o in m["select"]["options"]]
    return "", []


def _targets(cfg: dict, channel: str, include_base: bool) -> list[tuple[str, str]]:
    """판정 대상 (채널라벨, db_id) 목록.

    🔴 **base DB는 기본으로 읽지 않는다.** 결정 10-b로 스레드 635·링크드인 344를 채널 DB에
    **사본 생성하고 원본을 base에 남긴** 과도상태다(아카이브 미실행). base까지 읽으면 같은
    사람을 두 번 판정하고, 사람이 노션에서 같은 얼굴을 두 번 본다. 향후 착지·글누적은
    전부 채널 DB로 가므로(setdefault target-first) **채널 DB가 정본**이다.
    """
    m = (cfg.get("notion", {}).get("channel_db_ids") or {})
    out: list[tuple[str, str]] = []
    if channel:
        key = _KEY_BY_CHANNEL.get(channel.strip().lower(), channel.strip())
        if key not in m:
            raise SystemExit(f"🔴 모르는 채널 '{channel}' — 가능: {sorted(_CHANNEL_BY_KEY.values())}")
        out.append((_CHANNEL_BY_KEY.get(key, key), str(m[key]).split("://", 1)[-1]))
    else:
        for key, raw in m.items():
            db = str(raw).split("://", 1)[-1]
            if db and not db.endswith("TODO"):
                out.append((_CHANNEL_BY_KEY.get(key, key), db))
    if include_base:
        out.append(("(base)", notion._db_id(cfg)))
    return out


def _page_posts(token: str, page_id: str) -> list[dict]:
    """페이지 본문에 쌓인 글들(스레드 인물 행). judge가 볼 실제 근거."""
    out: list[dict] = []
    cursor, label = None, ""
    for _ in range(20):
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = notion._read(f"{notion.API}/blocks/{page_id}/children", token, params)
        if r.status_code != 200:
            return out
        d = r.json()
        for b in d.get("results", []):
            t = b.get("type") or ""
            rt = (b.get(t) or {}).get("rich_text") or []
            text = "".join(x.get("plain_text", "") for x in rt)
            if t == "heading_3":
                label = text
            elif t == "paragraph" and text.strip():
                out.append({"when": label, "text": text})
        if not d.get("has_more"):
            break
        cursor = d.get("next_cursor")
    return out


def export(cfg: dict, limit: int = 0, channel: str = "", include_base: bool = False) -> int:
    """미판정 행을 **채널 DB별로** 뽑아 judge 큐 파일을 만든다.

    🔴 2026-07-19 재작성 — 고친 것 3가지(전부 실측으로 확인된 결함):
      ① base DB 하나만 읽었다 → 07-15에 만든 **채널 DB 3개를 아예 안 봤다.** 그 뒤 착지분은
         judge에 영영 안 보였다.
      ② `limit` 기본 400에서 잘랐다 → 07-16 실행이 정확히 400에서 끊겨 **링크드인 0건**.
         (07-13·07-16 큐 둘 다 count=400) 이제 기본 0 = 무제한.
      ③ Tier/Channel이 모든 DB에 있다고 가정했다 → 앱스토어엔 Tier도 Channel도 **없다.**
         이제 DB별 라이브 스키마를 읽고 있는 칸만 뽑는다.
    """
    token = notion._token()
    if not token:
        print("🔴 NOTION_TOKEN 없음")
        return 1

    try:
        targets = _targets(cfg, channel, include_base)
    except SystemExit as e:
        print(e)
        return 1
    if not targets:
        print("🔴 대상 DB 없음 — config.notion.channel_db_ids 확인")
        return 1

    rows: list[dict] = []
    fields_by_ch: dict[str, str] = {}
    for ch, db in targets:
        try:
            schema = _schema_of(token, db)
        except RuntimeError as e:
            print(f"🔴 {e}")
            return 1
        field, allowed = _verdict_field(schema)
        if not field:
            print(f"⚠️  {ch}({db[:8]}): 판정 select 칸이 없다 — 건너뜀")
            continue
        fields_by_ch[ch] = f"{field}{allowed}"
        has = schema.__contains__

        url = f"{notion.API}/databases/{db}/query"
        payload: dict = {"filter": {"property": field, "select": {"is_empty": True}},
                         "page_size": 100}
        got = 0
        while True:
            r = notion._write("POST", url, token, payload)
            if r.status_code != 200:
                print(f"🔴 쿼리 실패 {ch} {r.status_code}: {r.text[:200]}")
                return 1
            d = r.json()
            for page in d.get("results", []):
                p = page.get("properties", {})
                item = {
                    "page_id": page["id"],
                    # 채널 DB엔 Channel 속성이 없다 — 어느 DB에서 읽었는지가 채널이다.
                    "channel": (((p.get("Channel") or {}).get("select") or {}).get("name") or ch)
                    if has("Channel") else ch,
                    # ★ 판정 계약을 **행마다 박아둔다.** apply가 이걸 보고 어느 칸에 무슨 값을
                    #   쓸지 정한다. 문서가 아니라 이 파일이 정본이다.
                    "verdict_field": field,
                    "allowed": allowed,
                    "has_ai_reason": has("AI Reason"),
                    "has_one_liner": has("One Liner"),
                    "name": notion._plain(p.get("서비스명")),
                    "url": (p.get("Post URL") or {}).get("url") or "",
                    "raw_text": notion._plain(p.get("Raw Text")),
                }
                # ★ bio. 글 한 편은 중앙값 75자라 정체를 모른다 — bio는 한 줄로 알려준다.
                #   「불당 22대로92 1층 더덕삼겹살 전문점!」 vs 「🚀 Founder & CEO of ONE」
                #   ⚠️ 정규식으로 자르지 않는다(사용자 결정) — **LLM이 읽는다.**
                if has("Author Headline"):
                    item["headline"] = notion._plain(p.get("Author Headline"))
                if has("Author Username"):
                    item["handle"] = notion._plain(p.get("Author Username"))
                if has("Followers"):
                    item["followers"] = (p.get("Followers") or {}).get("number")
                if has("Post Count"):
                    item["post_count"] = (p.get("Post Count") or {}).get("number")
                # ★ 링크드인의 결정 피처. 사람이 찍은 타깃 19명이 **전원 tenure < 6개월**이었다
                #   (비타깃 평균 45.4개월). 예전 export는 이 칸을 아예 안 실었다.
                if has("Tenure Months"):
                    item["tenure_months"] = (p.get("Tenure Months") or {}).get("number")
                for prop, key in (("Matched Keyword", "matched_keyword"),
                                  ("Sourcing Layers", "sourcing_layers"),
                                  ("Korea Signal", "korea_signal")):
                    if has(prop):
                        item[key] = [o["name"] for o in
                                     ((p.get(prop) or {}).get("multi_select") or [])]
                # 스레드 인물 행은 **본문에 쌓인 글 전부**가 판단 근거다(최신 글 하나가 아니라).
                if item["channel"] == "Threads":
                    item["posts"] = _page_posts(token, page["id"])
                rows.append(item)
                got += 1
                if limit and len(rows) >= limit:
                    break
            if (limit and len(rows) >= limit) or not d.get("has_more") or not d.get("next_cursor"):
                break
            payload["start_cursor"] = d["next_cursor"]
        print(f"   · {ch}({db[:8]}) {field} 빈칸 {got}건")
        if limit and len(rows) >= limit:
            print(f"   ⚠️  --limit {limit} 도달 — 나머지 채널은 뽑지 않았다")
            break

    out = {
        "generated": date.today().isoformat(),
        # ★ 채널마다 판정 어휘가 다르다(사람=Tier Catch/Trend/Wait · 앱스토어=판정 Pass/Drop).
        #   정본은 문서가 아니라 **라이브 스키마**이고, 행마다 allowed 로 박혀 있다.
        "verdict_fields": fields_by_ch,
        "criteria": "references/judge-criteria.md 를 읽고 판단할 것",
        "count": len(rows),
        "rows": rows,
    }
    path = DATA / f"judge_queue_{date.today().isoformat()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    by_ch: dict[str, int] = {}
    for r_ in rows:
        by_ch[r_["channel"]] = by_ch.get(r_["channel"], 0) + 1
    print(f"\n🎉 judge-export: 미판정 {len(rows)}건 → {path}")
    print(f"   채널별: {by_ch}")
    print(f"   판정 칸: {fields_by_ch}")
    if not include_base:
        print("   (base DB는 제외했다 — 스레드·링크드인 원본이 채널 DB와 중복이다. 포함하려면 --include-base)")
    print("\n다음: references/judge-criteria.md 를 읽고 판단해 아래 형식으로 저장한 뒤 judge-apply.")
    print('   [{"page_id": "...", "verdict": "Catch|Trend|Wait|Pass|Drop", '
          '"reason": "...", "one_liner": "..."}]')
    return 0


def apply(cfg: dict, file: str) -> int:
    token = notion._token()
    if not token:
        print("🔴 NOTION_TOKEN 없음")
        return 1
    verdicts = json.loads(Path(file).read_text(encoding="utf-8"))
    if isinstance(verdicts, dict):
        verdicts = verdicts.get("verdicts") or verdicts.get("rows") or []
    if not verdicts:
        print("· 적용할 판단 없음")
        return 0

    # 큐에 있던 page_id만 허용 — 오타/환각 id는 404를 내고 로그에 묻힌다.
    # ★ 큐가 **행마다 판정 계약**(어느 칸에·무슨 값이 허용되는지)을 들고 있다. 채널마다 다르므로
    #   여기서 하나의 Tier 집합으로 검증하면 앱스토어(Pass/Drop)가 전량 실패한다.
    queues = sorted(DATA.glob("judge_queue_*.json"))
    contract: dict[str, dict] = {}
    for q in queues[-3:]:
        try:
            for r_ in json.loads(q.read_text(encoding="utf-8"))["rows"]:
                contract[r_["page_id"]] = r_
        except (json.JSONDecodeError, KeyError):
            continue
    if not contract:
        print("🔴 judge_queue_*.json 이 없다 — judge-export 를 먼저 돌려라")
        return 1

    def _verdict_of(v: dict) -> str:
        for k in ("verdict", "tier", "판정"):
            if v.get(k):
                return str(v[k]).strip()
        return ""

    # ★★ 쓰기 전에 **전량 검증.** 하나라도 어긋나면 아무것도 쓰지 않는다.
    errs: list[str] = []
    for i, v in enumerate(verdicts):
        pid = (v.get("page_id") or "").strip()
        val = _verdict_of(v)
        row = contract.get(pid)
        if not pid:
            errs.append(f"[{i}] page_id 없음")
            continue
        if not row:
            errs.append(f"[{i}] page_id가 export 큐에 없다: {pid}")
            continue
        allowed = row.get("allowed") or []
        if val not in allowed:
            errs.append(f"[{i}] {row.get('channel','?')} 판정 '{val}' 는 "
                        f"{row.get('verdict_field','?')} 옵션이 아니다 (허용: {allowed})")
    if errs:
        print(f"🔴 검증 실패 {len(errs)}건 — **아무것도 쓰지 않았다.**")
        for e in errs[:12]:
            print(f"   · {e}")
        if len(errs) > 12:
            print(f"   … 외 {len(errs) - 12}건")
        print("\n   (이걸 그냥 쓰면 노션이 값을 조용히 버리고 PATCH는 200을 준다 —")
        print("    로그엔 '적용 완료', 실제 판정은 전부 빈칸이 된다.)")
        return 1

    # 사유를 적을 칸이 없는 DB(앱스토어엔 AI Reason이 없다)를 위한 사이드카.
    # **조용한 삭제 없음** 원칙상 사유는 어디로도 사라지면 안 된다.
    orphan_reasons: list[dict] = []

    ok = failed = 0
    for v in verdicts:
        pid = v["page_id"].strip()
        row = contract[pid]
        val = _verdict_of(v)
        props: dict = {row["verdict_field"]: {"select": {"name": val}}}
        if v.get("reason"):
            if row.get("has_ai_reason"):
                props["AI Reason"] = {"rich_text": [{"text": {"content": str(v["reason"])[:2000]}}]}
            else:
                orphan_reasons.append({"page_id": pid, "channel": row.get("channel"),
                                       "verdict": val, "reason": str(v["reason"])})
        if v.get("one_liner") and row.get("has_one_liner"):
            props["One Liner"] = {"rich_text": [{"text": {"content": str(v["one_liner"])[:2000]}}]}
        r = notion._write("PATCH", f"{notion.API}/pages/{pid}", token, {"properties": props})
        if r.status_code == 200:
            ok += 1
        else:
            failed += 1
            print(f"  ✗ {pid[:8]} status={r.status_code} {r.text[:140]}")

    tally: dict[str, int] = {}
    for v in verdicts:
        key = f"{contract[v['page_id'].strip()].get('channel','?')}/{_verdict_of(v)}"
        tally[key] = tally.get(key, 0) + 1
    print(f"🎉 judge-apply: {ok}건 적용 · 실패 {failed}건")
    print(f"   판정 분포: {tally}")

    if orphan_reasons:
        side = DATA / f"judge_reasons_{date.today().isoformat()}.json"
        side.write_text(json.dumps(orphan_reasons, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"   ⚠️  AI Reason 칸이 없는 채널 {len(orphan_reasons)}건의 사유 → {side}")
        print("      (앱스토어 DB엔 AI Reason이 없다. 노션에서 보려면 그 DB에 칸을 추가해야 한다)")
    return 0 if not failed else 1
