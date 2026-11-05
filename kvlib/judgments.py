"""판정 저장 — 한 건 한 번, 모델이 본 글과 낸 값을 그대로. 기준을 고치면 replay → diff 로 잰다.

    live      운영이 실제로 쓴 판정
    human     사람의 결정 — 사람이 이미 하는 일(노션 칸·슬랙 반응)에서 읽어 온다 (label). 사람이 정답을 따로 달지 않는다
    r-<sha>   고친 기준으로 다시 판정한 것. 저장된 입력을 재사용한다 — judge 콜백의 모델 호출은 별도

스킬마다 `verdict` 의 어휘가 다르다(링크드인 Tier · 비플랜 점수). 표는 하나다.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable

from . import ledger

SCHEMA = """
CREATE TABLE IF NOT EXISTS judgments(
  key TEXT, run TEXT, criteria_sha TEXT, input_text TEXT, raw_output TEXT,
  verdict TEXT, reason TEXT, ok INTEGER, error TEXT, page_id TEXT, created TEXT, extra TEXT,
  PRIMARY KEY(key, run));
"""
LIVE, HUMAN = "live", "human"
_COLS = "key,run,criteria_sha,input_text,raw_output,verdict,reason,ok,error,page_id,created,extra"


def ensure(con) -> None:
    """표를 만들고, 09-26 전에 만든 표엔 extra 열을 붙인다."""
    con.executescript(SCHEMA)
    if "extra" not in [r[1] for r in con.execute("PRAGMA table_info(judgments)")]:
        con.execute("ALTER TABLE judgments ADD COLUMN extra TEXT")


def criteria_sha(*parts: str) -> str:
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()[:12]


def save(con, run: str, sha: str, lines: dict[str, str], verdicts: list[dict], *, extra: dict[str, dict] | None = None) -> None:
    """`lines` = {key: 모델이 본 글}. 판정을 못 받은 key 도 ok=0 으로 남긴다 — 입력은 값이 있다.
    `verdicts` 항목 = {key, verdict, reason, raw}. `extra[key]` 는 점수 밖의 부가 출력(카드 문안 등) — JSON 으로 extra 열에."""
    import json
    got = {v["key"]: v for v in verdicts}
    for key, text in lines.items():
        v = got.get(key)
        con.execute(
            f"INSERT OR REPLACE INTO judgments({_COLS}) VALUES(?,?,?,?,?,?,?,?,?,(SELECT page_id FROM judgments WHERE key=? AND run=?),?,?)",
            (key, run, sha, text, v.get("raw") if v else None, v.get("verdict") if v else None, v.get("reason") if v else None,
             1 if v else 0, None if v else "판정 누락", key, run, ledger.now(),
             json.dumps(extra[key], ensure_ascii=False) if v and extra and extra.get(key) else None))


def set_page(con, key: str, page_id: str) -> None:
    con.execute("UPDATE judgments SET page_id=? WHERE key=? AND run=?", (page_id, key, LIVE))


def label(con, rows: list[dict], prop: str, to_label: Callable[[str], str | None] = lambda v: v,
          same: Callable[[str, str], bool] = lambda live, human: live == human, source: str | None = None) -> int:
    """행(page_id 로 live 와 잇는다)의 `prop` 값이 사람의 결정이다 — 결정이 있는 행은 **전부** human 으로 적는다(같아도). 일치율의 분모가 그것이다.
    `to_label` 은 칸 값을 정답 어휘로(None 이면 미결 — 건드리지 않는다). `same` 은 live 판정과 정답이 같은가.
    `source` 는 정답의 출처(reason 칸) — 기본 "노션 <prop>". 돌려주는 값 = 지금 갈린 건수."""
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
        con.execute(f"INSERT OR REPLACE INTO judgments({_COLS}) VALUES(?,?,'','','',?,?,1,NULL,?,?,NULL)",
                    (key, HUMAN, human, source or f"노션 {prop}", row["_page_id"], ledger.now()))
        n += int(not same(verdict, human))
    return n


def replay(con, judge_lines, criteria: str, sha: str, *, limit: int = -1) -> dict:
    """live 에 있고 r-<sha> 에 성공 판정이 없는 건의 **저장된 입력**으로 다시 판정한다.
    `judge_lines(lines, keys, criteria) -> Fetched[list[verdict]]` — keys 는 lines 와 **같은 순서**의 목록이다(키가 줄 안에 없는 스킬을 위해).
    저장된 입력에는 기준 본문이 없다 — 기준은 부를 때 붙인다. 그래서 고친 기준으로 다시 잴 수 있다."""
    run = f"r-{sha}"
    rows = con.execute("SELECT key, input_text FROM judgments j WHERE run=? AND ok=1 AND input_text!=''"
                       " AND NOT EXISTS(SELECT 1 FROM judgments r WHERE r.key=j.key AND r.run=? AND r.ok=1) ORDER BY created DESC LIMIT ?",
                       (LIVE, run, limit)).fetchall()
    lines = {r[0]: r[1] for r in rows}
    if not lines:
        return {"run": run, "judged": 0, "missing": 0}
    f = judge_lines(list(lines.values()), list(lines), criteria)
    verdicts = f.require() if f.is_measured else []
    save(con, run, sha, lines, verdicts)
    return {"run": run, "judged": len(verdicts), "missing": len(lines) - len(verdicts),
            "error": None if f.is_measured else f.reason}


def diff_text(rows: list[dict], a: str, b: str, *, unit: str = "건", raw: bool = False) -> str:
    """`diff()` 결과를 사람이 읽는 표로. 갈린 건 한 줄씩 + 요약(정답이 있으면 양쪽 일치 수)."""
    va, vb = ("raw_a", "raw_b") if raw else ("a", "b")
    out = [f"  {str(r[va]):8} → {str(r[vb]):8} 정답={r['human'] or '-':6} {r['key'][:50]}" for r in rows if r["a"] != r["b"]]
    lab = [r for r in rows if r["human"]]
    out.append(f"{a} ↔ {b}: 비교 {len(rows)}{unit} · 갈림 {sum(r['a'] != r['b'] for r in rows)}{unit}"
               + (f" | 정답 {len(lab)}{unit} 중 일치 {a}={sum(r['a'] == r['human'] for r in lab)} · {b}={sum(r['b'] == r['human'] for r in lab)}" if lab else ""))
    return "\n".join(out)


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


def comparison_report(con, a: str, b: str, bucket: Callable[[str], str] = lambda v: v,
                      *, keys: list[str] | None = None) -> dict:
    """Compare identical stored inputs, keeping failures and absent records visible.

    Default scope is every stored key in a, including failed judgments. Explicit
    keys define a sample; an empty sample stays empty. Absence does not prove a
    model was never called. Completeness describes results, not model quality.
    """
    records = {(key, run): (text, verdict, ok) for key, run, text, verdict, ok in con.execute(
        "SELECT key,run,input_text,verdict,ok FROM judgments WHERE run IN (?,?,?) ORDER BY created DESC,key",
        (a, b, HUMAN))}
    targets = list(dict.fromkeys(keys)) if keys is not None else [key for key, run in records if run == a]
    sides = {side: {"success": [], "failed": [], "absent": []} for side in ("a", "b")}
    rows, changed_inputs = [], []
    for key in targets:
        left, right = records.get((key, a)), records.get((key, b))
        for side, record in (("a", left), ("b", right)):
            status = "absent" if record is None else "success" if record[2] == 1 else "failed"
            sides[side][status].append(key)
        if left is None or right is None or left[2] != 1 or right[2] != 1:
            continue
        if left[0] != right[0]:
            changed_inputs.append(key)
            continue
        human = records.get((key, HUMAN))
        rows.append({"key": key, "a": bucket(left[1]), "b": bucket(right[1]),
                     "human": human[1] if human and human[2] == 1 else None,
                     "raw_a": left[1], "raw_b": right[1]})
    labeled = [r for r in rows if r["human"] is not None]
    return {"scope": "explicit" if keys is not None else "baseline", "targets": targets,
            "total": len(targets), "a": sides["a"], "b": sides["b"],
            "input_changed": changed_inputs, "rows": rows, "compared": len(rows),
            "labeled": len(labeled), "a_matches": sum(r["a"] == r["human"] for r in labeled),
            "b_matches": sum(r["b"] == r["human"] for r in labeled),
            "complete": bool(targets) and len(rows) == len(targets)}


def comparison_text(report: dict, a: str, b: str, *, unit: str = "건", raw: bool = False) -> str:
    scope = f"기준 실행 {a} 전체" if report["scope"] == "baseline" else "명시한 평가 대상"
    out = [f"범위: {scope} {report['total']}{unit}"]
    for side, run in (("a", a), ("b", b)):
        counts = report[side]
        out.append(f"{run}: 성공 {len(counts['success'])}{unit} · 판정 실패·누락 {len(counts['failed'])}{unit}"
                   f" · 기록 없음 {len(counts['absent'])}{unit}")
    out.append(f"입력 변경으로 비교 제외: {len(report['input_changed'])}{unit}")
    va, vb = ("raw_a", "raw_b") if raw else ("a", "b")
    out.extend(f"  {str(r[va]):8} → {str(r[vb]):8} 정답={r['human'] if r['human'] is not None else '-'} {r['key'][:50]}"
               for r in report["rows"] if r["a"] != r["b"])
    out.append(f"같은 입력 비교: {report['compared']}/{report['total']}{unit} · 사람 판단 포함 {report['labeled']}{unit}")
    if report["labeled"]:
        out.append(f"사람 판단과 일치: {a}={report['a_matches']}/{report['labeled']} · {b}={report['b_matches']}/{report['labeled']}")
    if not report["total"]:
        out.append("평가 대상 없음: 실행 이름과 평가 범위를 확인하세요. 품질을 판단할 수 없습니다.")
    elif not report["complete"]:
        out.append("주의: 일부 대상은 비교할 수 없습니다. 위 일치 수는 비교 가능한 사례에 한정됩니다.")
    return "\n".join(out)
