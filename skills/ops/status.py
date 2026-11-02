"""현황판 — 일곱 스킬의 문·스케줄·오늘 건수·마지막 실행·백업을 한 자리에. **읽기만 한다.**

    status   터미널 한 화면 · digest   하루 한 통(review_test) + ~/kv-data/_reports/dashboard.html · health   막힌 것(gaps)만 한 줄
스킬별 실패 경보(linkedin _alert 등)는 그대로 각자 낸다 — 묶는 건 **하루 요약**뿐이다.
"""
from __future__ import annotations

import html
import sqlite3
from datetime import date

from kvlib import constraints, ledger, paths

# (스킬, 문 키, 주말엔 보고서가 없는 게 정상인가). 새 스킬은 여기 한 줄 — 문 키가 스킬명과 다르고(mail→mail_observe) 주말 규칙이 스킬마다라 다른 자리가 없다
SKILLS = (("mail", "mail_observe", False), ("linkedin", "linkedin", True), ("appstore", "appstore", False),
          ("bplan", "bplan", False), ("memo", "memo", False), ("fund", "fund", False))
HEAD = ("스킬", "문", "오늘 판정", "오늘 행동", "정답 누적", "오늘 보고서", "스케줄", "마지막")


def _request_lane(task: str) -> bool:
    """요청 레인(투심·펀드제안서…)은 요청이 없는 날 보고서가 없는 게 정상이고, 대신 '접수 없는 요청'을 본다. 판별은 request_pattern 설정의 유무 — 이름 나열이 아니다."""
    try:
        constraints.get(f"{task}.request_pattern"); return True
    except KeyError:
        return False


def _db(skill: str):
    p = paths.data(skill, "db", create=False) / f"{skill}.sqlite"
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True) if p.exists() else None        # 읽기 전용 — 현황판은 아무것도 바꾸지 않는다


def gather(today: str | None = None) -> dict:
    import schedule as sched                                     # 리포 루트의 schedule.py — 선언·설치 대조의 정본
    from . import run as ops
    today = today or ledger.today()
    bounds = ledger.day_bounds(today)
    jobs, have = sched.declared(), sched.loaded()
    weekend = date.fromisoformat(today).weekday() >= 5
    out = {"today": today, "now": ledger.now(), "skills": [], "gaps": []}
    for skill, task, weekend_quiet in SKILLS:
        rows = [{"cmd": j["cmd"], "when": j["when"], "installed": j["label"] in have} for j in jobs if j["skill"] == skill]
        s = {"skill": skill, "gate": constraints.get(f"{task}.outbound"), "schedule": rows,
             "report_today": (paths.data(skill, "reports", create=False) / f"{today}.md").exists(),
             "actions": {}, "judged_today": 0, "human": 0, "last": None}
        con = _db(skill)
        if con:
            for kind, st, n in con.execute("SELECT kind, status, count(*) FROM actions WHERE created>=? AND created<? GROUP BY kind, status", bounds):
                k = f"{kind.rstrip('0123456789')}:{st}"
                s["actions"][k] = s["actions"].get(k, 0) + n
            s["judged_today"], s["human"], last = con.execute(
                "SELECT (SELECT count(*) FROM judgments WHERE run='live' AND created>=? AND created<?), (SELECT count(*) FROM judgments WHERE run='human'),"
                " max(coalesce((SELECT max(created) FROM actions), ''), coalesce((SELECT max(created) FROM judgments), ''))", bounds).fetchone()
            s["last"] = last or None
            if _request_lane(task):
                stuck = con.execute("SELECT count(*) FROM actions a WHERE a.kind='request' AND a.created>=? AND a.created<? AND NOT EXISTS"
                                    "(SELECT 1 FROM actions b WHERE b.key=a.key AND b.kind='ack')", bounds).fetchone()[0]
                if stuck:
                    out["gaps"].append(f"{skill}: 접수 답글 없는 요청 {stuck}건 — followup 이 안 돌았거나 채널을 못 읽는다")
            con.close()
        for r in rows:
            if not r["installed"]:
                out["gaps"].append(f"{skill}: 스케줄 미설치 — {r['cmd']} {r['when']}")
        if any(r["installed"] for r in rows) and not _request_lane(task) and not s["report_today"] and not (weekend_quiet and weekend):
            out["gaps"].append(f"{skill}: 오늘 보고서 없음")
        out["skills"].append(s)
    age = ops.backup_age_days()
    out["backup"] = {"age_days": None if age is None else round(age, 2), "text": "없음" if age is None else f"{age:.1f}일 전",
                     "schedule": [{"cmd": j["cmd"], "when": j["when"], "installed": j["label"] in have} for j in jobs if j["skill"] == "ops"]}
    if age is None or age > float(constraints.get("ops.backup_stale_days")):
        out["gaps"].append(f"ops: kv-data 백업 {out['backup']['text']}")
    if out["gaps"]:
        out["gaps"].append("맥이 잠들었거나 launchd 작업이 내려갔을 수 있다: `launchctl list | grep kv.`")   # 9/24 실전에서 정확히 맞았던 문장
    out["gaps_text"] = ("🔴 " + " / ".join(out["gaps"])) if out["gaps"] else "✅ 막힌 것 없음"
    return out


def _sched(rows: list[dict]) -> str:
    return " ".join(f"{r['cmd']}{'' if r['installed'] else '(미설치)'}" for r in rows) or "없음"


def _cells(s: dict) -> list[str]:
    """한 스킬 = 한 줄. 세 렌더러가 같은 칸을 쓴다(HEAD 순서)."""
    acts = " · ".join(f"{k} {n}" for k, n in sorted(s["actions"].items(), key=lambda kv: -kv[1])[:4]) or "—"
    return [s["skill"], str(s["gate"]), str(s["judged_today"]), acts, str(s["human"]), "✅" if s["report_today"] else "—", _sched(s["schedule"]), (s["last"] or "—")[:16]]


def _rows(st: dict) -> list[list[str]]:
    b = st["backup"]
    return [_cells(s) for s in st["skills"]] + [["ops", "—", "—", f"백업 {b['text']}", "—", "—", _sched(b["schedule"]), "—"]]


def render_text(st: dict) -> str:
    return "\n".join([f"kv 현황 {st['today']} · {st['now']}", ""] + ["  " + " │ ".join(c.ljust(w) for c, w in zip(r, (9, 5, 4, 48, 5, 3, 36, 16))) for r in _rows(st)] + ["", st["gaps_text"]])


def digest_text(st: dict) -> str:
    return "\n".join([f"*kv 하루* {st['today']} — {st['gaps_text']}"] + [f"• {r[0]} (문 {r[1]}) 판정 {r[2]} · {r[3]}" + ("" if r[5] != "—" or r[0] == "ops" else " · 보고서 없음") for r in _rows(st)]
                     + [f"• 현황판 `~/kv-data/_reports/dashboard.html` · 스킬 보고서 `~/kv-data/<스킬>/reports/{st['today']}.md`"])


def render_html(st: dict) -> str:
    cls = {"off": "bad", "slack": "mid", "on": "ok"}
    body = "".join("<tr>" + "".join(f"<td class={cls.get(c, '') if i == 1 else ''}>{html.escape(c)}</td>" for i, c in enumerate(r)) + "</tr>" for r in _rows(st))
    return f"""<!doctype html><html lang=ko><meta charset=utf-8><title>kv 현황 {st['today']}</title>
<style>body{{font:14px/1.5 -apple-system,system-ui,sans-serif;margin:24px;color:#222;background:#fafafa}}table{{border-collapse:collapse;background:#fff}}
td,th{{border:1px solid #ddd;padding:6px 10px;vertical-align:top;text-align:left}}th{{background:#f0f0f0}}.ok{{color:#1a7f37}}.mid{{color:#9a6700}}.bad{{color:#c00}}small{{color:#666}}h1{{font-size:18px}}</style>
<h1>kv 현황판 — {st['today']} <small>({st['now']} · 매일 18:30 갱신 · 읽기 전용)</small></h1>
<p>{html.escape(st['gaps_text'])}</p>
<table><tr>{''.join(f'<th>{h}</th>' for h in HEAD)}</tr>{body}</table>
<p><small>문을 여닫는 건 <code>$KV_CONFIG/deployment.yaml</code> 의 <code>&lt;스킬&gt;.outbound</code> 한 줄. 스케줄은 <code>./kv install --apply</code>. 이 페이지는 <code>./kv digest</code> 가 다시 쓴다.</small></p></html>"""


def write_dashboard(st: dict):
    out = paths.data("_reports") / "dashboard.html"
    out.write_text(render_html(st), encoding="utf-8")
    return out
