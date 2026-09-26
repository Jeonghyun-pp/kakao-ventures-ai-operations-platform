"""투심보고서 요청 레인 — 심사역이 슬랙 스레드에 "○○ 투심" + 자료를 올리면, 초안 docx 를 같은 스레드에 올린다.
메일·비플랜과 같은 뼈대(kvlib store·gate·report·judgments). 명령·흐름·규율은 SKILL.md.

🔴 노션에 쓰지 않는다. 나가는 것은 스레드의 글(ack·note)과 파일(deliver)뿐이고 전부 `memo.outbound` 문 뒤다.
🔴 모델 세션의 성공은 rc 가 아니라 **docx 가 생겼는가**로 판정한다(0단계 실측: 산출물은 9분에 나왔는데 마지막 API 호출이 망 오류).
"""
from __future__ import annotations

import argparse
import difflib
import json
import re
import shutil
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from kvlib import constraints, judgments, ledger, lock, paths, report, store
from kvlib.gate import Gate
from kv_transport import claude, notion, slack

TASK = "memo"
SLACK_KINDS = ("ack", "deliver", "note")           # 뒤에 버전 숫자가 붙는다(deliver2·note2…). 허용 목록 — 그 밖은 기록만
HEADLESS = Path(__file__).parent / "headless.md"
# 스레드 파일의 역할 짐작 — 체크리스트 표시용이다. 진짜 분류는 스킬의 classify 가 내용까지 보고 한다
DOC_KINDS = (("인띰", r"invest.*theme|인띰|investment theme"), ("IR덱", r"\bir\b|deck|덱|피칭|소개서"),
             ("텀시트", r"term.?sheet|텀시트|투자조건|투자계약"), ("등기부", r"등기"), ("주주명부", r"주주"),
             ("재무제표", r"재무|financial statement"), ("FP", r"projection|\bfp\b|추정|사용계획"))
_STRIP = re.compile(r"<@[^>]+>|투심(보고서)?|투자심사보고서|만들어\S*|작성\S*|부탁\S*|해\s*줘|줘|좀|요청|please", re.I)


def _cfg(k: str):
    return constraints.get(f"memo.{k}")


def db_path():
    return paths.data(TASK, "db") / "memo.sqlite"


def _reports_dir():
    return paths.data(TASK, "reports")


def _req_dir(key: str) -> Path:
    return paths.data(TASK, "requests", key.replace(".", "_"))


def _gate(dry_run: bool = False) -> Gate:
    con = store.connect(db_path(), judgments.SCHEMA)
    judgments.ensure(con)
    return Gate(con, TASK, lambda k: k.rstrip("0123456789") in SLACK_KINDS, force="off" if dry_run else None)


def _send(ch: str, text: str) -> str:
    return slack.post(ch, text).require()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _people() -> list[dict]:
    return yaml.safe_load((paths.repo_root() / "people.yaml").read_text(encoding="utf-8")) or []


def _requesters() -> set[str]:
    """요청을 받아 주는 슬랙 멤버 — people.yaml 의 slack_id + memo.extra_requesters. 비어 있으면 아무 요청도 안 받는다(기본 거부)."""
    return {p.get("slack_id") for p in _people() if p.get("slack_id")} | set(_cfg("extra_requesters") or [])


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def company_of(text: str) -> str:
    """"Example Company D Diagnostics 투심보고서 부탁드려요" → "Example Company D Diagnostics". 요청 문구 **앞**이 회사명이다. 앞이 비면 걷어내고 남는 첫 덩어리."""
    t = re.sub(r"<@[^>]+>", " ", text or "")
    m = re.search(_cfg("request_pattern"), t)
    before = t[:m.start()].strip(" :,.\n") if m else ""
    rest = _STRIP.sub(" ", t).split()
    return before or (rest[0].strip(" :,.") if rest else "")


def _kind(name: str) -> str:
    return next((k for k, rx in DOC_KINDS if re.search(rx, _nfc(name), re.I)), "기타")


# ---------------------------------------------------------------- 요청 발견 · 접수

def _find_notion(company: str):
    """스타트업 DB 에서 회사 페이지. 제목이 회사명을 품은 **DB 행**만 — 리서치·위키 페이지는 아니다. 못 찾으면 None(사실로 넘긴다)."""
    f = notion.find_page(company)
    if not f.is_measured:
        return None
    hits = [p for p in f.require() if company.lower() in (p.get("title") or "").lower() and p.get("parent")]
    return hits[0]["id"] if hits else None


def _checklist(files: list[dict]) -> str:
    have = {_kind(f["name"]) for f in files}
    return " · ".join(f"{k} {'✅' if k in have else '❌'}" for k, _ in DOC_KINDS)


def discover(g: Gate, ch: str) -> int:
    """채널 최상위 글 중 요청 문구가 있고 명단 안 사람이 쓴 것을 요청으로 등록한다. 등록은 기록이라 문 밖이다."""
    got = slack.history(ch)
    if not got.is_measured:
        print(f"  ⚠️  채널을 못 읽었다: {got.reason}")
        return 0
    allowed, pat, n = _requesters(), _cfg("request_pattern"), 0
    for m in got.require():
        if m.get("bot_id") or pat not in (m.get("text") or "") or m.get("user") not in allowed:
            continue
        key = m["ts"]
        if g.row(key, "request"):
            continue
        company = company_of(m.get("text", ""))
        if not company:
            continue
        g.put(key, "request", "done", {"company": company, "user": m.get("user"), "ch": ch, "text": (m.get("text") or "")[:300]})
        n += 1
    return n


def _ack(g: Gate, key: str, req: dict) -> None:
    ch, company = req["ch"], req["company"]
    page_id = _find_notion(company)
    g.put(key, "notion", "done", {"page_id": page_id})
    files = slack.thread_files(ch, key)
    files = files.require() if files.is_measured else []
    text = (f"📄 *{company} 투심 초안 요청 접수*\n"
            f"• 노션 스타트업 DB: {'찾음' if page_id else '못 찾음 — 미팅록 없이 씁니다'}\n"
            f"• 자료: {_checklist(files)}\n"
            f"• 있는 자료를 이 스레드에 올려 주세요. {_cfg('collect_wait_min')}분 뒤 있는 것만으로 초안을 냅니다. 없는 값은 (미확인) 으로 남깁니다")
    g.out(key, "ack", {"text": text, "files": len(files), "page_id": page_id}, lambda: slack.post(ch, text, thread_ts=key).require())


# ---------------------------------------------------------------- 수집 · 생성 · 전달

def _collect(g: Gate, key: str, ch: str, company: str, folder: Path) -> dict:
    """스레드의 사람 파일을 폴더로(이미 받은 id 는 건너뜀) + 노션 본문 md. 파일명은 NFC(리눅스·맥 차이)."""
    folder.mkdir(parents=True, exist_ok=True)
    had = {r.get("id") for r in json.loads((g.row(key, "collect") or {}).get("files", "[]"))} if g.row(key, "collect") else set()
    got, files = slack.thread_files(ch, key), []
    for f in (got.require() if got.is_measured else []):
        dest = folder / _nfc(f["name"])
        if f["id"] in had and dest.exists():
            files.append({"id": f["id"], "name": dest.name, "kind": _kind(dest.name)})
            continue
        d = slack.download(f["url"], dest)
        files.append({"id": f["id"], "name": dest.name, "kind": _kind(dest.name), **({} if d.is_measured else {"error": d.reason})})
    page_id = (g.row(key, "notion") or {}).get("page_id")
    notion_line = "스타트업 DB 페이지는 못 찾았다 — `22_노션연동` 의 '노션에 페이지 없음' 절차대로 진행한다(미팅록 유래 근거는 `(미확인 — 미팅록 확인 필요)`)."
    if page_id:
        t = notion.page_text(page_id)
        if t.is_measured:
            (folder / f"노션미팅록_{company}.md").write_text(t.require(), encoding="utf-8")
            notion_line = f"폴더의 `노션미팅록_{company}.md` 가 스타트업 DB 페이지 본문 그대로다. `22_노션연동` 이 요구하는 것은 그 파일에서 읽는다."
        else:
            notion_line = f"스타트업 DB 페이지를 읽지 못했다({t.reason}) — '노션에 페이지 없음' 절차대로."
    out = {"files": json.dumps(files, ensure_ascii=False), "notion_line": notion_line, "n": len(files)}
    g.put(key, "collect", "done", out)
    return out


def _render(company: str, folder: Path, notion_line: str, feedback: list[str]) -> str:
    fb = ""
    if feedback:
        fb = "\n## 심사역 피드백 — 이번 버전에서 반영할 것\n" + "\n".join(f"- {x}" for x in feedback) + \
             "\n폴더에 사람이 수정한 docx 가 있으면 그것이 기준이다. 구조·표를 깨지 말고 지시된 부분만 고친다.\n"
    return HEADLESS.read_text(encoding="utf-8").format(company=company, folder=folder, notion_line=notion_line, feedback_block=fb,
                                                       skill_dir=_skill_dir(), skill_python=_skill_python())


def _skill_dir() -> Path:
    return Path(_cfg("skill_dir")).expanduser()


def _skill_python() -> Path:
    return Path(_cfg("skill_python")).expanduser()


def _generate(g: Gate, key: str, company: str, folder: Path, notion_line: str, v: int, feedback: list[str]) -> dict | None:
    """모델 세션 한 번. 성공 = 새 docx. 판정·입력·산출 md 를 judgments 에 남긴다(v1 = live, 그 뒤 v2…)."""
    work = _req_dir(key)
    system = work / f"system_v{v}.md"
    system.write_text(_render(company, folder, notion_line, feedback), encoding="utf-8")
    (work / "mcp.json").write_text(json.dumps({"mcpServers": {"investment-memo": {
        "command": str(_skill_python()), "args": [str(_skill_dir() / "mcp" / "server.py")]}}}), encoding="utf-8")
    prompt = (f"`{_skill_dir() / 'SKILL.md'}` 를 읽고 그 절차(Phase 0~4)대로 {company} 투심보고서 초안을 무인으로 끝까지 만들어라.\n"
              f"시작: scan_inputs(\"{folder}\").")
    before = {p.name for p in folder.glob("투심보고서_*.docx")}
    t0 = _now()
    res = claude.run_skill(prompt, mcp_config=work / "mcp.json", system_file=system, allowed_tools=_cfg("allowed_tools"),
                           timeout_sec=int(_cfg("session_timeout_min")) * 60, cwd=_skill_dir())
    new = sorted((p for p in folder.glob("투심보고서_*.docx") if p.name not in before or p.stat().st_mtime >= t0.timestamp()),
                 key=lambda p: p.stat().st_mtime)
    secs = int((_now() - t0).total_seconds())
    if not new:
        g.put(key, f"generate{v}", "failed", {"seconds": secs, "error": res.reason if not res.is_measured else "docx 없음"})
        print(f"  ✗ {company} v{v}: docx 없음 ({secs}s) — {res.reason if not res.is_measured else ''}")
        return None
    docx, md = new[-1], new[-1].with_suffix(".md")
    keep = work / f"v{v}.docx"
    shutil.copy2(docx, keep)
    md_text = md.read_text(encoding="utf-8") if md.exists() else ""
    gaps = re.findall(r"\(미확인[^)]*\)", md_text)
    run = judgments.LIVE if v == 1 else f"v{v}"
    judgments.save(g.con, run, judgments.criteria_sha(HEADLESS.read_text(encoding="utf-8")), {key: json.dumps(
        {"company": company, "files": (g.row(key, "collect") or {}).get("files", "[]"), "notion": notion_line[:80], "feedback": feedback}, ensure_ascii=False)},
        [{"key": key, "verdict": "draft", "reason": f"미확인 {len(gaps)} · {secs}s · 세션 {'정상' if res.is_measured else res.reason[:60]}", "raw": md_text[:30000]}])
    g.put(key, f"generate{v}", "done", {"docx": str(keep), "seconds": secs, "gaps": len(gaps), "session_ok": res.is_measured})
    print(f"  ✓ {company} v{v}: {keep.name} ({secs}s · 미확인 {len(gaps)})")
    return {"docx": keep, "gaps": gaps, "secs": secs}


def _deliver(g: Gate, key: str, ch: str, company: str, v: int, made: dict) -> None:
    g.out(key, f"deliver{v}", {"docx": str(made["docx"])},
          lambda: slack.upload(ch, made["docx"], thread_ts=key, title=f"{company} 투심 초안 v{v}").require())
    gaps = "\n".join(f"  · {x}" for x in dict.fromkeys(made["gaps"]))[:1500]
    text = (f"📝 *{company} 투심 초안 v{v}* ({made['secs'] // 60}분)\n"
            f"• 사람이 확인할 것: 미확인 {len(made['gaps'])}곳" + (f"\n{gaps}" if gaps else "") +
            "\n• Word 에서 목차 우클릭 → 필드 업데이트\n"
            f"• 고칠 점을 이 스레드에 글이나 수정한 docx 로 올리면 v{v + 1} 을 냅니다. 끝났으면 이 글에 ✅")
    g.out(key, f"note{v}", {"text": text}, lambda: slack.post(ch, text, thread_ts=key).require())


# ---------------------------------------------------------------- 상태기계

def _versions(g: Gate, key: str) -> int:
    return g.con.execute("SELECT count(*) FROM actions WHERE key=? AND kind LIKE 'generate%' AND status='done'", (key,)).fetchone()[0]


def _since(created: str) -> timedelta:
    return _now() - datetime.fromisoformat(created.replace("Z", "+00:00"))


def followup(g: Gate) -> dict:
    """10분마다. 새 요청 등록 → 접수 답글 → 기다린 뒤 수집·생성·전달 → 피드백이 오면 다음 버전 → ✅ 면 완료(정답 기록)."""
    ch = _cfg("request_channel")
    n = Counter(new=discover(g, ch))
    yes = set(constraints.get("slack.reaction_yes"))
    for key, detail in g.con.execute("SELECT a.key, a.detail FROM actions a WHERE a.kind='request'"
                                     " AND NOT EXISTS(SELECT 1 FROM actions d WHERE d.key=a.key AND d.kind='done')").fetchall():
        req = json.loads(detail)
        company, folder = req["company"], _req_dir(key) / "in"
        ack = g.row(key, "ack")
        if not ack:
            _ack(g, key, req)
            n["acked"] += 1
            continue
        v = _versions(g, key)
        if v == 0:
            if _since(ack["created"]) < timedelta(minutes=int(_cfg("collect_wait_min"))):
                n["waiting"] += 1
                continue
            col = _collect(g, key, ch, company, folder)
            made = _generate(g, key, company, folder, col["notion_line"], 1, [])
            if made:
                _deliver(g, key, ch, company, 1, made)
                n["delivered"] += 1
            else:
                n["failed"] += 1
            continue
        note = g.row(key, f"note{v}") or {}
        if note.get("status") != "done":
            n["undelivered"] += 1                                    # 문이 닫혀 dry 였다 — 열리면 다음 실행에서 올라간다
            _deliver(g, key, ch, company, v, {"docx": Path(g.row(key, f"generate{v}")["docx"]), "gaps": [], "secs": 0}) if g.level() != "off" else None
            continue
        got = slack.replies(ch, key)
        if not got.is_measured:
            n["unread"] += 1
            continue
        replies = got.require()
        mine = next((m for m in replies if m.get("ts") == note["result"]), {})
        head = slack.history(ch)
        parent_react = next((m.get("reactions") or [] for m in (head.require() if head.is_measured else []) if m.get("ts") == key), [])
        names = {r.get("name") for r in (mine.get("reactions") or []) + parent_react}
        if names & yes:
            _done(g, key, company, v)
            n["done"] += 1
            continue
        fb = [m for m in replies if not m.get("bot_id") and float(m.get("ts", 0)) > float(note["result"]) and (m.get("text") or m.get("files"))]
        if fb and v < int(_cfg("max_versions")):
            col = _collect(g, key, ch, company, folder)
            texts = [m["text"][:500] for m in fb if m.get("text")]
            made = _generate(g, key, company, folder, col["notion_line"], v + 1, texts)
            if made:
                _deliver(g, key, ch, company, v + 1, made)
                n["revised"] += 1
            else:
                n["failed"] += 1
        else:
            n["open"] += 1
    if sum(n.values()) > n["waiting"] + n["open"]:
        daily_report(g)
    return dict(n)


def _done(g: Gate, key: str, company: str, v: int) -> None:
    """✅ 시점의 문서가 정답이다 — 사람이 스레드에 올린 docx 가 있으면 그것, 없으면 마지막 버전. v1 과 대조해 human 행에 지표를 남긴다."""
    folder = _req_dir(key) / "in"
    human = sorted((p for p in folder.glob("*.docx") if "투심보고서_" not in p.name), key=lambda p: p.stat().st_mtime)
    final = human[-1] if human else Path(g.row(key, f"generate{v}")["docx"])
    v1 = Path((g.row(key, "generate1") or {}).get("docx", ""))
    metrics = _metrics(v1, final) if v1.exists() and final.exists() else {}
    g.con.execute(f"INSERT OR REPLACE INTO judgments({judgments._COLS}) VALUES(?,?,'','','',?,?,1,NULL,NULL,?,?)",
                  (key, judgments.HUMAN, "done", f"✅ v{v} · {final.name}", ledger.now(), json.dumps(metrics, ensure_ascii=False)))
    g.put(key, "done", "done", {"final": str(final), "versions": v, **{k: metrics[k] for k in ("survival", "discard_rate", "ratio") if k in metrics}})
    print(f"  ✅ {company}: v{v} 완료 · {metrics.get('survival', '?')} 생존 · {metrics.get('discard_rate', '?')} 폐기")


def _paras(path: Path) -> list[str]:
    import docx                                                # 초안 vs 완성본 대조에만 쓴다 — docx 를 읽는 유일한 자리
    d = docx.Document(str(path))
    return [_nfc(p.text.strip()) for p in d.paragraphs if len(p.text.strip()) >= 15]


def _metrics(draft: Path, final: Path) -> dict:
    """스킬의 backtest 와 같은 자: 생존율(완성본 문단 중 초안 유래) · 폐기율(초안 문단 중 버려진 것) · 분량비 · 버려진 문단 표본."""
    a, b = _paras(draft), _paras(final)
    def best(x, pool): return max((difflib.SequenceMatcher(None, x, y).ratio() for y in pool), default=0.0)
    kept = sum(len(x) for x in b if best(x, a) > 0.6)
    discarded = [x for x in a if best(x, b) <= 0.6]
    return {"survival": round(kept / max(1, sum(map(len, b))), 2), "discard_rate": round(len(discarded) / max(1, len(a)), 2),
            "ratio": round(sum(map(len, a)) / max(1, sum(map(len, b))), 2), "discarded_sample": discarded[:8]}


# ---------------------------------------------------------------- 로컬 한 건 (문 밖 · 시험용)

def run_folder(company: str, folder: Path, *, dry_run: bool = False) -> int:
    """슬랙 없이 폴더 하나로 초안 한 건. 2단계 검증·코퍼스 보정용. 슬랙엔 아무것도 안 나간다(deliver 를 부르지 않는다)."""
    g = _gate(dry_run)
    key = f"local-{company}-{ledger.today()}"
    g.put(key, "request", "done", {"company": company, "user": "local", "ch": "", "text": str(folder)})
    notion_line = "스타트업 DB 페이지는 못 찾았다 — '노션에 페이지 없음' 절차대로 진행한다."
    md = next(iter(sorted(folder.glob("노션미팅록_*.md"))), None)
    if md:
        notion_line = f"폴더의 `{md.name}` 가 스타트업 DB 페이지 본문 그대로다. `22_노션연동` 이 요구하는 것은 그 파일에서 읽는다."
    if dry_run:
        print(_render(company, folder, notion_line, []))
        return 0
    made = _generate(g, key, company, folder, notion_line, _versions(g, key) + 1, [])
    return 0 if made else 2


# ---------------------------------------------------------------- 보고서 · health

def daily_report(g: Gate) -> None:
    today = ledger.today()
    rows = g.con.execute("SELECT key, kind, status, detail, created FROM actions WHERE created>=? ORDER BY created", (today,)).fetchall()
    if not rows:
        return
    by = Counter(k.rstrip("0123456789") + ":" + s for _, k, s, _, _ in rows)
    done = [(k, json.loads(d)) for k, kind, s, d, _ in rows if kind == "done"]
    gaps = Counter(x for k, kind, s, d, _ in rows if kind.startswith("note") for x in re.findall(r"· (\(미확인[^)]*\))", json.loads(d).get("text", "")))
    ex = g.con.execute("SELECT key, extra FROM judgments WHERE run=? AND created>=?", (judgments.HUMAN, today)).fetchall()
    samples = [x for _, e in ex for x in (json.loads(e or "{}").get("discarded_sample") or [])][:10]
    head = (f"요청 {by['request:done']} · 접수 {by['ack:done'] + by['ack:dry']} · 초안 {by['generate:done']}(실패 {by['generate:failed']})"
            f" · 전달 {by['deliver:done']}(dry {by['deliver:dry']}) · 완료 {len(done)} · 문 {g.level()}")
    md = [f"# 투심 요청 레인 하루 보고 — {today}", "", head, "",
          "## 🚩 심사역이 지운 초안 문단 표본 (기준을 깎을 재료 — headless.md · 스킬 프롬프트)", ""]
    md += [f"- {s[:160]}" for s in samples] or ["없음(오늘 완료된 요청 없음)"]
    md += ["", "## 완료된 요청 — 초안 v1 대비", ""] + [f"- `{k}` 버전 {d.get('versions')} · 생존 {d.get('survival', '?')} · 폐기 {d.get('discard_rate', '?')} · 분량비 {d.get('ratio', '?')}" for k, d in done] or ["없음"]
    md += ["", "## 미확인 상위", ""] + [f"- {x} × {c}" for x, c in gaps.most_common(8)] or ["없음"]
    out = _reports_dir() / f"{today}.md"
    first = not out.exists()
    out.write_text("\n".join(md), encoding="utf-8")
    if first:
        print(f"· 보고서 {out} | 슬랙 {report.post(TASK, f'*투심 레인 하루* — {head}' + chr(10) + f'전체: `{out}`', _send)}")


def health() -> int:
    """요청이 있었는데 접수 답글이 없으면 한 줄. 요청 없는 날은 조용하다(그 자체가 정상)."""
    g = _gate()
    today = ledger.today()
    stuck = [k for (k,) in g.con.execute("SELECT key FROM actions WHERE kind='request' AND created>=?", (today,))
             if not g.row(k, "ack")]
    if stuck:
        print("🔴 " + report.post(TASK, f"🔴 *투심 요청 {len(stuck)}건이 접수 답글 없이 서 있습니다* — followup 이 안 돌았거나 채널을 못 읽습니다", _send))
        return 2
    print(f"✅ 오늘 요청 {g.con.execute('SELECT count(*) FROM actions WHERE kind=? AND created>=?', ('request', today)).fetchone()[0]}건 · 막힌 것 없음")
    return 0


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="memo")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("followup", help="요청 채널을 읽어 접수·생성·전달·피드백·완료를 잇는다"); f.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("run", help="슬랙 없이 폴더 하나로 초안 한 건 (시험·코퍼스 보정)")
    r.add_argument("--company", required=True); r.add_argument("--folder", required=True); r.add_argument("--dry-run", action="store_true", help="무인 규칙 본문만 찍는다")
    sub.add_parser("health"); sub.add_parser("status"); sub.add_parser("report")
    a = p.parse_args(argv)
    try:
        if a.cmd == "followup":
            with lock.hold("memo-followup"):
                g = _gate(a.dry_run)
                print(f"· followup: {followup(g)} | 문 {g.level()}")
                return 0
        if a.cmd == "run":
            with lock.hold("memo-run"):
                return run_folder(a.company, Path(a.folder).expanduser(), dry_run=a.dry_run)
        if a.cmd == "health":
            return health()
        if a.cmd == "report":
            daily_report(_gate()); return 0
        if a.cmd == "status":
            g = _gate()
            for k, kind, st, created in g.con.execute("SELECT key, kind, status, created FROM actions ORDER BY created DESC LIMIT 40"):
                print(f"  {created[:16]} {k:22} {kind:10} {st}")
            print(f"문 {g.level()} · 요청 {g.con.execute('SELECT count(*) FROM actions WHERE kind=?', ('request',)).fetchone()[0]}건")
            return 0
    except lock.Busy as e:
        print(f"🔴 {e}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
