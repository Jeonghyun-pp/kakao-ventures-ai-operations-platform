"""hello-mail — Hello 계정 메일 트리아지 하네스.

  triage : 인박스 스캔 → 분류 → 계획 리포트. **아무것도 바꾸지 않는다.** (기본)
  apply  : 직전 계획을 실행 — 아카이빙 + 초안 생성. 발송은 하지 않는다.
  run    : triage + apply

4개 루틴 태스크(광고성 아카이빙 / Webflow 문의 / 협력·섭외·채용·출자 / 인바운드 투자)를
하나의 파이프라인으로 처리한다: 스캔 → 분류 → 라우팅 → 액션.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from core import gmail, llm, route, rules, state

PROJ = Path(__file__).resolve().parent
load_dotenv(PROJ / ".env", override=True)

ICON = {"protect": "🔒", "archive": "📥", "reply": "✉️", "forward": "📤", "escalate": "🚩"}


def cfg() -> dict:
    # [fork] 설정은 하네스 최상위 통합 config 하나만 쓴다 (드리프트 방지).
    return yaml.safe_load((PROJ.parent / "config.yaml").read_text())


def _plan_path(ts: str) -> Path:
    return PROJ / "data" / f"plan_{ts}.json"


def _latest_plan() -> Path | None:
    plans = sorted((PROJ / "data").glob("plan_*.json"))
    return plans[-1] if plans else None


# ── triage ──────────────────────────────────────────────────────────────

def triage(c: dict, quiet: bool = False) -> Path:
    svc = gmail.service()
    msgs = gmail.inbox_messages(svc, c["scan"]["lookback_days"], c["scan"]["max_threads"])
    fresh = [m for m in msgs if not state.seen(m["id"])]

    if not quiet:
        print(f"📬 인박스 {len(msgs)}건 (최근 {c['scan']['lookback_days']}일) "
              f"— 미처리 {len(fresh)}건\n")

    # ① 규칙 (결정적)
    hits: dict[str, dict] = {}
    todo_llm = []
    for m in fresh:
        h = rules.classify(m, c)
        if h:
            hits[m["id"]] = h
        else:
            todo_llm.append(m)

    # ② LLM (규칙이 못 잡은 것만). 키가 없으면 전부 UNKNOWN → 사람에게.
    if todo_llm:
        if not quiet:
            state_msg = "LLM 분류" if llm.available() else "LLM 키 없음 → UNKNOWN 처리"
            print(f"   규칙 {len(hits)}건 확정 · 나머지 {len(todo_llm)}건 {state_msg}\n")
        for mid, h in llm.classify(todo_llm, c).items():
            hits[mid] = h

    # ③ 라우팅 + 명시적 심사역 지명 오버라이드
    plan = []
    by_msg = {m["id"]: m for m in fresh}
    for mid, h in hits.items():
        m = by_msg[mid]
        # 특정 심사역이 지명된 섭외/제안은 그 사람에게 직접 포워드 (가이드라인)
        partner = rules.find_named_partner(m, c)
        if partner and h["category"] in (rules.EVENT_SPEAKER, rules.PARTNERSHIP, rules.UNKNOWN):
            act = {"action": "forward", "to": partner,
                   "body": route._fwd(c, "심사역님 앞으로 온 메일이 있어 포워드 드립니다."),
                   "note": f"특정 심사역 지명 → {partner} 포워드"}
        else:
            act = route.decide(m, h, c)
        plan.append({
            "id": mid, "thread_id": m["thread_id"], "subject": m["subject"],
            "sender": m["sender"], "category": h["category"],
            "why": h.get("reason", ""), **act,
        })

    plan.sort(key=lambda p: (p["action"] != "escalate", p["category"]))

    if not quiet:
        _report(plan)

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = _plan_path(ts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    if not quiet:
        print(f"\n📝 계획 저장: {path.name}")
        print("   실행하려면: main.py apply")
    return path


def _report(plan: list[dict]):
    if not plan:
        print("✅ 처리할 새 메일이 없습니다.")
        return
    counts: dict[str, int] = {}
    for p in plan:
        counts[p["action"]] = counts.get(p["action"], 0) + 1
    print("── 계획 요약 " + "─" * 50)
    for a, n in counts.items():
        print(f"   {ICON.get(a,'?')} {a:9s} {n}건")
    print("─" * 62 + "\n")

    for p in plan:
        print(f"{ICON.get(p['action'],'?')} [{p['category']}] {p['subject'][:58]}")
        print(f"   from {p['sender']}  ·  {p['why']}")
        print(f"   → {p['note']}")
        if p.get("to"):
            print(f"   → 수신자: {p['to']}")
        if p.get("also_reply"):
            print(f"   → (추가 초안) {p['also_reply']['note']}")
        print()


# ── apply ───────────────────────────────────────────────────────────────

def apply(c: dict, plan_path: Path | None = None):
    plan_path = plan_path or _latest_plan()
    if not plan_path or not plan_path.exists():
        print("❌ 실행할 계획이 없습니다. 먼저 `main.py triage`를 실행하세요.")
        sys.exit(1)

    plan = json.loads(plan_path.read_text())
    svc = gmail.service()
    done = {"archive": 0, "reply": 0, "forward": 0, "escalate": 0, "protect": 0}
    fails = []

    for p in plan:
        if state.seen(p["id"]):
            continue
        a = p["action"]
        msg = {"id": p["id"], "thread_id": p["thread_id"], "subject": p["subject"],
               "sender": p["sender"], "sender_raw": p["sender"], "to": [],
               "message_id_header": None, "date": "", "body": ""}
        try:
            if a == "protect":
                done["protect"] += 1
                continue                      # 이력에도 남기지 않는다 — 매번 그냥 지나친다

            if a == "escalate":
                done["escalate"] += 1
                state.mark(p["id"], p["category"], a, p["note"])
                continue

            if a == "archive":
                if not c["autonomy"]["archive_auto"]:
                    print(f"   (archive_auto=false — 건너뜀) {p['subject'][:40]}")
                    continue
                gmail.archive(svc, p["id"])
                done["archive"] += 1

            elif a in ("reply", "forward"):
                # 초안을 만들려면 원본을 다시 읽어야 한다 (인용·In-Reply-To 헤더).
                full = svc.users().messages().get(
                    userId="me", id=p["id"], format="full").execute()
                hdr = {h["name"].lower(): h["value"]
                       for h in full.get("payload", {}).get("headers", [])}
                msg.update({
                    "message_id_header": hdr.get("message-id"),
                    "date": hdr.get("date", ""),
                    "sender_raw": hdr.get("from", p["sender"]),
                    "to": gmail._addrs(hdr.get("to", "")),
                    "body": gmail._body_text(full.get("payload", {})),
                })
                fn = gmail.draft_reply if a == "reply" else gmail.draft_forward
                fn(svc, msg, p["body"], p["to"])
                done[a] += 1
                if p.get("also_reply"):
                    ar = p["also_reply"]
                    gmail.draft_reply(svc, msg, ar["body"], ar["to"])
                    done["reply"] += 1

            state.mark(p["id"], p["category"], a, p.get("note", ""))
            print(f"{ICON.get(a,'?')} {a:8s} {p['subject'][:52]}")

        except Exception as e:
            fails.append((p["subject"][:50], str(e)))
            print(f"❌ 실패 [{a}] {p['subject'][:45]} — {e}")

    print("\n── 실행 결과 " + "─" * 50)
    print(f"   📥 아카이브 {done['archive']}  ·  ✉️ 회신초안 {done['reply']}  ·  "
          f"📤 포워드초안 {done['forward']}")
    print(f"   🚩 사람 확인 필요 {done['escalate']}  ·  🔒 비플랜 보호 {done['protect']}")
    if fails:
        print(f"\n⚠️ 실패 {len(fails)}건:")
        for s, e in fails:
            print(f"   - {s}: {e}")
    if done["reply"] or done["forward"]:
        print("\n📮 초안이 Gmail에 생성되었습니다. **발송은 직접 확인 후 하세요.**")
        print("   (수신자·성함·사명을 마지막까지 확인 — 발송 후 1분간 undo 가능)")


def reclassify(c: dict, assigns: list[str]):
    """UNKNOWN 건에 카테고리를 주입하고 다시 라우팅한다.

    ANTHROPIC_API_KEY 없이 Claude Code 안에서 쓰는 경로다. Claude가 메일을 읽고 분류해
    `reclassify <msg_id>=INBOUND_KO ...` 형태로 넣으면, 계획이 갱신된다.
    (키가 있으면 core/llm.py가 같은 일을 무인으로 한다)
    """
    path = _latest_plan()
    if not path:
        print("❌ 계획이 없습니다. 먼저 triage를 실행하세요.")
        sys.exit(1)
    plan = json.loads(path.read_text())
    idx = {p["id"]: p for p in plan}

    for a in assigns:
        if "=" not in a:
            print(f"⚠️ 형식 오류(무시): {a}  — 'msg_id=CATEGORY' 형태여야 합니다")
            continue
        mid, cat = a.split("=", 1)
        cat = cat.strip().upper()
        if mid not in idx:
            print(f"⚠️ 계획에 없는 id(무시): {mid}")
            continue
        if cat not in rules.LLM_CATEGORIES:
            print(f"⚠️ 알 수 없는 카테고리(무시): {cat}")
            continue
        p = idx[mid]
        msg = {"id": mid, "thread_id": p["thread_id"], "subject": p["subject"],
               "sender": p["sender"], "body": ""}
        act = route.decide(msg, {"category": cat, "reason": "사람/Claude 분류"}, c)
        # 이전 라우팅의 잔여 필드를 먼저 지운다. 안 지우면 예컨대 UNKNOWN(→슬랙 forward)이
        # RECRUIT(→escalate)로 바뀌어도 슬랙 주소가 남아 리포트가 거짓말을 한다.
        for stale in ("to", "body", "also_reply", "unknown"):
            p.pop(stale, None)
        p.update({"category": cat, "why": "사람/Claude 분류", **act})
        print(f"✏️  {cat:14s} {p['subject'][:50]}  →  {act['note']}")

    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2))
    print(f"\n📝 계획 갱신: {path.name}\n   실행하려면: main.py apply")


def main():
    ap = argparse.ArgumentParser(prog="hello-mail")
    ap.add_argument("cmd", choices=["triage", "apply", "run", "reclassify"],
                    nargs="?", default="triage")
    ap.add_argument("assigns", nargs="*", help="reclassify용: <msg_id>=<CATEGORY> ...")
    args = ap.parse_args()
    c = cfg()

    if args.cmd == "triage":
        triage(c)
    elif args.cmd == "apply":
        apply(c)
    elif args.cmd == "reclassify":
        reclassify(c, args.assigns)
    elif args.cmd == "run":
        path = triage(c)
        print("\n" + "=" * 62 + "\n실행합니다...\n")
        apply(c, path)


if __name__ == "__main__":
    main()
