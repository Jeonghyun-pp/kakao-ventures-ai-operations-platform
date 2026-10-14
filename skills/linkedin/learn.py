"""주간 학습 — 반응(정답)에서 **특징**을 뽑아 기준 근거를 갱신하고, 기준 수정안을 낸다 [사용자 결정 2026-10-14].

    정답    _reaction_labels — ✅ vs 안 고름(본 날의 ❌·무반응). ❌ 는 "이번 주엔 관심 없음"이라 안 고른 것과 같다.
            아무도 안 본 날은 정답 아님. 최근 learn_weeks 주 스레드를 다시 읽어 늦은 반응까지
    특징    features.extract — 코드가 뽑는 고정 목록
    표      특징 구간별 n · ✅율 · 기저 대비 배수 · 윌슨 95% 구간. n < learn_min_n 이거나 한쪽이 3 미만이면 '표본 부족'
    제안    문턱을 넘은 구간이 있을 때만 claude 가 criteria.md 수정안 → replay → diff (판정이 정답을 더 잘 가르나)
    반영    🔴 criteria.md 는 자동으로 안 바뀐다 — 사람이 승인(Reviewer C yes/no, 9/29 회의). 표본이 작고, 기준이 카드를 바꾸면
            카드가 반응을 바꾸는 순환이 생긴다.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from kvlib import ledger, paths, report
from kv_transport import claude

from . import features, judge
from . import recheck as RC
from . import run as R

MIN_SIDE = 3          # 구간 안에 ✅·❌ 가 각각 이만큼은 있어야 비율을 말한다 — 0 이나 1 로는 우연과 못 가른다


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (c - r) / d, (c + r) / d


def refresh_snapshots() -> int:
    """최근 N주 카드 스레드를 다시 읽어 반응이 바뀐 카드만 snapshot. followup 의 늦은 훑기가 못 보는 것(✅ 로 닫힌 뒤 붙은 ❌ 등)까지."""
    since = (date.fromisoformat(ledger.today()) - timedelta(weeks=R._cfg("learn_weeks"))).isoformat()
    posted = {k: r for k, r in R._last(R.POSTED).items() if (r.get("day") or "") >= since and r.get("parent")}
    have = {r["key"]: (r.get("yes_by") or [], r.get("no_by") or []) for r in ledger.rows(R.TASK, ev=R.SNAPSHOT)}
    by_parent: dict[str, list] = defaultdict(list)
    for k, r in posted.items():
        by_parent[r["parent"]].append((k, r))
    n = 0
    for parent, rows in by_parent.items():
        got = R.slack.replies(rows[0][1].get("ch") or "net", parent)
        if not got.is_measured:
            print(f"  🔴 스레드 {parent} 못 읽음: {got.reason}")
            continue
        react = {m["ts"]: m.get("reactions") or [] for m in got.require()}
        for k, r in rows:
            mine = react.get(r.get("slack_ts") or r.get("ts"), [])
            yes, no = R._pressers(mine, "reaction_yes"), R._pressers(mine, "reaction_no")
            if (yes or no) and have.get(k) != (yes, no):
                ledger.append(R.TASK, k, R.SNAPSHOT, yes_by=yes, no_by=no, day=r.get("day"), src="learn")
                n += 1
    return n


TAGGED = "tagged"     # 장부 — 카드 한 장에 붙인 태그(features.TAGS). 한 번 붙이면 다시 안 부른다


def profile_text(it: dict) -> str:
    """태그를 붙일 재료 — 헤드라인·소개·현직·경력(회사·직함·기간). 이름은 넣지 않는다(특징만 본다)."""
    exp = [e for e in (it.get("experience") or []) if isinstance(e, dict)][:8]
    lines = [f"헤드라인: {str(it.get('headline') or '')[:200]}", f"소개: {str(it.get('about') or '')[:600]}"]
    lines += [f"경력: {e.get('position') or e.get('title') or ''} @ {e.get('companyName') or ''}" for e in exp]
    lines += [f"학력: {e.get('schoolName') or e.get('title') or ''} {e.get('degree') or ''}"
              for e in (it.get("education") or [])[:3] if isinstance(e, dict)]
    return " / ".join(x.replace("\n", " ").replace("\t", " ") for x in lines)


def tag(raw: dict[str, dict], keys: list[str]) -> dict[str, list[str]]:
    """key → 태그 목록. 장부에 있으면 그걸, 없으면 claude(도구 끔 · 50명씩)로 붙여 장부에 남긴다."""
    have = {r["key"]: r.get("tags") or [] for r in ledger.rows(R.TASK, ev=TAGGED)}
    todo = [k for k in keys if k not in have and raw.get(k)]
    vocab = "\n".join(f"- {t}: {d.split(' (')[0]}" for t, d in features.TAGS.items())
    for i in range(0, len(todo), judge.BATCH):
        chunk = todo[i:i + judge.BATCH]
        prompt = (f"# 태그 정의 — 이 어휘만 쓴다\n{vocab}\n\n# 대상 {len(chunk)}명 (key⇥프로필)\n"
                  + "\n".join(f"{k}\t{profile_text(raw[k])}" for k in chunk)
                  + f"\n\n# 출력\n정확히 {len(chunk)}줄. `key<TAB>태그,태그` — 해당 없으면 `key<TAB>-`. 근거가 프로필에 있을 때만 붙인다. 다른 말 없이.\n")
        try:
            out = claude.run_session(prompt, system="너는 벤처캐피털 소싱 담당자를 돕는다. 요구된 형식만 출력한다.", tools="")
        except claude.SessionError as e:
            print(f"  ⚠️  태그 배치 실패({e}) — 다음 주에 다시")
            break
        for line in out.splitlines():
            parts = line.strip().split("\t")
            if len(parts) >= 2 and parts[0] in chunk and parts[0] not in have:
                tags = [t.strip() for t in parts[1].split(",") if t.strip() in features.TAGS]   # 어휘 밖은 버린다
                have[parts[0]] = tags
                ledger.append(R.TASK, parts[0], TAGGED, tags=tags)
    return {k: have[k] for k in keys if k in have}


def table(con) -> tuple[list[dict], dict]:
    """정답 있는 카드 → 특징 구간별 행. (행 목록, 요약)."""
    labels = R._reaction_labels()
    posted = R._last(R.POSTED)
    landed = R._last(ledger.LANDED)
    live = dict(con.execute("SELECT key, verdict FROM judgments WHERE run='live' AND ok=1"))
    raw = RC.raw_by_key()
    tags = tag(raw, list(labels))
    rows: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])     # (특징, 구간) → [✅, 결론]
    for k, lab in labels.items():
        p, ld = posted.get(k, {}), landed.get(k, {})
        card = {"axis": ld.get("axis"), "tier": live.get(k) or ld.get("tier"), "posted_day": p.get("day"),
                "captured_day": ld.get("ts", "")[:10] if str(ld.get("ts", "")).startswith("20") else None,
                "explore": p.get("explore"), "resurfaced": bool(p.get("gk"))}
        for f, b in features.extract(raw.get(k), card, tags.get(k)).items():
            rows[(f, b)][0] += lab["label"] == "yes"
            rows[(f, b)][1] += 1
    n = len(labels)
    yes = sum(v["label"] == "yes" for v in labels.values())
    base = yes / n if n else 0.0
    min_n = R._cfg("learn_min_n")
    out = []
    for (f, b), (k, m) in sorted(rows.items()):
        lo, hi = _wilson(k, m)
        enough = m >= min_n and k >= MIN_SIDE and m - k >= MIN_SIDE
        out.append({"feature": f, "bin": b, "yes": k, "n": m, "rate": k / m, "lift": (k / m) / base if base else None,
                    "lo": lo, "hi": hi, "enough": enough,
                    "signal": enough and (lo > base or hi < base)})                 # 구간이 기저를 벗어남 = 우연으로 보기 어렵다
    lat = sorted((date.fromisoformat(r["ts"][:10]) - date.fromisoformat(posted[r["key"]]["day"])).days
                 for r in ledger.rows(R.TASK, ev=R.RESOLVED)
                 if r.get("by") in ("reaction_yes", "reaction_no") and r["key"] in posted and posted[r["key"]].get("day")
                 and r["ts"][:2] == "20")
    return out, {"n": n, "yes": yes, "no": n - yes, "base": base, "latency": lat}


def _people_terms() -> set[str]:
    """정답 받은 사람의 이름·회사명 — criteria.md 에 있으면 안 된다(사람이 아니라 특징만)."""
    posted = R._last(R.POSTED)
    terms = set()
    for k in R._reaction_labels():
        for f in ("name", "company"):
            v = str(posted.get(k, {}).get(f) or "").strip()
            if len(v) >= 3 and v not in ("(미상)", "스텔스"):
                terms.add(v)
    return terms


def leaks(text: str) -> list[str]:
    """기계 검증 — 기준 글에 특정 사람(이름·회사명)이 박혀 있으면 그 목록."""
    low = text.lower()
    return sorted(t for t in _people_terms() if t.lower() in low)


def evaluate(texts: dict[str, str]) -> dict[str, dict]:
    """기준 글들을 **같은 입력**으로 정답에 맞댄다 — 이름 → {n, catch, catch_yes, yes, agree}.
    🔴 저장된 판정 줄(replay)을 쓰지 않는다: 옛 줄엔 이전경력 칸이 없어 새 기준이 볼 근거가 빠진다(10/14).
       원본 아카이브에서 지금 형식의 줄을 다시 만든다. 기준을 만든 정답으로 다시 재는 것이라 수정안 점수는 낙관적이다."""
    labels, landed, raw = R._reaction_labels(), R._last(ledger.LANDED), RC.raw_by_key()
    keys = [k for k in labels if k in raw]
    lines = []
    for k in keys:
        s = R._summary({**raw[k], "_axis": landed.get(k, {}).get("axis", "")})
        s["key"] = k
        lines.append(judge._line(s))
    out = {}
    for name, text in texts.items():
        f = judge.judge_lines(lines, keys, text) if keys else None
        vs = {v["key"]: v["verdict"] for v in (f.require() if f and f.is_measured else [])}
        lab = [(vs[k], labels[k]["label"]) for k in keys if k in vs]
        out[name] = {"n": len(lab), "catch": sum(v == "Catch" for v, _ in lab), "yes": sum(l == "yes" for _, l in lab),
                     "catch_yes": sum(v == "Catch" and l == "yes" for v, l in lab),
                     "agree": sum((v == "Catch") == (l == "yes") for v, l in lab)}
    return out


def _fmt(m: dict) -> str:
    p = f"{m['catch_yes']}/{m['catch']}" + (f" ({m['catch_yes'] / m['catch']:.0%})" if m["catch"] else "")
    return f"Catch 중 ✅ {p} · ✅ 중 Catch {m['catch_yes']}/{m['yes']} · 일치 {m['agree']}/{m['n']}"


def propose(con, rows: list[dict], summary: dict, out_dir: Path) -> str:
    """매주: 지금 기준의 성적(같은 입력) + 문턱을 넘은 특징이 있으면 claude 수정안과 나란히. 반영은 사람."""
    crit = judge.criteria_text()
    sig = [r for r in rows if r["signal"]]
    if not sig:
        m = evaluate({"now": crit})["now"]
        return f"지금 기준: {_fmt(m)} (기저 ✅율 {summary['base']:.0%})\n문턱을 넘은 특징이 없다 — 수정안 없음"
    facts = "\n".join(f"- {r['feature']} = {r['bin']}: ✅ {r['yes']}/{r['n']} ({r['rate']:.0%}, 기저 {summary['base']:.0%}, "
                      f"95% {r['lo']:.0%}~{r['hi']:.0%})" for r in sig)
    prompt = (f"# 현재 criteria.md\n\n{crit}\n\n# 지난 {R._cfg('learn_weeks')}주+ 심사역 반응에서 우연으로 보기 어려운 특징\n{facts}\n\n"
              "# 할 일\n위 특징을 반영해 criteria.md 전체를 다시 써라. 이미 반영된 특징이면 근거 수치만 갱신한다. 규칙:\n"
              "- 룰마다 근거 수치와 n 을 단다.\n- 특정 사람 이름·회사명을 쓰지 않는다 — 특징만.\n"
              "- 위 목록에 없는 새 룰을 지어내지 않는다. 출력 형식 절은 그대로 둔다.\n- 머리말 없이 파일 본문만 출력한다.\n")
    try:
        text = claude.run_session(prompt, system="너는 벤처캐피털 소싱 기준 문서를 고친다. 요구된 본문만 출력한다.", tools="")
    except claude.SessionError as e:
        return f"🔴 수정안 생성 실패: {e}"
    bad = leaks(text)
    if bad:
        return f"🔴 수정안에 특정 사람이 들어갔다 — 버린다: {', '.join(bad[:5])}"
    cand = out_dir / f"learn-{ledger.today()}-criteria.md"
    cand.write_text(text, encoding="utf-8")
    m = evaluate({"now": crit, "cand": text})
    pn = m["now"]["catch_yes"] / m["now"]["catch"] if m["now"]["catch"] else 0
    pc = m["cand"]["catch_yes"] / m["cand"]["catch"] if m["cand"]["catch"] else 0
    better = pc > pn and m["cand"]["catch_yes"] >= m["now"]["catch_yes"] - 1           # 정밀도가 오르고 잡는 ✅ 가 크게 안 줄 때만
    return (f"지금 기준: {_fmt(m['now'])}\n수정안:   {_fmt(m['cand'])}  `{cand}`\n"
            + (f"→ 수정안이 낫다. 반영은 사람이 승인한다: `cp {cand} skills/linkedin/criteria.md` 뒤 `python -m tools.linkedin_retier --apply`"
               if better else "→ 지금 기준 유지 (수정안이 낫지 않다)")
            + "\n(기준을 만든 정답으로 다시 잰 값이라 낙관적이다 — 탐색 카드 줄이 표본 밖 검증이다)")


def run(*, dry_run: bool = False) -> int:
    print(f"▶ learn {ledger.today()}")
    g = R._gate(dry_run)
    if not dry_run:
        print(f"· 반응 다시 읽음 — 바뀐 카드 {refresh_snapshots()}장")
        R._sync_labels(g.con)
    rows, s = table(g.con)
    out_dir = paths.data("linkedin", "reports")
    prop = "dry-run — 제안 생략" if dry_run else propose(g.con, rows, s, out_dir)
    lat = s["latency"]
    md = [f"# 링크드인 주간 학습 — {ledger.today()}", "",
          f"정답 {s['n']}명 (✅ {s['yes']} · 안 고름 {s['no']} — 본 날의 ❌·무반응) · 기저 ✅율 {s['base']:.0%}",
          f"반응까지 걸린 일수: 중앙값 {lat[len(lat) // 2] if lat else '-'}일 · 7일 넘음 {sum(d > 7 for d in lat)}/{len(lat)}", "",
          "## 기준 수정안", "", prop, "",
          "## 특징 표 (✅ / 정답 수)", "",
          "| 특징 | 구간 | ✅/n | ✅율 | 배수 | 95% | |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        mark = "🔺" if r["signal"] and r["lo"] > s["base"] else "🔻" if r["signal"] else ("" if r["enough"] else "표본 부족")
        md.append(f"| {r['feature']} | {r['bin']} | {r['yes']}/{r['n']} | {r['rate']:.0%} | "
                  f"{(f'{r['lift']:.2f}' if r['lift'] else '-')} | {r['lo']:.0%}~{r['hi']:.0%} | {mark} |")
    md += ["", "> 안 본 날의 무반응은 정답이 아니다. 탐색 카드 행 = 판정 하위에서 올린 카드의 ✅율 — 기준이 놓친 비율.",
           "> 사람 특징 → criteria.md · 신선도 → card 정렬 · 서버로 걸 수 있는 특징 → axes.", ""]
    path = out_dir / f"learn-{ledger.today()}.md"
    path.write_text("\n".join(md), encoding="utf-8")
    print(f"· 표 {len(rows)}행 · 신호 {sum(r['signal'] for r in rows)} · 보고서 {path}")
    if not dry_run:
        print(f"· 슬랙 {report.post('linkedin', f'*링크드인 주간 학습* — 정답 {s['n']}명(✅ {s['yes']} · 안 고름 {s['no']}) · {prop.splitlines()[0]}\n전체: `{path}`', R._send)}")
    return 0
