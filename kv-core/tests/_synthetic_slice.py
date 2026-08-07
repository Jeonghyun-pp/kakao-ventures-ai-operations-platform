"""수직선 A 합성 증명 — 그물망 미팅메일 파이프라인을 claude -p 로 end-to-end. [W5]

    collect(코드) → judge(claude -p) → validating(WAP Audit) → draft(claude -p, 로컬 초안)

증명하는 것(전부 무발송·무과금):
  1. claude 판단/초안이 session.py 통로로 실제로 나온다.
  2. validating 이 quarantine(가역: 나쁜 판정만 격리) / halt(비가역: 발송 앞 전체중단)를 계약대로 한다.
  3. 재실행이 멱등하다 — 완료 스테이지 스킵, 초안 중복 0, **claude 재호출 0**.
  4. 발송 0 — 초안은 로컬 파일 스탠드인, kv_core.gmail 은 부르지 않는다(구조적 금지 유지).

주의: 판단/초안 스테이지는 실제 claude 를 부른다(구독 쿼터 소량). halt 는 claude 를 억지로
틀리게 만들 수 없어 **결함 주입(INJECT_BAD)** 으로 검증한다 — soft 출력이 계약을 어긴 상황 재현.
"""
from __future__ import annotations

import os
os.environ.setdefault("KV_NO_SEND", "1")   # [W9] 실발송 킬 스위치 — 스텁 누락 시의 최후 방어

import json
import os
import sys
from pathlib import Path

# scratch KV_DATA (실 데이터 안 건드림)
os.environ.setdefault("KV_DATA", str(Path(__file__).resolve().parent / "_scratch_slice_data"))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import job, paths, session, validate as V  # noqa: E402
from kv_core import gmail  # noqa: E402  (구조적 무발송 확인용)

SK = "synthetic-slice"
REPO = Path(__file__).resolve().parents[2]
CONTRACTS = V.load_contracts(REPO / "config" / "contracts.yaml")

_SYS_JSON = "You output ONLY valid JSON matching the requested shape. No prose, no code fences."

# claude 호출 횟수 카운터(멱등 재실행 시 0 증명용)
_CALLS = {"n": 0}
_real_run_session = session.run_session


def _counting_session(*a, **k):
    _CALLS["n"] += 1
    return _real_run_session(*a, **k)


session.run_session = _counting_session   # 이 프로세스 안에서만


def _work() -> Path:
    return paths.data(SK, "work")


def _drafts() -> Path:
    return paths.data(SK, "drafts")


# ─────────────────────────── 스테이지 ───────────────────────────

def s_collect(skill: str, job_id: str) -> None:
    """코드: 합성 후보를 큐로. 명백한 keep 2 + promo 1."""
    cands = [
        {"id": 0, "handle": "gaboo_kim", "text": "제가 만든 가계부 앱을 드디어 출시했어요! 피드백 환영합니다"},
        {"id": 1, "handle": "news_bot", "text": "OpenAI가 새 모델을 발표했다는 소식을 전합니다"},
        {"id": 2, "handle": "stealth_lee", "text": "6개월째 팀과 B2B SaaS 만들고 있어요. 곧 베타 오픈"},
    ]
    (_work() / "candidates.json").write_text(json.dumps(cands, ensure_ascii=False), encoding="utf-8")


def s_judge(skill: str, job_id: str) -> None:
    """claude -p: keep/promo/unsure 분류 → WAP Audit(quarantine) → 통과분만 저장."""
    cands = json.loads((_work() / "candidates.json").read_text(encoding="utf-8"))
    lines = "\n".join(f'[{c["id"]}] @{c["handle"]}: {c["text"]}' for c in cands)
    prompt = (
        "다음 게시글을 keep(작성자 본인이 만든 제품 홍보) / promo(남의 소식·광고) / unsure 로 분류해.\n"
        f"{lines}\n\n"
        'JSON 배열로만: [{"id":0,"verdict":"keep|promo|unsure","reason":"짧게"}]'
    )
    raw = session.run_session(prompt, system=_SYS_JSON, timeout=120)
    ok, bad = V.validate_text(raw, CONTRACTS["judge"], dlq_path=_work() / "judge_dlq.json")
    (_work() / "verdicts.json").write_text(json.dumps(ok, ensure_ascii=False), encoding="utf-8")
    print(f"   [judge] claude 판정 {len(ok)+len(bad)}건 → 통과 {len(ok)} · 격리 {len(bad)}")


def s_draft(skill: str, job_id: str) -> None:
    """claude -p: keep 후보에게 미팅요청 초안 생성 → WAP Audit → 로컬 초안 파일.

    [W9 Phase0] 계약은 **quarantine** 이다(contracts.yaml `meeting-mail`). 이 독스트링과
    아래 ③ 케이스가 오래 "halt" 라고 적혀 있었는데 선언은 W6 에 quarantine 으로 바뀌었다 —
    테스트가 **선언이 아니라 기억을 단언**하던 것(W9 '선언 불일치' 유형). 초안 생성은
    발송이 아니고 뒤에 승인 게이트가 있으므로, 한 건 어긋났다고 전량 중단할 이유가 없다.

    다만 격리는 **기록으로 남아야** 한다 — dlq_path 없이 quarantine 을 부르면 어긋난
    레코드가 조용히 사라진다(불변 규칙: 조용한 삭제 없음). 그래서 DLQ 를 넘긴다.
    """
    verdicts = json.loads((_work() / "verdicts.json").read_text(encoding="utf-8"))
    keeps = [v for v in verdicts if v.get("verdict") == "keep"]

    drafts: list[dict] = []
    for v in keeps:
        prompt = (
            f'@{v["id"]} 창업자에게 보낼 짧은 미팅 요청 메일 초안을 써줘(한국어, 벤처캐피털 심사역 톤).\n'
            'JSON 으로만: {"to":"수신자표시","subject":"제목","body":"본문"}'
        )
        raw = session.run_session(prompt, system=_SYS_JSON, timeout=120)
        rec = V.parse_json(raw)
        if isinstance(rec, list):
            rec = rec[0] if rec else {}
        rec["_id"] = str(v["id"])
        drafts.append(rec)

    if os.environ.get("INJECT_BAD"):          # 결함 주입: soft 출력이 계약을 어긴 상황
        drafts.append({"to": "x", "subject": "결함", "body": "", "_id": "BAD"})

    # WAP Audit(halt) — 하나라도 어긋나면 ValidationHalt 로 스테이지 실패(아무것도 발행 안 함)
    ok, _ = V.validate_records(drafts, CONTRACTS["meeting-mail"],
                               dlq_path=_work() / "draft_dlq.jsonl")

    # 통과분만 로컬 초안으로(멱등). gmail 은 부르지 않는다 — 발송 아님.
    def _write(rec: dict) -> None:
        p = _drafts() / f'{rec["_id"]}.txt'
        p.write_text(f'TO: {rec["to"]}\nSUBJECT: {rec["subject"]}\n\n{rec["body"]}\n',
                     encoding="utf-8")

    job.process_items(skill, job_id, ok, key=lambda r: r["_id"], process=_write, stage="draft")
    print(f"   [draft] 초안 {len(ok)}건 → 로컬 파일(발송 아님)")


def _stages() -> list:
    return [job.Stage("collect", s_collect),
            job.Stage("judge", s_judge),
            job.Stage("draft", s_draft)]


def _run(job_id: str) -> "job.Derived":
    jid = job.create(SK, {"channel": job_id}, job_id=job_id)
    return job.run_pipeline(SK, jid, _stages(), resources=["claude"])


# ─────────────────────────── 증명 시나리오 ───────────────────────────

def main() -> int:
    import shutil
    scratch = Path(os.environ["KV_DATA"])
    if scratch.exists():
        shutil.rmtree(scratch)                 # 깨끗한 시작

    ok_all = True

    print("① 정상 경로 (claude 판단+초안, 무발송)")
    d = _run("happy")
    n_drafts = len(list(_drafts().glob("*.txt"))) if _drafts().exists() else 0
    calls_after_1 = _CALLS["n"]
    p1 = d.state == "completed" and n_drafts > 0
    print(f"   → 상태={d.state}, 초안 {n_drafts}건, claude 호출 {calls_after_1}회 · {'PASS' if p1 else 'FAIL'}")
    ok_all &= p1

    print("② 멱등 재실행 (완료 스테이지 스킵, claude 재호출 0, 초안 중복 0)")
    d2 = _run("happy")
    new_calls = _CALLS["n"] - calls_after_1
    n_drafts2 = len(list(_drafts().glob("*.txt")))
    p2 = d2.state == "completed" and new_calls == 0 and n_drafts2 == n_drafts
    print(f"   → 상태={d2.state}, 새 claude 호출 {new_calls}회, 초안 {n_drafts2}건(불변) · {'PASS' if p2 else 'FAIL'}")
    ok_all &= p2

    print("③ 결함 주입 (계약 위반 초안 → 격리·기록, 발행 0 / 정상분은 진행)")
    os.environ["INJECT_BAD"] = "1"
    d3 = _run("quarantine-test")
    bad_drafts = _drafts() / "BAD.txt"
    dlq = _work() / "draft_dlq.jsonl"
    # [W9 Phase0] 단언을 **선언과 일치**시켰다. 예전엔 state=="failed"(halt)를 기대했는데
    # contracts.yaml 은 quarantine 이라 영구 FAIL 이었다. quarantine 의 계약은 세 가지:
    #   ① 어긋난 건은 발행되지 않는다 ② 정상분은 진행한다 ③ **격리가 기록에 남는다**
    dlq_has_bad = dlq.exists() and "BAD" in dlq.read_text(encoding="utf-8")
    p3 = d3.state == "completed" and not bad_drafts.exists() and dlq_has_bad
    print(f"   → 상태={d3.state}(completed 기대), BAD 초안={bad_drafts.exists()}(False 기대), "
          f"DLQ 격리기록={dlq_has_bad}(True 기대) · {'PASS' if p3 else 'FAIL'}")
    ok_all &= p3
    del os.environ["INJECT_BAD"]

    print("④ 구조적 무발송 (AST: gmail·approvals_tick·slackbot 에 .send() 호출 0건)")
    # 예전 검증(_assert_no_send 호출→raise 확인)은 동어반복이었다 — send 헬퍼가 추가돼도
    # 통과했다(리뷰 D2). 이제 소스 AST 에서 실제 .send 호출 부재를 직접 단언한다.
    # [W8] 검사 대상 확장: 승인 실행기·슬랙봇도 — 검사의 목적은 **gmail 발송** 차단이다
    # (slack chat.postMessage 는 requests.post 라 .send 패턴에 안 걸림 — 의도된 범위).
    import ast as _ast
    from kv_core import approvals_tick as _at, slackbot as _sb
    _send_calls = []
    for _mod in (gmail, _at, _sb):
        _src = Path(_mod.__file__).read_text(encoding="utf-8")
        _send_calls += [f"{Path(_mod.__file__).name}:L{n.lineno}"
                        for n in _ast.walk(_ast.parse(_src))
                        if isinstance(n, _ast.Call) and isinstance(n.func, _ast.Attribute)
                        and n.func.attr == "send"]
    p4 = not _send_calls
    print(f"   → 3개 모듈 .send() 호출 {len(_send_calls)}건(0 기대){_send_calls or ''}, "
          f"초안 경로={_drafts()} · {'PASS' if p4 else 'FAIL'}")
    ok_all &= p4

    print("\n" + ("🎉 W5 수직선 증명 전부 PASS" if ok_all else "🔴 일부 FAIL"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
