"""펀드제안서(한글) 요청 레인 — 심사역이 슬랙 스레드에 "○○ 제안서" + 노션 제안서 DB 링크 + 공고 서식 hwp 를 올리면,
노션 원문을 그 서식으로 옮긴 hwp 를 같은 스레드에 올리고, 노션이 바뀔 때마다 다시 올리고, ✅ 시점 제출본을 정답으로 남긴다.
투심 레인과 같은 뼈대(kvlib store·gate·report·judgments). 명령·흐름·규율은 SKILL.md.

🔴 모델 호출이 없다 — 글자는 노션 원문 그대로다(심사위원이 원문과 대조한다). 판단이 필요한 것(슬래시 셀·미확보 그림·밖에 둔 항목)은 고치지 않고 보고한다.
🔴 노션에 쓰지 않는다. 나가는 것은 스레드의 글(ack·note)과 파일(deliver)뿐이고 전부 `fund.outbound` 문 뒤다.
🔴 파일은 늘 새 이름(vN.hwp)으로 낸다 — 한글에 열려 있는 파일에 쓰면 반영이 안 된다(8~9월에 열 번).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from kvlib import constraints, judgments, ledger, lock, paths, people, report, store
from kvlib.gate import Gate
from kv_transport import hwp, notion, slack

from . import audit, build, convert

TASK = "fund"
SLACK_KINDS = ("ack", "deliver", "note")
PROPS = ["항목번호", "항목명", "대섹션", "상태"]
DB_ID = re.compile(r"([0-9a-f]{8})-?([0-9a-f]{4})-?([0-9a-f]{4})-?([0-9a-f]{4})-?([0-9a-f]{12})")
IMAGE_EXT = (".png", ".jpg", ".jpeg")
_STRIP = re.compile(r"<[^>]+>|제안서|출자신청서|계획서|만들어\S*|작성\S*|부탁\S*|해\s*줘|줘|좀|요청|please", re.I)


def _cfg(k: str):
    return constraints.get(f"fund.{k}")


def db_path():
    return paths.data(TASK, "db") / "fund.sqlite"


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


def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def title_of(text: str) -> str:
    """"LP 성장펀드 제안서 부탁해요 <링크>" → "LP 성장펀드". 요청 문구 앞이 제목이다."""
    t = re.sub(r"<[^>]+>", " ", text or "")
    m = re.search(_cfg("request_pattern"), t)
    before = t[:m.start()].strip(" :,.\n") if m else ""
    rest = _STRIP.sub(" ", t).split()
    return before or (rest[0].strip(" :,.") if rest else "")


def db_id_of(text: str) -> str:
    m = DB_ID.search((text or "").lower())
    return "".join(m.groups()) if m else ""


# ---------------------------------------------------------------- 요청 발견 · 접수

def discover(g: Gate, ch: str, msgs: list[dict]) -> int:
    allowed, pat, n = people.requesters(_cfg("extra_requesters")), _cfg("request_pattern"), 0
    for m in msgs:
        if m.get("bot_id") or pat not in (m.get("text") or "") or m.get("user") not in allowed or g.row(m["ts"], "request"):
            continue
        title = title_of(m.get("text", ""))
        if title:
            g.put(m["ts"], "request", "done", {"title": title, "user": m.get("user"), "ch": ch, "text": (m.get("text") or "")[:300]})
            n += 1
    return n


def _inputs(msgs: list[dict]) -> dict:
    """스레드에서 노션 DB id · 서식 hwp(사람이 올린 첫 .hwp) · 그림 파일을 찾는다. 사람이 올린 것만 — 우리가 올린 vN.hwp 는 서식이 아니다."""
    human = [m for m in msgs if not m.get("bot_id")]
    db = next((d for m in human if (d := db_id_of(m.get("text") or ""))), "")
    files = slack.files_of(human)
    form = next((f for f in files if _nfc(f["name"]).lower().endswith(".hwp")), None)
    images = [f for f in files if _nfc(f["name"]).lower().endswith(IMAGE_EXT)]
    return {"db": db, "form": form, "images": images}


def _ack(g: Gate, key: str, req: dict, msgs: list[dict]) -> None:
    ch, title = req["ch"], req["title"]
    inp = _inputs(msgs)
    text = (f"📄 *{title} 제안서 이관 요청 접수*\n"
            f"• 노션 제안서 DB 링크: {'있음' if inp['db'] else '❌ 이 스레드에 올려 주세요'}\n"
            f"• 공고 서식 hwp: {'있음 — ' + _nfc(inp['form']['name']) if inp['form'] else '❌ 이 스레드에 올려 주세요'}\n"
            f"• 그림: {len(inp['images'])}장 (📁 줄이 가리키는 파일명 그대로 올리면 자리에 들어갑니다)\n"
            f"• 둘 다 있으면 다음 바퀴(10분 안)에 v1 을 냅니다. 노션을 고치면 그때마다 다음 버전이 나옵니다")
    g.out(key, "ack", {"text": text, **{k: bool(v) for k, v in inp.items()}}, lambda: slack.post(ch, text, thread_ts=key).require())


# ---------------------------------------------------------------- 수집 · 빌드 · 전달

def _pull(db: str, folder: Path) -> tuple[list[dict], dict[str, list[dict]], str]:
    """노션 DB 행 + 본문 행의 블록. 폴더에 JSON 으로 남긴다(replay·✅ 대조용). 반환 (rows, blocks, 최신 수정시각)."""
    rows = notion.query(db, PROPS).require()
    inside, _ = convert.body_rows(rows)
    blocks = {r["_page_id"]: notion.page_blocks(r["_page_id"]).require() for r in inside}
    edited = max((r.get("_edited") or "" for r in inside), default="")
    (folder / "notion.json").write_text(json.dumps({"rows": rows, "blocks": blocks, "edited": edited}, ensure_ascii=False), encoding="utf-8")
    return rows, blocks, edited


def _profile(key: str, form: Path) -> dict:
    """요청당 하나. 처음 서식에서 배우고, 그 뒤엔 파일이 있으면 사람이 손질한 값을 존중한다."""
    p = _req_dir(key) / "profile.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    prof = build.learn_profile(form)
    p.write_text(json.dumps(prof, ensure_ascii=False, indent=1), encoding="utf-8")
    return prof


def _generate(g: Gate, key: str, title: str, db: str, form: Path, images: Path | None, v: int) -> bool:
    work = _req_dir(key)
    try:
        rows, blocks, edited = _pull(db, work)
    except Exception as e:                                        # 노션을 못 읽으면 이번 바퀴는 실패로 적고 다음 바퀴에 다시
        g.put(key, f"generate{v}", "failed", {"error": f"notion:{e}"[:300]})
        print(f"  ✗ {title} v{v}: 노션 {e}")
        return False
    items, found = convert.convert(rows, blocks, images)
    prof = _profile(key, form)
    out = work / f"v{v}.hwp"
    r = build.build(items, prof, form, out, work / f"v{v}")
    tr = {"ok": False, "checked": 0, "missing": [], "cells_checked": 0, "cells_bad": [], "tables_unmatched": [], "forbidden": []}
    if r["ok"]:
        txt, ins = hwp.text(out), hwp.inspect(out)
        if txt.is_measured and ins.is_measured:
            tr = audit.transfer(items, txt.require(), ins.require().get("tables", []))
    summary = {"hwp": str(out) if r["ok"] else "", "edited": edited, "items": len(items), "tables": r.get("tables", 0), "ops": r["ops"],
               "structure": r.get("structure", False), "format": r.get("format", []), "build_warnings": r["warnings"],
               "transfer": {k: (len(v) if isinstance(v, list) else v) for k, v in tr.items()},
               "missing": [m[1] for m in tr["missing"]][:10], "cells_bad": [f"{a} ({b},{c}) {d!r}≠{e!r}" for a, b, c, d, e in tr["cells_bad"]][:10],
               "findings": {k: v[:12] for k, v in found.items()}}
    judgments.save(g.con, judgments.LIVE if v == 1 else f"v{v}", judgments.criteria_sha(json.dumps(prof["sizes"]), json.dumps(prof["bullets"])),
                   {key: json.dumps({"db": db, "edited": edited, "form": form.name, "items": len(items)}, ensure_ascii=False)},
                   [{"key": key, "verdict": "built" if r["ok"] else "failed", "reason": f"문장 {tr['checked']} 누락 {len(tr['missing'])} · 셀 {tr['cells_checked']} 불일치 {len(tr['cells_bad'])} · 표 {r.get('tables', 0)}",
                     "raw": json.dumps(summary, ensure_ascii=False)[:30000]}])
    if not r["ok"]:
        g.put(key, f"generate{v}", "failed", {**summary, "steps": r["steps"]})
        print(f"  ✗ {title} v{v}: 빌드 실패 {[s for s in r['steps'] if not s[1]][:1]}")
        return False
    g.put(key, f"generate{v}", "done", summary)
    print(f"  ✓ {title} v{v}: {out.name} (항목 {len(items)} · 표 {r.get('tables')} · 누락 {len(tr['missing'])} · 셀 불일치 {len(tr['cells_bad'])})")
    return True


def _note_text(title: str, v: int, made: dict, prof: dict | None) -> str:
    t, f = made["transfer"], made["findings"]
    judge = [f"슬래시 셀 {len(f.get('slash_cells', []))}", f"미확보 그림 {len(f.get('images_missing', []))}", f"밖에 둔 항목 {len(f.get('outside', []))}"]
    lines = [f"📝 *{title} 제안서 v{v}* — 노션 {made['edited'][:16].replace('T', ' ')} 기준 · 항목 {made['items']} · 표 {made['tables']}",
             f"• 원문 대조: 문장 {t['checked']}개 중 누락 {t['missing']} · 셀 {t['cells_checked']}개 중 불일치 {t['cells_bad']} · 작업용 문구 {t['forbidden']}",
             f"• 판단 필요: {' · '.join(judge)}" + (("\n  · " + "\n  · ".join(f.get("images_missing", [])[:5])) if f.get("images_missing") else "")]
    if made.get("format"):
        lines.append("• 서식 검사: " + " · ".join(f"{n} {g}" for n, g, s in made["format"][:4]))
    if made.get("build_warnings"):
        lines.append("• ⚠️ " + " · ".join(made["build_warnings"][:3]))
    if prof:
        lines.append("• 이 서식으로 읽었습니다 — 틀리면 알려 주세요\n" + build.profile_text(prof))
    lines.append(f"• 한글에서 열어 보세요(정적 검사는 통과했지만 렌더링은 사람만 봅니다). 노션을 고치면 다음 바퀴에 v{v + 1}. 끝났으면 이 글에 ✅ (제출본을 올리면 그게 정답)")
    return "\n".join(lines)


def _deliver(g: Gate, key: str, ch: str, title: str, v: int) -> None:
    made = g.row(key, f"generate{v}")
    g.out(key, f"deliver{v}", {"hwp": made["hwp"]}, lambda: slack.upload(ch, Path(made["hwp"]), thread_ts=key, title=f"{title} 제안서 v{v}").require())
    prof = json.loads((_req_dir(key) / "profile.json").read_text(encoding="utf-8")) if v == 1 and (_req_dir(key) / "profile.json").exists() else None
    text = _note_text(title, v, made, prof)
    g.out(key, f"note{v}", {"text": text}, lambda: slack.post(ch, text, thread_ts=key).require())


# ---------------------------------------------------------------- 상태기계

def _versions(g: Gate, key: str) -> int:
    return g.con.execute("SELECT count(*) FROM actions WHERE key=? AND kind LIKE 'generate%' AND status='done'", (key,)).fetchone()[0]


def _latest_edit(db: str) -> str:
    rows = notion.query(db, PROPS)
    if not rows.is_measured:
        return ""
    inside, _ = convert.body_rows(rows.require())
    return max((r.get("_edited") or "" for r in inside), default="")


def _download_inputs(key: str, inp: dict) -> tuple[Path | None, Path]:
    folder = _req_dir(key) / "in"
    folder.mkdir(parents=True, exist_ok=True)
    form = None
    if inp["form"]:
        dest = folder / _nfc(inp["form"]["name"])
        form = dest if dest.exists() or slack.download(inp["form"]["url"], dest).is_measured else None
    images = folder / "images"
    images.mkdir(exist_ok=True)
    for f in inp["images"]:
        dest = images / _nfc(f["name"])
        if not dest.exists():
            slack.download(f["url"], dest)
    return form, images


def followup(g: Gate) -> dict:
    """10분마다. 새 요청 → 접수 → DB·서식이 갖춰지면 v1 → 노션이 바뀌면 다음 버전 → ✅ 면 완료. 채널은 한 번, 스레드는 요청당 한 번 읽는다."""
    ch = _cfg("request_channel")
    head = slack.history(ch)
    if not head.is_measured:
        print(f"  ⚠️  채널을 못 읽었다: {head.reason}")
        return {"unread": 1}
    n = Counter(new=discover(g, ch, head.require()))
    yes = set(constraints.get("slack.reaction_yes"))
    for key, detail in g.con.execute("SELECT a.key, a.detail FROM actions a WHERE a.kind='request'"
                                     " AND NOT EXISTS(SELECT 1 FROM actions d WHERE d.key=a.key AND d.kind='done')").fetchall():
        req = json.loads(detail)
        title = req["title"]
        got = slack.thread(ch, key)
        if not got.is_measured:
            n["unread"] += 1
            continue
        msgs = got.require()
        if not g.row(key, "ack"):
            _ack(g, key, req, msgs)
            n["acked"] += 1
            continue
        inp = _inputs(msgs)
        form, images = _download_inputs(key, inp)
        if not (inp["db"] and form):
            n["waiting"] += 1
            continue
        v = _versions(g, key)
        if v == 0:
            if _generate(g, key, title, inp["db"], form, images, 1):
                _deliver(g, key, ch, title, 1); n["delivered"] += 1
            else:
                n["failed"] += 1
            continue
        _deliver(g, key, ch, title, v)                                     # 문이 닫혀 dry 였던 전달은 문이 열리면 여기서 올라간다
        note = g.row(key, f"note{v}")
        if note["status"] != "done":
            n["undelivered"] += 1
            continue
        mine = next((m for m in msgs if m.get("ts") == note["result"]), {})
        if {r.get("name") for r in mine.get("reactions") or []} & yes:
            _done(g, key, title, v, msgs, note["result"])
            n["done"] += 1
            continue
        fb = [m for m in msgs if not m.get("bot_id") and m.get("ts") != key and float(m.get("ts", 0)) > float(note["result"]) and m.get("text")]
        for m in fb:                                                       # 서식 피드백은 사람이 profile.json 을 손질할 재료다 — 모델이 없으니 자동 반영은 없다
            if not g.row(f"{key}/{m['ts']}", "feedback"):
                g.put(f"{key}/{m['ts']}", "feedback", "done", {"text": m["text"][:500], "v": v})
                n["feedback"] += 1
        edited = _latest_edit(inp["db"])
        last = (g.row(key, f"generate{v}") or {}).get("edited", "")
        if edited and edited > last and v < int(_cfg("max_versions")):
            if _generate(g, key, title, inp["db"], form, images, v + 1):
                _deliver(g, key, ch, title, v + 1); n["revised"] += 1
            else:
                n["failed"] += 1
        else:
            n["open"] += 1
    if sum(n.values()) > n["waiting"] + n["open"]:
        daily_report(g)
    bdir = db_path().parent / "backup"
    if not (bdir / f"fund-{datetime.now(timezone.utc):%Y-%m-%d}.sqlite").exists():
        store.backup(g.con, bdir, "fund", _cfg("backup_keep"))
    return dict(n)


def _done(g: Gate, key: str, title: str, v: int, msgs: list[dict], note_ts: str) -> None:
    """✅ 시점 문서가 정답 — 마지막 안내 뒤 사람이 올린 hwp 가 있으면 그것(final.hwp), 없으면 마지막 버전.
    정답에서 배우는 것 둘: ① 노션 원문 중 제출본에 없는 문장(= hwp 에서만 고친 것, 노션에 되돌려 적을 것) ② 제출본 서식 실측 vs 프로필(다음 공고 기본값 재료)."""
    work = _req_dir(key)
    final = Path(g.row(key, f"generate{v}")["hwp"])
    later = [f for f in slack.files_of([m for m in msgs if not m.get("bot_id") and m.get("ts") != key])       # 요청 글의 서식 hwp 는 제출본이 아니다
             if float(f["ts"]) > float(note_ts) and _nfc(f["name"]).lower().endswith(".hwp")]
    if later and slack.download(later[-1]["url"], work / "final.hwp").is_measured:
        final = work / "final.hwp"
    metrics: dict = {"final": final.name}
    cache = work / "notion.json"
    txt, ins = hwp.text(final), hwp.inspect(final)
    if cache.exists() and txt.is_measured and ins.is_measured:
        d = json.loads(cache.read_text(encoding="utf-8"))
        items, _ = convert.convert(d["rows"], d["blocks"], work / "in" / "images")
        metrics.update(audit.coverage(items, txt.require(), ins.require().get("tables", [])))
    prof_p = work / "profile.json"
    learned = hwp.learn_format(final)
    if prof_p.exists() and learned.is_measured:
        prof, f = json.loads(prof_p.read_text(encoding="utf-8")), learned.require()
        body, table = f.get("body") or {}, f.get("table") or {}
        diff = {k: (a, b) for k, a, b in (("font", prof["font"], body.get("fontKo")), ("body_pt", prof["sizes"]["TXT"], body.get("fontPt")),
                                          ("table_pt", prof["sizes"]["TBL"], table.get("cellFontPt")), ("header_fill", prof["header_fill"], (table.get("headerFillColor") or "").lower()))
                if b not in (None, "") and a != b}
        metrics["format_diff"] = diff
    judgments.save(g.con, judgments.HUMAN, "", {key: ""}, [{"key": key, "verdict": "done", "reason": f"✅ v{v} · {final.name}", "raw": ""}], extra={key: metrics})
    g.put(key, "done", "done", {"final": str(final), "versions": v, "kept": metrics.get("kept"), "format_diff": metrics.get("format_diff", {})})
    print(f"  ✅ {title}: v{v} 완료 · 원문 생존 {metrics.get('kept', '?')} · 서식 차이 {list(metrics.get('format_diff', {}))}")


# ---------------------------------------------------------------- 로컬 한 건 (문 밖 · 시험용)

def run_local(title: str, db: str, form: Path, images: Path | None) -> int:
    """슬랙 없이 한 건. 요청 행을 만들지 않으니 followup 의 상태기계에 안 들어간다. 결과는 ~/kv-data/fund/requests/local-<제목>-<날짜>/vN.hwp."""
    g = _gate()
    key = f"local-{title}-{ledger.today()}"
    ok = _generate(g, key, title, db, form, images, _versions(g, key) + 1)
    if ok:
        v = _versions(g, key)
        made = g.row(key, f"generate{v}")
        print(_note_text(title, v, made, json.loads((_req_dir(key) / "profile.json").read_text(encoding="utf-8")) if v == 1 else None))
    return 0 if ok else 2


# ---------------------------------------------------------------- 보고서

def daily_report(g: Gate) -> None:
    today = ledger.today()
    rows = g.con.execute("SELECT key, kind, status, detail FROM actions WHERE created>=? ORDER BY created", (today,)).fetchall()
    if not rows:
        return
    by = Counter(k.rstrip("0123456789") + ":" + s for _, k, s, _ in rows)
    done = [(k, json.loads(d)) for k, kind, s, d in rows if kind == "done"]
    made = [json.loads(d) for k, kind, s, d in rows if kind.startswith("generate") and s == "done"]
    judge = Counter()
    for m in made:
        for k in ("slash_cells", "images_missing", "outside", "unknown", "emdash"):
            judge[k] += len(m.get("findings", {}).get(k, []))
    ex = [json.loads(e or "{}") for (e,) in g.con.execute("SELECT extra FROM judgments WHERE run=? AND created>=?", (judgments.HUMAN, today))]
    changed = [x for m in ex for x in (m.get("changed_in_hwp") or [])][:10]
    fmt = [f"{k}: 프로필 {a} → 제출본 {b}" for m in ex for k, (a, b) in (m.get("format_diff") or {}).items()]
    fb = [json.loads(d).get("text", "")[:120] for k, kind, s, d in rows if kind == "feedback"]
    head = (f"요청 {by['request:done']} · 접수 {by['ack:done'] + by['ack:dry']} · 빌드 {by['generate:done']}(실패 {by['generate:failed']})"
            f" · 전달 {by['deliver:done']}(dry {by['deliver:dry']}) · 완료 {len(done)} · 문 {g.level()}")
    md = [f"# 펀드제안서 레인 하루 보고 — {today}", "", head, "",
          "## 🚩 제출본에서만 고친 문장 (노션에 되돌려 적을 것)", ""] + ([f"- {s[:160]}" for s in changed] or ["없음(오늘 완료된 요청 없음)"])
    md += ["", "## 서식 — 제출본 실측 vs 프로필 (다음 공고 기본값 재료)", ""] + (fmt or ["차이 없음 / 완료 없음"])
    md += ["", "## 서식 피드백 (profile.json 손질 재료)", ""] + ([f"- {x}" for x in fb] or ["없음"])
    md += ["", "## 판단 필요 (오늘 빌드 합계)", ""] + [f"- {k} × {c}" for k, c in judge.most_common()]
    md += ["", "## 완료된 요청", ""] + ([f"- `{k}` 버전 {d.get('versions')} · 원문 생존 {d.get('kept', '?')}" for k, d in done] or ["없음"])
    out = _reports_dir() / f"{today}.md"
    first = not out.exists()
    out.write_text("\n".join(md), encoding="utf-8")
    if first:
        print(f"· 보고서 {out} | 슬랙 {report.daily(TASK, f'*펀드제안서 레인 하루* — {head}' + chr(10) + f'전체: `{out}`', _send)}")


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fund")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("followup", help="요청 채널을 읽어 접수·빌드·전달·재빌드·완료를 잇는다"); f.add_argument("--dry-run", action="store_true")
    r = sub.add_parser("run", help="슬랙 없이 한 건 (시험·검증)")
    r.add_argument("--title", required=True); r.add_argument("--db", required=True, help="노션 제안서 DB id 또는 링크")
    r.add_argument("--form", required=True, help="공고 서식 hwp"); r.add_argument("--images", help="📁 줄이 가리키는 그림 폴더")
    sub.add_parser("status"); sub.add_parser("report")
    a = p.parse_args(argv)
    try:
        if a.cmd == "followup":
            with lock.hold("fund-followup"):
                g = _gate(a.dry_run)
                print(f"· followup: {followup(g)} | 문 {g.level()}")
                return 0
        if a.cmd == "run":
            with lock.hold("fund-run"):
                return run_local(a.title, db_id_of(a.db) or a.db, Path(a.form).expanduser(), Path(a.images).expanduser() if a.images else None)
        if a.cmd == "report":
            daily_report(_gate()); return 0
        if a.cmd == "status":
            g = _gate()
            for k, kind, st, created in g.con.execute("SELECT key, kind, status, created FROM actions ORDER BY created DESC LIMIT 40"):
                print(f"  {created[:16]} {k:28} {kind:10} {st}")
            print(f"문 {g.level()} · 요청 {g.con.execute('SELECT count(*) FROM actions WHERE kind=?', ('request',)).fetchone()[0]}건")
            return 0
    except lock.Busy as e:
        print(f"🔴 {e}")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
