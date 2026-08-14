r"""[W12 재빌드] 비플랜 드랍 메일 초안 — `To Drop` 행 → Gmail **초안**(발송 아님).

옛 `bplan/main.py run_drop` 을 대체한다. **멱등 단위가 다르다.**

## 🔴 이 재빌드의 이유 — 같은 사람이 거절 메일을 5통 받았다

옛 코드의 멱등 키는 **노션 `page_id`** 다. 그런데 한 창업자가 여러 번 접수하면 행이
여러 개 생기고, 각 행이 **따로 초안을 받는다.** 2026-08-13 실측:

    같은 이메일 2행 이상        23명 / 54행
    그중 이미 드랍메일 체크 2개+  19명
    최다                      person-22eaa5b584e0@example.com — 5행 전부 To Drop·드랍메일=True

`person-2b1c008ad932@example.com` 은 회사명을 세 번 바꿔가며 냈다(트리플디메디칼·코지코르·디앤디메디텍).
사람은 하나인데 코드는 셋으로 센다. **발송은 비가역이다** — 사람이 순서대로 보내면 그대로 나간다.

    멱등 단위 = **이메일**(정규화)     · 사람이 하나면 초안도 하나
    유효기간   = **1주일**(사용자 결정) · 영구로 막으면 3개월 뒤 재접수에 답장이 안 간다

나머지 행은 **초안 없이 체크박스만** 켠다 — 안 그러면 다음 실행에 또 후보로 올라온다.

## 함께 고친 것 (리뷰 C)

**F-B15 남의 스레드를 집었다.** `find_original_message` 가 `messages[0]` 을 무검증 채택했다.
회사명이 공란이면 질의가 `subject:"[카카오벤처스]  로부터 접수된 사업계획서"` 가 되어
**19건이 매칭**된다(실측). 그 남의 메일에 초안이 붙고 `In-Reply-To` 도 남의 것이 된다.
→ 회사명이 2자 미만이면 건너뛰고, 검색 결과가 2건 이상이면 **loud 스킵**한다.

**F-B16 한글 대표명인데 영문 레터가 나갔다.** 본문 한글비율만 보므로 딥테크 팀이 영문으로
답하면 `en` 이 되고 인사말은 `Hello 김민수,` 가 된다(실측 재현). → **모순 신호를 본다** —
본문이 en 인데 대표명·기업명이 한글이면 `ko` 로 되돌리고 사유를 남긴다.

**F-B17 `안녕하세요  대표님,` 이 그대로 나갔다.** 대표명 공란 가드가 없었다.
→ 대표명이 비었거나 20자를 넘으면(회사명이 잘못 들어온 경우) **초안을 안 만들고 보고**한다.

**F-B13 장부가 500건에서 잘렸다.** `LEDGER_MAX_ENTRIES=500` 이 오래된 키를 버렸다 —
`checkbox_done: false` 잔건이 잘리면 **초안 두 벌**이 된다. → append-only jsonl.

## 안전 경계

    쓰는 것    Gmail **초안**(`drafts().create`) · 노션 `드랍메일` 체크박스
    안 쓰는 것 **발송**(`messages().send` 는 리포 전체에 0건) · `To do` 이동 · 라벨·아카이빙

🔴 이 잡은 컷오버 전에는 **돌지 않는다**(`SKILL.md` HARD-GATE #1). 기본값은 `--dry` 다.

실행:
    python jobs/bplan_drop_job.py                    # dry(기본) — Gmail·노션 쓰기 0
    python jobs/bplan_drop_job.py --apply --limit 3
    python jobs/bplan_drop_job.py --synthetic        # Gmail·노션 불호출
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
_REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PROJ))
sys.path.insert(0, str(_REPO / "kv-core"))

from kv_core import alerts, job, notion, paths, secrets  # noqa: E402
from kv_core.job import Stage  # noqa: E402
from core.labels import bplan_label  # noqa: E402

SKILL = "mail-harness"

# 같은 사람에게 이 기간 안에 이미 초안을 만들었으면 다시 만들지 않는다.
# 🔴 영구로 두면 안 된다 — 그 창업자가 몇 달 뒤 새 아이템으로 다시 내고 또 드랍돼도
#    **답장이 영영 안 간다.** 그건 조용한 실패라 더 나쁘다. 사용자 결정: 1주일.
DEDUPE_DAYS = 7

_HANGUL = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")
_LATIN = re.compile(r"[A-Za-z]")
_MAX_CEO_NAME = 20


def _cfg() -> dict:
    secrets.load()
    return {"token": os.environ.get("NOTION_TOKEN"),
            "db": os.environ.get("NOTION_DATABASE_ID")}


def norm_email(e: str) -> str:
    """멱등 키 — 사람 하나에 값 하나. 대소문자·공백만 정규화한다.

    ⚠️ gmail 의 `+태그`·점 제거까지 하지는 **않는다.** 그건 "같은 사람"의 추정이지
       사실이 아니고, 틀리면 **다른 사람에게 답장을 안 보내는** 방향으로 실패한다.
       `job.py:41` — *"퍼지 매칭은 키가 아니다."*
    """
    return (e or "").strip().lower()


def _sent_path() -> Path:
    return paths.data(SKILL) / "bplan_drop_sent.jsonl"


def _recent_sent(today: date) -> dict[str, str]:
    """최근 DEDUPE_DAYS 안에 초안을 만든 이메일 → 그 날짜.

    🔴 append-only jsonl 이다. 옛 장부는 json 을 덮어쓰면서 `LEDGER_MAX_ENTRIES=500` 으로
       오래된 키를 **잘랐다** — 잘린 자리에서 중복 초안이 다시 생긴다. events 규약이
       append-only 인 이유와 같다(정본 결정 B).
    """
    p = _sent_path()
    if not p.exists():
        return {}
    cutoff = (today - timedelta(days=DEDUPE_DAYS)).isoformat()
    out: dict[str, str] = {}
    for ln in p.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue                      # 마지막 줄 torn 만 가능(append+fsync)
        d = str(r.get("date", ""))[:10]
        if d >= cutoff:
            out[norm_email(r.get("email", ""))] = d
    return out


def _record_sent(email: str, page_id: str, today: date) -> None:
    p = _sent_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps({"email": norm_email(email), "page_id": page_id,
                            "date": today.isoformat()}, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())              # 옛 장부엔 이게 없었다(전원 차단 = 중복 초안)


def decide_language(body: str, ceo_name: str, company: str) -> tuple[str, str]:
    """본문 한글비율로 정하되 **모순 신호**를 본다.

    🔴 [리뷰 C-F-B16] 옛 코드는 본문만 봤다. 딥테크·AI 팀이 영문으로 답하면 `en` 이 되고
       인사말이 `Hello 김민수,` 가 된다(실측: 한글 대표명 + 영문 답변 → 한글비율 0.8% → en).
       한글 이름 + 영문 본문은 **성의 없음의 강한 신호**다.
    """
    ko = len(_HANGUL.findall(body or ""))
    la = len(_LATIN.findall(body or ""))
    total = ko + la
    ratio = (ko / total) if total else 0.0
    lang = "ko" if ratio >= 0.15 or total < 20 else "en"
    if lang == "en" and (_HANGUL.search(ceo_name or "") or _HANGUL.search(company or "")):
        return "ko", f"본문 한글 {ratio:.0%} 이나 대표명·기업명이 한글 — 모순 신호로 ko"
    return lang, f"본문 한글 {ratio:.0%}"


def check_recipient(page: dict) -> str:
    """초안을 만들면 안 되는 이유. 빈 문자열이면 통과.

    🔴 [리뷰 C-F-B17] 옛 코드엔 이 가드가 없어 `안녕하세요  대표님,` 이 그대로 생성됐고,
       `To` 헤더가 빈 초안도 만들어졌다. 실 접수메일 중 성함이 빈 건이 존재한다.
    """
    email = norm_email(page.get("email"))
    ceo = (page.get("ceo_name") or "").strip()
    if not email or "@" not in email:
        return f"이메일이 비었거나 형식이 아니다: {email!r}"
    if not ceo:
        return "대표명이 비었다 — '안녕하세요  대표님,' 이 나간다"
    if len(ceo) > _MAX_CEO_NAME:
        return f"대표명이 {len(ceo)}자 — 회사명이 잘못 들어온 것으로 보인다: {ceo[:30]!r}"
    return ""


def pick_thread(msgs: list[dict], company: str) -> tuple[dict | None, str]:
    """원본 메일 1건을 고른다. **애매하면 안 고른다.**

    🔴 [리뷰 C-F-B15] 옛 코드는 `messages[0]` 을 무검증 채택했다. 회사명이 공란이면
       질의가 `subject:"[카카오벤처스]  로부터 접수된 사업계획서"` 가 되어 **19건이 매칭**된다
       (실측). 그러면 **남의 접수메일**에 우리 초안이 붙고 `In-Reply-To` 도 남의 것이 된다.
       재접수 다건(실측 23명)에서는 어느 스레드가 잡힐지 비결정적이다.
    """
    if len((company or "").strip()) < 2:
        return None, f"기업명이 {len(company or '')}자 — 질의가 남의 메일을 집는다"
    if not msgs:
        return None, "원본 메일을 못 찾음"
    if len(msgs) > 1:
        return None, f"원본 후보가 {len(msgs)}건 — 어느 것인지 확정 못 한다(재접수 의심)"
    return msgs[0], ""


# ─────────────────────────── Gmail 초안 ───────────────────────────
# 🔴 템플릿 문구는 **사람이 쓴 것**이라 옛 `bplan/main.py` 에서 그대로 옮겼다. 한 글자도
#    안 고쳤다 — 거절 메일 문안은 코드 판단이 아니다.
#    MIME 구조(plain + html div 블록)도 그대로다: Gmail 초안 에디터가 긴 text/plain 을
#    중간에서 wrap 하고 문단 빈 줄을 접는 현상을 피하려고 수동 작성 구조를 모방한 것이다.

_DROP_MAIL_TEMPLATE = """\
안녕하세요 {ceo_name} 대표님,
카카오벤처스입니다.

먼저 카카오벤처스에 관심을 가지고 연락 주셔서 감사드립니다.

보내주신 이메일을 바탕으로 내부 검토 과정을 거쳤으나, 아쉽게도 본 투자 건에 대한 추가 검토 진행은 힘들 것으로 결론나게 되었습니다. 가능하면 대표님 한 분 한 분을 찾아 뵙고 미팅을 가지고 싶지만, 저희 역시 여러 한계와 어려움 속에서 일 하다 보니 안타깝게도 만나 뵙지 못하는 분들이 많아 죄송스런 마음입니다.

투자 결정에 있어서 다양한 요소가 작용하는 만큼 추가 검토가 어려운 이유를 꼭 집어서 말씀드리기 어려운 점 또한 양해 부탁드립니다. 저희 또한 다양한 투자사 중 하나에 불과하기 때문에 판단이 틀릴 때가 있음을 고려하셔서 처음 가지셨던 믿음과 의지로 사업을 해 나가시길 바랍니다. 멀리서나마 사업 번창하시고, 건승하시기를 바라겠습니다.

감사합니다.
카카오벤처스 드림"""

_DROP_MAIL_TEMPLATE_EN = """\
Hello {ceo_name},
Thank you for reaching out to Kakao Ventures and sending your deck.

We wanted to let you know that we've finished performing an internal review of your business proposal, but unfortunately, decided not to advance it further. The reasons why we cannot proceed further are subjective, and considering that we are simply one investor among many, there are many times in which we make mistakes. As such, we hope that you continue persevering to see the realization of your business, and if possible, please let us know how it goes. We would love to learn from our mistakes.

We wish you success in all your future endeavors.

Best regards,
Kakao Ventures"""


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _to_gmail_html(text: str) -> str:
    """템플릿 텍스트를 Gmail 수동 reply 와 같은 <div> 블록 구조로."""
    parts = []
    for line in (text or "").split("\n"):
        parts.append("<div><br></div>" if line == "" else f"<div>{_html_escape(line)}</div>")
    return "".join(parts) or "<div><br></div>"


def build_mime(to_email: str, subject: str, ceo_name: str, lang: str, in_reply_to: str | None):
    """드랍메일 초안용 multipart/alternative. **발송이 아니라 초안용이다.**"""
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    template = _DROP_MAIL_TEMPLATE if lang == "ko" else _DROP_MAIL_TEMPLATE_EN
    plain = template.format(ceo_name=ceo_name)
    mime = MIMEMultipart("alternative")
    mime["To"] = to_email
    mime["Subject"] = subject if subject.startswith("Re: ") else f"Re: {subject}"
    if in_reply_to:
        mime["In-Reply-To"] = in_reply_to
        mime["References"] = in_reply_to
    mime.attach(MIMEText(plain, "plain", "utf-8"))
    mime.attach(MIMEText(_to_gmail_html(plain), "html", "utf-8"))
    return mime


def _gmail():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    return build("gmail", "v1", cache_discovery=False,
                 credentials=Credentials.from_authorized_user_file(
                     str(secrets.credential_path("google/token.json"))))


def find_original(svc, company: str, label: str) -> tuple[dict | None, str]:
    """접수메일 원본 1건. **애매하면 안 고른다**(`pick_thread` 가 판정).

    🔴 옛 코드는 `messages[0]` 을 무검증 채택했다 — `pick_thread` 독스트링 참조.
    """
    q = f'label:"{label}" subject:"[카카오벤처스] {company} 로부터 접수된 사업계획서"'
    msgs = svc.users().messages().list(userId="me", q=q).execute().get("messages", [])
    picked, why = pick_thread(msgs, company)
    if not picked:
        return None, why
    full = svc.users().messages().get(userId="me", id=picked["id"], format="full").execute()
    hdr = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
    return {"thread_id": full.get("threadId"), "message_id": hdr.get("Message-ID"),
            "subject": hdr.get("Subject", ""), "body": _body_text(full.get("payload", {}))}, ""


def _body_text(payload: dict) -> str:
    """본문 텍스트만 뽑는다 — 언어 판정의 입력이다."""
    import base64
    out = []
    stack = [payload]
    while stack:
        p = stack.pop()
        if p.get("mimeType") == "text/plain":
            data = (p.get("body") or {}).get("data")
            if data:
                out.append(base64.urlsafe_b64decode(data).decode("utf-8", "replace"))
        stack.extend(p.get("parts") or [])
    return "\n".join(out)


def mark_done(tok: str, page_id: str) -> None:
    """노션 `드랍메일` 체크 — 이 잡이 쓰는 **유일한 노션 칸**이다."""
    r = notion.write("PATCH", f"{notion.API}/pages/{page_id}", tok,
                     {"properties": {"드랍메일": {"checkbox": True}}})
    if r.status_code != 200:
        raise RuntimeError(f"체크박스 실패 {r.status_code}: {r.text[:200]}")


# ─────────────────────────── stages ───────────────────────────

def s_collect(skill: str, job_id: str) -> None:
    """`To Drop` + `드랍메일=False` 행을 큐로. 읽기 전용."""
    c = _cfg()
    tok, db = c["token"], c["db"]
    if not tok or not db:
        raise RuntimeError("NOTION_TOKEN / NOTION_DATABASE_ID 없음")

    rows, cur = [], None
    while True:
        payload = {"page_size": 100,
                   "filter": {"and": [
                       {"property": "To do", "select": {"equals": "To Drop"}},
                       {"property": "드랍메일", "checkbox": {"equals": False}}]},
                   **({"start_cursor": cur} if cur else {})}
        r = notion.write("POST", f"{notion.API}/databases/{db}/query", tok, payload)
        if r.status_code != 200:
            raise RuntimeError(f"노션 조회 실패 {r.status_code}: {r.text[:200]}")
        d = r.json()
        for p in d["results"]:
            P = p["properties"]
            rows.append({"page_id": p["id"],
                         "company": _plain(P, "기업명"), "email": _plain(P, "이메일"),
                         "ceo_name": _plain(P, "대표명"), "svc": _plain(P, "서비스정의")})
        if not d.get("has_more"):
            break
        cur = d["next_cursor"]

    print(f"· To Drop 미처리 {len(rows)}건")
    if not rows:
        alerts.observe("empty_harvest", "bplan-drop-vacuum",
                       "⚠️ bplan-drop: 대상 0건. 정상일 수도 있고 필터·토큰이 어긋났을 수도 있다.",
                       task="bplan-drop", candidates=0)
    job.atomic_write(job.job_dir(SKILL, job_id) / "artifacts" / "queue.json",
                     json.dumps({"rows": rows}, ensure_ascii=False, indent=1))


def _plain(props: dict, key: str) -> str:
    v = props.get(key) or {}
    t = v.get("type")
    if t in ("rich_text", "title"):
        return "".join(x.get("plain_text", "") for x in v[t]).strip()
    return ""


def plan(rows: list[dict], sent: dict[str, str], today: date) -> tuple[list[dict], list[dict]]:
    """(초안 만들 행, 초안 없이 체크만 할 행). **이 함수가 이 잡의 핵심 판단이다.**

    같은 이메일이 여러 행이면 **하나만** 초안을 받는다. 나머지는 체크박스만 켜서
    다음 실행 후보에서 빠지게 한다 — 안 그러면 매 실행 같은 사람이 다시 올라온다.
    """
    draft: list[dict] = []
    mark_only: list[dict] = []
    claimed: set[str] = set()
    for r in sorted(rows, key=lambda x: x["page_id"]):     # 결정적 순서(재실행 동일)
        em = norm_email(r["email"])
        if em in sent:
            r["_why"] = f"{sent[em]} 에 이미 초안(최근 {DEDUPE_DAYS}일)"
            mark_only.append(r)
        elif em in claimed:
            r["_why"] = "같은 사람의 다른 행이 이번에 초안을 받는다"
            mark_only.append(r)
        else:
            claimed.add(em)
            draft.append(r)
    return draft, mark_only


def s_draft(skill: str, job_id: str, *, limit: int, dry: bool, label: str) -> None:
    """초안 생성 — 사람 하나에 한 통. 나머지 행은 체크박스만 켠다.

    순서가 결정 C 그대로다: **부작용(초안 생성) → 노션 체크 → 발송장부 → item_done.**
    중간에 죽으면 `item_done` 이 없어 다음 실행이 재시도하고, 발송장부(이메일+1주일)가
    중복을 흡수한다.
    """
    q = json.loads((job.job_dir(SKILL, job_id) / "artifacts" / "queue.json").read_text(encoding="utf-8"))
    rows = q.get("rows") or []
    today = date.today()
    draft, mark_only = plan(rows, _recent_sent(today), today)
    print(f"· 초안 대상 {len(draft)}명 · 체크만 {len(mark_only)}행(같은 사람의 다른 행)")

    if limit and len(draft) > limit:
        print(f"· 상한 {limit}명 — 잔여 {len(draft) - limit}명은 다음 실행")
        draft = draft[:limit]

    tok = _cfg()["token"]
    svc = None if dry else _gmail()

    def _one(r: dict) -> None:
        why = check_recipient(r)
        if why:
            # 초안을 만들지 않고 **사실로 보고**한다. 옛 코드는 `안녕하세요  대표님,` 을 그대로 냈다.
            print(f"  ⏭️  건너뜀 [{r['company'][:20]}] — {why}")
            return
        if dry:
            lang, w = decide_language("", r["ceo_name"], r["company"])
            print(f"  [dry] {norm_email(r['email']):32} {r['company'][:20]} · {lang} ({w})")
            return
        orig, why2 = find_original(svc, r["company"], label)
        if not orig:
            print(f"  ⏭️  건너뜀 [{r['company'][:20]}] — {why2}")
            return
        lang, w = decide_language(orig["body"], r["ceo_name"], r["company"])
        mime = build_mime(r["email"], orig["subject"], r["ceo_name"], lang, orig["message_id"])
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
        svc.users().drafts().create(
            userId="me", body={"message": {"raw": raw, "threadId": orig["thread_id"]}}).execute()
        mark_done(tok, r["page_id"])
        _record_sent(r["email"], r["page_id"], today)      # 부작용 뒤에 장부
        print(f"  ✅ {norm_email(r['email']):32} {r['company'][:20]} · {lang} ({w})")

    job.process_items(SKILL, job_id, draft, key=lambda r: norm_email(r["email"]),
                      process=_one, stage="draft")

    # 같은 사람의 나머지 행 — 초안 없이 체크만. 안 하면 다음 실행에 또 후보로 올라온다.
    for r in mark_only:
        if dry:
            print(f"  [dry-mark] {r['company'][:20]} — {r['_why']}")
            continue
        mark_done(tok, r["page_id"])
        print(f"  · 체크만 {r['company'][:20]} — {r['_why']}")


def _stages(limit: int, dry: bool, label: str) -> list[Stage]:
    return [Stage("collect", s_collect),
            Stage("draft", lambda sk, ji: s_draft(sk, ji, limit=limit, dry=dry, label=label))]


def _synthetic() -> int:
    ok, fail = [], []

    def chk(n: str, c: bool, d: str = "") -> None:
        (ok if c else fail).append(n)
        print(f"  {'✅' if c else '❌'} {n}" + (f"  ({d})" if d else ""))

    T = date(2026, 8, 13)
    rows = [{"page_id": "p1", "email": "A@X.com", "company": "가", "ceo_name": "김"},
            {"page_id": "p2", "email": "a@x.com ", "company": "나", "ceo_name": "김"},
            {"page_id": "p3", "email": "person-48232851fed9@example.com", "company": "다", "ceo_name": "박"}]
    d, m = plan(rows, {}, T)
    chk("① 같은 사람 3행 중 초안은 1통", len(d) == 2 and len(m) == 1,
        f"초안 {[x['page_id'] for x in d]} · 체크만 {[x['page_id'] for x in m]}")
    chk("② 나머지 행도 체크는 켠다(다음 실행 후보에서 빠진다)", m[0]["page_id"] == "p2")

    d2, _ = plan(rows, {"a@x.com": "2026-08-10"}, T)
    chk("③ 이미 보낸 사람은 초안 없음", len(d2) == 1 and d2[0]["page_id"] == "p3",
        f"초안 {[x['page_id'] for x in d2]}")

    # ④ 유효기간은 `plan` 이 아니라 `_recent_sent` 의 책임이다 — **거기서** 재야 한다.
    #    (처음엔 오래된 날짜를 `plan` 에 넘겨 시험했는데, `plan` 은 넘겨받은 것을 전부
    #     "보냈음"으로 취급하므로 통과할 수 없었다. 테스트가 계약을 잘못 물었다.)
    _sent_path().parent.mkdir(parents=True, exist_ok=True)
    _sent_path().write_text(
        json.dumps({"email": "old@x.com", "page_id": "q", "date": "2026-08-01"}) + "\n"
        + json.dumps({"email": "new@x.com", "page_id": "q", "date": "2026-08-10"}) + "\n",
        encoding="utf-8")
    rec = _recent_sent(T)
    chk("④ 1주일 지난 것은 창에서 빠진다(영구 차단 아님)", "old@x.com" not in rec, str(rec))
    chk("⑤ 1주일 안의 것은 창에 남는다", "new@x.com" in rec, str(rec))

    chk("⑥ 같은 입력에 같은 결과(재실행 안정)",
        [x["page_id"] for x in plan(rows, {}, T)[0]] == [x["page_id"] for x in d])

    lang, why = decide_language("This is our AI platform for enterprises.", "김민수", "테스트")
    chk("⑦ 한글 대표명 + 영문 본문 → ko (모순 신호)", lang == "ko", why)
    # ⚠️ 본문이 20자 미만이면 신호 부족으로 ko 가 기본이다(옛 `MIN_SIGNAL_CHARS` 규율).
    #    실 접수메일은 수백 자라 이 경로는 드물지만, 시험 문자열은 그걸 감안해야 한다.
    chk("⑧ 영문 지원자는 en 유지",
        decide_language("We build AI infrastructure for enterprise customers.",
                        "John Smith", "Acme")[0] == "en")
    chk("⑨ 신호가 부족하면 ko 로 떨어진다(보수적 기본값)",
        decide_language("ok", "John", "Acme")[0] == "ko")
    chk("⑩ 한글 본문은 ko", decide_language("안녕하세요 저희는 커머스입니다.", "김", "가")[0] == "ko")

    chk("⑪ 대표명 공란은 초안 거부", bool(check_recipient({"email": "a@b.com", "ceo_name": ""})))
    chk("⑫ 이메일 공란은 초안 거부", bool(check_recipient({"email": "", "ceo_name": "김"})))
    chk("⑬ 대표명이 회사명이면 거부",
        bool(check_recipient({"email": "a@b.com", "ceo_name": "Sharpie·Silvy (개인 개발 / 법인 미설립)"})))
    chk("⑭ 정상 수신자는 통과", not check_recipient({"email": "a@b.com", "ceo_name": "김민수"}))

    chk("⑮ 기업명 공란이면 스레드를 안 고른다", pick_thread([{"id": "m"}], "")[0] is None)
    chk("⑯ 후보 2건이면 확정 안 한다(남의 스레드 방지)",
        pick_thread([{"id": "m1"}, {"id": "m2"}], "테스트")[0] is None)
    chk("⑰ 후보 1건이면 고른다", pick_thread([{"id": "m1"}], "테스트")[0] == {"id": "m1"})

    print(f"\n{'🎉 synthetic PASS' if not fail else '🔴 실패: ' + str(fail)}")
    return 0 if not fail else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=3)
    # 🔴 재빌드 첫 판엔 이 인자가 **없었다** — `_stages(limit, dry, label)` 의 label 은
    #    출처가 아예 없었다. main() 이 컷오버 가드에서 먼저 죽어 아무도 못 봤을 뿐이다.
    #    sync 가 붙이는 라벨과 **같은 곳**(core/labels.py)에서 온다.
    ap.add_argument("--label", default=bplan_label(),
                    help="이 라벨 안에서 접수 원본을 찾는다 (sync 가 붙이는 그 라벨)")
    ap.add_argument("--synthetic", action="store_true")
    args = ap.parse_args()
    if args.synthetic:
        assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"
        return _synthetic()
    raise SystemExit(
        "🔴 컷오버 전에는 실행하지 않는다 — skills/mail-harness/SKILL.md HARD-GATE #1.\n"
        "   `--synthetic` 만 허용된다. 컷오버 후 이 가드를 사람이 명시적으로 연다.")


if __name__ == "__main__":
    raise SystemExit(main())
