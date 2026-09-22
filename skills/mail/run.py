"""메일 트리아지.

    plan     인박스 읽기 → 규칙 → claude → 계획 파일
    apply    계획 실행 — 아카이브 · 초안. **발송은 없다**
    status   계획·실행 이력

## 🔴 사람이 그 사이에 메일함을 만지는 건 오류가 아니라 입력이다

`plan` 과 `apply` 사이에 정현님이 메일을 먼저 처리할 수 있다. 그걸 전제로 짠다 —
실행 직전에 **하나씩 다시 보고**, 바뀐 항목만 건너뛰고, 건너뛴 걸 말한다.
전체를 멈추지 않는다. 메일 하나 때문에 62통이 멈추면 사람이 그 검사를 끄게 된다.

    ① 존재 확인   지금도 인박스에 있나            요청 한 번 (번호만 받는다)
    ② 스레드 확인  그 뒤로 대화가 진행됐나          초안 만들 것만
    ③ 장부       한 번 한 일은 다시 안 한다        멱등 키 = 메일번호 + 종류

## 지키는 규율

    미상은 0통이 아니다      망이 죽거나 토큰이 죽으면 "메일 없음"이 아니라 미상이다
    인증부터 본다           인박스를 부르기 전에 확인한다. 나중에 401 로 알면 0통과 구별이 안 된다
    실행 먼저, 장부 나중      순서가 반대면 실패한 게 "했음"으로 남아 영영 안 잡힌다
    쓴 것은 되읽는다        200 은 저장을 뜻하지 않는다
    판정 못 받은 건을 안 버린다  세고, 찍고, 종료코드에 반영한다
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from collections import Counter
from datetime import date, datetime, timezone

from kvlib import constraints, ledger, lock, paths
from kv_transport import gmail, slack

from . import brief, judge, route, rules

TASK = "mail"

POSTED = "posted"
RESOLVED = "resolved"
WARNED = "warned"
ARCHIVED = "archived"
DRAFTED = "drafted"
SKIPPED = "skipped"
# 🔴 protect 는 기록하지 않는다. "아무것도 안 했다"를 하루 13번씩 남기면 장부만 부푼다.

_ICON = {"protect": "🔒", "archive": "📥", "reply": "✉️", "forward": "📤",
         "escalate": "🚩", "slack": "💬"}

# 🔴 슬랙 채널. **등록과 사용은 별개다** — 실채널을 등록해두되 지금은 test 로만 보낸다
#    [사용자 결정 2026-08-18] 자동으로 도는 건 전부 review-test.
#    실채널 게시는 사람이 그때그때 지시한다. 켤 때는 CH 한 줄만 바꾼다.
# 채널 등록은 route.py 에 있다 — 어느 채널로 보낼지 정하는 POST_CH 바로 옆이다.


class Degraded(RuntimeError):
    """돌긴 돌았는데 온전하지 않다. 크론은 종료코드만 보므로 0 과 달라야 한다."""


def _tally(msgs: list[dict]) -> dict:
    """메일 목록에서 {메일 수, 대화 수}."""
    return {"messages": len(msgs), "threads": len({m.get("thread_id") for m in msgs})}


def _both(c: dict | None) -> str:
    """🔴 메일 수와 대화 수를 **함께** 말한다. 하나만 말하면 사람이 화면에서 보는
    수와 어긋나고, 그러면 매번 "왜 다르지"를 다시 확인하게 된다(실측 09-02: 34 vs 31).
    처리는 메일 단위여야 하고 — 한 대화에 다른 사람의 문의가 섞여 있다 — 보고는
    사람이 보는 단위여야 한다. 그래서 고르지 않고 둘 다 적는다."""
    if not c:
        return "미상"
    m, t = c.get("messages"), c.get("threads")
    if t is None or m == t:
        return f"{m}통"
    return f"{t}개 대화 · {m}통"


def _plans_dir():
    return paths.data("mail", "plans")


def plan(*, days: int | None = None, limit: int | None = None, no_judge: bool = False) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    print(f"▶ plan {stamp}")

    # ── 인증부터. 죽었으면 인박스를 부르기 전에 멈춘다 ──────────────────
    auth = gmail.auth_check()
    if not auth.is_measured:
        print(f"🔴 지메일 인증이 안 된다: {auth.reason}")
        print("   재인증은 브라우저 로그인이 필요하다 — 자동으로 못 고친다.")
        return 1

    before = gmail.inbox_count()
    print(f"  · 인박스 총 {_both(before.or_default(None))}")

    f = gmail.inbox(days=days, limit=limit)
    if not f.is_measured:
        print(f"🔴 인박스를 못 읽었다: {f.reason}  ← 0통이 아니라 미상이다")
        return 1
    msgs = f.require()
    print(f"  · 최근 {days or '기본'}일 {_both(_tally(msgs))} 읽음"
          + (f"  ⚠️ 일부 실패 {f.meta.get('failed')}건" if f.partial else ""))

    # ── 규칙 ──────────────────────────────────────────────────────────
    # 🔴 웹플로우 폼은 **문의자가 보낸 메일로 바꿔** claude 에게 넘긴다.
    #    폼의 `문의 유형` 드롭다운은 문의자가 고르는 값이라 우리 갈래와 무관하다
    #    (실측: '섭외 문의' 로 온 것이 사칭 확인이었고, '기타 문의' 넷은 강연 초청·
    #     템프서울 입주·사칭이 섞여 있었다). 그래서 창구가 아니라 내용으로 가른다.
    #    바꾼 메시지는 sender 가 문의자라, 회신 초안이 구조적으로 no-reply 로 못 간다.
    view: dict[str, dict] = {}      # 판정·라우팅에 쓸 메시지 (웹플로우만 원본과 다르다)
    hits: dict[str, dict] = {}
    misses: list[dict] = []
    n_wf = 0
    for m in msgs:
        h = rules.classify(m)
        if h and h["category"] == rules.WEBFLOW:
            alt = rules.webflow_as_message(m)
            if alt is None:
                # 주소를 못 찾았다. 추측하면 반송된다 — 사람에게 올린다.
                hits[m["id"]] = h
                view[m["id"]] = m
                continue
            n_wf += 1
            m, h = alt, None
        view[m["id"]] = m
        if h:
            hits[m["id"]] = h
        else:
            misses.append(m)
    print(f"  · 규칙 확정 {len(hits)}통 · claude 몫 {len(misses)}통"
          + (f" (웹플로우 폼 {n_wf}건 포함 — 내용으로 가른다)" if n_wf else ""))

    # ── claude ────────────────────────────────────────────────────────
    unjudged: list[dict] = []
    if misses and not no_judge:
        v = judge.judge_mail(misses)
        if not v.is_measured:
            print(f"🔴 판정 실패: {v.reason}")
            return 1
        vmap = {x["id"]: x for x in v.require()}
        print(f"  · claude 판정 {len(vmap)}/{len(misses)}"
              + (f" · DLQ {v.meta.get('dlq')}" if v.meta.get("dlq") else ""))
        for m in misses:
            got = vmap.get(m["id"])
            if got:
                hits[m["id"]] = {"category": got["category"], "why": got["reason"]}
            else:
                # 🔴 조용히 버리지 않는다. 판정을 못 받은 건 UNKNOWN 으로 두되 세어둔다.
                unjudged.append(m)
                hits[m["id"]] = {"category": rules.UNKNOWN, "why": "claude 판정을 못 받았다"}
    elif misses:
        for m in misses:
            hits[m["id"]] = {"category": rules.UNKNOWN, "why": NO_JUDGE_WHY}

    # ── 라우팅 ────────────────────────────────────────────────────────
    rows = []
    for m0 in msgs:
        m = view[m0["id"]]
        h = hits[m["id"]]
        act = route.apply_named_partner(m, h["category"], route.decide(m, h))
        wf = m.get("webflow_kind")
        if wf:
            # 🔴 회신 제목은 문의자가 쓴 것을 쓴다. 실행 시점에 지메일에서 다시 읽는
            #    원본은 웹플로우 알림이라 제목이 "New form submission on Webflow..." 다.
            act.setdefault("draft_subject", m["subject"])
        rows.append({"id": m["id"], "thread_id": m["thread_id"], "subject": m["subject"],
                     "sender": m["sender"], "date": m["date"],
                     "internal_date": m.get("internal_date", 0),
                     "category": h["category"],
                     "why": (f"홈페이지 폼('{wf}') · " if wf else "") + h["why"], **act})
    n_dup = _dedupe_recipient(rows)

    # ── 슬랙에 올릴 것만 요약을 쓴다 ────────────────────────────────────
    # 🔴 하루 1~3건이라 배치가 아니라 한 건씩 부른다. 실패해도 계획은 세운다 —
    #    요약이 없으면 원문 앞머리로 물러난다(빈 글은 안 올린다).
    n_brief = 0
    for r in rows:
        if r["action"] != "slack":
            continue
        b = brief.write(view[r["id"]])
        if b.is_measured:
            r["brief"] = b.require()
            r["text"] = route.rebuild_post(view[r["id"]], r, b.require())
            n_brief += 1
        else:
            print(f"  ⚠️  요약 실패({b.reason}) — 원문 앞머리로 올린다: {r['subject'][:40]}")
    if n_brief:
        print(f"  · 슬랙 요약 {n_brief}건")
    # 🔴 본문·헤더는 계획에 안 담는다. 초안이 필요할 때 실행 시점에 다시 읽는다 —
    #    계획 파일이 메일 본문 덩어리가 되지 않고, 인용문이 항상 최신이다.

    # 🚩 사람이 봐야 하는 것부터 위로
    rows.sort(key=lambda r: (r["action"] != "escalate", r["category"], r["subject"]))

    # ── 저장 ──────────────────────────────────────────────────────────
    d = _plans_dir()
    (d / f"{stamp}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / f"{stamp}.md").write_text(_report(rows, stamp, len(msgs), unjudged), encoding="utf-8")

    # ── 보고는 만든 파일을 다시 읽어서 ──────────────────────────────────
    saved = json.loads((d / f"{stamp}.json").read_text(encoding="utf-8"))
    _print(saved)
    print(f"\n  계획 파일: {d / f'{stamp}.md'}")
    if n_dup:
        print(f"  · 같은 수신자 중복 {n_dup}건 — 최신 것만 남기고 나머지는 인박스에 뒀다")

    # 🔴 여기서 '실행 전후 총수가 같다 → 안 건드렸다'를 주장하지 않는다.
    #    메일함엔 다른 행위자가 있다(사람·발신자·옛 크론). 2~5분 사이에 한 통 오고
    #    한 통 나가면 총수는 그대로다 — 바뀌었는데 ✅ 가 뜬다. 반대로 한 통만 와도
    #    아무 잘못 없이 ⚠️ 가 뜬다. 거짓 경보를 내는 검사는 곧 무시당한다.
    #    "우리가 안 썼다"의 근거는 A8(정적 검사)이지 관찰이 아니다.

    if unjudged:
        raise Degraded(f"claude 판정을 못 받은 메일 {len(unjudged)}통 — UNKNOWN 으로 뒀다")
    if f.partial:
        raise Degraded(f"인박스 일부를 못 읽었다 ({f.meta.get('failed')}건)")
    return 0


# 여러 건이 나란히 가야 하는 공유 수신함. 수신자 중복 판정에서 뺀다.
_SHARED_INBOX = {route.SLACK_BPLAN.lower()}


def _prefer(a: dict, b: dict) -> bool:
    """같은 사람의 메일 둘 중 a 가 나은가.

    ① 직접 보낸 메일이 홈페이지 폼보다 낫다
    ② 그다음은 최신

    🔴 ①이 없으면 폼 쪽이 이긴다(대개 폼이 나중에 온다). 그러면 회신이 웹플로우
       **알림 메일**에 붙는다 — 제목을 지어내야 하고, 본인이 보낸 메일에 안 달린다.
       실측 2026-09-02: Robo Goalie 가 08-31 직접 메일 · 09-01 폼으로 두 번 왔는데
       폼 쪽이 잡혀 제목이 "Re: New form submission on Webflow..." 로 나갔다.
       직접 메일이면 제목도 스레드도 그대로 맞다 — 지어낼 게 없다.
    """
    aw, bw = bool(a.get("draft_subject")), bool(b.get("draft_subject"))
    if aw != bw:
        return not aw          # 폼이 아닌 쪽(draft_subject 가 없는 쪽)이 이긴다
    return a.get("internal_date", 0) > b.get("internal_date", 0)


def _dedupe_recipient(rows: list[dict]) -> int:
    """같은 사람에게 같은 글이 두 번 가지 않게 한다. **가장 최근 것만 남긴다.**

    🔴 스레드 확인만으로는 부족하다. 그건 *같은 대화* 안에서만 막는다.
       실측 09-02: person-c95eeee0b1c1@example.com 가 열흘 간격으로 두 번 보낸 건 같은 대화라 막혔다.
       하지만 같은 사람이 **대화를 나눠서** 보내면 둘 다 초안이 만들어진다 — 그러면
       같은 안내가 두 통 간다. 07월에 옛 시스템이 같은 사람에게 초안 5통을 만든 적이 있다.

    최신을 남기는 이유: 나중 메일에 더 최신 정보가 있고, 사람이 인박스에서 보는 것도 그쪽이다.
    지운 게 아니라 `protect` 로 바꾼다 — **인박스에 남아 사람 눈에 띈다.**
    """
    best: dict[str, dict] = {}
    for r in rows:
        if r["action"] not in ("reply", "forward"):
            continue
        to = (r.get("to") or "").lower()
        # 🔴 공유 수신함은 중복이 아니다. 비플랜방에는 서로 다른 건이 여러 개 가야 한다.
        #    (첫 구현이 여기서 물어 PARTNERSHIP·UNKNOWN 두 건을 조용히 protect 로 돌렸다.
        #     "같은 사람에게 두 번" 을 막으려던 규칙이 "같은 방에 두 건" 까지 막았다.)
        if not to or to in _SHARED_INBOX:
            continue
        cur = best.get(to)
        if cur is None or _prefer(r, cur):
            best[to] = r

    n = 0
    keep = {id(r) for r in best.values()}

    for r in rows:
        if r["action"] not in ("reply", "forward"):
            continue
        to = (r.get("to") or "").lower()
        # 🔴 두 검사가 **같아야 한다.** 첫 실행 때 이 줄에 공유함 예외를 빼먹어서,
        #    best 에 안 들어간 공유함 행이 전부 "중복" 으로 잡혔다 — 비플랜방 4건이
        #    조용히 protect 로 돌아갔다. 조건이 갈라지면 그 사이로 새는 게 생긴다.
        if not to or to in _SHARED_INBOX or id(r) in keep:
            continue
        n += 1
        # 🔴 [사용자 결정 2026-09-02] protect 가 아니라 archive 다.
        #    protect 로 두던 이유는 "사람 눈에 띄게" 였는데, 그 사람은 **이미 답을 받는다** —
        #    사람이 할 일이 없다. 그러면 인박스에 영영 쌓이고 매일 계획에 다시 뜬다.
        #    안전한 이유: 답장 초안이 실패해도 **고른 쪽 메일은 인박스에 그대로 남는다**
        #    (reply 는 아카이브를 안 한다). 그래서 요청이 통째로 사라지는 경우가 없다.
        #    대가: 같은 사람이 **다른 용건**으로 두 번 보냈으면 한쪽이 조용히 치워진다.
        #    지금은 수신자만 보고 용건은 안 본다 — 아카이브라 되살릴 수는 있다.
        r["action"] = "archive"
        r["note"] = f"같은 수신자({to})에게 답장할 메일이 따로 있다 — 이쪽은 아카이빙"
        r.pop("to", None)
        r.pop("body", None)
        r.pop("also_reply", None)
    return n


def _print(rows: list[dict]) -> None:
    c = Counter(r["action"] for r in rows)
    print("\n" + "=" * 78)
    print("  " + "  ".join(f"{_ICON[k]} {k} {c.get(k, 0)}" for k in _ICON))
    print("=" * 78)
    for r in rows:
        print(f"  {_ICON[r['action']]} {r['category']:14} {r['subject'][:40]:42} {r['sender'][:28]}")


def _report(rows: list[dict], stamp: str, total: int, unjudged: list[dict]) -> str:
    c = Counter(r["action"] for r in rows)
    cats = Counter(r["category"] for r in rows)
    unknown_ratio = cats.get(rules.UNKNOWN, 0) / total if total else 0

    out = [f"# 메일 분류 계획 — {stamp}", "",
           f"인박스 {total}통. **아무것도 실행하지 않았다** — 이 문서는 계획일 뿐이다.", "",
           "## 행동별", "", "| 행동 | 수 | 뜻 |", "|---|---|---|",
           f"| 🔒 protect | {c.get('protect', 0)} | 아무것도 안 한다 (비플랜이 쓰는 메일) |",
           f"| 📥 archive | {c.get('archive', 0)} | 인박스에서만 뺀다 (되돌릴 수 있다) |",
           f"| ✉️ reply | {c.get('reply', 0)} | 답장 **초안**. 발송은 사람이 |",
           f"| 📤 forward | {c.get('forward', 0)} | 포워드 **초안**. 발송은 사람이 |",
           f"| 🚩 escalate | {c.get('escalate', 0)} | 인박스에 두고 사람이 본다. 초안 없음 |", ""]

    alert = constraints.get("gmail.unknown_alert_ratio")
    if unknown_ratio > alert:
        out += [f"> 🔴 **UNKNOWN 비율 {unknown_ratio:.0%}** — 기준({alert:.0%})을 넘었다. 분류기가 이상하다는 신호다.", ""]
    if unjudged:
        out += [f"> 🔴 **claude 판정을 못 받은 메일 {len(unjudged)}통** — UNKNOWN 으로 뒀다. 조용히 버리지 않았다.", ""]

    out += ["## 카테고리별", "", "| 카테고리 | 수 |", "|---|---|"]
    out += [f"| `{k}` | {v} |" for k, v in cats.most_common()]

    esc = [r for r in rows if r["action"] == "escalate"]
    if esc:
        out += ["", "## 🚩 사람이 봐야 하는 것", "",
                "**요약하지 않고 그대로 적는다.**", "",
                "| 카테고리 | 제목 | 발신 | 왜 |", "|---|---|---|---|"]
        out += [f"| `{r['category']}` | {r['subject']} | {r['sender']} | {r['note']} |" for r in esc]

    out += ["", "## 전체", "", "| 행동 | 카테고리 | 제목 | 발신 | 수신자 | 근거 |", "|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {_ICON[r['action']]} {r['action']} | `{r['category']}` | {r['subject']} "
                   f"| {r['sender']} | {r.get('to', '—')} | {r['why']} |")

    out += ["", "---", "",
            "🔴 **발송 경로가 코드에 없다.** 회신·포워드는 전부 초안이고, 보내기는 사람이 누른다.",
            "🔴 신뢰 경로·제휴 거절·채용·사칭 넷은 무수정률과 무관하게 **영원히 수동**이다.", ""]
    return "\n".join(out)


# 🔴 --no-judge 로 만든 계획은 실행하면 안 된다.
#    claude 몫이 전부 UNKNOWN → forward 가 되어, 규칙이 못 잡은 메일이 통째로
#    #Squad-비플랜 으로 나간다. 실측 09-02: 9통이 그렇게 잡혔다(실행 전에 발견).
#    계획 파일만 지우는 건 해결이 아니다 — 다음에 또 만든다. **실행이 거부해야 한다.**
NO_JUDGE_WHY = "--no-judge 로 claude 를 건너뛰었다"


def _latest_plan():
    files = sorted(_plans_dir().glob("*.json"))
    return files[-1] if files else None


def _done() -> tuple[set, set, set]:
    """장부에서 이미 한 일. (아카이브, (메일,종류) 초안, 슬랙 게시)"""
    arch = {r["key"] for r in ledger.rows(TASK, ev=ARCHIVED)}
    drafts = {(r["key"], r.get("kind")) for r in ledger.rows(TASK, ev=DRAFTED)}
    posted = {r["key"] for r in ledger.rows(TASK, ev=POSTED)}
    return arch, drafts, posted


def apply(*, plan_stamp: str | None = None, dry_run: bool = False, limit: int | None = None,
          only: str | None = None, no_slack: bool = False) -> int:
    """계획을 실행한다. **실행 직전에 하나씩 다시 본다.**

    사람이 그 사이에 메일함을 만졌으면 그건 오류가 아니라 입력이다 — 그 항목만 건너뛴다.
    """
    path = Path(plan_stamp and (_plans_dir() / f"{plan_stamp}.json") or (_latest_plan() or ""))
    if not path.exists():
        print("🔴 계획이 없다. 먼저 plan 을 돌려라.")
        return 1
    rows = json.loads(path.read_text(encoding="utf-8"))

    # 🔴 판정을 건너뛴 계획은 실행하지 않는다. 세고, 말하고, 멈춘다.
    n_nj = sum(1 for r in rows if r.get("why") == NO_JUDGE_WHY)
    if n_nj:
        print(f"🔴 이 계획은 --no-judge 로 만들어졌다 — {n_nj}통이 판정 없이 UNKNOWN 이다.")
        print("   그대로 실행하면 그 메일들이 통째로 #Squad-비플랜 으로 나간다.")
        print("   plan 을 --no-judge 없이 다시 돌려라.")
        return 1

    age_h = (datetime.now(timezone.utc)
             - datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)).total_seconds() / 3600
    print(f"▶ apply {path.stem} · {len(rows)}건 · 계획 {age_h:.1f}시간 전"
          + ("  (dry-run)" if dry_run else "") + ("  🚫 슬랙 보류" if no_slack else ""))

    # 🔴 계획이 오래됐다 = 오늘 plan 이 안 돌았다는 뜻이다. 실행 자체는 안전하다
    #    (항목마다 다시 보므로) 하지만 **오늘 온 메일은 손도 안 댄 상태**다.
    #    조용히 성공으로 끝내면 그 사실이 묻힌다.
    stale = age_h > constraints.get("gmail.plan_stale_hours")

    # ① 존재 확인 — 요청 한 번으로 현재 인박스 번호 전체
    cur = gmail.inbox_ids()
    if not cur.is_measured:
        print(f"🔴 인박스를 못 읽었다: {cur.reason}  ← 대조 없이 실행하지 않는다")
        return 1
    live = set(cur.require())
    if cur.partial:
        print("🔴 인박스 목록이 상한에서 잘렸다 — 대조가 불완전하다. 중단.")
        return 1

    arch_done, draft_done, posted_done = _done()
    tally = Counter()
    skips = Counter()
    fails = []

    if only:
        rows = [r for r in rows if r["action"] == only]
    if limit:
        rows = rows[:limit]

    for r in rows:
        mid, act = r["id"], r["action"]

        if act == "protect":
            tally["protect"] += 1
            continue
        if act == "escalate":
            tally["escalate"] += 1
            continue

        # ③ 장부 — 한 번 한 일은 다시 안 한다
        if act == "archive" and mid in arch_done:
            skips["이미함"] += 1
            continue

        # ① 사람이 먼저 처리했나
        if mid not in live:
            skips["사라짐" if act != "archive" else "이미아카이브"] += 1
            if not dry_run:
                ledger.append(TASK, mid, SKIPPED, why="not_in_inbox", act=act,
                              subject=r["subject"][:80])
            continue

        if act == "archive":
            if dry_run:
                tally["archive(예정)"] += 1
                continue
            res = gmail.archive(mid)
            if not res.is_measured:
                fails.append(f"archive {mid}: {res.reason}")
                continue
            ledger.append(TASK, mid, ARCHIVED, subject=r["subject"][:80],
                          category=r["category"], plan=path.stem)
            tally["archive"] += 1
            continue

        # 초안 — ② 스레드가 그 뒤로 움직였나
        after = gmail.thread_after(r["thread_id"], mid)
        if not after.is_measured:
            fails.append(f"thread {mid}: {after.reason}")
            continue
        if after.require() > 0:
            skips["스레드변함"] += 1
            if not dry_run:
                ledger.append(TASK, mid, SKIPPED, why="thread_moved", act=act,
                              n_after=after.require(), subject=r["subject"][:80])
            continue

        # 🔴 슬랙 게시도 위의 스레드 확인을 통과해야 여기 온다. 일부러 그렇게 뒀다 —
        #    그 뒤로 대화가 진행됐으면 사람이 이미 관여한 것이고, 그런 건에 심사역
        #    판단을 다시 묻는 건 혼란만 준다.
        if act == "slack":
            # 🔴 슬랙은 **밖으로 나가는 유일한 경로**다. 아카이브·초안은 우리 계정 안에서
            #    끝나지만 이건 사람들에게 알림이 간다. 무인으로 도는 동안은 막아둔다.
            #    [사용자 결정 2026-09-02] 판정이 일주일 맞는 걸 보고 연다.
            #    막았다고 성공으로 세지 않는다 — 따로 세고, 메일은 인박스에 남는다.
            if no_slack:
                tally["slack(보류 · 인박스에 남김)"] += 1
                continue
            if mid in posted_done:
                skips["이미함"] += 1
                continue
            if dry_run:
                tally["slack(예정)"] += 1
                continue
            res = slack.post(r["ch"], r["text"])
            if not res.is_measured:
                fails.append(f"slack {mid}: {res.reason}")
                continue
            # 🔴 착지 먼저, 장부 나중. ts 를 못 남기면 반응을 영영 못 찾는다.
            ts = res.require()
            ledger.append(TASK, mid, POSTED, ch=r["ch"], ts=ts,
                          kind=r.get("kind", ""), category=r["category"],
                          day=ledger.today(), subject=r["subject"][:80])
            tally["slack"] += 1

            # 메일 전문을 스레드에 붙인다. 맨 위는 훑는 자리, 스레드는 읽는 자리다.
            # 🔴 장부를 남긴 **뒤에** 한다. 여기서 실패해도 반응 수거는 살아 있다 —
            #    ts 가 이미 남았기 때문이다. 실패는 세되 게시를 무르지 않는다.
            got = gmail.message(mid)
            if not got.is_measured:
                fails.append(f"전문 재읽기 {mid}: {got.reason}")
                continue
            parts = route.body_chunks(got.require())
            parts.append(f"*메일함에서 보기*\n{route.mail_link(mid)}")
            for chunk in parts:
                sub = slack.post(r["ch"], chunk, thread_ts=ts)
                if not sub.is_measured:
                    fails.append(f"전문 스레드 {mid}: {sub.reason}")
                    break
            continue

        jobs = _draft_jobs(r)
        if jobs and not dry_run:
            got = gmail.message(mid)          # 인용문·헤더는 지금 다시 읽는다
            if not got.is_measured:
                fails.append(f"reread {mid}: {got.reason}")
                continue
            msg = got.require()

        for kind, to, body in jobs:
            if (mid, kind) in draft_done:
                skips["이미함"] += 1
                continue
            if dry_run:
                tally[f"{kind}(예정)"] += 1
                continue
            if kind == "reply":
                res = gmail.draft_reply(msg, body, to, subject=r.get("draft_subject"))
            else:
                res = gmail.draft_forward(msg, body, to)
            if not res.is_measured:
                fails.append(f"{kind} {mid}: {res.reason}")
                continue
            ledger.append(TASK, mid, DRAFTED, kind=kind, draft_id=res.require(), to=to,
                          category=r["category"], plan=path.stem, subject=r["subject"][:80])
            tally[kind] += 1

    print("\n" + "=" * 70)
    for k, v in tally.most_common():
        print(f"  ✅ {k:16} {v}")
    for k, v in skips.most_common():
        print(f"  ⏭️  건너뜀({k:8}) {v}")
    for f in fails:
        print(f"  🔴 {f}")
    print("=" * 70)

    if fails:
        raise Degraded(f"실패 {len(fails)}건")
    if stale:
        raise Degraded(f"계획이 {age_h:.0f}시간 전 것이다 — 오늘 plan 이 안 돌았을 수 있다")
    return 0


def _draft_jobs(r: dict):
    """이 항목이 만들 초안 목록. 제휴는 포워드 + 거절 회신 **둘 다**."""
    out = []
    if r["action"] in ("reply", "forward") and r.get("to"):
        out.append((r["action"], r["to"], r.get("body", "")))
    also = r.get("also_reply")
    if also and also.get("to"):
        out.append(("reply", also["to"], also.get("body", "")))
    return out


def _days_since(day: str) -> int:
    """게시일(KST)에서 오늘까지 며칠. 🔴 만기를 저장하지 않고 매번 센다 —
    절대시각을 저장하면 표준시 실수가 조용히 며칠을 밀어낸다."""
    y, m, d = (int(x) for x in day.split("-"))
    t = ledger.today().split("-")
    return (date(int(t[0]), int(t[1]), int(t[2])) - date(y, m, d)).days


def followup(*, dry_run: bool = False) -> int:
    """슬랙에 올려둔 건의 반응을 수거하고, 만기가 된 건을 처리한다.

    🔴 이건 우리 스킬 중 **시간이 지나야 발동하는 유일한 종류**다.
       다른 명령은 전부 "지금 인박스를 보고 지금 판단"인데, 이건 과거에 올린 것을 되돌아본다.
    """
    print("▶ followup")
    open_posts = {}
    for r in ledger.rows(TASK, ev=POSTED):
        open_posts[r["key"]] = r
    for r in ledger.rows(TASK, ev=RESOLVED):
        open_posts.pop(r["key"], None)
    warned = {r["key"] for r in ledger.rows(TASK, ev=WARNED)}
    if not open_posts:
        print("  · 기다리는 건이 없다")
        return 0
    print(f"  · 반응을 기다리는 건 {len(open_posts)}개")

    yes = set(constraints.get("slack.reaction_yes"))
    no = set(constraints.get("slack.reaction_no"))
    due_d = constraints.get("slack.followup_days")
    warn_d = constraints.get("slack.followup_warn_days")
    stale_d = constraints.get("slack.followup_unreachable_days")

    tally, fails = Counter(), []
    for mid, row in open_posts.items():
        age = _days_since(row.get("day") or ledger.today())
        kind = row.get("kind", "")
        got = slack.reactions(row["ch"], row["ts"])
        if not got.is_measured:
            # 🔴 미상은 만기 시계를 멈춘다 — "반응이 없다"가 아니라 "못 읽었다"이기 때문이다.
            #    메시지가 지워지거나 장부의 ch 가 실제와 어긋나면 그 대기는 영원해진다.
            #    실측 08-18: ch 를 bplan 으로 적었는데 메시지는 test 에 있었고, 13일간
            #    매 10분 종료코드 1 이 났다. 짧은 망 오류는 계속 실패로 세되,
            #    이만큼 오래 미상이면 사람에게 보이는 자리에 남기고 대기에서 뺀다.
            if age >= stale_d:
                _resolve(mid, row, "unreachable", archive=False, draft=None, dry_run=dry_run)
                tally[f"🔇 {stale_d}일 넘게 반응을 못 읽음 → 대기 해제"] += 1
            else:
                fails.append(f"reactions {mid}: {got.reason}")
            continue
        names = {x.get("name") for x in got.require()}

        if names & no:
            _resolve(mid, row, "reaction_no", archive=True, draft=None, dry_run=dry_run)
            tally["❌ 거절 → 아카이브"] += 1
        elif names & yes:
            # ⭕ 는 "받자" — 신뢰 경로면 폼 생략 접수 안내를 만든다
            tpl = "trusted_direct_ko" if kind == "trusted_ref" else None
            _resolve(mid, row, "reaction_yes", archive=False, draft=tpl, dry_run=dry_run)
            tally["⭕ 승인" + ("(접수 안내 초안)" if tpl else "")] += 1
        elif age >= due_d:
            # 🔴 만기는 **모든 kind 에 있다.** 나가는 글만 갈린다.
            #    전에는 trusted_ref 에만 있어서 event 같은 건 무한 대기였다 —
            #    08-18 의 강연요청 두 건이 그래서 13일째 "대기 N일차" 로 남아 있었다.
            tpl = _EXPIRY_DECLINE.get(kind)
            if tpl:
                # 거절은 **초안**이고 보내는 건 사람이다.
                _resolve(mid, row, "expiry", archive=True, draft=tpl, dry_run=dry_run)
                tally[f"⏰ {due_d}일 무반응 → 거절 초안 + 아카이브"] += 1
            else:
                # 슬랙에 이미 올라가 사람이 봤다. 우리가 더 할 일은 없으니 대기만 푼다 —
                # 인박스도 초안도 건드리지 않는다.
                _resolve(mid, row, "expiry", archive=False, draft=None, dry_run=dry_run)
                tally[f"⏰ {due_d}일 무반응 → 대기 해제(인박스 그대로)"] += 1
        elif kind in _EXPIRY_DECLINE and age >= warn_d and mid not in warned:
            if not dry_run:
                r2 = slack.post(row["ch"], f":alarm_clock: 내일 자동으로 거절 초안이 생깁니다. "
                                           f"({due_d}일 무반응) — {row.get('subject', '')[:60]}",
                                thread_ts=row["ts"])
                if r2.is_measured:
                    ledger.append(TASK, mid, WARNED, ts=row["ts"], ch=row["ch"])
            tally[f"⚠️ {warn_d}일차 예고"] += 1
        else:
            tally[f"… 대기 {age}일차"] += 1

    print("\n" + "=" * 70)
    for k, v in tally.most_common():
        print(f"  {k}  {v}")
    for f in fails:
        print(f"  🔴 {f}")
    print("=" * 70)
    if fails:
        raise Degraded(f"반응 조회 실패 {len(fails)}건")
    return 0


# 무반응 만기에 **어떤 거절문**을 초안으로 만드나. 여기 없는 kind 는 초안 없이 대기만 푼다.
#
# 🔴 종류마다 글이 달라야 한다. 옛 코드는 전부 `decline_ko` 였는데 그건 **투자 거절문**이다
#    ("본 투자 건에 대한 추가 검토 진행은 힘들 것으로"). 행사 초청에 그 글이 나가면
#    묻지도 않은 답이 된다 — 그쪽은 돈을 달라고 한 게 아니라 와달라고 한 것이다.
#    [사용자 결정 2026-09-02] event 도 만기에 거절 초안을 만든다. 실제로 그런 건이 있었다:
#    이스트포인트 라운드테이블 — 정현님이 비플랜으로 두 번 포워드했는데 반응이 없었다.
_EXPIRY_DECLINE = {
    "trusted_ref": "decline_ko",
    "event": "decline_event_ko",
}


def _resolve(mid: str, row: dict, by: str, *, archive: bool, draft: str | None,
             dry_run: bool) -> None:
    """만기·반응으로 결론난 건을 마무리한다. **실행 먼저, 장부 나중.**"""
    if dry_run:
        return
    if draft:
        got = gmail.message(mid)
        if got.is_measured:
            msg = got.require()
            # format 은 안 쓰는 인자를 무시하므로 템플릿마다 분기할 필요가 없다
            res = gmail.draft_reply(msg, route.tpl(draft, google_form=route.GOOGLE_FORM),
                                    msg["sender"])
            if res.is_measured:
                ledger.append(TASK, mid, DRAFTED, kind="reply", draft_id=res.require(),
                              to=msg["sender"], via=by, template=draft)
    if archive:
        res = gmail.archive(mid)
        if res.is_measured:
            ledger.append(TASK, mid, ARCHIVED, via=by, subject=row.get("subject", "")[:80])
    ledger.append(TASK, mid, RESOLVED, by=by, ts=row["ts"], ch=row["ch"],
                  kind=row.get("kind", ""))


def status() -> int:
    d = _plans_dir()
    files = sorted(d.glob("*.json"))
    if not files:
        print("계획 이력이 없다.")
        return 0
    print(f"계획 {len(files)}개 · 최근:")
    for p in files[-5:]:
        rows = json.loads(p.read_text(encoding="utf-8"))
        c = Counter(r["action"] for r in rows)
        print(f"  {p.stem}  {len(rows):3}통  " + " ".join(f"{_ICON[k]}{c.get(k, 0)}" for k in _ICON))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mail")
    sub = p.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("plan", help="분류 계획을 만든다 (인박스를 건드리지 않는다)")
    q.add_argument("--days", type=int, default=None)
    q.add_argument("--limit", type=int, default=None)
    q.add_argument("--no-judge", action="store_true", help="claude 를 안 부른다 (규칙만)")
    w = sub.add_parser("apply", help="계획을 실행한다 (실행 직전에 하나씩 다시 본다)")
    w.add_argument("--plan", default=None, help="특정 계획 (기본: 최신)")
    w.add_argument("--dry-run", action="store_true")
    w.add_argument("--limit", type=int, default=None)
    w.add_argument("--only", choices=["archive", "reply", "forward", "slack"], default=None)
    w.add_argument("--no-slack", action="store_true",
                   help="슬랙 게시를 건너뛴다 (메일은 인박스에 남는다)")
    fu = sub.add_parser("followup", help="슬랙 반응 수거 · 만기 처리")
    fu.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    ob = sub.add_parser("observe", help="관찰 — 모으고 분류하고 기록한다. 밖으로 나가는 건 mail_observe.outbound 문이 정한다(기본 off)")
    ob.add_argument("--op", default="run",
                    choices=["run", "status", "report", "replay", "diff", "label", "decide", "backup", "restore-test", "health"])
    ob.add_argument("--decision", choices=["yes", "no"], default=None)
    ob.add_argument("--days", type=int, default=None, help="이만큼 거슬러 수집 (첫 실행·밀린 날)")
    ob.add_argument("--no-judge", action="store_true")
    ob.add_argument("--criteria", default=None, help="replay 에 쓸 기준 파일 (기본: classify_criteria.md)")
    ob.add_argument("--limit", type=int, default=None)
    ob.add_argument("--a", default="live")
    ob.add_argument("--b", default="legacy")
    ob.add_argument("--id", default=None)
    ob.add_argument("--category", default=None)
    ob.add_argument("--why", default=None)
    a = p.parse_args(argv)

    try:
        if a.cmd == "plan":
            with lock.hold("mail-plan"):
                return plan(days=a.days, limit=a.limit, no_judge=a.no_judge)
        if a.cmd == "apply":
            with lock.hold("mail-apply"):
                return apply(plan_stamp=a.plan, dry_run=a.dry_run, limit=a.limit,
                             only=a.only, no_slack=a.no_slack)
        if a.cmd == "followup":
            with lock.hold("mail-followup"):
                return followup(dry_run=a.dry_run)
        if a.cmd == "status":
            return status()
        if a.cmd == "observe":
            # 🔴 여기서 import 한다. 새 코드가 import 단계에서 죽어도 09:10 plan 은 산다.
            from . import observe
            return observe.main(a)
    except lock.Busy as e:
        print(f"🔴 {e}")
        return 3
    except Degraded as e:
        print(f"🔴 온전하지 않다: {e}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
