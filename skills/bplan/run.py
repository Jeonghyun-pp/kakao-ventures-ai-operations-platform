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

from kvlib import ledger, lock, paths, secrets
from kv_transport import gmail, notion, sheets

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
    s = sub.add_parser("sync", help="시트 → 노션 행 → 접수 메일 라벨")
    s.add_argument("--dry-run", action="store_true", help="계획만. 노션·지메일 쓰기 0")
    s.add_argument("--limit", type=int, default=0, help="이번 실행 최대 생성 건수 (0=전부)")
    s.add_argument("--since", default=None, help="바닥선을 명시한다 (예: 2026-08-13T00:00:00). 과거를 되살릴 때만")
    sub.add_parser("status")
    sub.add_parser("selftest")
    a = p.parse_args(argv)
    try:
        if a.cmd == "sync":
            with lock.hold("bplan-sync"):
                return sync(dry_run=a.dry_run, limit=a.limit, since=a.since)
        if a.cmd == "status":
            return status()
        if a.cmd == "selftest":
            return selftest()
    except lock.Busy as e:
        print(f"🔴 {e}")
        return 3
    except Degraded as e:
        print(f"🔴 온전하지 않다: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
