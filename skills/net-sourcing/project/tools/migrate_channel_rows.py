#!/usr/bin/env python3
"""기존 기본 DB(10fb616a)의 Threads/LinkedIn 행을 **채널별 DB로 이관**. 멱등·재개가능.

## 배경 (2026-07-15)
스레드·링크드인을 채널별 DB로 분리했다(config.channel_db_ids). 처음엔 "신규만 새 DB로"(무이관)로
갔으나, 사용자가 **기존 큐레이션 행도 옮기기로** 결정했다. 이 스크립트가 그 이관을 한다:
기본 DB의 Threads 635 / LinkedIn 344 행을 각 채널 DB로 **사본 생성 후 원본 아카이브**.

## 왜 조심해야 하나
스레드 행은 **페이지 본문에 글이 누적**돼 있고, 각 글 heading_3의 **href 링크가 중복 append 방지의
유일한 키**다(core/notion.existing_post_urls가 그 href를 읽는다). 이관이 링크를 잃으면, 다음 착지 때
그 사람의 글을 '처음 보는 글'로 오인해 **본문이 무한히 자란다.** → rich_text의 link를 그대로 복제한다.

## 사용 (안전 순서)
    python tools/migrate_channel_rows.py                      # dry-run (쓰기 없음, 리포트만)
    python tools/migrate_channel_rows.py --apply              # 대상 DB에 사본 생성(멱등, 본문 글 복제)
    #   → 새 DB 행수 검증
    python tools/migrate_channel_rows.py --apply --archive    # 사본 확인된 원본 행을 아카이브(휴지통, 30일 복원가능)
    python tools/migrate_channel_rows.py --channel threads    # 채널 한정 (threads|linkedin|all)

멱등: 대상 DB에 이미 사본이 있으면 생성을 건너뛴다(착지키로 판정). 중간에 끊겨도 재실행하면 이어서 한다.
아카이브: **사본이 대상 DB에 확인된 행만** 아카이브한다(사본 없이 원본을 지우지 않는다).
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

import main as _main  # noqa: E402
from core import notion  # noqa: E402

API = notion.API
BASE = "00000000-0000-0000-0000-d609b10d8459"
CH_KO = {"threads": "스레드", "linkedin": "링크드인"}
CH_NOTION = {"threads": "Threads", "linkedin": "LinkedIn"}
_MAX_CHILDREN = 90  # 노션 생성/append 요청당 블록 상한(100)보다 여유


# ---------- 값 변환 (read-format → write-format) ----------
def _clean_rt(rt_list: list) -> list:
    """rich_text 배열을 생성용으로 정리 — **link(href) 보존**, 읽기전용 필드 제거."""
    out = []
    for r in rt_list or []:
        if r.get("type") != "text":
            # mention/equation 등은 평문으로만 보존(이 DB엔 사실상 없음)
            txt = r.get("plain_text", "")
            if txt:
                out.append({"type": "text", "text": {"content": txt[:1900]}})
            continue
        t = r.get("text") or {}
        node = {"type": "text", "text": {"content": (t.get("content") or "")[:1900]}}
        link = t.get("link")
        if link and link.get("url"):
            node["text"]["link"] = {"url": link["url"]}
        ann = r.get("annotations") or {}
        # 기본값이 아닌 서식만 실어 보낸다(payload 절약, 안전)
        keep = {k: v for k, v in ann.items()
                if k in ("bold", "italic", "strikethrough", "underline", "code") and v}
        if ann.get("color") and ann["color"] != "default":
            keep["color"] = ann["color"]
        if keep:
            node["annotations"] = keep
        out.append(node)
    return out


def _prop_write(ptype: str, pval: dict):
    """페이지 property 값(read) → 생성 payload(write). 빈/미지원이면 None(=생략)."""
    if pval is None:
        return None
    if ptype == "title":
        text = "".join(x.get("plain_text", "") for x in (pval.get("title") or []))
        # [W12] `[:2000]` 은 안전하지 않다 — 노션은 UTF-16 유닛을 센다(이모지 1자 = 2유닛).
        # 2026-07-27 에 링크드인 495건 중 7건이 그렇게 통째로 착지 실패했다.
        return ({"title": [{"type": "text", "text": {"content": notion._clip16(text)}}]}
                if text.strip() else None)
    if ptype == "rich_text":
        rt = _clean_rt(pval.get("rich_text") or [])
        return {"rich_text": rt} if rt else None
    if ptype == "number":
        return {"number": pval.get("number")} if pval.get("number") is not None else None
    if ptype == "select":
        s = pval.get("select")
        return {"select": {"name": s["name"]}} if s and s.get("name") else None
    if ptype == "multi_select":
        opts = [{"name": o["name"]} for o in (pval.get("multi_select") or []) if o.get("name")]
        return {"multi_select": opts} if opts else None
    if ptype == "date":
        d = pval.get("date")
        if not d or not d.get("start"):
            return None
        out = {"start": d["start"]}
        if d.get("end"):
            out["end"] = d["end"]
        return {"date": out}
    if ptype == "url":
        return {"url": pval["url"]} if pval.get("url") else None
    if ptype == "checkbox":
        return {"checkbox": bool(pval.get("checkbox"))}
    return None  # formula/rollup/people/relation/created_* 등 — 이관 대상 아님


# ---------- 본문 블록 ----------
def _read_blocks(token: str, pid: str) -> list[dict]:
    out, cursor = [], None
    for _ in range(30):
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = notion._read(f"{API}/blocks/{pid}/children", token, params)
        if r.status_code != 200:
            raise RuntimeError(f"본문 조회 실패 {pid[:8]} {r.status_code}")
        d = r.json()
        out.extend(d.get("results", []))
        if not d.get("has_more"):
            break
        cursor = d.get("next_cursor")
    return out


def _blocks_write(raw: list[dict]) -> list[dict]:
    """읽은 블록 → 생성용. heading_1~3·paragraph·리스트 항목의 rich_text(link 포함)를 그대로."""
    out = []
    for b in raw:
        t = b.get("type") or ""
        payload = b.get(t) or {}
        rt = payload.get("rich_text")
        if rt is None:
            continue  # rich_text 없는 블록(구분선 등)은 이 DB에 사실상 없음 — 건너뜀
        out.append({"object": "block", "type": t, t: {"rich_text": _clean_rt(rt)}})
    return out


# ---------- 착지 키 (기존 dedup과 동일 규칙) ----------
def _row_keys(props: dict) -> list[str]:
    keys = []
    app = notion._plain(props.get("App ID"))
    if app:
        keys.append(f"app:{app}")
    post_url = (props.get("Post URL") or {}).get("url")
    if post_url:
        keys.append(post_url.strip())
    li = (props.get("LinkedIn URL") or {}).get("url")
    if li:
        keys.append("li:" + notion._normalize_linkedin_url(li))
    if notion._is_threads_row(props):
        h = notion._plain(props.get("Author Username")).strip().lstrip("@").lower()
        if h:
            keys.append(f"threads:{h}")
    return keys


def _query_all(token: str, db: str):
    payload = {"page_size": 100}
    while True:
        r = notion._write("POST", f"{API}/databases/{db}/query", token, payload)
        if r.status_code != 200:
            raise RuntimeError(f"query 실패 {db[:8]} {r.status_code}: {r.text[:200]}")
        d = r.json()
        yield from d.get("results", [])
        if not d.get("has_more") or not d.get("next_cursor"):
            return
        payload["start_cursor"] = d["next_cursor"]


def _target_keys(token: str, db: str) -> set[str]:
    keys: set[str] = set()
    for pg in _query_all(token, db):
        keys.update(_row_keys(pg.get("properties", {})))
    return keys


# ---------- 이관 ----------
def migrate_channel(cfg: dict, token: str, ch: str, apply: bool, archive: bool) -> None:
    src = CH_KO[ch]
    tgt_db = notion.db_for(cfg, src)
    tgt_schema = notion._fetch_schema({"notion": {"db_id": tgt_db}}, token)
    tgt_keys = _target_keys(token, tgt_db)
    base_rows = [pg for pg in _query_all(token, BASE)
                 if ((pg["properties"].get("Channel") or {}).get("select") or {}).get("name")
                 == CH_NOTION[ch]]
    print(f"\n=== {src} ({CH_NOTION[ch]}) → DB {tgt_db[:8]} ===")
    print(f"  base 대상 {len(base_rows)}행 · 대상 DB 기존 사본 {len(tgt_keys)}키")

    to_create = to_archive = already = created = archived = failed = 0
    blocks_total = 0
    for pg in base_rows:
        props = pg["properties"]
        pid = pg["id"]
        keys = _row_keys(props)
        exists = any(k in tgt_keys for k in keys)

        if exists:
            already += 1
            if archive and apply:
                r = notion._write("PATCH", f"{API}/pages/{pid}", token, {"archived": True})
                if r.status_code == 200:
                    archived += 1
                else:
                    failed += 1
                    print(f"  ✗ 아카이브 실패 {pid[:8]} {r.status_code}")
            else:
                to_archive += 1
            continue

        # 사본 생성
        to_create += 1
        out_props = {}
        for name, meta in tgt_schema.items():
            if name not in props:
                continue
            w = _prop_write(meta.get("type", ""), props.get(name))
            if w is not None:
                out_props[name] = w
        try:
            raw = _read_blocks(token, pid)
        except RuntimeError as e:
            print(f"  ✗ {pid[:8]} 본문 조회 실패 — 건너뜀(다음 실행 재시도): {e}")
            failed += 1
            continue
        children = _blocks_write(raw)
        blocks_total += len(children)

        if not apply:
            continue

        body = {"parent": {"database_id": tgt_db}, "properties": out_props}
        head = children[:_MAX_CHILDREN]
        if head:
            body["children"] = head
        r = notion._write("POST", f"{API}/pages", token, body)
        if r.status_code != 200:
            failed += 1
            print(f"  ✗ 생성 실패 {pid[:8]} {r.status_code}: {r.text[:200]}")
            continue
        new_pid = r.json().get("id", "")
        created += 1
        for k in keys:
            tgt_keys.add(k)                       # 배치 내 재생성 방지
        # 남은 블록 append
        rest = children[_MAX_CHILDREN:]
        for i in range(0, len(rest), _MAX_CHILDREN):
            chunk = rest[i:i + _MAX_CHILDREN]
            ra = notion._write("PATCH", f"{API}/blocks/{new_pid}/children", token, {"children": chunk})
            if ra.status_code != 200:
                print(f"  ⚠️ {new_pid[:8]} 본문 일부 append 실패 {ra.status_code} — 재실행 시 사본이 이미 있어 스킵되니 수동 확인 필요")

    if apply:
        print(f"  ✅ 생성 {created} · 아카이브 {archived} · 이미있음 {already} · 실패 {failed} · 복제 블록 {blocks_total}")
    else:
        print(f"  [dry-run] 생성예정 {to_create} · 이미있음 {already}"
              f"{f' · 아카이브예정 {to_archive}' if archive else ''} · 복제할 블록 {blocks_total}")


def main() -> int:
    apply = "--apply" in sys.argv
    archive = "--archive" in sys.argv
    ch = "all"
    for i, a in enumerate(sys.argv):
        if a == "--channel" and i + 1 < len(sys.argv):
            ch = sys.argv[i + 1]
    _main.load_env()
    cfg = _main.load_config()
    token = notion._token()
    if not token:
        print("🔴 NOTION_TOKEN 없음")
        return 1
    chans = ["threads", "linkedin"] if ch == "all" else [ch]
    mode = "APPLY" + ("+ARCHIVE" if archive else "") if apply else "DRY-RUN"
    print(f"이관 모드: {mode} · 채널: {chans}")
    for c in chans:
        migrate_channel(cfg, token, c, apply, archive)
    if not apply:
        print("\n→ 실제 이관: --apply (사본 생성) 후, 검증하고 --apply --archive (원본 아카이브)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
