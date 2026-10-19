"""사람이 메일함에서 한 일을 되읽어 정답으로 — 2026-10-01 [사용자 결정: 표 비교 대신 메일함에서 처리하고 나중에 한 번에 대조].

메일함에 쓰지 않는다. 판정된 메일마다 지금 상태(라벨·휴지통·스레드 뒤의 보낸 메일)를 읽어 사람의 **행동**을 알아내고,
모델이 했을 행동과 맞으면 그 카테고리를 정답으로 적는다. 행동은 알아도 카테고리는 모르는 경우(치웠는데 모델은 회신이라 했다 등)는 적지 않고 **사람에게 묻는 목록**으로 낸다.

  사람 행동     trash(휴지통) · reply(스레드 뒤에 우리가 보낸 메일) · forward(그 보낸 메일이 Fwd) · label:<카테고리>(카테고리 이름 라벨) · archive(받은편지함에서 빠짐) · none
  모델 행동     route.decide 의 행동 — archive · reply · handoff(forward·slack) · protect 는 대조하지 않는다
  일치          archive/trash↔archive · reply↔reply · forward↔handoff → 정답 = 모델 카테고리 · label:<카테고리> → 정답 = 그 카테고리(행동과 무관하게 가장 세다)
  불일치        묻는다. none 은 보류(아직 안 봤다).
"""
from __future__ import annotations

from . import route, rules

SKIP = {rules.BPLAN_INTAKE, rules.INTERNAL, rules.WEBFLOW}
# 🔴 모델 행동은 route.decide 에서 끌어온다 — 여기 따로 적어 두면 기준이 바뀔 때 둘이 갈린다(10-08 결정이 그렇게 어긋났다).
_AS = {"archive": "archive", "reply": "reply", "forward": "handoff", "slack": "handoff", "fwd_mail": "handoff", "intake": "handoff"}


def model_action(category: str) -> str:
    if category in SKIP:
        return "skip"
    act = route.decide({"sender": "", "subject": "", "body": ""}, {"category": category, "why": ""})["action"]
    return _AS.get(act, "skip")                                       # protect(옛 어휘 등) — 손대지 않을 메일이라 대조하지 않는다


def human_action(labels: list[str], names: dict[str, str], tail: list[dict], subject_of) -> tuple[str, str]:
    """(행동, 근거). names = 라벨 id → 이름. tail = 스레드에서 그 메시지 뒤의 메시지들. subject_of(id) → 제목(보낸 메일이 Fwd 인지 볼 때만 부른다)."""
    user = [names.get(l, "") for l in labels if l.startswith("Label_")]
    cat = next((n.split("/")[-1].upper() for n in user if n.split("/")[-1].upper() in rules.JUDGE_CATEGORIES), None)
    if cat:
        return f"label:{cat}", f"라벨 {cat}"
    if "TRASH" in labels:
        return "trash", "휴지통"
    sent = [m["id"] for m in tail if "SENT" in m.get("labels", [])]
    if sent:
        subj = (subject_of(sent[-1]) or "").lower()
        return ("forward", "우리가 전달함") if subj.startswith(("fwd:", "fw:", "전달:")) else ("reply", "우리가 답장함")
    if "INBOX" not in labels:
        return "archive", "받은편지함에서 빠짐"
    return "none", "그대로"


def scan(con, gmail) -> list[dict]:
    """판정된 받은 메일 전부. 통당 GET 하나(+ 스레드 하나). 반환 행: {id, subject, category, model, human, why, verdict, answer}"""
    names = gmail.labels().or_default({})
    by_id = {v: k for k, v in names.items()}
    rows = con.execute("SELECT j.message_id, j.category, m.subject, m.thread_id FROM judgments j JOIN messages m ON m.id=j.message_id"
                       " WHERE j.run='live' AND j.ok=1 AND m.direction='in' ORDER BY m.internal_ms").fetchall()
    out = []
    for mid, cat, subject, tid in rows:
        if cat in SKIP:
            continue
        h = gmail.headers_of(mid, ["Subject"])
        if not h.is_measured:
            out.append({"id": mid, "subject": subject, "category": cat, "model": model_action(cat), "human": "unread", "why": h.reason[:80], "verdict": "unread", "answer": None})
            continue
        tail = gmail.thread_tail(tid or h.meta.get("thread_id", ""), mid).or_default([])
        act, why = human_action(h.meta.get("labels", []), by_id, tail, lambda i: (gmail.headers_of(i, ["Subject"]).or_default({}) or {}).get("Subject", ""))
        m = model_action(cat)
        if act.startswith("label:"):
            verdict, answer = "exact", act[6:]
        elif act == "none":
            verdict, answer = "pending", None
        elif (act in ("archive", "trash") and m == "archive") or (act == "reply" and m == "reply") or (act == "forward" and m == "handoff"):
            verdict, answer = "agree", cat
        else:
            verdict, answer = "ask", None
        out.append({"id": mid, "subject": subject, "category": cat, "model": m, "human": act, "why": why, "verdict": verdict, "answer": answer})
    return out


def apply(con, rows: list[dict], save_human) -> int:
    """일치·정확한 것만 정답으로 적는다. 이미 같은 정답이 있으면 건너뛴다."""
    n = 0
    for r in rows:
        if r["answer"] is None:
            continue
        prev = con.execute("SELECT category FROM judgments WHERE message_id=? AND run='human'", (r["id"],)).fetchone()
        if prev and prev[0] == r["answer"]:
            continue
        save_human(r["id"], r["answer"], f"메일함 대조 {r['human']} ↔ 모델 {r['model']}({r['category']}) · {r['why']}")
        n += 1
    return n


def report(rows: list[dict]) -> str:
    from collections import Counter
    c = Counter(r["verdict"] for r in rows)
    md = [f"# 메일함 대조 — 판정 {len(rows)}통: 일치 {c['agree']} · 라벨로 확정 {c['exact']} · 물을 것 {c['ask']} · 아직 안 봄 {c['pending']} · 못 읽음 {c['unread']}", ""]
    md += ["## 물을 것 (사람 행동과 모델 판정이 다르다)", "", "| 메일 | 모델 | 사람 | id |", "|---|---|---|---|"]
    md += [f"| {r['subject'][:50]} | {r['category']} ({r['model']}) | {r['human']} — {r['why']} | `{r['id']}` |" for r in rows if r["verdict"] == "ask"] or ["없음"]
    md += ["", "## 일치 (정답으로 적음)", "", "| 메일 | 카테고리 | 사람 |", "|---|---|---|"]
    md += [f"| {r['subject'][:50]} | {r['answer']} | {r['human']} |" for r in rows if r["verdict"] in ("agree", "exact")] or ["없음"]
    md += ["", "## 아직 안 봄", ""] + [f"- {r['subject'][:60]} ({r['category']})" for r in rows if r["verdict"] == "pending"]
    return "\n".join(md)
