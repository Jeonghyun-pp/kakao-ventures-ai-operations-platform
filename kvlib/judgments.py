"""판정 저장 — 한 건 한 번, 모델이 본 글과 낸 값을 그대로. 기준을 고치면 replay → diff 로 잰다.

    live      운영이 실제로 쓴 판정
    human     사람의 결정 — 노션 칸에서 읽어 온다 (label). 사람이 정답을 따로 달지 않는다
    r-<sha>   고친 기준으로 다시 판정한 것. 저장된 입력만 다시 쓴다 — 바깥 호출 0

스킬마다 `verdict` 의 어휘가 다르다(링크드인 Tier · 비플랜 점수). 표는 하나다.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable

from . import ledger

SCHEMA = """
CREATE TABLE IF NOT EXISTS judgments(
  key TEXT, run TEXT, criteria_sha TEXT, input_text TEXT, raw_output TEXT,
  verdict TEXT, reason TEXT, ok INTEGER, error TEXT, page_id TEXT, created TEXT,
  PRIMARY KEY(key, run));
"""
LIVE, HUMAN = "live", "human"
_COLS = "key,run,criteria_sha,input_text,raw_output,verdict,reason,ok,error,page_id,created"


def criteria_sha(*parts: str) -> str:
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()[:12]


def save(con, run: str, sha: str, lines: dict[str, str], verdicts: list[dict], *, extra: dict[str, dict] | None = None) -> None:
    """`lines` = {key: 모델이 본 글}. 판정을 못 받은 key 도 ok=0 으로 남긴다 — 입력은 값이 있다.
    `verdicts` 항목 = {key, verdict, reason, raw}. `extra[key]` 는 reason 뒤에 붙일 부가 출력(카드 문안 등)을 JSON 으로."""
    import json
    got = {v["key"]: v for v in verdicts}
    for key, text in lines.items():
        v = got.get(key)
        reason = v.get("reason") if v else None
        if v and extra and extra.get(key):
            reason = json.dumps({"reason": reason, **extra[key]}, ensure_ascii=False)
        con.execute(
            f"INSERT OR REPLACE INTO judgments({_COLS}) VALUES(?,?,?,?,?,?,?,?,?,(SELECT page_id FROM judgments WHERE key=? AND run=?),?)",
            (key, run, sha, text, v.get("raw") if v else None, v.get("verdict") if v else None, reason,
             1 if v else 0, None if v else "판정 누락", key, run, ledger.now()))


def set_page(con, key: str, page_id: str) -> None:
    con.execute("UPDATE judgments SET page_id=? WHERE key=? AND run=?", (page_id, key, LIVE))


def label(con, rows: list[dict], prop: str, to_label: Callable[[str], str | None] = lambda v: v,
          same: Callable[[str, str], bool] = lambda live, human: live == human) -> int:
    """노션 행의 `prop` 값이 사람의 결정이다. live 와 다르면 human 으로 적고, 같아지면 지운다.
    `to_label` 은 칸 값을 정답 어휘로(None 이면 미결 — 건드리지 않는다). `same` 은 live 판정과 정답이 같은가.
    돌려주는 값 = 지금 갈린 건수."""
    live = {pid: (key, verdict) for key, verdict, pid in con.execute(
        "SELECT key, verdict, page_id FROM judgments WHERE run=? AND ok=1 AND page_id IS NOT NULL", (LIVE,))}
    n = 0
    for row in rows:
        hit = live.get(row.get("_page_id"))
        if not hit:
            continue
        human = to_label(row.get(prop)) if row.get(prop) else None
        if human is None:
            continue
        key, verdict = hit
        if not same(verdict, human):
            con.execute(f"INSERT OR REPLACE INTO judgments({_COLS}) VALUES(?,?,'','','',?,?,1,NULL,?,?)",
                        (key, HUMAN, human, f"노션 {prop}", row["_page_id"], ledger.now()))
            n += 1
        else:
            con.execute("DELETE FROM judgments WHERE key=? AND run=?", (key, HUMAN))
    return n


def replay(con, judge_lines, criteria: str, sha: str, *, limit: int = -1) -> dict:
    """live 에 있고 r-<sha> 에 없는 건의 **저장된 입력**으로 다시 판정한다.
    `judge_lines(lines, keys, criteria) -> Fetched[list[verdict]]` — keys 는 lines 와 **같은 순서**의 목록이다(키가 줄 안에 없는 스킬을 위해).
    저장된 입력에는 기준 본문이 없다 — 기준은 부를 때 붙인다. 그래서 고친 기준으로 다시 잴 수 있다."""
    run = f"r-{sha}"
    rows = con.execute("SELECT key, input_text FROM judgments j WHERE run=? AND ok=1 AND input_text!=''"
                       " AND NOT EXISTS(SELECT 1 FROM judgments r WHERE r.key=j.key AND r.run=?) ORDER BY created DESC LIMIT ?",
                       (LIVE, run, limit)).fetchall()
    lines = {r[0]: r[1] for r in rows}
    if not lines:
        return {"run": run, "judged": 0, "missing": 0}
    f = judge_lines(list(lines.values()), list(lines), criteria)
    verdicts = f.require() if f.is_measured else []
    save(con, run, sha, lines, verdicts)
    return {"run": run, "judged": len(verdicts), "missing": len(lines) - len(verdicts),
            "error": None if f.is_measured else f.reason}


def diff(con, a: str, b: str, bucket: Callable[[str], str] = lambda v: v) -> list[dict]:
    """두 run 을 나란히 + 사람 정답. `bucket` 으로 어휘를 정답 어휘에 맞춘다(비플랜: 점수 → drop/keep)."""
    out = []
    for key, va, vb, vh in con.execute(
            "SELECT ja.key, ja.verdict, jb.verdict, jh.verdict FROM judgments ja"
            " JOIN judgments jb ON jb.key=ja.key AND jb.run=? AND jb.ok=1"
            " LEFT JOIN judgments jh ON jh.key=ja.key AND jh.run=?"
            " WHERE ja.run=? AND ja.ok=1 ORDER BY ja.created DESC", (b, HUMAN, a)):
        out.append({"key": key, "a": bucket(va), "b": bucket(vb), "human": vh, "raw_a": va, "raw_b": vb})
    return out
