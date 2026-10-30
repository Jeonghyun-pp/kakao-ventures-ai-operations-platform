"""비플랜 블라인드 라벨링 (2026-10-14) — 정답이 모자라서 사람이 매일 조금씩 단다.

    send     시트 접수(bplan_label.since 이후)에서 **고정된 무작위 순서**로 아직 안 보낸 N건 → review_test 카드 N장
             카드 = 기업명·접수일·서비스(폼 1번)·팀(폼 6번) 원문 + 덱 링크. 나머지 폼 칸은 스레드에 원문 그대로
             🔴 모델 점수·요약은 싣지 않는다 — 라벨이 모델에 끌려가면 모델을 재는 자로 못 쓴다
    collect  카드의 반응(⭕ 볼만 · ❌ 아님 · 🤔 애매) + 스레드에 사람이 쓴 이유 한 줄 → blind_labels
    report   지금까지 라벨 분포 · 노션 to do(기존 정답)와 대조 — 기존 드랍 중 볼만이었던 비율이 기존 라벨의 신뢰도다

왜 무작위인가: 노션 to do 의 keep 2% 는 실제 비율이 아니라 "누가 옮긴 비율"이다(인턴 일괄 드랍·덜 본 건이 섞였을 수 있다).
첫 100건은 순수 무작위로 실제 볼만 비율부터 잰다. 표본을 기울일지는 그 다음에 정한다.

문은 `bplan_label.outbound`(kvlib.gate) — 비플랜 본 문(bplan.outbound)과 따로다. 채널은 review_test 하나뿐이다(코드에 고정).
노션·메일에는 아무것도 쓰지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import timedelta

from kvlib import constraints, ledger
from kvlib.gate import Gate
from kvlib.text import clip
from kv_transport import slack

CH = "test"                                  # 🔴 review_test 하나. 라벨 카드는 심사역 채널로 가지 않는다
TASK = "bplan_label"
KINDS = ("label_card", "label_form")          # 슬랙 글 — slack 단계에서 실제로 나가는 종류(허용 목록)
SCHEMA = """
CREATE TABLE IF NOT EXISTS blind_labels(
  key TEXT PRIMARY KEY, verdict TEXT, reason TEXT, users TEXT, card_ts TEXT, created TEXT, updated TEXT);
"""
FORM = (("문제정의", "2. 문제"), ("솔루션", "3. 솔루션"), ("비즈니스모델", "4. BM"), ("경쟁사", "5. 경쟁사·경쟁력"),
        ("경쟁력", "7. 왜 우리 팀"), ("추가내용", "추가 내용"))


def _cfg(k: str):
    return constraints.get(f"{TASK}.{k}")


def gate(con, *, dry_run: bool = False) -> Gate:
    con.executescript(SCHEMA)
    return Gate(con, TASK, lambda k: k in KINDS, force="off" if dry_run else None)


def order(key: str) -> str:
    """고정된 무작위 순서. 키의 해시라 실행마다·기계마다 같다 — 새 접수가 들어와도 이미 정한 순서가 안 밀린다."""
    return hashlib.sha256(f"{_cfg('salt')}|{key}".encode()).hexdigest()


def pick(subs: list[dict], sent: set[str], n: int) -> list[dict]:
    pool = [s for s in subs if s["ts"] >= _cfg("since") and s["key"] not in sent]
    return sorted(pool, key=lambda s: order(s["key"]))[:n]


def card_text(s: dict) -> str:
    p = s["props"]
    one = lambda k, cap: clip((p.get(k) or "").strip() or "(빈 칸)", cap)[0].replace("\n", " ")
    deck = p.get("사업계획서") or ""
    return (f"*{p.get('기업명') or '(기업명 없음)'}* · 접수 {s['ts'][:10]}\n"
            f"서비스: {one('서비스정의', 200)}\n"
            f"팀: {one('팀이력', 300)}\n"
            + (f"<{deck}|덱 열기> · " if deck else "덱 없음 · ")
            + "⭕ 볼만  ❌ 아님  🤔 애매 — 이유는 스레드에 한 줄(선택)")


def form_text(s: dict) -> str:
    p = s["props"]
    return "\n\n".join(f"*{label}*\n{clip((p.get(k) or '').strip() or '(빈 칸)', 1500)[0]}" for k, label in FORM)


def send(g: Gate, subs: list[dict], *, n: int | None = None) -> dict:
    """오늘 몫 N건. 이미 보낸 건(card done)은 다시 고르지 않는다 — 실패한 건은 다음 실행이 다시 고른다."""
    sent = {k for (k,) in g.con.execute("SELECT key FROM actions WHERE kind='label_card' AND status='done'")}
    out = Counter()
    for s in pick(subs, sent, n or _cfg("per_day")):
        st = g.out(s["key"], "label_card", {"company": s["props"].get("기업명")},
                   lambda s=s: slack.post(CH, card_text(s)).require())
        out[st] += 1
        card = g.row(s["key"], "label_card")
        if st == "done" and card:
            g.out(s["key"], "label_form", {}, lambda ts=card["result"], s=s: slack.post(CH, form_text(s), thread_ts=ts).require())
    return dict(out)


def decide(names: set[str]) -> str | None:
    """반응 이름 → 라벨. 서로 다른 라벨이 둘 이상 달려 있으면(⭕ 과 ❌) 사람이 헷갈린 것이라 '애매'."""
    v = {lab for lab in ("yes", "no", "maybe") if names & set(_cfg(f"reaction_{lab}"))}
    if not v:
        return None
    return v.pop() if len(v) == 1 else "maybe"


def collect(g: Gate, *, reactions=slack.reactions, replies=slack.replies) -> dict:
    """라벨 안 된 카드 + 라벨 뒤 reason_days 안의 카드(이유를 늦게 다는 경우)만 읽는다. 사람이 쓴 답글만 이유로 — 봇의 폼 답글은 뺀다."""
    g.con.executescript(SCHEMA)
    cards = [(k, json.loads(d)["result"]) for k, d in g.con.execute(
        "SELECT key, detail FROM actions WHERE kind='label_card' AND status='done'")]
    settled = ledger.now(timedelta(days=_cfg("reason_days")))
    first = dict(g.con.execute("SELECT key, created FROM blind_labels"))
    n = Counter()
    for key, ts in cards:
        if key in first and first[key] < settled:                    # 라벨 뒤 reason_days 가 지나면 더 안 읽는다
            continue
        r = reactions(CH, ts)
        if not r.is_measured:
            n["unread"] += 1
            continue
        names = {x.get("name", "").split("::")[0] for x in r.require()}
        users = sorted({u for x in r.require() for u in (x.get("users") or [])})
        verdict = decide(names)
        if not verdict:
            n["waiting"] += 1
            continue
        rp = replies(CH, ts)
        reason = " / ".join(m.get("text", "").strip() for m in (rp.require() if rp.is_measured else [])
                            if not m.get("bot_id") and m.get("text", "").strip())
        now = ledger.now()
        g.con.execute("INSERT INTO blind_labels VALUES(?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                      "verdict=excluded.verdict, reason=excluded.reason, users=excluded.users, updated=excluded.updated",
                      (key, verdict, reason, json.dumps(users), ts, now, now))
        n[verdict] += 1
    g.con.commit()
    return dict(n)


def report(con, notion_todo: dict[str, str] | None = None) -> str:
    """분포 + 기존 노션 to do 와의 대조. notion_todo = {접수키: to do}."""
    con.executescript(SCHEMA)
    rows = con.execute("SELECT key, verdict, reason FROM blind_labels").fetchall()
    sent = con.execute("SELECT count(*) FROM actions WHERE kind='label_card' AND status='done'").fetchone()[0]
    c = Counter(v for _, v, _ in rows)
    total = len(rows)
    out = [f"# 비플랜 블라인드 라벨 — {ledger.now()[:10]}", "",
           f"보낸 카드 {sent} · 라벨 {total} (⭕ 볼만 {c['yes']} · ❌ 아님 {c['no']} · 🤔 애매 {c['maybe']})"]
    if total:
        out.append(f"볼만 비율 {c['yes'] / total:.1%} · 볼만+애매 {(c['yes'] + c['maybe']) / total:.1%}")
    if notion_todo:
        x = Counter((notion_todo.get(k) or "노션에 없음", v) for k, v, _ in rows)
        dropped = [(k, r) for k, v, r in rows if v in ("yes", "maybe") and notion_todo.get(k) == "To Drop"]
        out += ["", "## 기존 노션 to do 와 대조", "", "| 노션 to do | ⭕ | ❌ | 🤔 |", "|---|---|---|---|"]
        for todo in sorted({t for t, _ in x}):
            out.append(f"| {todo} | {x[(todo, 'yes')]} | {x[(todo, 'no')]} | {x[(todo, 'maybe')]} |")
        out += ["", f"🚩 노션에서 To Drop 인데 볼만·애매로 단 건 {len(dropped)}"] + [f"- {k} — {r or '(이유 없음)'}" for k, r in dropped]
    reasons = [(k, v, r) for k, v, r in rows if r]
    if reasons:
        out += ["", "## 이유", ""] + [f"- {v} · {k.split('|')[0]} — {r}" for k, v, r in reasons]
    return "\n".join(out) + "\n"
