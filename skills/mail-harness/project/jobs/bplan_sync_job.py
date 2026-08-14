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

## 진행 상태는 **바닥선(floor) + 접수키** 두 축이다

🔴 처음엔 "커서를 아예 없앤다 — 노션에 없으면 신규다"로 설계했다. **dry-run 이 그걸
   즉시 깨뜨렸다**: 신규 36건을 기대했는데 **5,033건**이 나왔다.

    시트    5,548건 · 2020~2026 (6년치 전체 이력)
    노션      515건 · **2026-03-17 ~ 2026-08-01 만**

   노션 DB 는 최근 4.5개월만 담고 있다(오래된 행은 정리됐거나 DB 신설 전이다).
   즉 **"노션에 없다" ≠ "아직 안 넣었다"** — 5,033건은 이미 처리됐거나 처리 대상이
   아니었던 것이다. 그대로 돌렸으면 6년치 접수를 노션에 쏟아부었다.

   → 두 축으로 나눈다:
       **바닥선(floor)**  "이 시각 이후 접수만 본다" — 되살리기를 막는다
       **접수키**         "그 창 안에서 이미 넣었나" — 재실행·크래시를 막는다

   키만으로는 "한 번도 안 넣음"과 "넣었다 지움"을 구분할 수 없다. 바닥선이 그 구분이다.

   바닥선 = `max(저장된 바닥선, 노션 접수키의 최대 시각)` — **단조 증가**다.
   저장 파일이 사라져도 노션 쪽이 받쳐주고(보수적), 사람이 최근 행을 지워도 저장 파일이
   받쳐준다. 어느 쪽으로도 과거를 되살리지 않는다.

⚠️ 대가: 매 실행마다 노션 전수 조회(≈6요청). judge 도 같은 비용을 낸다.
   행이 수천 단위가 되면 `접수키 is_not_empty` 필터 + 최근 N개만 조회로 좁힌다.

## 🔴 [2026-08-14] 라벨 단계를 되살렸다 — 뺀 것이 안전 경계가 아니라 **일의 절반**이었다

재빌드 첫 판(08-13)의 이 자리엔 이렇게 적혀 있었다:

    안 쓰는 것 Gmail · 라벨 · 아카이빙 …
    이 잡은 메일을 아예 안 본다. 라벨링은 컷오버 이후 별도 결정으로 남긴다.

**틀렸다.** 라벨은 sync 의 부수 효과가 아니라 **다음 단계의 입력**이다:

    bplan_drop_job.find_original()  →  q = f`label:"{label}" subject:"…"`
                                          ↑ 이 라벨이 없으면 원본을 **영영 못 찾는다**

게다가 트리아지는 `BPLAN_INTAKE` 를 `protect` 로 둔다 — **일부러 안 건드리고 sync 에
양보한다**(`references/routing.md`). 그래서 sync 가 빠지자 42건이 **아무도 담당하지 않는
상태**로 인박스에 남았다(08-03~08-13, 노션엔 42/42 전부 있음. 08-14 실측).

sync 와 drop 을 따로 재빌드하고 각각 격리 검증한 것이 원인이다. **"앞 단계의 산출물이
뒷 단계의 입력을 만족하는가"** 를 아무도 안 봤다. 그 계약을 `_synthetic_outbound.py` O9 로 옮겼다.

## 이 잡의 경계

    쓰는 것    노션 **행 생성**(신규 접수만) · Gmail **라벨 부여 + INBOX 제거**(=아카이빙)
    안 쓰는 것 **발송** · `To do` 이동 · 드랍 초안 · 시트(A1 포함) · 접수메일 **본문**

아카이빙은 **되돌릴 수 있다**(INBOX 라벨 재부여). 삭제가 아니다.

### 라벨 단계는 자기 수리한다 — 옛 코드가 못 하던 것

옛 sync 는 "방금 만든 행"에만 라벨을 붙였고, 실패하면 `⚠️` 한 줄 찍고 **영구 고아**로
남겼다(장부가 "완료"라 다시 안 잡는다). 여기서는 작업 대상을 다르게 잡는다:

    작업 대상 = "라벨이 없고 아직 인박스에 있는 접수 메일"   ← 노션 상태가 아니라 **Gmail 상태**

그래서 ① 지난 실행의 실패분이 다음 실행에 자동으로 다시 잡히고 ② 재실행이 무해하며
(질의 자체가 이미 처리된 것을 제외한다) ③ 이번 42건 같은 **소급 복구가 같은 코드**로 된다.

⚠️ 노션에 행이 없는 접수메일은 **건드리지 않는다.** 라벨만 붙여 인박스에서 빼면 아무도
   추적하지 않는 메일이 조용히 사라진다 — 그건 이 잡이 막아야 할 사고지 처리할 일이 아니다.

실행:
    python jobs/bplan_sync_job.py --dry            # 계획만(노션·Gmail 쓰기 0) — 기본값
    python jobs/bplan_sync_job.py --apply --limit 5
    python jobs/bplan_sync_job.py --apply --relabel-only   # 라벨 복구만(행 생성 안 함)
    python jobs/bplan_sync_job.py --synthetic      # 시트·노션·Gmail 불호출
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(_REPO / "kv-core"))

from kv_core import alerts, job, notion, paths, secrets  # noqa: E402
from kv_core.job import Stage  # noqa: E402
from core.labels import bplan_label  # noqa: E402

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
    floor = _floor(have)
    # 바닥선 **이후** 접수만 본다(되살리기 금지) + 그 창 안에서 접수키로 중복 제거.
    todo = [s for s in subs if s["ts"] > floor and s["key"] not in have]
    print(f"· 시트 접수 {len(subs)} · 노션 보유 {len(have)} · 바닥선 {floor} · **신규 {len(todo)}**")

    if not todo:
        # 진공을 성공으로 조용히 넘기지 않는다(R5). 상류가 죽어도 여기선 0 으로 보인다.
        alerts.observe("empty_harvest", "bplan-sync-vacuum",
                       f"⚠️ bplan-sync: 신규 접수 0건 (시트 {len(subs)} · 노션 {len(have)}). "
                       f"정상일 수도 있고 시트 연결·권한이 끊겼을 수도 있다.",
                       task="bplan-sync", candidates=0)
    job.atomic_write(_queue_path(job_id),
                     json.dumps({"rows": todo}, ensure_ascii=False, indent=1))


def _floor_path() -> Path:
    return paths.data(SKILL) / "bplan_sync_floor.json"


def _floor(have: set[str]) -> str:
    """이 시각 **이후** 접수만 처리한다. 단조 증가 — 과거를 되살리지 않는다.

    `max(저장값, 노션 접수키의 최대 시각)`:
      · 저장 파일이 사라져도 노션이 받쳐준다(보수적 — 오래된 것을 안 되살린다)
      · 사람이 최근 행을 지워도 저장 파일이 받쳐준다(같은 방향)
    둘 다 실패하면 **오늘 자정**으로 떨어진다 — 그것도 되살리기보다 안전한 쪽이다
    (놓친 접수는 사람이 `--since` 로 명시해 넣는다. 조용히 6년치를 붓는 것보다 낫다).
    """
    stored = ""
    p = _floor_path()
    if p.exists():
        try:
            stored = json.loads(p.read_text(encoding="utf-8")).get("last_ts", "")
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️  바닥선 파일 판독 실패({e}) — 노션 쪽 값으로 진행한다")
    live = max((k.split("|", 1)[1] for k in have if "|" in k), default="")
    floor = max(stored, live) or date.today().isoformat() + "T00:00:00"
    if not stored and not live:
        print(f"🔴 바닥선 근거가 하나도 없다 — 오늘 자정({floor})으로 잡는다. "
              f"과거 접수를 넣으려면 사람이 명시적으로 처리하라.")
    return floor


def _save_floor(ts: str) -> None:
    """성공한 것까지만 전진. 실패한 건은 다음 실행에 다시 잡힌다(구멍 방지)."""
    cur = ""
    p = _floor_path()
    if p.exists():
        try:
            cur = json.loads(p.read_text(encoding="utf-8")).get("last_ts", "")
        except (json.JSONDecodeError, OSError):
            cur = ""
    if ts > cur:
        job.atomic_write(p, json.dumps({"last_ts": ts}, ensure_ascii=False, indent=1))


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

    # ⚠️ `process_items` 는 `Derived` 가 아니라 **(done, skipped) 튜플**을 돌려준다.
    #    `d.done_keys` 로 썼다가 첫 실행에서 AttributeError 로 죽었다 — 2건을 실제로
    #    처리한 **뒤에** 죽어서, 부작용은 났는데 잡은 failed 였다. dry-run 이었기에
    #    망정이지 --apply 였으면 "노션엔 들어갔는데 실패로 기록"이 됐다.
    done, skipped = job.process_items(SKILL, job_id, rows, key=lambda s: s["key"],
                                      process=_one, stage="upsert")
    print(f"· 생성 {done}건 · 건너뜀 {skipped}건")

    # 바닥선은 **실제로 처리한 것까지만** 전진한다. 실패분은 다음 실행에 다시 잡힌다.
    if not dry and done:
        finished = job.rebuild(SKILL, job_id).done_keys
        ok_ts = [r["ts"] for r in rows if r["key"] in finished]
        if ok_ts:
            _save_floor(max(ok_ts))
            print(f"· 바닥선 전진 → {max(ok_ts)}")


# ─────────────────────────── Gmail 라벨 ───────────────────────────

_SUBJ_RE = re.compile(r"^\[카카오벤처스\]\s*(.*?)\s*로부터 접수된 사업계획서")


def _company_from_subject(subject: str) -> str:
    """제목에서 기업명. 못 뽑으면 빈 문자열 — 추측하지 않는다."""
    m = _SUBJ_RE.match(subject or "")
    return m.group(1).strip() if m else ""


def _norm(s: str) -> str:
    """기업명 대조용 정규화. 노션 제목과 메일 제목은 표기가 흔들린다
    (`(주)차엔차` vs `차엔차` · 공백·괄호). 대조에만 쓰고 **저장하지 않는다**."""
    return re.sub(r"[\s()（）,.\-_]|주식회사|\(주\)|㈜", "", s or "").lower()


def _gmail():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    return build("gmail", "v1", cache_discovery=False,
                 credentials=Credentials.from_authorized_user_file(
                     str(secrets.credential_path("google/token.json"))))


def _notion_titles(tok: str, db: str) -> set[str]:
    """노션 Bplan DB 의 기업명(정규화) 집합."""
    out, cur = set(), None
    while True:
        body = {"page_size": 100} | ({"start_cursor": cur} if cur else {})
        r = notion.write("POST", f"{notion.API}/databases/{db}/query", tok, body)
        if r.status_code != 200:
            raise RuntimeError(f"노션 조회 실패 {r.status_code}: {r.text[:200]}")
        j = r.json()
        for p in j["results"]:
            for v in p["properties"].values():
                if v["type"] == "title":
                    out.add(_norm("".join(x["plain_text"] for x in v["title"])))
                    break
        cur = j.get("next_cursor")
        if not j.get("has_more"):
            return out


def s_label(skill: str, job_id: str, *, dry: bool, label: str) -> None:
    """접수메일에 담당자 라벨을 붙이고 인박스에서 뺀다. **자기 수리** — 윗 독스트링 참조.

    작업 대상은 Gmail 상태로 정의한다: *라벨 없고 아직 인박스에 있는 접수메일*.
    그래서 지난 실행의 실패분이 다음 실행에 다시 잡히고, 재실행이 무해하다.
    """
    svc = _gmail()
    ids = {l["name"]: l["id"] for l in
           svc.users().labels().list(userId="me").execute().get("labels", [])}
    if label not in ids:
        # 🔴 조용히 넘어가면 "라벨 0건 처리"가 정상처럼 보인다. 라벨은 사람이 만든다.
        raise RuntimeError(f"Gmail 라벨 '{label}' 이 없다 — 라벨을 먼저 만들어라. "
                           f"이 단계를 건너뛰면 drop 이 원본을 영영 못 찾는다.")

    q = f'subject:"로부터 접수된 사업계획서" in:inbox -label:"{label}"'
    msgs, tokn = [], None
    while True:
        r = svc.users().messages().list(userId="me", q=q, pageToken=tokn,
                                        maxResults=500).execute()
        msgs += r.get("messages", [])
        tokn = r.get("nextPageToken")
        if not tokn:
            break
    if not msgs:
        print("· 라벨 대상 없음 — 접수메일 전부 처리됨")
        return

    c = _cfg()
    titles = _notion_titles(c["token"], c["db"])
    print(f"· 라벨 대상 {len(msgs)}건 (노션 기업명 {len(titles)}종과 대조)")

    orphan: list[str] = []

    def _one(m: dict) -> None:
        full = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                          metadataHeaders=["Subject"]).execute()
        subj = {h["name"]: h["value"]
                for h in full.get("payload", {}).get("headers", [])}.get("Subject", "")
        comp = _company_from_subject(subj)
        if len(comp) < 2:
            orphan.append(f"{m['id']} 기업명 추출 실패: {subj[:50]!r}")
            return
        # ⚠️ 노션에 없으면 **안 건드린다.** 라벨만 붙여 인박스에서 빼면 아무도 추적하지
        #    않는 메일이 조용히 사라진다 — 접수는 됐는데 sync 가 안 된 상태를 감추게 된다.
        if _norm(comp) not in titles:
            orphan.append(f"{comp} — 노션에 행이 없다(sync 누락 의심)")
            return
        if dry:
            print(f"  [dry] 라벨+아카이브 → {comp[:30]}")
            return
        svc.users().messages().modify(
            userId="me", id=m["id"],
            body={"addLabelIds": [ids[label]], "removeLabelIds": ["INBOX"]}).execute()
        print(f"  ✅ {comp[:30]}")

    done, skipped = job.process_items(SKILL, job_id, msgs, key=lambda m: m["id"],
                                      process=_one, stage="label")
    print(f"· 라벨 {done}건 · 건너뜀 {skipped}건")
    if orphan:
        # 조용히 세지 않는다 — 이게 "접수는 왔는데 노션에 없다"의 유일한 관측 지점이다.
        print(f"🔴 라벨 보류 {len(orphan)}건 — 사람이 봐야 한다:")
        for o in orphan[:20]:
            print(f"     {o}")
        alerts.warn(f"[bplan-sync] 라벨 보류 {len(orphan)}건 — 노션에 행이 없는 접수메일. "
                    f"첫 건: {orphan[0]}")


def _stages(limit: int, dry: bool, assignee: str, *, label: str,
            relabel_only: bool = False) -> list[Stage]:
    upsert = [Stage("fetch", s_fetch),
              Stage("upsert", lambda sk, ji: s_upsert(sk, ji, limit=limit, dry=dry,
                                                      assignee=assignee))]
    # 라벨은 **행 생성 뒤**다. 순서가 뒤집히면 노션에 없는 메일을 인박스에서 빼게 된다.
    return ([] if relabel_only else upsert) + [
        Stage("label", lambda sk, ji: s_label(sk, ji, dry=dry, label=label))]


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
    ap.add_argument("--label", default=bplan_label(),
                    help="접수메일을 옮길 Gmail 라벨 (drop 이 이 라벨 안에서 원본을 찾는다)")
    ap.add_argument("--relabel-only", action="store_true",
                    help="행 생성 없이 라벨 복구만 — 지난 실패분·소급 복구용")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()

    if args.synthetic:
        assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"
        return _synthetic()

    # 🔴 [2026-08-14] dry 와 apply 가 **같은 job_id 를 쓰면 안 된다.** 실제로 이렇게 났다:
    #      --relabel-only(dry)  → 잡이 completed 로 끝남
    #      --relabel-only --apply → 같은 id → run_pipeline 이 즉시 반환 → 42건 그대로
    #    그리고 `→ completed` 를 찍었다. **아무것도 안 한 것이 성공과 구분되지 않았다.**
    #    모드를 id 에 넣으면 dry 가 apply 의 자리를 차지할 수 없다. 계획을 세는 것과
    #    실행하는 것은 다른 잡이다 — id 도 달라야 한다.
    mode = "" if args.apply else "-dry"
    scope = "-relabel" if args.relabel_only else ""
    jid = f"bplan-sync-{date.today().isoformat()}{scope}{mode}"
    for n in range(1, 4):
        cand = jid if n == 1 else f"{jid}-r{n}"
        if job.rebuild(SKILL, cand).state in ("failed", "completed"):
            continue        # 실패분만이 아니라 **끝난 잡도 비켜간다** — 같은 id 는 1회다
        jid = cand
        break
    else:
        # 3회 다 차 있으면 조용히 마지막 것을 재사용하지 않는다 — 그게 위 사고의 형태다.
        raise SystemExit(f"🔴 오늘자 {jid} 계열이 이미 3개 다 끝났다. 정말 또 돌려야 하면 "
                         f"job_id 를 사람이 지정해라 — 무의식적 재실행을 막는다.")
    job.create(SKILL, {"kind": "bplan-sync", "period": date.today().isoformat()}, jid)
    # gmail-inbox 자원을 잡는다 — 라벨 단계가 인박스를 바꾼다. 트리아지와 직렬화되지 않으면
    # 같은 메일을 한쪽은 protect 로 읽고 다른 쪽이 INBOX 에서 빼는 순간이 생긴다.
    d = job.run_pipeline(SKILL, jid,
                         _stages(args.limit, not args.apply, args.assignee,
                                 label=args.label, relabel_only=args.relabel_only),
                         resources=["sheet-a1", "notion-bplan", "gmail-inbox"])
    print(f"[bplan-sync] {jid} → {d.state}")
    return 0 if d.state == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
