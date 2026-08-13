r"""[W12 재빌드] 비플랜 접수 동기화 — 구글폼 시트 → 노션. **upsert**.

옛 `bplan/main.py` 의 sync 를 대체한다. 같은 일을 하지만 **커서와 멱등이 완전히 다르다.**

## 무엇이 달라졌나

| | 옛 sync | 이 잡 |
|---|---|---|
| 멱등 키 | 시트 행 서수 `str(5483)` | **접수키** `이메일\|타임스탬프` |
| 커서 | 시트 `Setting!A1` (리포 밖·락 없음·사람이 편집) | **커서가 없다** — 노션이 진실 |
| 장부 | json 덮어쓰기 · 500건 절삭 · fsync 없음 | `events.jsonl` append-only |
| 원자적 쓰기 | 고정 tmp 이름(동시 writer 400회에 예외 21회) | `job.atomic_write` (pid 분리) |
| 락 | 0개 (`threading.Lock` 은 프로세스 간 무효) | `resources=[sheet-a1, notion-bplan]` |
| 부작용 멱등 | 가드 한 겹 → 생성 200 직후 크래시 = 중복 | 가드 + **노션 조회 upsert** 두 겹 |

## 커서가 없는 것이 핵심이다

접수키를 노션 행에 심어두면 **"이 접수가 이미 있나"를 노션에 직접 물어볼 수 있다.**
그러면 별도 커서가 필요 없다 — 부작용을 담은 곳(노션)이 곧 진행 상태다.

옛 방식은 진행도를 세 곳에 두었다(시트 A1 · sync_ledger · 노션 실물). 셋이 어긋나면
어느 것도 믿을 수 없었고, 실제로 A1 을 되돌리면 그 구간이 영영 유실됐다.
지금은 **한 곳**이고, 그 한 곳이 부작용 자체다(정본 R1 의 정공법).

⚠️ 대가: 매 실행마다 노션 전수 조회(519행 ≈ 6요청). judge 도 같은 비용을 낸다.
   행이 수천 단위가 되면 `접수키 is_not_empty` 필터 + 최근 N개만 조회로 좁힌다.

## 이 잡이 건드리지 않는 것 — 안전 경계

    쓰는 것    노션 **행 생성**(신규 접수만)
    안 쓰는 것 Gmail · 라벨 · 아카이빙 · `To do` 이동 · 드랍 초안 · 시트(A1 포함)

옛 sync 는 접수메일에 담당자 라벨을 붙이고 **인박스에서 뺐다**(`removeLabelIds:['INBOX']`).
그건 되돌리기 번거롭고, 회사명이 공란이면 **남의 메일**을 건드린다(리뷰 C-F-B15).
이 잡은 메일을 아예 안 본다. 라벨링은 컷오버 이후 별도 결정으로 남긴다.

실행:
    python jobs/bplan_sync_job.py --dry            # 계획만(노션 쓰기 0) — 기본값
    python jobs/bplan_sync_job.py --apply --limit 5
    python jobs/bplan_sync_job.py --synthetic      # 시트·노션 불호출
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(_REPO / "kv-core"))

from kv_core import alerts, job, notion, paths, secrets  # noqa: E402
from kv_core.job import Stage  # noqa: E402

sys.path.insert(0, str(PROJ / "tools"))
from bplan_backfill_key import KEY_PROP, parse_ts, submission_key  # noqa: E402

SKILL = "mail-harness"

# 🔴 시트 헤더 → 노션 칸. **접두사로 맞춘다.**
#    옛 코드는 질문 문장 **전체**를 키로 썼다(`row.get('2. 문제 정의 : 누구의 & 어떤 구체적…')`).
#    폼 문구를 한 글자만 고쳐도 그 칸이 조용히 `'None'` 이 된다 — 사고가 나도 400 이 아니라
#    "빈 값이 들어간 행"이라 눈에 안 띈다. 번호·머리말은 잘 안 바뀌므로 접두사로 잡고,
#    **하나라도 못 찾으면 fail-loud** 한다(아래 `_resolve_columns`).
_COLS: dict[str, str] = {
    "이메일": "이메일 주소",
    "대표명": "대표님의 성함",
    "연락처": "대표님의 연락처",
    "서비스정의": "1. 팀의 Service/Product",
    "문제정의": "2. 문제 정의",
    "솔루션": "3. 솔루션",
    "비즈니스모델": "4. 핵심 비즈니스 모델",
    "경쟁사": "5. 핵심 경쟁사",
    "팀이력": "6. 대표님과 핵심 구성원",
    "경쟁력": "7. 우리 팀이 이 서비스",
    "추가내용": "(선택)추가로 공유",
    "사업계획서": "팀의 사업계획서를 첨부",
    "기업명": "기업명을 알려주세요",
}
_TS_COL = "타임스탬프"
_URL_PROPS = {"사업계획서"}
_TITLE_PROP = "기업명"


def _cfg() -> dict:
    secrets.load()
    return {"token": os.environ.get("NOTION_TOKEN"),
            "db": os.environ.get("NOTION_DATABASE_ID"),
            "sheet": os.environ.get("SPREADSHEET_URL"),
            "ws": os.environ.get("WORKSHEET_NAME", "비플랜DB2"),
            "creds": os.environ.get("GOOGLE_CREDENTIALS_FILE")}


def _queue_path(job_id: str) -> Path:
    return job.job_dir(SKILL, job_id) / "artifacts" / "queue.json"


def _resolve_columns(headers: list[str]) -> dict[str, int]:
    """헤더 접두사 → 열 인덱스. **하나라도 못 찾으면 멈춘다.**

    조용히 넘기면 그 칸이 빈 채로 노션에 들어가고, 판정(bplan-score)은 빈 칸을 근거로
    Red 를 낸다 — 데이터 사고가 판정 사고로 번진다.
    """
    idx: dict[str, int] = {}
    missing: list[str] = []
    for prop, prefix in _COLS.items():
        hit = [i for i, h in enumerate(headers) if (h or "").strip().startswith(prefix)]
        if len(hit) == 1:
            idx[prop] = hit[0]
        else:
            missing.append(f"{prop}←'{prefix}' ({len(hit)}개 매칭)")
    ts = [i for i, h in enumerate(headers) if (h or "").strip().startswith(_TS_COL)]
    if len(ts) == 1:
        idx["_ts"] = ts[0]
    else:
        missing.append(f"_ts←'{_TS_COL}' ({len(ts)}개 매칭)")
    if missing:
        raise RuntimeError(
            "🔴 시트 헤더를 못 찾았다 — 폼 문구가 바뀌었을 수 있다:\n   "
            + "\n   ".join(missing)
            + f"\n   실제 헤더: {[h[:30] for h in headers]}")
    return idx


# ─────────────────────────── stages ───────────────────────────

def s_fetch(skill: str, job_id: str) -> None:
    """시트에서 **아직 노션에 없는 접수**를 골라 큐로. 읽기 전용."""
    import gspread
    c = _cfg()
    for k in ("token", "db", "sheet", "creds"):
        if not c[k]:
            raise RuntimeError(f"환경변수 부족: {k}")

    gc = gspread.service_account(filename=c["creds"])
    rows = gc.open_by_url(c["sheet"]).worksheet(c["ws"]).get_all_values()
    if not rows:
        raise RuntimeError("시트가 비었다 — 연결/권한 확인")
    idx = _resolve_columns(rows[0])

    subs: list[dict] = []
    bad_ts = 0
    for n, r in enumerate(rows[1:], start=2):
        if not any(r):
            continue
        raw = r[idx["_ts"]] if len(r) > idx["_ts"] else ""
        ts = parse_ts(raw)
        if ts is None:
            bad_ts += 1
            continue
        email = (r[idx["이메일"]] if len(r) > idx["이메일"] else "").strip()
        rec = {p: (r[i] if len(r) > i else "").strip()
               for p, i in idx.items() if not p.startswith("_")}
        subs.append({"key": submission_key(email, ts), "row": n,
                     "ts": ts.isoformat(timespec="seconds"), "props": rec})
    if bad_ts:
        # 조용히 버리면 그 접수는 영영 안 들어간다. 사실로 남긴다.
        print(f"⚠️  타임스탬프를 못 읽어 제외한 행 {bad_ts}건 — 시트 확인 요망")

    have = _existing_keys(c["token"], c["db"])
    todo = [s for s in subs if s["key"] not in have]
    print(f"· 시트 접수 {len(subs)} · 노션 보유 {len(have)} · **신규 {len(todo)}**")

    if not todo:
        # 진공을 성공으로 조용히 넘기지 않는다(R5). 상류가 죽어도 여기선 0 으로 보인다.
        alerts.observe("empty_harvest", "bplan-sync-vacuum",
                       f"⚠️ bplan-sync: 신규 접수 0건 (시트 {len(subs)} · 노션 {len(have)}). "
                       f"정상일 수도 있고 시트 연결·권한이 끊겼을 수도 있다.",
                       task="bplan-sync", candidates=0)
    job.atomic_write(_queue_path(job_id),
                     json.dumps({"rows": todo}, ensure_ascii=False, indent=1))


def _existing_keys(tok: str, db: str) -> set[str]:
    """노션에 이미 있는 접수키 전부. **이게 커서다.**"""
    out: set[str] = set()
    cur = None
    while True:
        r = notion.write("POST", f"{notion.API}/databases/{db}/query", tok,
                         {"page_size": 100, **({"start_cursor": cur} if cur else {})})
        if r.status_code != 200:
            raise RuntimeError(f"노션 조회 실패 {r.status_code}: {r.text[:200]}")
        d = r.json()
        for p in d["results"]:
            v = (p["properties"].get(KEY_PROP) or {}).get("rich_text") or []
            if v and v[0].get("plain_text"):
                out.add(v[0]["plain_text"].strip())
        if not d.get("has_more"):
            break
        cur = d["next_cursor"]
    return out


def _payload(db: str, sub: dict, assignee: str) -> dict:
    props: dict = {KEY_PROP: {"rich_text": [{"text": {"content": sub["key"]}}]},
                   "To do": {"select": {"name": "Newly Added"}},
                   "담당자": {"select": {"name": assignee}}}
    for prop, val in sub["props"].items():
        if prop in _URL_PROPS:
            if val.strip():
                props[prop] = {"url": val.strip()}
            continue
        # 🔴 `[:2000]` 이 아니라 clip16 — 노션은 UTF-16 유닛을 센다(이모지 1자 = 2유닛).
        #    옛 코드의 `content[:2000]` 은 2026-07-27 에 링크드인 7건을 죽인 그 형태다.
        text = notion.clip16(val)
        key = "title" if prop == _TITLE_PROP else "rich_text"
        props[prop] = {key: [{"text": {"content": text}}]}
    return {"parent": {"database_id": db}, "properties": props}


def s_upsert(skill: str, job_id: str, *, limit: int, dry: bool, assignee: str) -> None:
    """접수키로 upsert. 부작용(노션 생성) → 그 뒤에 item_done(결정 C 순서)."""
    q = json.loads(_queue_path(job_id).read_text(encoding="utf-8"))
    rows = q.get("rows") or []
    if limit and len(rows) > limit:
        print(f"· 상한 {limit}건 — 잔여 {len(rows) - limit}건은 다음 실행")
        rows = rows[:limit]
    if not rows:
        print("· 신규 0건")
        return

    c = _cfg()
    tok, db = c["token"], c["db"]

    def _one(sub: dict) -> None:
        if dry:
            print(f"  [dry] {sub['key']}  {sub['props'].get('기업명','')[:24]}")
            return
        # ② 부작용 멱등 — 만들기 **직전에** 노션에 다시 묻는다. 가드(events)와 두 겹이다.
        #    가드만 있으면 "생성 200 직후 장부 쓰기 전 크래시"가 그대로 중복이 된다
        #    (옛 sync 가 실제로 그렇게 4쌍을 만들었다 — W12 배치 9-1 실측).
        r = notion.write("POST", f"{notion.API}/databases/{db}/query", tok,
                         {"page_size": 1,
                          "filter": {"property": KEY_PROP,
                                     "rich_text": {"equals": sub["key"]}}})
        if r.status_code == 200 and r.json().get("results"):
            print(f"  · 이미 있음(스킵) {sub['key']}")
            return
        cr = notion.write("POST", f"{notion.API}/pages", tok, _payload(db, sub, assignee))
        if cr.status_code != 200:
            raise RuntimeError(f"노션 생성 실패 {cr.status_code}: {cr.text[:300]}")
        print(f"  ✅ {sub['key']}  {sub['props'].get('기업명','')[:24]}")

    d = job.process_items(SKILL, job_id, rows, key=lambda s: s["key"], process=_one,
                          stage="upsert")
    print(f"· 생성 {len(d.done_keys)}건 · 건너뜀 {len(d.skipped)}건")


def _stages(limit: int, dry: bool, assignee: str) -> list[Stage]:
    return [Stage("fetch", s_fetch),
            Stage("upsert", lambda sk, ji: s_upsert(sk, ji, limit=limit, dry=dry,
                                                    assignee=assignee))]


def _synthetic() -> int:
    """시트·노션 불호출 자기검사 — 헤더 매칭과 payload 안전성만 본다."""
    ok, fail = [], []

    def chk(name: str, cond: bool, detail: str = "") -> None:
        (ok if cond else fail).append(name)
        print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))

    hdr = ["타임스탬프", "이메일 주소", "대표님의 성함을 알려주세요.(Please tell us your name.)",
           "대표님의 연락처를 알려주세요. (Please share…)",
           "1. 팀의 Service/Product를 한 문장으로…", "2. 문제 정의 : 누구의…",
           "3. 솔루션 : 그 고충/불편을…", "4. 핵심 비즈니스 모델(e.g. 거래 수수료)…",
           "5. 핵심 경쟁사들은 어떤 곳이…", "6. 대표님과 핵심 구성원(c-level)…",
           "7. 우리 팀이 이 서비스 & 기술을…", "(선택)추가로 공유해 주고 싶으신…",
           "팀의 사업계획서를 첨부해주세요.(…)", "기업명을 알려주세요.(…)"]
    idx = _resolve_columns(hdr)
    chk("① 실제 헤더 14개를 전부 해석한다", len(idx) == len(_COLS) + 1, f"{len(idx)}개")

    try:
        _resolve_columns([h for h in hdr if not h.startswith("2. 문제")])
        chk("② 헤더가 사라지면 fail-loud", False, "조용히 통과함")
    except RuntimeError as e:
        chk("② 헤더가 사라지면 fail-loud", "문제정의" in str(e))

    ts = parse_ts("2026. 8. 13 오후 1:02:03")
    chk("③ 한국어 로케일 타임스탬프를 읽는다", ts is not None and ts.hour == 13, str(ts))
    chk("④ 접수키가 이메일·시각만으로 결정된다(행 위치 무관)",
        submission_key(" A@B.COM ", ts) == f"a@b.com|{ts.isoformat(timespec='seconds')}")

    sub = {"key": "x@y|2026-01-01T00:00:00",
           "props": {"기업명": "테스트", "이메일": "x@y", "사업계획서": "",
                     "추가내용": "가" * 1500 + "🚀" * 400}}
    p = _payload("db", sub, "에반 Evan")["properties"]
    chk("⑤ 접수키가 payload 에 실린다", KEY_PROP in p)
    chk("⑥ 기업명은 title 로 간다", "title" in p["기업명"])
    chk("⑦ 빈 URL 칸은 아예 안 싣는다(400 방지)", "사업계획서" not in p)
    units = len(p["추가내용"]["rich_text"][0]["text"]["content"].encode("utf-16-le")) // 2
    chk("⑧ UTF-16 유닛 기준으로 잘린다(이모지 안전)", units <= 2000, f"{units}유닛")
    danger = {"To do", "드랍메일"} & set(p)
    chk("⑨ 드랍 경로 칸을 안 쓴다", danger == {"To do"},
        f"To do 는 'Newly Added' 신규 생성용 · 드랍메일 미포함: {sorted(set(p) & {'드랍메일'})}")

    print(f"\n{'🎉 synthetic PASS' if not fail else '🔴 실패: ' + str(fail)}")
    return 0 if not fail else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제로 노션에 생성(기본은 dry)")
    ap.add_argument("--limit", type=int, default=5, help="이번 실행 최대 생성 건수(0=무제한)")
    ap.add_argument("--assignee", default=os.getenv("BPLAN_ASSIGNEE", "에반 Evan"))
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    if args.synthetic:
        assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"
        return _synthetic()

    jid = f"bplan-sync-{date.today().isoformat()}"
    for n in range(1, 4):
        cand = jid if n == 1 else f"{jid}-r{n}"
        if job.rebuild(SKILL, cand).state == "failed":
            continue
        jid = cand
        break
    job.create(SKILL, {"kind": "bplan-sync", "period": date.today().isoformat()}, jid)
    d = job.run_pipeline(SKILL, jid, _stages(args.limit, not args.apply, args.assignee),
                         resources=["sheet-a1", "notion-bplan"])
    print(f"[bplan-sync] {jid} → {d.state}")
    return 0 if d.state == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
