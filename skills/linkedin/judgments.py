"""판정 저장 — 한 사람 한 번, 모델이 본 줄과 낸 줄을 그대로. 기준을 고치면 replay → diff 로 잰다.

    live      collect 가 실제로 쓴 판정
    human     노션 Tier 칸 — 사람이 고친 것이 곧 정답이다 (label 이 동기화한다)
    r-<sha>   고친 기준으로 다시 판정한 것 (Apify 0 · 노션 0 — 저장된 입력 줄만 다시 묶는다)
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS judgments(
  key TEXT, run TEXT, criteria_sha TEXT, input_text TEXT, raw_output TEXT,
  tier TEXT, reason TEXT, ok INTEGER, error TEXT, page_id TEXT, created TEXT,
  PRIMARY KEY(key, run));
"""
LIVE, HUMAN = "live", "human"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def criteria_sha(system: str, criteria: str) -> str:
    return hashlib.sha256((system + criteria).encode("utf-8")).hexdigest()[:12]


def save(con, run: str, sha: str, lines: dict[str, str], verdicts: list[dict]) -> None:
    """`lines` = {key: 모델이 본 줄}. 판정을 못 받은 key 는 ok=0 으로 남긴다 — 돈 주고 산 사람이다."""
    got = {v["key"]: v for v in verdicts}
    for key, text in lines.items():
        v = got.get(key)
        con.execute(
            "INSERT OR REPLACE INTO judgments(key,run,criteria_sha,input_text,raw_output,tier,reason,ok,error,page_id,created)"
            " VALUES(?,?,?,?,?,?,?,?,?,(SELECT page_id FROM judgments WHERE key=? AND run=?),?)",
            (key, run, sha, text, v.get("raw") if v else None, v.get("tier") if v else None, v.get("reason") if v else None,
             1 if v else 0, None if v else "판정 누락", key, run, _now()))


def set_page(con, key: str, page_id: str) -> None:
    con.execute("UPDATE judgments SET page_id=? WHERE key=? AND run=?", (page_id, key, LIVE))


def label(con, notion_rows: list[dict]) -> int:
    """노션 Tier ≠ live 판정이면 human 으로 적는다(같아졌으면 지운다). 돌려주는 값 = 지금 갈린 사람 수."""
    live = {pid: (key, tier) for key, tier, pid in con.execute(
        "SELECT key, tier, page_id FROM judgments WHERE run=? AND ok=1 AND page_id IS NOT NULL", (LIVE,))}
    n = 0
    for row in notion_rows:
        hit = live.get(row.get("_page_id"))
        if not hit or not row.get("Tier"):
            continue
        key, tier = hit
        if row["Tier"] != tier:
            con.execute("INSERT OR REPLACE INTO judgments(key,run,criteria_sha,input_text,raw_output,tier,reason,ok,error,page_id,created)"
                        " VALUES(?,?,'','','',?,?,1,NULL,?,?)", (key, HUMAN, row["Tier"], "노션 Tier", row["_page_id"], _now()))
            n += 1
        else:
            con.execute("DELETE FROM judgments WHERE key=? AND run=?", (key, HUMAN))
    return n


def replay(con, judge_lines, criteria: str, sha: str, *, limit: int = -1) -> dict:
    """live 에 있고 r-<sha> 에 없는 사람의 **저장된 입력 줄**로 다시 판정한다."""
    run = f"r-{sha}"
    rows = con.execute("SELECT key, input_text FROM judgments j WHERE run=? AND ok=1 AND input_text!=''"
                       " AND NOT EXISTS(SELECT 1 FROM judgments r WHERE r.key=j.key AND r.run=?) ORDER BY created DESC LIMIT ?",
                       (LIVE, run, limit)).fetchall()
    lines = {r[0]: r[1] for r in rows}
    if not lines:
        return {"run": run, "judged": 0, "missing": 0}
    f = judge_lines(list(lines.values()), set(lines), criteria)
    verdicts = f.require() if f.is_measured else []
    save(con, run, sha, lines, verdicts)
    return {"run": run, "judged": len(verdicts), "missing": len(lines) - len(verdicts),
            "error": None if f.is_measured else f.reason}


def diff(con, a: str, b: str) -> list[dict]:
    return [dict(zip(("key", "a", "b", "human"), r)) for r in con.execute(
        "SELECT ja.key, ja.tier, jb.tier, jh.tier FROM judgments ja"
        " JOIN judgments jb ON jb.key=ja.key AND jb.run=? AND jb.ok=1"
        " LEFT JOIN judgments jh ON jh.key=ja.key AND jh.run=?"
        " WHERE ja.run=? AND ja.ok=1 ORDER BY ja.created DESC", (b, HUMAN, a))]
