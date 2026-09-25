"""비플랜 접수 동기화 — 구글폼 시트 → 노션 Bplan DB → 접수 메일 라벨 이동.

    sync [--dry-run] [--limit N] [--since ISO]   새 접수를 노션 행으로 만들고, 접수 메일을 담당자 라벨로 옮긴다
    status                                       장부 요약
    selftest                                     망 불호출 자기검사 (헤더·타임스탬프·키·중복 규칙)

## 이 스킬이 하는 일과 안 하는 일

    하는 것    노션 **행 생성**(새 접수만) · 접수 메일에 **라벨 부여 + 인박스 제거**
    안 하는 것  판정(점수) · `To do` 이동 · 드랍 초안 · **발송** · 시트 쓰기 · 접수 메일 본문 읽기

라벨 이동은 삭제가 아니라 아카이빙이라 되돌릴 수 있다. 판정은 별도 스킬(Phase 2)의 몫이고,
드랍 초안도 별도다 — 이 스킬은 "접수가 왔다"를 노션에 옮기는 데서 끝난다.

## 🔴 진행 상태는 **바닥선 + 접수키** 두 축이다 (옛 시스템에서 실측으로 배운 것)

"노션에 없으면 신규다"는 틀렸다 — 시트는 6년치 5,600행이고 노션은 최근 몇 달만 담는다.
그대로 돌리면 5,000건을 노션에 쏟아붓는다(2026-08-13 dry-run 실측). 그래서:

    바닥선(floor)   이 시각 **이후** 접수만 본다. 단조 증가. 과거를 되살리지 않는다
                   = max(장부의 마지막 착지 시각, 노션 접수키의 최대 시각)
    접수키          `이메일|접수시각`. 그 창 안에서 "이미 넣었나". 시트 행 위치와 무관하다

옛 sync 의 멱등 키는 시트 **행 서수**였고, 행이 하나 밀리면 같은 서수가 다른 회사를 가리켰다.
그래서 같은 사람이 드랍 메일을 6통 받았다. 접수키는 그 사고를 구조로 막는다.

## 🔴 접수키가 없는 옛 행 — 2026-08-30 에 옛 코드로 68행이 들어갔다

그 68행은 접수키가 없다. 키만 보면 전부 "없음"이라 다시 만들게 된다 — 그날 이미 중복 2벌이
들어간 DB 에 3벌째를 얹는 꼴이다. 그래서 키 없는 행은 **이메일**로 대조하되,
**행 생성 시각보다 뒤에 온 접수는 새것**으로 본다 — 행이 접수보다 먼저 생길 수는 없으니
그 뒤의 재접수는 그 행이 대표할 수 없다.

🔴 기업명은 대조에 안 쓴다. 심사역이 노션 제목을 고쳐 쓴다("주식회사 카즈아" → "주식회사 카즈아
(MAKEMORY)"). 첫 dry-run 에서 (이메일, 기업명)으로 대조하니 14쌍이 그 이유로 어긋나 옛 접수 10건을
다시 만들려 했다. 이메일은 사람이 안 고친다.

## 지키는 규율

    인증부터 본다           시트·지메일·노션 셋 다. 나중에 401 로 알면 0건과 구별이 안 된다
    미상은 0건이 아니다      시트를 못 읽으면 "신규 0건"이 아니라 미상이고 종료코드가 0이 아니다
    착지 먼저, 장부 나중     순서가 반대면 실패한 것이 "했음"으로 남아 영영 안 잡힌다
    쓴 것은 되읽는다        통로가 한다. 200 은 저장을 뜻하지 않는다
    노션에 없는 메일은 안 건드린다  라벨만 붙여 인박스에서 빼면 아무도 추적하지 않는 접수가 조용히 사라진다
    보고는 장부에서         "56건 만들었다"는 장부 줄 수다
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

from kvlib import Fetched, constraints, judgments, ledger, lock, paths, report, secrets, store
from kvlib.gate import Gate
from kv_transport import claude, drive, gmail, notion, sheets, slack

from . import judge

TASK = "bplan"
DB_KEY = "bplan"

LANDED = ledger.LANDED          # key = 접수키
LABELED = "labeled"             # key = 메일번호

# [사용자 결정] 접수 담당자. 노션 `담당자` select 옵션이어야 한다 — 통로가 라이브 스키마로 대조하고,
# 옵션에 없으면 만들기 전에 죽는다(노션은 모르는 이름을 주면 **새 옵션을 조용히 만든다**).
ASSIGNEE = "에반 Evan"
# sync 가 붙이고, 드랍 초안이 **이 라벨 안에서** 원본 메일을 찾는다. 둘이 어긋나면 드랍이
# 예외 없이 "0건" 을 찾는다 — 실패가 정상처럼 보인다(옛 시스템 2026-08-14 실측).
GMAIL_LABEL = "3. Evan"
TODO_PROP = "to do"
NEW_TODO = "Newly Added"

# 🔴 시트 헤더 → 노션 칸. **접두사로 맞춘다.** 옛 코드는 질문 문장 전체를 키로 써서 폼 문구를
#    한 글자만 고쳐도 그 칸이 조용히 빈 값이 됐다. 번호·머리말은 잘 안 바뀐다.
#    하나라도 못 찾으면 멈춘다 — 빈 칸이 노션에 들어가면 판정이 빈 칸을 근거로 Red 를 낸다.
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
KEY_PROP = "접수키"
TITLE_PROP = "기업명"
CREATED_PROP = "DB 업데이트 시간"

# 구글폼 한국어 로케일: '2026. 8. 13 오후 1:02:03'
_TS_RE = re.compile(r"\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\s*(오전|오후)?\s*(\d{1,2}):(\d{2}):(\d{2})")
_KST = timezone(timedelta(hours=9))

# 접수 알림 제목. 답장(`Re:`)은 잡지 않는다 — 창업자의 후속 메일은 사람이 봐야 한다.
_SUBJ_RE = re.compile(r"^\[카카오벤처스\]\s*(.*?)\s*로부터\s*접수된\s*사업계획서\s*$")
_INTAKE_QUERY = 'subject:"로부터 접수된 사업계획서" in:inbox -label:"%s"'


class Degraded(RuntimeError):
    """돌긴 돌았는데 온전하지 않다. 크론은 종료코드만 보므로 0 과 달라야 한다."""


# ---------------------------------------------------------------- 순수 함수 (selftest 대상)

def parse_ts(s: str) -> datetime | None:
    m = _TS_RE.match(s or "")
    if not m:
        return None
    y, mo, d, ap, h, mi, se = m.groups()
    h = int(h)
    if ap == "오후" and h != 12:
        h += 12
    if ap == "오전" and h == 12:
        h = 0
    try:
        return datetime(int(y), int(mo), int(d), h, int(mi), int(se))
    except ValueError:
        return None


def submission_key(email: str, ts: datetime) -> str:
    """접수의 불변 식별자. 시트 위치와 무관하다. 노션에 심어둔 옛 키와 같은 형식이다."""
    return f"{(email or '').strip().lower()}|{ts.isoformat(timespec='seconds')}"


def norm_company(s: str) -> str:
    """기업명 대조용 정규화. 메일 제목과 노션 제목은 표기가 흔들린다 — 대조에만 쓰고 저장하지 않는다."""
    # 🔴 긴 패턴이 먼저다. 문자 클래스가 앞에 오면 "(주)" 의 괄호만 먼저 지워져 "주" 가 남는다(selftest ⑤가 잡았다).
    return re.sub(r"주식회사|\(주\)|㈜|[\s()（）,.\-_/·]", "", s or "").lower()


def resolve_columns(headers: list[str]) -> dict[str, int]:
    """헤더 접두사 → 열 번호. **하나라도 못 찾으면 멈춘다.**"""
    idx: dict[str, int] = {}
    missing: list[str] = []
    for prop, prefix in list(_COLS.items()) + [("_ts", _TS_COL)]:
        hit = [i for i, h in enumerate(headers) if (h or "").strip().startswith(prefix)]
        if len(hit) == 1:
            idx[prop] = hit[0]
        else:
            missing.append(f"{prop}←'{prefix}' ({len(hit)}개 매칭)")
    if missing:
        raise RuntimeError("🔴 시트 헤더를 못 찾았다 — 폼 문구가 바뀌었을 수 있다:\n   "
                           + "\n   ".join(missing)
                           + f"\n   실제 헤더: {[h[:30] for h in headers]}")
    return idx


def company_from_subject(subject: str) -> str:
    """접수 알림 제목에서 기업명. 답장·못 읽는 제목은 빈 문자열 — 추측하지 않는다."""
    m = _SUBJ_RE.match(subject or "")
    return m.group(1).strip() if m else ""


def utc_to_kst_iso(ts: str) -> str:
    """노션 created_time(UTC, 'Z') → 시트와 같은 KST 초 단위 ISO. 두 축을 같은 눈금으로 비교한다."""
    try:
        d = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return ""
    return d.astimezone(_KST).replace(tzinfo=None).isoformat(timespec="seconds")


def is_legacy_dup(sub: dict, legacy: dict[str, str]) -> bool:
    """접수키 없는 옛 행과 같은 접수인가.

    같은 이메일의 키 없는 행이 있고 **그 행이 접수보다 뒤에 생겼으면** 같은 접수다.
    행이 접수보다 먼저 생길 수는 없으므로, 행 생성 뒤에 온 재접수는 새것이다.
    기업명은 안 본다 — 심사역이 노션 제목을 고쳐 쓴다.
    """
    created = legacy.get(sub["email"])
    return bool(created) and sub["ts"] <= created


def pick_new(subs: list[dict], keys: set[str], legacy: dict, floor: str) -> tuple[list[dict], Counter]:
    """바닥선 이후 · 키 없음 · 옛 행 중복 아님 → 신규. 걸러진 이유를 센다."""
    why = Counter()
    out = []
    for s in subs:
        if s["ts"] <= floor:
            why["바닥선 이전"] += 1
        elif s["key"] in keys:
            why["접수키 있음"] += 1
        elif is_legacy_dup(s, legacy):
            why["옛 행(키 없음)과 같은 접수"] += 1
        else:
            out.append(s)
    return out, why


# ---------------------------------------------------------------- 읽기

def _read_sheet() -> tuple[list[dict], int]:
    f = sheets.values(secrets.get("SPREADSHEET_URL"), secrets.get("WORKSHEET_NAME"))
    if not f.is_measured:
        raise Degraded(f"시트를 못 읽었다: {f.reason}  ← 0행이 아니라 미상이다")
    rows = f.require()
    if not rows:
        raise Degraded("시트가 비었다 — 연결·권한 확인")
    idx = resolve_columns(rows[0])
    width = len(rows[0])
    subs: list[dict] = []
    bad_ts = 0
    for r in rows[1:]:
        if not any(r):
            continue
        r = r + [""] * (width - len(r))          # 뒤쪽 빈 칸은 생략돼 온다
        ts = parse_ts(r[idx["_ts"]])
        if ts is None:
            bad_ts += 1
            continue
        props = {p: r[i].strip() for p, i in idx.items() if not p.startswith("_")}
        email = props["이메일"].lower()
        subs.append({"key": submission_key(email, ts), "ts": ts.isoformat(timespec="seconds"),
                     "email": email, "props": props})
    return subs, bad_ts


def _read_notion() -> tuple[set[str], dict[str, str], set[str]]:
    """노션 전량(칸 4개만) → (접수키 집합, 키 없는 행의 이메일→최근 생성시각(KST), 기업명 집합)."""
    f = notion.query(notion.writable_id(DB_KEY), [KEY_PROP, TITLE_PROP, "이메일", CREATED_PROP])
    if not f.is_measured:
        raise Degraded(f"노션을 못 읽었다: {f.reason}")
    if f.partial:
        raise Degraded("노션 목록이 상한에서 잘렸다 — 대조가 불완전하다")
    keys: set[str] = set()
    legacy: dict[str, str] = {}
    titles: set[str] = set()
    for row in f.require():
        co = row.get(TITLE_PROP) or ""
        titles.add(norm_company(co))
        k = (row.get(KEY_PROP) or "").strip()
        if k:
            keys.add(k)
            continue
        email = (row.get("이메일") or "").strip().lower()
        created = utc_to_kst_iso(row.get(CREATED_PROP) or "")
        if email and created > legacy.get(email, ""):
            legacy[email] = created
    return keys, legacy, titles


def _floor(keys: set[str], since: str | None) -> str:
    """이 시각 **이후** 접수만. 단조 증가.

    근거가 둘 다 없을 때의 폴백 — 순서대로:
      1순위  `--since` 를 사람이 명시한다 (놓친 기간을 아는 사람이 정한다)
      최후   오늘 자정. 되살리기보다 안전한 쪽이다
      금지   "노션에 없으면 전부 신규" — 6년치를 붓는다
    """
    if since:
        return since
    from_ledger = max((r.get("submitted", "") for r in ledger.rows(TASK, ev=LANDED)), default="")
    from_notion = max((k.split("|", 1)[1] for k in keys if "|" in k), default="")
    floor = max(from_ledger, from_notion)
    if not floor:
        floor = datetime.now(_KST).strftime("%Y-%m-%dT00:00:00")
        print(f"🔴 바닥선 근거가 하나도 없다 — 오늘 자정({floor})으로 잡는다. "
              f"과거 접수를 넣으려면 --since 로 명시하라.")
    return floor


# ---------------------------------------------------------------- 실행

def _props_for(sub: dict) -> dict:
    p = sub["props"]
    # 🔴 칸 이름은 `to do` (소문자). 옛 코드의 "To do" 는 첫 실행에서 통로가 "칸이 없다"로 막았다 —
    #    쓰기 전에 잡혀서 한 행도 안 만들어졌다.
    out: dict = {KEY_PROP: sub["key"], TODO_PROP: NEW_TODO, "담당자": ASSIGNEE, TITLE_PROP: p.get("기업명", "")}
    for name, val in p.items():
        if name == "기업명":
            continue
        out[name] = val or None        # 빈 칸은 아예 안 싣는다 (빈 url 은 400)
    return out


def _reports_dir():
    return paths.data("bplan", "reports")


def sync(*, dry_run: bool, limit: int, since: str | None) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    print(f"▶ sync {stamp}" + ("  (dry-run — 노션·지메일 쓰기 0)" if dry_run else ""))
    notion.register_writable(DB_KEY, secrets.get("NOTION_DATABASE_ID"))

    # ── 인증부터. 셋 중 하나라도 죽었으면 읽기 전에 멈춘다 ─────────────
    for name, chk in (("시트", sheets.auth_check()), ("지메일", gmail.auth_check())):
        if not chk.is_measured:
            print(f"🔴 {name} 인증이 안 된다: {chk.reason}")
            return 1
    try:
        notion.allowed_options(notion.writable_id(DB_KEY), "담당자")   # 스키마 조회 = 노션 인증 확인
    except notion.NotionError as e:
        print(f"🔴 노션에 못 닿는다: {e}")
        return 1
    labels = gmail.labels()
    if not labels.is_measured:
        print(f"🔴 지메일 라벨 목록을 못 읽었다: {labels.reason}")
        return 1
    label_id = labels.require().get(GMAIL_LABEL)
    if not label_id:
        # 조용히 넘어가면 "라벨 0건"이 정상처럼 보인다. 라벨은 사람이 만든다.
        print(f"🔴 지메일 라벨 '{GMAIL_LABEL}' 이 없다 — 먼저 만들어라. 없으면 드랍이 원본을 영영 못 찾는다.")
        return 1

    # ── 읽기 ─────────────────────────────────────────────────────────
    subs, bad_ts = _read_sheet()
    keys, legacy, titles = _read_notion()
    floor = _floor(keys, since)
    todo, why = pick_new(subs, keys, legacy, floor)
    todo.sort(key=lambda s: s["ts"])
    print(f"  · 시트 접수 {len(subs)}건" + (f" (타임스탬프 못 읽음 {bad_ts}건 — 제외)" if bad_ts else ""))
    print(f"  · 노션 접수키 {len(keys)}행 · 키 없는 옛 행 {len(legacy)}명")
    print(f"  · 바닥선 {floor}" + (" (--since)" if since else ""))
    print(f"  · 신규 {len(todo)}건  걸러짐: " + ", ".join(f"{k} {v}" for k, v in why.most_common()))

    # ── 인박스의 접수 알림을 **한 번** 읽는다 — 계획의 "메일 유무" 와 라벨 단계가 같은 목록을 쓴다 ──
    # 대상은 지메일 상태로 정한다: "라벨 없고 아직 인박스에 있는 접수 알림". 그래서 지난 실행의
    # 실패분과 옛 행(8/30)의 메일이 같은 코드로 잡히고, 재실행이 무해하다.
    mails: list[tuple[str, str]] = []          # (메일번호, 기업명) — 접수 알림만. 답장은 뺀다
    replies = 0
    fails: list[str] = []
    ids = gmail.search_ids(_INTAKE_QUERY % GMAIL_LABEL)
    if not ids.is_measured:
        fails.append(f"접수 메일 조회: {ids.reason}")
    else:
        if ids.partial:
            print("  ⚠️ 접수 메일 목록이 상한에서 잘렸다 — 나머지는 다음 실행")
        for mid in ids.require():
            h = gmail.headers_of(mid, ["Subject"])
            if not h.is_measured:
                fails.append(f"헤더 {mid}: {h.reason}")
                continue
            co = company_from_subject(h.require()["Subject"])
            if co:
                mails.append((mid, co))
            else:
                replies += 1                    # Re: 등 — 창업자의 후속 메일. 사람이 본다
    mail_cos = Counter(norm_company(co) for _, co in mails)
    for s in todo:
        s["has_mail"] = norm_company(s["props"].get("기업명", "")) in mail_cos
    print(f"  · 인박스 접수 알림 {len(mails)}통" + (f" · 답장 {replies}통(안 건드린다)" if replies else "")
          + f" · 신규 중 메일 없는 접수 {sum(1 for s in todo if not s['has_mail'])}건")

    # ── 노션 행 생성 ──────────────────────────────────────────────────
    landed_before = ledger.keys(TASK, LANDED)
    tally = Counter()
    made: list[dict] = []
    rows = todo[:limit] if limit else todo
    if limit and len(todo) > limit:
        print(f"  · 상한 {limit}건 — 잔여 {len(todo) - limit}건은 다음 실행")
    for s in rows:
        co = s["props"].get("기업명", "")
        if s["key"] in landed_before:
            tally["장부에 이미 있음(스킵)"] += 1     # 장부엔 있는데 노션엔 없다 = 사람이 지웠다. 되살리지 않는다
            continue
        if dry_run:
            tally["생성(예정)"] += 1
            made.append(s)
            titles.add(norm_company(co))      # 계획대로면 이 행이 생긴다 — 라벨 단계도 그 전제로 센다
            continue
        try:
            res = notion.create(DB_KEY, _props_for(s))
        except notion.NotionError as e:
            # 스키마가 어긋났다(칸 이름·옵션). 한 행도 안 만들고 멈춘다 — 계속 돌면 76번 같은 오류다.
            print(f"🔴 노션 스키마와 안 맞는다: {e}")
            return 1
        if not res.is_measured:
            fails.append(f"create {s['key']} {co[:20]}: {res.reason}")
            continue
        # 🔴 접수시각은 `submitted` 다. `ts=` 로 넘기면 장부의 기록 시각(ts)을 **덮어쓴다** —
        #    첫 실행에서 실제로 그랬다(75줄의 ts 가 접수시각이 됐다. 09-16 복구).
        ledger.append(TASK, s["key"], LANDED, page_id=res.require(), company=co[:80], submitted=s["ts"])
        titles.add(norm_company(co))
        tally["생성"] += 1
        made.append(s)
        print(f"  ✅ {s['ts']}  {co[:30]}")

    # ── 접수 메일 라벨 이동 ────────────────────────────────────────────
    # 노션에 기업명이 있는 접수 알림만 옮긴다. 없는 메일은 건드리지 않는다 — 라벨만 붙여
    # 인박스에서 빼면 아무도 추적하지 않는 접수가 조용히 사라진다.
    labeled_before = ledger.keys(TASK, LABELED)
    orphans: list[str] = []
    for mid, co in mails:
        if norm_company(co) not in titles:
            orphans.append(f"{co} — 노션에 행이 없다 (접수 누락 의심)")
            continue
        if mid in labeled_before:
            tally["라벨 장부에 이미 있음"] += 1
            continue
        if dry_run:
            tally["라벨 이동(예정)"] += 1
            continue
        res = gmail.relabel(mid, add=[label_id], remove=["INBOX"])
        if not res.is_measured:
            fails.append(f"relabel {mid} {co[:20]}: {res.reason}")
            continue
        ledger.append(TASK, mid, LABELED, company=co[:80], label=GMAIL_LABEL)
        tally["라벨 이동"] += 1

    # ── 보고 ─────────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    for k, v in tally.most_common():
        print(f"  ✅ {k:22} {v}")
    if replies:
        print(f"  ⏭️  답장(Re:)이라 건드리지 않음  {replies}")
    for o in orphans:
        print(f"  🔴 라벨 보류: {o}")
    for f in fails:
        print(f"  🔴 {f}")
    print("=" * 70)

    d = _reports_dir()
    (d / f"{stamp}.md").write_text(
        _report(stamp, dry_run, floor, len(subs), why, made, tally, orphans, replies, fails),
        encoding="utf-8")
    print(f"  보고: {d / f'{stamp}.md'}")

    if fails:
        raise Degraded(f"실패 {len(fails)}건")
    if orphans:
        raise Degraded(f"노션에 행이 없는 접수 메일 {len(orphans)}건 — 인박스에 그대로 뒀다")
    return 0


def _report(stamp, dry_run, floor, n_sheet, why, made, tally, orphans, replies, fails) -> str:
    out = [f"# 비플랜 sync — {stamp}" + ("  (dry-run)" if dry_run else ""), "",
           ("**아무것도 쓰지 않았다** — 계획일 뿐이다." if dry_run else "실행했다. 아래는 장부와 같다."), "",
           f"- 시트 접수 {n_sheet}건 · 바닥선 `{floor}`",
           "- 걸러진 이유: " + ", ".join(f"{k} {v}" for k, v in why.most_common()), "",
           "## 결과", "", "| 항목 | 수 |", "|---|---|"]
    out += [f"| {k} | {v} |" for k, v in tally.most_common()]
    if replies:
        out.append(f"| 답장(Re:)이라 건드리지 않음 | {replies} |")
    out += ["", f"## 노션 행 {'생성 예정' if dry_run else '생성'} {len(made)}건", "",
            "| 접수시각 | 기업명 | 이메일 | 대표 | 접수 메일 |", "|---|---|---|---|---|"]
    for s in made:
        p = s["props"]
        out.append(f"| {s['ts'].replace('T', ' ')} | {p.get('기업명', '')} | {s['email']} | {p.get('대표명', '')} "
                   f"| {'인박스' if s.get('has_mail') else '🔴 없음'} |")
    if orphans:
        out += ["", "## 🔴 노션에 행이 없는 접수 메일 — 인박스에 그대로 뒀다", ""] + [f"- {o}" for o in orphans]
    if fails:
        out += ["", "## 🔴 실패", ""] + [f"- {f}" for f in fails]
    out += ["", "---", "", "🔴 판정·드랍 초안·발송은 이 스킬에 없다. 여기서 끝나는 일은 '접수가 왔다'를 노션에 옮기는 것뿐이다.", ""]
    return "\n".join(out)


# ---------------------------------------------------------------- 판정 (한 접수 한 번)

_JUDGE_PROPS = ["기업명", "대표명", "이메일", "접수키", "사업계획서", TODO_PROP, "점수", CREATED_PROP, *judge.FIELDS]
# 심사역의 `to do` 가 곧 정답. 갈래 둘 — drop / keep. 그 밖(Newly Added·Review Done·Hold)은 미결이라 None.
_TODO_LABEL = {"To Drop": "drop", "미팅요청": "keep", "Mail Fwd": "keep", "Q&A": "keep"}
DROP_SCORES = ("칼드랍", "Red")                     # 드랍 초안이 나가는 갈래. Orange·Green 은 카드, ? 는 사람
SLACK_KINDS = ("card", "ack", "remind1", "remind2", "remind3")   # 슬랙 글 — slack 단계에서 실제로 나가는 종류(허용 목록)
ACK = ("raising_hand", "man-raising-hand", "woman-raising-hand", "person_raising_hand", "hand")


def bucket(score: str | None) -> str:
    return "drop" if score in DROP_SCORES else "keep" if score in ("Orange", "Green") else "?"


def db_path():
    return paths.data("bplan", "db") / "bplan.sqlite"


def _gate(dry_run: bool = False) -> Gate:
    con = store.connect(db_path(), judgments.SCHEMA)
    judgments.ensure(con)
    return Gate(con, "bplan", lambda k: k in SLACK_KINDS, force="off" if dry_run else None)


def _row_key(row: dict) -> str:
    return (row.get(KEY_PROP) or "").strip() or row["_page_id"]          # 옛 68행은 접수키가 없다 — 그때만 page_id


def _llm(criteria: str, data: str) -> str:
    return claude.run_session(f"{criteria}\n\n{data}", system=judge.SYSTEM, tools="")


def judge_rows(con, rows: list[dict], all_rows: list[dict], *, llm=_llm, deck=drive.text, run: str = judgments.LIVE,
               criteria: str | None = None) -> dict:
    """행마다 덱을 읽고 모델을 한 번 부른다. 호출 자체가 실패하면(인증·차단선) **멈춘다** — 나머지도 같은 이유로 실패한다."""
    crit = criteria or judge.criteria_text()
    sha = judgments.criteria_sha(judge.SYSTEM, crit)
    allowed = notion.allowed_options(notion.writable_id(DB_KEY), "점수")
    by_email = Counter((r.get("이메일") or "").strip().lower() for r in all_rows)
    done = bad = 0
    for row in rows:
        key = _row_key(row)
        d = deck(row.get("사업계획서") or "")
        email = (row.get("이메일") or "").strip().lower()
        facts = [f"접수 {utc_to_kst_iso(row.get(CREATED_PROP) or '')[:10]}",
                 f"같은 이메일의 접수 {by_email.get(email, 1)}건(이번 포함)"]
        text = judge.build_input(row, d, allowed, facts)
        try:
            raw = llm(crit, text)
        except Exception as e:                                       # noqa: BLE001 — 통로의 SessionError. 여기서 멈춘다
            return {"judged": done, "invalid": bad, "stopped": f"{type(e).__name__}: {e}"}
        p = judge.parse(raw, allowed)
        v = [{"key": key, "verdict": p["verdict"], "reason": p["reason"], "raw": raw}] if p["ok"] else []
        judgments.save(con, run, sha, {key: text}, v,
                       extra={key: {"summary": p.get("summary"), "team": p.get("team"), "risk": p.get("risk")}} if p["ok"] else None)
        if not p["ok"]:
            con.execute("UPDATE judgments SET error=? WHERE key=? AND run=?", (p["error"], key, run))
        judgments.set_page(con, key, row["_page_id"])
        done, bad = done + p["ok"], bad + (1 - p["ok"])
    return {"judged": done, "invalid": bad, "stopped": None}


def _notion_rows() -> list[dict]:
    notion.register_writable(DB_KEY, secrets.get("NOTION_DATABASE_ID"))
    f = notion.query(notion.writable_id(DB_KEY), _JUDGE_PROPS)
    if not f.is_measured:
        raise Degraded(f"노션을 못 읽었다: {f.reason}")
    if f.partial:
        raise Degraded("노션 목록이 상한에서 잘렸다")
    return f.require()


def judge_new(con, rows: list[dict], *, past: int = 0, limit: int = -1) -> dict:
    """미판정 행. 기본은 `to do = Newly Added`(아직 아무도 안 본 접수). `past=N` 은 **결정 난** 옛 행 N건 — 기준 보정용, 행동은 안 한다."""
    have = {r[0] for r in con.execute("SELECT key FROM judgments WHERE run=?", (judgments.LIVE,))}
    if past:
        pool = [r for r in rows if r.get(TODO_PROP) in _TODO_LABEL and _row_key(r) not in have]
        pool.sort(key=lambda r: r.get(CREATED_PROP) or "", reverse=True)
        pool = pool[:past]
    else:
        pool = [r for r in rows if r.get(TODO_PROP) == NEW_TODO and _row_key(r) not in have]
        pool.sort(key=lambda r: r.get(CREATED_PROP) or "")
        if limit > 0:
            pool = pool[:limit]
    return judge_rows(con, pool, rows)


def label(con, rows: list[dict]) -> int:
    return judgments.label(con, rows, TODO_PROP, _TODO_LABEL.get, same=lambda live, human: bucket(live) == human)


# ---------------------------------------------------------------- 행동 (문 뒤) — score · card · draft

slack.register("test",  "C000E8E4F75")     # review_test
slack.register("bplan", "C0000AEB349")     # #Squad-비플랜 — 봇 초대 필요
_HANGUL = re.compile(r"[가-힣]")
_KO_DOMAINS = (".kr", "naver.com", "daum.net", "hanmail.net", "kakao.com", "nate.com")


def drop_lang(row: dict) -> str:
    """드랍 문안 언어. 🔴 한글 이름·기업명·한국 도메인이면 ko — 한국 창업자가 영문 거절을 받는 쪽으로만 틀리지 않게(옛 실측 2/561)."""
    text = " ".join(str(row.get(k) or "") for k in ("기업명", "대표명", *judge.FIELDS))
    ko = len(_HANGUL.findall(text)); la = len(re.findall(r"[A-Za-z]", text))
    if _HANGUL.search(str(row.get("대표명") or "") + str(row.get("기업명") or "")):
        return "ko"
    if any(str(row.get("이메일") or "").lower().endswith(d) for d in _KO_DOMAINS):
        return "ko"
    return "ko" if (ko + la) < 20 or ko / (ko + la) >= 0.15 else "en"


def drop_body(row: dict) -> str:
    tpl = (paths.repo_root() / "skills" / "bplan" / "templates" / f"drop_{drop_lang(row)}.txt").read_text(encoding="utf-8")
    return tpl.format(ceo_name=(row.get("대표명") or "").strip() or ("대표" if drop_lang(row) == "ko" else "there"))


def _intake_mail(company: str) -> str | None:
    """이 회사의 접수 알림 메일 번호 — 장부(labeled)에서, 없으면 라벨 안을 제목으로 찾는다. 드랍 초안은 그 스레드에 답장이다."""
    want = norm_company(company)
    for r in ledger.rows(TASK, ev=LABELED):
        if norm_company(r.get("company", "")) == want:
            return r["key"]
    f = gmail.search_ids(f'subject:"{company} 로부터 접수된 사업계획서" label:"{GMAIL_LABEL}"', limit=5)
    return (f.require() or [None])[0] if f.is_measured else None


def _fields(reason: str, extra: str | None) -> dict:
    """카드 문안(summary·team·risk)은 extra 열에. 09-26 오전 한때 reason 안에 JSON 으로 넣던 행도 읽는다."""
    if extra:
        return {"reason": reason, **json.loads(extra)}
    return json.loads(reason) if str(reason).startswith("{") else {"reason": reason}


def _card_text(row: dict, card: dict, verdict: str) -> str:
    pid = row["_page_id"].replace("-", "")
    ask = "애매합니다 — 사람이 봐 주세요" if verdict == "?" else "볼 만합니다"
    return (f"*{row.get('기업명')}*  `{verdict}` · {ask}\n{card.get('summary', '')}\n"
            f"*팀* {card.get('team', '') or '미상'}\n*되는 이유* {card.get('reason', '')}\n*리스크* {card.get('risk', '')}\n"
            f"<https://www.notion.so/{pid}|노션> · <{row.get('사업계획서') or ''}|덱>\n\n_⭕ 미팅요청 · ❌ 드랍 · 🙋 내가 볼게_")


def act_all(g: Gate, rows: list[dict]) -> dict:
    """판정은 있는데 행동이 없는 접수 — 아직 `Newly Added` 인 것만. 심사역이 이미 움직인 행은 건드리지 않는다.
    전부 `Gate.out` 뒤: score(노션 점수·포워드 이유) · card(볼만·애매 → 슬랙 스레드) · draft(칼드랍·Red → 드랍 초안)."""
    by_page = {r["_page_id"]: r for r in rows if r.get(TODO_PROP) == NEW_TODO}
    live = g.con.execute("SELECT key, verdict, reason, page_id, extra FROM judgments WHERE run=? AND ok=1 AND page_id IS NOT NULL", (judgments.LIVE,)).fetchall()
    n = Counter()
    ch, parent = _cfg("card_channel"), {}

    def head_ts() -> str:
        if "ts" not in parent:
            parent["ts"] = slack.post(ch, f"*오늘의 비플랜* — 볼 만한 접수와 애마한 접수입니다. 스레드에서 ⭕ ❌ 🙋 로 알려 주세요").require()
        return parent["ts"]

    for key, verdict, reason, pid, extra in live:
        row = by_page.get(pid)
        if not row:
            continue
        card = _fields(reason, extra)
        if (g.row(key, "score") or {}).get("status") != "done":
            n["score"] += 1
            g.out(key, "score", {"verdict": verdict}, lambda pid=pid, v=verdict, c=card: notion.update(
                pid, DB_KEY, {"점수": v, "포워드 이유": notion.clip16(" / ".join(x for x in (c.get("reason"), c.get("risk")) if x))}).require())
        if verdict in DROP_SCORES:
            _draft(g, key, row, n)
        else:
            if (g.row(key, "card") or {}).get("status") == "done":
                continue
            n["card"] += 1
            g.out(key, "card", {"verdict": verdict, "company": row.get("기업명"), "ch": ch, "page_id": pid},
                  lambda row=row, card=card, v=verdict: {"parent": head_ts(), "ts": slack.post(ch, _card_text(row, card, v), thread_ts=head_ts()).require()})
    return dict(n)


def _draft(g: Gate, key: str, row: dict, n: Counter) -> None:
    """칼드랍·Red(또는 심사역 ❌) → 접수 알림 스레드에 드랍 초안. 같은 이메일엔 drop_after_days 에 하나."""
    if (g.row(key, "draft") or {}).get("status") in ("done", "skipped"):
        return
    email = (row.get("이메일") or "").strip().lower()
    since = (datetime.now(timezone.utc) - timedelta(days=_cfg("drop_after_days"))).strftime("%Y-%m-%dT%H:%M:%SZ")
    if g.con.execute("SELECT 1 FROM actions WHERE kind='draft' AND status='done' AND created>=? AND json_extract(detail,'$.email')=?",
                     (since, email)).fetchone():
        g.put(key, "draft", "skipped", {"email": email, "why": f"같은 사람에게 {_cfg('drop_after_days')}일 안에 이미 초안"})
        n["skipped"] += 1
        return
    n["draft"] += 1

    def do():
        mid = _intake_mail(row.get("기업명", ""))
        if not mid:
            raise RuntimeError("접수 알림 메일을 못 찾았다 — 초안을 붙일 스레드가 없다")
        return gmail.draft_reply(gmail.message(mid).require(), drop_body(row), email).require()
    g.out(key, "draft", {"email": email, "lang": drop_lang(row), "company": row.get("기업명")}, do)


def _thread(g: Gate, key: str, kind: str, text: str) -> str:
    """올라간 카드의 스레드에 한 줄. 카드가 실제로 안 올라갔으면 이것도 dry 다."""
    card = g.row(key, "card") or {}

    def do():
        if card.get("status") != "done":
            raise RuntimeError("카드가 아직 안 올라갔다")
        return slack.post(card["ch"], text, thread_ts=card["result"]["parent"]).require()
    return g.out(key, kind, {"text": text}, do)


def followup(g: Gate, rows_fn=None) -> dict:
    """올라간 카드의 스레드 반응을 읽어 잇는다 — ⭕ 미팅요청 · ❌ To Drop + 드랍 초안 · 🙋 맡음. 조용하면 리마인드. 만기 없음(접수는 답을 받아야 한다)."""
    yes, no = set(constraints.get("slack.reaction_yes")), set(constraints.get("slack.reaction_no"))
    n = Counter()
    cards = [(k, json.loads(d), c) for k, d, c in g.con.execute(
        "SELECT a.key, a.detail, a.created FROM actions a WHERE a.kind='card' AND a.status='done'"
        " AND NOT EXISTS(SELECT 1 FROM actions t WHERE t.key=a.key AND t.kind IN ('todo','ack') AND t.status='done')")]
    if not cards:
        return dict(n)
    rows = None
    by_parent: dict = {}
    for k, d, created in cards:
        by_parent.setdefault((d["ch"], d["result"]["parent"]), []).append((k, d, created))
    for (ch, parent), items in by_parent.items():
        got = slack.replies(ch, parent)
        if not got.is_measured:
            n["unread"] += len(items)
            continue
        react = {m.get("ts"): m.get("reactions") or [] for m in got.require()}
        for k, d, created in items:
            names = {x.get("name") for x in react.get(d["result"]["ts"], [])}
            users = [u for x in react.get(d["result"]["ts"], []) for u in (x.get("users") or [])]
            decision = "no" if names & no else "yes" if names & yes else "ack" if {x.split("::")[0] for x in names if x} & set(ACK) else None
            if decision in ("yes", "no"):
                if rows is None:
                    rows = (rows_fn or _notion_rows)()
                row = next((r for r in rows if r["_page_id"] == d["page_id"]), None)
                if not row:
                    n["missing_row"] += 1
                    continue
                todo = "미팅요청" if decision == "yes" else "To Drop"
                g.out(k, "todo", {"to": todo, "by": users[:1]}, lambda pid=d["page_id"], todo=todo: notion.update(pid, DB_KEY, {TODO_PROP: todo}).require())
                n[decision] += 1
                if decision == "no":
                    _draft(g, k, row, n)
            elif decision == "ack":
                who = next((p_["names"][0] for p_ in _people() for u in users if p_.get("slack_id") == u), None)
                _thread(g, k, "ack", f"🙋 {who or '누군가'} 님이 맡았습니다. 노션 `담당심사역` 칸에 이름을 남겨 주세요.")
                n["ack"] += 1
            else:
                n["reminded"] += _remind(g, k, created)
    return dict(n)


def _people() -> list[dict]:
    import yaml
    return yaml.safe_load((paths.repo_root() / "people.yaml").read_text(encoding="utf-8")) or []


def _remind(g: Gate, key: str, card_created: str) -> int:
    done = g.con.execute("SELECT count(*) FROM actions WHERE key=? AND kind LIKE 'remind%' AND status='done'", (key,)).fetchone()[0]
    if done >= _cfg("remind_max"):
        return 0
    last = g.con.execute("SELECT max(created) FROM actions WHERE key=? AND status='done' AND (kind='card' OR kind LIKE 'remind%')", (key,)).fetchone()[0] or card_created
    if (datetime.now(timezone.utc) - datetime.fromisoformat(last.replace("Z", "+00:00"))).days < _cfg("remind_days"):
        return 0
    return int(_thread(g, key, f"remind{done + 1}", "⏰ 아직 답이 없습니다 — ⭕ 미팅요청 · ❌ 드랍 · 🙋 내가 볼게") == "done")


def _cfg(k: str):
    return constraints.get(f"bplan.{k}")


def _send(ch: str, text: str) -> str:
    return slack.post(ch, text).require()


# ---------------------------------------------------------------- 하루 보고 · 진입점

def daily_report(g: Gate, acted: dict) -> None:
    """하루 한 번. 첫 줄 = **드랍이라 했는데 심사역이 살린 건** — 이 필터에서 가장 아픈 실수라 맨 위에 둔다."""
    out = paths.data("bplan", "reports") / f"{ledger.today()}.md"
    if out.exists():
        return
    today = ledger.today()
    gap = g.con.execute("SELECT l.key, l.verdict, h.verdict FROM judgments h JOIN judgments l ON l.key=h.key AND l.run=? WHERE h.run=?",
                        (judgments.LIVE, judgments.HUMAN)).fetchall()
    saved = [k for k, live, human in gap if bucket(live) == "drop" and human == "keep"]
    killed = [k for k, live, human in gap if bucket(live) == "keep" and human == "drop"]
    dist = dict(g.con.execute("SELECT verdict, count(*) FROM judgments WHERE run=? AND ok=1 AND created>=? GROUP BY verdict", (judgments.LIVE, today)))
    acts = dict(g.con.execute("SELECT kind||':'||status, count(*) FROM actions WHERE created>=? GROUP BY 1", (today,)))
    waiting = g.con.execute("SELECT count(*) FROM actions a WHERE kind='card' AND status='done'"
                            " AND NOT EXISTS(SELECT 1 FROM actions t WHERE t.key=a.key AND t.kind IN ('todo','ack') AND t.status='done')").fetchone()[0]
    # 승격 — gmail.promote 원칙 그대로: 창 안에 정답 붙은 드랍 판정이 min_samples 이상이고, 그중 심사역이 살린 게 없어야(rate) 켜도 된다
    pr = constraints.get("gmail.promote")
    floor = ledger.now(timedelta(days=pr["window_days"]))
    drops = g.con.execute("SELECT l.key, h.verdict FROM judgments l JOIN judgments h ON h.key=l.key AND h.run=? WHERE l.run=? AND l.ok=1 AND l.created>=?"
                          " AND l.verdict IN (?,?)", (judgments.HUMAN, judgments.LIVE, floor, *DROP_SCORES)).fetchall()
    # 정답이 붙은 드랍 판정: human 행이 있으면 갈린 것(=살림). 갈리지 않은 드랍은 to do=To Drop 인데 human 행이 없다 — 그건 노션에서 센다
    n_lab, agree = len(drops), sum(1 for _, h in drops if h == "drop")
    head = (f"🔴 드랍이라 했는데 심사역이 살린 건 {len(saved)} · 볼만이라 넘겼는데 드랍 {len(killed)} · 카드 답 대기 {waiting}"
            f" | 오늘 판정 {sum(dist.values())}" + (" (" + " · ".join(f"{k} {v}" for k, v in sorted(dist.items(), key=lambda x: -x[1])) + ")" if dist else ""))
    md = [f"# 비플랜 하루 보고 — {today}", "", head, "",
          "## 🚩 드랍이라 했는데 심사역이 살린 접수 (기준이 넓다 — 가장 먼저 볼 것)", ""]
    md += [f"- `{k}`" for k in saved] or ["없음"]
    md += ["", "## 볼만이라 넘겼는데 심사역이 드랍한 접수 (기준이 좁다)", ""] + ([f"- `{k}`" for k in killed] or ["없음"])
    md += ["", "## 오늘 행동 (문 = " + str(g.level()) + ")", "", " · ".join(f"{k} {v}" for k, v in sorted(acts.items())) or "없음",
           "", f"이번 실행: {acted or '없음'}", "",
           "## 발송 승격 (gmail.promote 원칙)", "",
           f"- 드랍 판정 중 심사역 결정이 붙은 표본 {n_lab}/{pr['min_samples']} · 살린 건 {n_lab - agree} → **"
           + ("켜져 있다" if _cfg("send_branches") else "켜도 된다" if n_lab >= pr["min_samples"] and agree / n_lab >= pr["rate"] else "아직") + "**",
           "", "## 고치는 법", "", "```bash", "cd ~/kv",
           "# 정답은 노션 to do 를 움직이면 된다 — 다음 run 이 human 으로 가져간다",
           "# 기준: skills/bplan/criteria.md → 저장된 접수로 다시 판정 → 무엇이 달라졌나",
           "./.venv/bin/python -m skills.bplan.run replay [--limit 50]",
           "./.venv/bin/python -m skills.bplan.run diff --b r-<sha>", "```", ""]
    out.write_text("\n".join(md), encoding="utf-8")
    summary = f"*비플랜 하루* — {head}\n전체: `{out}`"
    print(f"· 보고서 {out} | 슬랙 {report.post('bplan', summary, _send)}")


def run_all(*, dry_run: bool = False) -> int:
    """매일 09:20 — sync → 판정 → 행동(문 뒤) → 보고서 → 백업. sync 가 온전하지 않아도 판정은 간다(서로 독립)."""
    rc = 0
    try:
        rc = sync(dry_run=dry_run, limit=0, since=None)
    except Degraded as e:
        print(f"🔴 sync 온전하지 않다: {e} — 판정은 계속한다")
        rc = 2
    g = _gate(dry_run)
    rows = _notion_rows()
    n_lab = label(g.con, rows)
    j = judge_new(g.con, rows)
    print(f"· 판정 {j['judged']} · 형식 실패 {j['invalid']}" + (f" · 🔴 멈춤: {j['stopped']}" if j["stopped"] else "") + f" · 정답 갈림 {n_lab}")
    acted = act_all(g, rows)
    print(f"· 행동: {acted or '없음'} | 문 {g.level()}")
    daily_report(g, acted)
    bdir = paths.data("bplan", "db", "backup")
    if not (bdir / f"bplan-{datetime.now(timezone.utc):%Y-%m-%d}.sqlite").exists():
        store.backup(g.con, bdir, "bplan", _cfg("backup_keep"))
    return 2 if j["stopped"] else rc


def status() -> int:
    s = ledger.summary(TASK)
    print(f"장부 {s['ledger_lines']}줄 · 마지막 {s['last_ts'] or '없음'} · 오늘 {s['counts'] or '0'}")
    keys = ledger.keys(TASK, LANDED)
    print(f"착지 누적 {len(keys)}건 · 라벨 이동 누적 {len(ledger.keys(TASK, LABELED))}건")
    reports = sorted(_reports_dir().glob("*.md"))
    for p in reports[-5:]:
        print(f"  {p.name}")
    return 0


def selftest() -> int:
    """망 불호출 자기검사. 순수 함수만 본다."""
    ok, fail = [], []

    def chk(name, cond, detail=""):
        (ok if cond else fail).append(name)
        print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))

    hdr = ["타임스탬프", "이메일 주소", "대표님의 성함을 알려주세요.", "대표님의 연락처를 알려주세요.",
           "1. 팀의 Service/Product를", "2. 문제 정의 : 누구의", "3. 솔루션 : 그", "4. 핵심 비즈니스 모델(e.g.",
           "5. 핵심 경쟁사들은", "6. 대표님과 핵심 구성원(c-level)", "7. 우리 팀이 이 서비스 & 기술을",
           "(선택)추가로 공유해", "팀의 사업계획서를 첨부해주세요.", "기업명을 알려주세요."]
    idx = resolve_columns(hdr)
    chk("① 헤더 14개를 전부 해석한다", len(idx) == len(_COLS) + 1, f"{len(idx)}개")
    try:
        resolve_columns([h for h in hdr if not h.startswith("2. 문제")])
        chk("② 헤더가 사라지면 fail-loud", False, "조용히 통과함")
    except RuntimeError as e:
        chk("② 헤더가 사라지면 fail-loud", "문제정의" in str(e))
    ts = parse_ts("2026. 8. 13 오후 1:02:03")
    chk("③ 한국어 로케일 타임스탬프", ts is not None and ts.hour == 13, str(ts))
    chk("③-2 오전 12시는 0시", parse_ts("2026. 1. 1 오전 12:00:01").hour == 0)
    chk("③-3 못 읽는 값은 None (추측 안 함)", parse_ts("8/13/2026 13:02") is None)
    chk("④ 접수키는 이메일·시각만으로 결정된다", submission_key(" A@B.COM ", ts) == f"a@b.com|{ts.isoformat(timespec='seconds')}")
    chk("⑤ 기업명 정규화", norm_company("(주)차엔차") == norm_company("차엔차") == norm_company("주식회사 차엔차 "))
    chk("⑥ 제목에서 기업명", company_from_subject("[카카오벤처스]주식회사 플레드 로부터 접수된 사업계획서") == "주식회사 플레드")
    chk("⑥-2 답장은 안 잡는다", company_from_subject("Re: [카카오벤처스]예시플랫폼 로부터 접수된 사업계획서") == "")
    chk("⑦ UTC→KST", utc_to_kst_iso("2026-08-30T04:21:00.000Z") == "2026-08-30T13:21:00")
    pairs = {"a@b.com": "2026-08-30T13:34:00"}
    old = {"key": "a@b.com|2026-08-29T10:00:00", "ts": "2026-08-29T10:00:00", "email": "a@b.com", "props": {"기업명": "사랑의빛"}}
    new = {"key": "a@b.com|2026-09-01T15:13:00", "ts": "2026-09-01T15:13:00", "email": "a@b.com", "props": {"기업명": "사랑의빛"}}
    chk("⑧ 옛 행보다 먼저 온 접수는 그 행과 같은 접수", is_legacy_dup(old, pairs))
    chk("⑧-1 기업명이 달라도(심사역이 고침) 이메일로 잡는다", is_legacy_dup({**old, "props": {"기업명": "사랑의빛 (EACP)"}}, pairs))
    chk("⑧-2 옛 행보다 뒤에 온 재접수는 새것", not is_legacy_dup(new, pairs))
    picked, why = pick_new([old, new, {**new, "key": "k", "ts": "2026-08-01T00:00:00"}], {"k"}, pairs, "2026-08-13T11:12:43")
    chk("⑨ 바닥선·키·옛 행 셋을 순서대로 거른다", [s["key"] for s in picked] == [new["key"]] and why["바닥선 이전"] == 1
        and why["옛 행(키 없음)과 같은 접수"] == 1, dict(why))
    p = _props_for(new)
    chk("⑩ 빈 칸은 안 싣는다 · 드랍 칸은 안 건드린다", p.get("연락처") is None and "드랍메일" not in p and p[TODO_PROP] == NEW_TODO)
    print(f"\n{'🎉 selftest PASS' if not fail else '🔴 실패: ' + str(fail)}")
    return 0 if not fail else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="bplan")
    sub = p.add_subparsers(dest="cmd", required=True)
    r_ = sub.add_parser("run", help="매일: sync → 판정 → 행동(문 뒤) → 보고서 → 백업"); r_.add_argument("--dry-run", action="store_true")
    sub.add_parser("health", help="오늘 보고서가 있나 — 없으면 슬랙 한 줄")
    s = sub.add_parser("sync", help="시트 → 노션 행 → 접수 메일 라벨")
    s.add_argument("--dry-run", action="store_true", help="계획만. 노션·지메일 쓰기 0")
    s.add_argument("--limit", type=int, default=0, help="이번 실행 최대 생성 건수 (0=전부)")
    s.add_argument("--since", default=None, help="바닥선을 명시한다 (예: 2026-08-13T00:00:00). 과거를 되살릴 때만")
    sub.add_parser("status")
    sub.add_parser("selftest")
    j = sub.add_parser("judge", help="미판정 접수(to do=Newly Added)를 판정해 저장한다. 노션·슬랙·메일에 쓰지 않는다")
    j.add_argument("--past", type=int, default=0, help="결정 난 옛 행 N건을 판정 — 기준 보정용")
    j.add_argument("--limit", type=int, default=-1)
    sub.add_parser("label", help="노션 to do(심사역 결정) → human")
    ac = sub.add_parser("act", help="판정 있는 접수에 행동 — 문(bplan.outbound) 뒤. 기본 off 면 기록만"); ac.add_argument("--dry-run", action="store_true")
    fu = sub.add_parser("followup", help="카드 스레드의 ⭕/❌/🙋 를 읽어 잇는다 · 리마인드"); fu.add_argument("--dry-run", action="store_true")
    rp = sub.add_parser("replay"); rp.add_argument("--limit", type=int, default=-1); rp.add_argument("--criteria", default=None)
    df = sub.add_parser("diff"); df.add_argument("--a", default="live"); df.add_argument("--b", required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == "run":
            with lock.hold("bplan-run"):
                return run_all(dry_run=a.dry_run)
        if a.cmd == "health":
            return report.health("bplan", paths.data("bplan", "reports"), _send, label="비플랜 run")
        if a.cmd == "sync":
            with lock.hold("bplan-sync"):
                return sync(dry_run=a.dry_run, limit=a.limit, since=a.since)
        if a.cmd == "status":
            return status()
        if a.cmd == "selftest":
            return selftest()
        if a.cmd == "judge":
            with lock.hold("bplan-judge"):
                con = _gate().con
                rows = _notion_rows()
                n_lab = label(con, rows)
                r = judge_new(con, rows, past=a.past, limit=a.limit)
                print(f"· 판정 {r['judged']} · 형식 실패 {r['invalid']}" + (f" · 🔴 멈춤: {r['stopped']}" if r["stopped"] else "")
                      + f" · 정답 갈림 {n_lab}")
                return 2 if r["stopped"] else 0
        if a.cmd == "act":
            with lock.hold("bplan-act"):
                g = _gate(a.dry_run)
                print(f"· 행동: {act_all(g, _notion_rows())} | 문 {g.level()}")
                return 0
        if a.cmd == "followup":
            with lock.hold("bplan-followup"):
                g = _gate(a.dry_run)
                print(f"· followup: {followup(g)} | 문 {g.level()}")
                return 0
        if a.cmd == "label":
            con = _gate().con
            print(f"· 심사역 결정이 판정과 갈린 접수 {label(con, _notion_rows())}건 → human")
            return 0
        if a.cmd == "replay":
            con = _gate().con
            crit = open(a.criteria, encoding="utf-8").read() if a.criteria else judge.criteria_text()
            notion.register_writable(DB_KEY, secrets.get("NOTION_DATABASE_ID"))
            allowed = notion.allowed_options(notion.writable_id(DB_KEY), "점수")

            def judge_lines(lines, keys, criteria):
                out = []
                for k, text in zip(keys, lines):
                    pr = judge.parse(_llm(criteria, text), allowed)
                    if pr["ok"]:
                        out.append({"key": k, "verdict": pr["verdict"], "reason": pr["reason"], "raw": ""})
                return Fetched.measured(out)
            r = judgments.replay(con, judge_lines, crit, judgments.criteria_sha(judge.SYSTEM, crit), limit=a.limit)
            print(f"replay {r['run']}: 판정 {r['judged']} · 누락 {r['missing']}\n  다음: bplan diff --b {r['run']}")
            return 0
        if a.cmd == "diff":
            rows = judgments.diff(_gate().con, a.a, a.b, bucket=bucket)
            for r in rows:
                if r["a"] != r["b"]:
                    print(f"  {r['raw_a']:5} → {r['raw_b']:5} 정답={r['human'] or '-':5} {r['key'][:50]}")
            lab = [r for r in rows if r["human"]]
            print(f"{a.a} ↔ {a.b}: 비교 {len(rows)}건 · 갈림 {sum(r['a'] != r['b'] for r in rows)}건"
                  + (f" | 정답 {len(lab)}건 중 일치 {a.a}={sum(r['a'] == r['human'] for r in lab)} · {a.b}={sum(r['b'] == r['human'] for r in lab)}" if lab else ""))
            return 0
    except lock.Busy as e:
        print(f"🔴 {e}")
        return 3
    except Degraded as e:
        print(f"🔴 온전하지 않다: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
