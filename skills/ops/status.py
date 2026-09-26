"""현황판 — 여섯 스킬의 문·스케줄·오늘 건수·마지막 실행·백업을 한 자리에. **읽기만 한다.**

    status     터미널 한 화면
    digest     하루 한 통(review_test) + ~/kv-data/_reports/<날짜>.md + dashboard.html   ← 스킬별 하루 요약 슬랙을 이것 하나로 묶는다(ops.digest_slack)
    health     여섯 스킬의 "오늘 보고서가 있나" + 백업 나이 + 투심 접수 지연을 한 번에. 조용하면 조용하다

스킬별 실패 경보(linkedin _alert 등)는 그대로 각자 낸다 — 묶는 건 **하루 요약**뿐이다.
"""
from __future__ import annotations

import html
import json
import sqlite3
from datetime import date

from kvlib import constraints, ledger, paths

# (스킬, 문 키, 주말엔 보고서가 없는 게 정상인가). 새 스킬은 여기 한 줄
SKILLS = (("mail", "mail_observe", False), ("linkedin", "linkedin", True), ("appstore", "appstore", False),
          ("bplan", "bplan", False), ("memo", "memo", False))


def _db(skill: str):
    p = paths.data(skill, "db", create=False) / f"{skill}.sqlite"
    return sqlite3.connect(f"file:{p}?mode=ro", uri=True) if p.exists() else None


def gather(today: str | None = None) -> dict:
    import schedule as sched                                     # 리포 루트의 schedule.py — 선언·설치 대조의 정본
    from . import run as ops
    today = today or ledger.today()
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
            for kind, st, n in con.execute("SELECT kind, status, count(*) FROM actions WHERE created>=? GROUP BY kind, status", (today,)):
                k = f"{kind.rstrip('0123456789')}:{st}"
                s["actions"][k] = s["actions"].get(k, 0) + n
            s["judged_today"] = con.execute("SELECT count(*) FROM judgments WHERE run='live' AND created>=?", (today,)).fetchone()[0]
            s["human"] = con.execute("SELECT count(*) FROM judgments WHERE run='human'").fetchone()[0]
            s["last"] = max(filter(None, (con.execute("SELECT max(created) FROM actions").fetchone()[0],
                                          con.execute("SELECT max(created) FROM judgments").fetchone()[0])), default=None)
            if skill == "memo":
                stuck = con.execute("SELECT count(*) FROM actions a WHERE a.kind='request' AND a.created>=? AND NOT EXISTS"
                                    "(SELECT 1 FROM actions b WHERE b.key=a.key AND b.kind='ack')", (today,)).fetchone()[0]
                if stuck:
                    out["gaps"].append(f"memo: 접수 답글 없는 요청 {stuck}건")
            con.close()
        installed = [r for r in rows if r["installed"]]
        for r in rows:
            if not r["installed"]:
                out["gaps"].append(f"{skill}: 스케줄 미설치 — {r['cmd']} {r['when']}")
        if installed and skill != "memo" and not s["report_today"] and not (weekend_quiet and weekend):
            out["gaps"].append(f"{skill}: 오늘 보고서 없음")
        out["skills"].append(s)
    age = ops.backup_age_days()
    out["backup"] = {"age_days": None if age is None else round(age, 2),
                     "schedule": [{"cmd": j["cmd"], "when": j["when"], "installed": j["label"] in have} for j in jobs if j["skill"] == "ops"]}
    if age is None or age > float(constraints.get("ops.backup_stale_days")):
        out["gaps"].append("ops: kv-data 백업이 " + ("없음" if age is None else f"{age:.1f}일째 없음"))
    return out


def _acts(s: dict) -> str:
    top = sorted(s["actions"].items(), key=lambda kv: -kv[1])[:4]
    return " · ".join(f"{k} {n}" for k, n in top) or "—"


def render_text(st: dict) -> str:
    L = [f"kv 현황 {st['today']} · {st['now']}", ""]
    for s in st["skills"]:
        sched = " ".join(f"{r['cmd']}{'' if r['installed'] else '(미설치)'}" for r in s["schedule"]) or "스케줄 없음"
        L.append(f"  {s['skill']:9} 문 {s['gate']:5} │ 오늘 판정 {s['judged_today']:3} · 행동 {_acts(s)} │ 정답 누적 {s['human']} │ 보고서 {'✅' if s['report_today'] else '—'} │ {sched} │ 마지막 {(s['last'] or '—')[:16]}")
    b = st["backup"]
    L.append(f"  {'ops':9} 백업 {'없음' if b['age_days'] is None else str(b['age_days']) + '일 전'} │ " + " ".join(f"{r['cmd']}{'' if r['installed'] else '(미설치)'}" for r in b["schedule"]))
    L += ["", ("🔴 " + " / ".join(st["gaps"])) if st["gaps"] else "✅ 막힌 것 없음"]
    return "\n".join(L)


def digest_text(st: dict) -> str:
    lines = [f"*kv 하루* {st['today']} — " + ("🔴 " + " / ".join(st["gaps"]) if st["gaps"] else "✅ 막힌 것 없음")]
    for s in st["skills"]:
        lines.append(f"• {s['skill']} (문 {s['gate']}) 판정 {s['judged_today']} · {_acts(s)}" + ("" if s["report_today"] else " · 보고서 없음"))
    b = st["backup"]
    lines.append(f"• 백업 {'없음' if b['age_days'] is None else str(b['age_days']) + '일 전'} · 현황판 `~/kv-data/_reports/dashboard.html` · 스킬 보고서 `~/kv-data/<스킬>/reports/{st['today']}.md`")
    return "\n".join(lines)


def render_html(st: dict) -> str:
    def row(s):
        sched = "<br>".join(f"{html.escape(r['cmd'])} <small>{html.escape(r['when'])}</small>" + ("" if r["installed"] else " <b class=bad>미설치</b>") for r in s["schedule"]) or "<small>없음</small>"
        gate = {"off": "bad", "slack": "mid", "on": "ok"}.get(str(s["gate"]), "")
        return (f"<tr><td><b>{s['skill']}</b></td><td class={gate}>{s['gate']}</td><td>{s['judged_today']}</td><td>{html.escape(_acts(s))}</td>"
                f"<td>{s['human']}</td><td>{'✅' if s['report_today'] else '—'}</td><td>{sched}</td><td><small>{(s['last'] or '—')[:16]}</small></td></tr>")
    b = st["backup"]
    gaps = "".join(f"<li>{html.escape(g)}</li>" for g in st["gaps"]) or "<li class=ok>막힌 것 없음</li>"
    return f"""<!doctype html><html lang=ko><meta charset=utf-8><meta http-equiv=refresh content=600><title>kv 현황 {st['today']}</title>
<style>body{{font:14px/1.5 -apple-system,system-ui,sans-serif;margin:24px;color:#222;background:#fafafa}}table{{border-collapse:collapse;background:#fff}}
td,th{{border:1px solid #ddd;padding:6px 10px;vertical-align:top;text-align:left}}th{{background:#f0f0f0}}.ok{{color:#1a7f37}}.mid{{color:#9a6700}}.bad{{color:#c00}}small{{color:#666}}
h1{{font-size:18px}}ul{{padding-left:18px}}</style>
<h1>kv 현황판 — {st['today']} <small>({st['now']} · 10분마다 새로고침 · 읽기 전용)</small></h1>
<ul>{gaps}</ul>
<table><tr><th>스킬</th><th>문</th><th>오늘 판정</th><th>오늘 행동</th><th>정답 누적</th><th>오늘 보고서</th><th>스케줄</th><th>마지막</th></tr>
{''.join(row(s) for s in st['skills'])}
<tr><td><b>ops</b></td><td colspan=5>kv-data 백업 {'없음' if b['age_days'] is None else str(b['age_days']) + '일 전'}</td><td>{'<br>'.join(html.escape(r['cmd']) + ' <small>' + html.escape(r['when']) + '</small>' + ('' if r['installed'] else ' <b class=bad>미설치</b>') for r in b['schedule'])}</td><td></td></tr>
</table>
<p><small>문을 여닫는 건 <code>~/kv/constraints.yaml</code> 의 <code>&lt;스킬&gt;.outbound</code> 한 줄. 스케줄은 <code>python schedule.py install --apply</code>. 이 페이지는 <code>kv digest</code>·<code>kv dashboard</code> 가 다시 쓴다.</small></p>
<script type="application/json" id="status">{json.dumps(st, ensure_ascii=False)}</script></html>"""


def write_dashboard(st: dict):
    out = paths.data("_reports")
    (out / "dashboard.html").write_text(render_html(st), encoding="utf-8")
    (out / f"{st['today']}.md").write_text(render_text(st), encoding="utf-8")
    return out / "dashboard.html"
