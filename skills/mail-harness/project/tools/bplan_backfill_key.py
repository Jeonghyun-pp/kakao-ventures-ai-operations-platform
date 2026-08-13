"""비플랜 접수 **불변 식별자** 소급 — 재빌드(W12 배치 9)의 1단계.

## 왜 이게 먼저인가

옛 `bplan/main.py` 의 sync 는 멱등 키가 **시트 행 서수**(`str(row_index)`)다.
`kv_core/job.py:56` 은 멱등 키를 *"고유·불변·정확"* 으로 요구하고 `:41` 은 *"퍼지 매칭은
키가 아니다"* 라고 못박는데, 서수는 셋 다 아니다 — 시트에 행이 하나 삽입되거나 사람이
`Setting!A1` 을 되돌리면 **같은 서수가 다른 회사를 가리킨다.** 그러면 접수 1건이 노션에
영영 안 들어가고 로그는 `↩️ 이전 실행에서 이미 노션 등록됨` 만 남는다(누락이 성공처럼 보인다).

새 sync 는 **접수 자체의 불변 식별자**로 upsert 한다:

    submission_key = "<이메일 소문자>|<타임스탬프 ISO>"

구글폼 응답은 (이메일, 제출시각) 이 유일하다. 시트 행이 밀리든 A1 이 되돌아가든 안 변한다.
이 키를 노션 행에 심어두면 **"이 접수가 이미 있나"를 노션에 물어볼 수 있다** = upsert 가 성립.

## 소급이 되는가 — 먼저 쟀다 (2026-08-13 실측)

    시트 유효행 5,548 · A1 커서 5,512(=36건 미동기) · 노션 519행

    이메일+기업명 매칭:  유일 467 · 다중후보 52 · **누락 0**
    다중후보를 `노션 생성시각 이전의 가장 최근 접수` 로 배정 → **519/519 배정**
    타임스탬프 파싱 실패 0건

즉 100% 소급 가능하다. 규칙이 그렇게 단순한 이유는 sync 가 접수 **뒤에** 행을 만들기 때문이다.

## 덤으로 드러난 것 — 중복 행 4쌍

두 노션 행이 같은 접수를 가리키는 경우가 4건 있다. 전부 **같은 분 안에 생성된 같은 내용**이고,
그중 3건은 양쪽 다 `드랍메일=True` 다(= 같은 사람이 드랍 초안을 2벌 받았다).
옛 sync 의 멱등이 **가드 한 겹**(장부)뿐이고 노션 생성이 upsert 가 아니라서 생긴 것이다
(`job.py` 결정 C 는 가드 + 부작용 멱등 **두 겹**을 요구한다).

    이 도구는 그 쌍을 **지우지 않는다.** 같은 키를 양쪽에 심고 목록으로 보고한다 —
    어느 쪽을 남길지는 사람이 정한다(판정 결과가 다른 쌍도 있다: 칼드랍 vs Red).

## 쓰는 법

    python tools/bplan_backfill_key.py            # dry-run(기본) — 계획만 출력, 노션 쓰기 0
    python tools/bplan_backfill_key.py --apply    # 실제 기입

`--apply` 는 노션에 **속성 1칸을 추가**하고 519행에 값을 쓴다. 되돌리려면 그 칸을 비우면 된다
(기존 데이터는 건드리지 않는다 — 새 칸만 쓴다).
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import os
import re
import sys
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(_REPO / "kv-core"))

from kv_core import notion, secrets  # noqa: E402

KEY_PROP = "접수키"          # 노션에 새로 만드는 rich_text 칸
_TS = re.compile(r"\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\s*(오전|오후)?\s*(\d{1,2}):(\d{2}):(\d{2})")


def parse_ts(s: str) -> dt.datetime | None:
    """구글폼 한국어 로케일 타임스탬프 → datetime. 예: '2026. 7. 30 오전 11:54:25'."""
    m = _TS.match(s or "")
    if not m:
        return None
    y, mo, d, ap, h, mi, se = m.groups()
    h = int(h)
    if ap == "오후" and h != 12:
        h += 12
    if ap == "오전" and h == 12:
        h = 0
    return dt.datetime(int(y), int(mo), int(d), h, int(mi), int(se))


def submission_key(email: str, ts: dt.datetime) -> str:
    """접수의 불변 식별자. 시트 위치·A1 커서와 무관하다."""
    return f"{(email or '').strip().lower()}|{ts.isoformat(timespec='seconds')}"


def _plain(props: dict, key: str) -> str:
    v = props.get(key) or {}
    t = v.get("type")
    if t in ("rich_text", "title"):
        return "".join(x.get("plain_text", "") for x in v[t]).strip()
    if t == "select":
        return (v["select"] or {}).get("name", "")
    if t == "checkbox":
        return v["checkbox"]
    return ""


def load_sheet() -> list[dict]:
    import gspread
    gc = gspread.service_account(filename=os.environ["GOOGLE_CREDENTIALS_FILE"])
    sh = gc.open_by_url(os.environ["SPREADSHEET_URL"])
    rows = sh.worksheet(os.environ.get("WORKSHEET_NAME", "비플랜DB2")).get_all_values()[1:]
    out = []
    for n, r in enumerate(rows):
        if not any(r):
            continue
        out.append({"ts_raw": r[0], "ts": parse_ts(r[0]),
                    "email": (r[1] or "").strip().lower(),
                    "co": (r[13] if len(r) > 13 else "").strip(),
                    "row": n + 2})
    return out


def load_notion(tok: str, db: str) -> list[dict]:
    rows, cur = [], None
    while True:
        payload = {"page_size": 100, **({"start_cursor": cur} if cur else {})}
        r = notion.write("POST", f"{notion.API}/databases/{db}/query", tok, payload)
        if r.status_code != 200:
            raise RuntimeError(f"노션 조회 실패 {r.status_code}: {r.text[:200]}")
        d = r.json()
        for p in d["results"]:
            P = p["properties"]
            # 노션 created_time 은 UTC — 시트 타임스탬프는 KST 다. 비교 전에 맞춘다.
            created = dt.datetime.fromisoformat(
                p["created_time"].replace("Z", "+00:00")).replace(tzinfo=None) + dt.timedelta(hours=9)
            rows.append({"id": p["id"], "created": created,
                         "email": _plain(P, "이메일").lower(), "co": _plain(P, "기업명"),
                         "todo": _plain(P, "To do"), "score": _plain(P, "점수"),
                         "drop": _plain(P, "드랍메일"),
                         "existing": _plain(P, KEY_PROP)})
        if not d.get("has_more"):
            break
        cur = d["next_cursor"]
    return rows


def assign(sheet: list[dict], nrows: list[dict]) -> tuple[dict[str, str], list[str], dict]:
    """노션 page_id → submission_key. 배정 규칙과 그 근거는 모듈 독스트링 참조."""
    by = collections.defaultdict(list)
    for s in sheet:
        by[(s["email"], s["co"])].append(s)

    mapping: dict[str, str] = {}
    unresolved: list[str] = []
    used: dict[str, list[str]] = collections.defaultdict(list)
    for n in nrows:
        cands = sorted((c for c in by.get((n["email"], n["co"]), []) if c["ts"]),
                       key=lambda x: x["ts"])
        # 노션 행은 접수 **뒤에** 만들어진다 → 생성시각 이전의 가장 최근 접수가 그 행의 접수다.
        before = [c for c in cands if c["ts"] <= n["created"]]
        pick = before[-1] if before else (cands[0] if cands else None)
        if not pick:
            unresolved.append(n["id"])
            continue
        k = submission_key(pick["email"], pick["ts"])
        mapping[n["id"]] = k
        used[k].append(n["id"])
    dupes = {k: ids for k, ids in used.items() if len(ids) > 1}
    return mapping, unresolved, dupes


def ensure_prop(tok: str, db: str, apply: bool) -> bool:
    """노션 DB 에 `접수키` rich_text 칸이 있는지 보고, 없으면 (apply 일 때) 만든다."""
    schema = notion.fetch_schema(db, tok)
    if KEY_PROP in schema:
        return True
    if not apply:
        print(f"  · `{KEY_PROP}` 칸 없음 — --apply 시 rich_text 로 **추가**한다(기존 칸 불변)")
        return False
    r = notion.write("PATCH", f"{notion.API}/databases/{db}", tok,
                     {"properties": {KEY_PROP: {"rich_text": {}}}})
    if r.status_code != 200:
        raise RuntimeError(f"`{KEY_PROP}` 칸 생성 실패 {r.status_code}: {r.text[:200]}")
    print(f"  ✅ `{KEY_PROP}` 칸 생성")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 노션에 쓴다(기본은 dry-run)")
    args = ap.parse_args()

    secrets.load()
    tok, db = os.environ["NOTION_TOKEN"], os.environ["NOTION_DATABASE_ID"]

    print(f"{'═' * 66}\n  비플랜 접수키 소급 — {'APPLY' if args.apply else 'DRY-RUN(쓰기 0)'}\n{'═' * 66}")
    sheet = load_sheet()
    bad_ts = [s for s in sheet if s["ts"] is None]
    print(f"  시트 유효행 {len(sheet)} · 타임스탬프 파싱 실패 {len(bad_ts)}")
    if bad_ts:
        print(f"  🔴 파싱 실패 행은 배정에서 제외된다: {[s['row'] for s in bad_ts[:5]]}")

    nrows = load_notion(tok, db)
    print(f"  노션 {len(nrows)}행 (이미 접수키 있음 {sum(1 for n in nrows if n['existing'])}행)")

    mapping, unresolved, dupes = assign(sheet, nrows)
    print(f"\n  배정 {len(mapping)}/{len(nrows)} · 미배정 {len(unresolved)}")

    if dupes:
        print(f"\n  🔴 같은 접수를 가리키는 노션 행 {len(dupes)}쌍 — **옛 sync 가 만든 중복이다.**")
        print("     지우지 않는다. 같은 키를 양쪽에 심고 아래 목록으로 남긴다 — 어느 쪽을")
        print("     남길지는 사람이 정한다(판정이 다른 쌍도 있다).")
        idx = {n["id"]: n for n in nrows}
        for k, ids in dupes.items():
            print(f"     · {k}")
            for i in ids:
                n = idx[i]
                print(f"         {n['created']:%Y-%m-%d %H:%M} · To do={n['todo']!r} "
                      f"점수={n['score']!r} 드랍={n['drop']} · {i}")

    changed = {i: k for i, k in mapping.items()
               if next(n for n in nrows if n["id"] == i)["existing"] != k}
    print(f"\n  기입 대상 {len(changed)}행 (이미 같은 값인 행은 건너뛴다 — 멱등)")

    if not args.apply:
        print(f"\n  ⏸️  DRY-RUN — 노션 쓰기 0. 실행하려면 --apply")
        return 0

    if not ensure_prop(tok, db, apply=True):
        return 1
    n_ok = 0
    for pid, key in changed.items():
        r = notion.write("PATCH", f"{notion.API}/pages/{pid}", tok,
                         {"properties": {KEY_PROP: {"rich_text": [{"text": {"content": key}}]}}})
        if r.status_code != 200:
            print(f"  ⚠️ 기입 실패 {pid[:8]}: {r.status_code} {r.text[:120]}")
            continue
        n_ok += 1
    print(f"\n  ✅ {n_ok}/{len(changed)}행 기입 완료")
    return 0 if n_ok == len(changed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
