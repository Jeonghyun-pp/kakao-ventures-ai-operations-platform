"""분류 라벨 — 판정을 메일함에 `kv/<카테고리>` 라벨로 보이게 하고, 사람이 라벨을 바꾸면 그게 정답이다 [사용자 결정 2026-09-29].

왜 따로 있는가: 판정은 DB 안에만 있어서 메일함을 열면 아무것도 안 보였다. 라벨은 나가는 글이 아니라 **정리**라 되돌릴 수 있고 받는 사람이 없다 —
그래서 발송 문(mail_observe.outbound)과 따로 `mail_labels.outbound` 로 연다. 정답 달기도 표를 보는 대신 메일함에서 라벨을 옮기는 것으로 대신한다.

  apply   live 판정이 있고 아직 라벨 행동이 없는 메일에 kv/<카테고리> 를 붙인다(문이 off 면 무엇을 붙였을지만 dry)
  sync    붙인 지 sync_days 안인 메일의 라벨을 되읽는다. 사람이 다른 kv/ 라벨로 옮겼으면 judgments human 으로 적는다(한 통 한 번, 바뀔 때만 갱신)

🔴 지메일 쓰기는 통로의 relabel(되읽음)·create_label(되읽음)뿐이다. 라벨을 떼지 않는다 — 사람이 뗀 라벨은 "정답 없음"으로 두고 건드리지 않는다.
"""
from __future__ import annotations

from kvlib import constraints
from kvlib.gate import Gate

from . import rules

TASK = "mail_labels"
KIND = "label"
SKIP = {rules.BPLAN_INTAKE, rules.WEBFLOW, rules.INTERNAL}              # 비플랜이 제 라벨을 쓰거나(접수), 우리가 보낸 것 — 분류 라벨 대상이 아니다
ALIAS = {rules.MASS_BLAST: rules.AD_NOISE}                              # 규칙 이름 → 사람이 보는 이름


def _cfg(k: str):
    return constraints.get(f"{TASK}.{k}")


def name_of(category: str) -> str | None:
    c = ALIAS.get(category, category)
    return None if c in SKIP or not c else f"{_cfg('prefix')}{c}"


def gate(con, *, force: str | None = None) -> Gate:
    return Gate(con, TASK, lambda k: False, force=force)                 # slack 단계가 없다 — off 아니면 on


def _ids(gmail, names: set[str], *, create: bool) -> dict[str, str]:
    """{라벨이름: id}. 없으면 문이 열렸을 때만 만든다(만든 것도 되읽는다)."""
    have = gmail.labels().require()
    for n in sorted(names - set(have)):
        if create:
            have[n] = gmail.create_label(n).require()
    return have


def apply(con, gmail, *, force: str | None = None) -> dict:
    g = gate(con, force=force)
    rows = con.execute(
        "SELECT j.message_id, j.category FROM judgments j WHERE j.run='live' AND j.ok=1"
        " AND NOT EXISTS(SELECT 1 FROM actions a WHERE a.key=j.message_id AND a.kind=? AND a.status='done')"
        " ORDER BY j.created", (KIND,)).fetchall()
    todo = [(mid, name_of(cat)) for mid, cat in rows if name_of(cat)]
    n = {"labeled": 0, "dry": 0, "failed": 0, "skipped": len(rows) - len(todo)}
    if not todo:
        return n
    ids = _ids(gmail, {name for _, name in todo}, create=g.level() == "on") if g.level() == "on" else {}
    for mid, name in todo:
        st = g.out(mid, KIND, {"label": name}, lambda: gmail.relabel(mid, add=[ids[name]], remove=[]).require())
        n["labeled" if st == "done" else "dry" if st == "dry" else "failed"] += 1
    return n


def sync(con, gmail, *, save_human) -> dict:
    """사람이 옮긴 라벨 → 정답. `save_human(mid, category, why)` 는 observe 의 저장 함수다(judgments 표는 그쪽 소관)."""
    prefix = _cfg("prefix")
    since = constraints.get("mail_labels.sync_days")
    rows = con.execute(
        "SELECT a.key, json_extract(a.detail,'$.label') FROM actions a WHERE a.kind=? AND a.status='done'"
        " AND a.created >= datetime('now', ?)", (KIND, f"-{int(since)} days")).fetchall()
    n = {"checked": 0, "changed": 0, "removed": 0, "unread": 0}
    if not rows:
        return n
    by_id = {v: k for k, v in gmail.labels().or_default({}).items()}
    for mid, applied in rows:
        got = gmail.headers_of(mid, ["Subject"])
        if not got.is_measured:
            n["unread"] += 1
            continue
        n["checked"] += 1
        ours = [by_id.get(i, "") for i in got.meta.get("labels", []) if by_id.get(i, "").startswith(prefix)]
        if not ours:
            n["removed"] += 1                                            # 사람이 뗐다 — 정답이 아니라 '보류'. 건드리지 않는다
            continue
        current = ours[0][len(prefix):]
        if ours[0] != applied and current in rules.JUDGE_CATEGORIES:
            prev = con.execute("SELECT category FROM judgments WHERE message_id=? AND run='human'", (mid,)).fetchone()
            if not prev or prev[0] != current:
                save_human(mid, current, f"메일함에서 라벨 이동 {applied} → {ours[0]}")
                n["changed"] += 1
    return n
