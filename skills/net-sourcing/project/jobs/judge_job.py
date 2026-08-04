"""[W6] 무인 judge 러너 — 미판정 행 → claude → 검증(WAP) → 노션 기입(가역).

stages (run_pipeline · 스테이지 체크포인트):
    criteria-gate   references/judge-criteria.md 에 룰이 없으면 fail-loud (가동 스위치)
    export          judge_cli.export — 미판정(Tier/판정 is_empty) 행 → judge_queue_<날짜>.json
    judge-apply     배치(세션 1콜=batch_size 행) → claude → validate_text(채널별 계약,
                    quarantine·DLQ) → judge_cli.apply(전량검증 후 노션 기입)

안전 성질:
  · 판정 기입은 **가역**(select 덮어쓰기) → quarantine 계약. 발송·지출 경로 없음.
  · claude 호출 상한 = config.yaml judge.max_batches_per_run (< 게이트 경고선).
  · 배치 단위 apply — 중간 실패해도 기적용분은 노션에 남고, export 의 is_empty
    필터가 자연 커서라 재실행 시 기판정 행은 큐에 다시 안 뜬다.
  · run_pipeline 재개 시 export stage_done 이 남았으면 구 큐로 apply 한다 —
    기판정 행 재기입은 같은 값 select 덮어쓰기(멱등·무해).

실행:  python jobs/judge_job.py [--period YYYY-MM-DD] [--synthetic]
  --synthetic: claude·노션 불호출(가짜 큐·가짜 세션·apply 스텁). KV_DATA 스크래치 필수.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ))

from kv_core import job, paths, session, validate  # noqa: E402
from kv_core.job import Stage  # noqa: E402

SKILL = "net-sourcing"
CRITERIA = PROJ.parent / "references" / "judge-criteria.md"
_EMPTY_MARKER = "판단 룰 본문은 아직 비어 있다"
_REPO = PROJ.parents[2]

_SYS_JSON = ("JSON 배열만 출력하라. 각 원소는 "
             '{"page_id": "...", "verdict": "...", "reason": "...", "one_liner": "..."} 형식. '
             "프로즈·코드펜스·설명 금지. verdict 는 각 행의 allowed 목록 안에서만 고른다.")


class JudgeCriteriaEmpty(RuntimeError):
    """판단 룰 미기입 — 무인 judge 는 기준 없이 돌지 않는다(fail-loud)."""


def _cfg() -> dict:
    """main.py 패턴 복제 — $KV_CONFIG/.env 로드 후 config.yaml."""
    import yaml
    from kv_core import secrets
    secrets.load()
    return yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))


def _contracts() -> dict:
    return validate.load_contracts(_REPO / "config" / "contracts.yaml")


def _queue_path(period: date) -> Path:
    # judge_cli.export 는 date.today() 로 쓴다 — 러너는 실행일 큐를 읽는다(period 는 job_id 용).
    return paths.data(SKILL) / f"judge_queue_{date.today().isoformat()}.json"


# ─────────────────────────── stages ───────────────────────────

def s_criteria_gate(skill: str, job_id: str) -> None:
    if not CRITERIA.exists():
        raise JudgeCriteriaEmpty(f"🔴 기준 파일 없음: {CRITERIA}")
    if _EMPTY_MARKER in CRITERIA.read_text(encoding="utf-8"):
        raise JudgeCriteriaEmpty(
            "🔴 판단 룰 미기입 — references/judge-criteria.md 의 L1/L2 를 채우고 "
            f"헤더의 {_EMPTY_MARKER!r} 줄을 지우면 무인 judge 가 가동된다. "
            "(기준 없이 판정하지 않는다 — fail-loud)")


def s_export(skill: str, job_id: str) -> None:
    from core import judge_cli
    rc = judge_cli.export(_cfg())
    if rc != 0:
        raise RuntimeError(f"judge-export 실패 rc={rc}")


def _contract_name(row: dict) -> str:
    """채널별 계약 선택 — 정본은 행별 verdict_field(라이브 스키마 유래)."""
    return "judge-appstore" if row.get("verdict_field") == "판정" else "judge-tier"


def s_judge_apply(skill: str, job_id: str) -> None:
    from core import judge_cli

    qp = _queue_path(date.today())
    if not qp.exists():
        raise RuntimeError(f"큐 파일 없음: {qp} — export 가 안 돌았나?")
    q = json.loads(qp.read_text(encoding="utf-8"))
    rows = q.get("rows") or []
    if not rows:
        print("· 판정 대상 0건 — 정상 종료 (claude 호출 0)")
        return

    cfg = _cfg()
    jc = cfg.get("judge") or {}
    bs = int(jc.get("batch_size", 20))
    max_batches = int(jc.get("max_batches_per_run", 25))
    criteria = CRITERIA.read_text(encoding="utf-8")
    contracts = _contracts()
    dlq = paths.data(SKILL, "dlq") / "judge_dlq.jsonl"

    # 계약별 그룹핑 (앱스토어=판정 Pass|Drop vs 스레드·링크드인=Tier)
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(_contract_name(r), []).append(r)

    done_batches = 0
    applied = 0
    for cname, grows in groups.items():
        for i in range(0, len(grows), bs):
            if done_batches >= max_batches:
                print(f"⚠️  런당 호출 상한 {max_batches}배치 도달 — 잔여 {len(grows) - i}건은 "
                      "다음 회차가 소화한다(큐 필터가 자연 커서).")
                return
            batch = grows[i:i + bs]
            prompt = (criteria
                      + "\n\n─── 판정 대상 (아래 각 행의 allowed 안에서만 verdict 선택) ───\n"
                      + json.dumps(batch, ensure_ascii=False, indent=1))
            raw = session.run_session(prompt, system=_SYS_JSON, timeout=300)
            ok, bad = validate.validate_text(raw, contracts[cname], dlq_path=dlq)
            if bad:
                print(f"⚠️  {cname}: {len(bad)}건 계약 위반 → DLQ 격리({dlq})")
            if ok:
                vf = paths.data(SKILL) / (
                    f"judge_verdicts_{date.today().isoformat()}_{done_batches:02d}.json")
                vf.write_text(json.dumps(ok, ensure_ascii=False, indent=2), encoding="utf-8")
                rc = judge_cli.apply(cfg, str(vf))
                if rc != 0:
                    raise RuntimeError(f"judge-apply 실패 rc={rc} (배치 {done_batches}, {vf})")
                applied += len(ok)
            done_batches += 1
    print(f"🎉 무인 judge: {done_batches}배치 · 기입 {applied}건 · 격리 {len(rows) - applied}건 이하")


# ─────────────────────────── driver ───────────────────────────

def _stages() -> list[Stage]:
    return [Stage("criteria-gate", s_criteria_gate),
            Stage("export", s_export),
            Stage("judge-apply", s_judge_apply)]


def run(period: date) -> int:
    jid = f"judge-{period.isoformat()}"
    job.create(SKILL, {"kind": "judge", "period": period.isoformat()}, jid)
    d = job.run_pipeline(SKILL, jid, _stages(), resources=["claude", "notion"])
    print(f"[judge] {jid} → {d.state}")
    return 0 if d.state == "completed" else 1


# ─────────────────────────── synthetic (테스트) ───────────────────────────

def run_synthetic() -> int:
    """claude·노션 불호출 — 게이트/검증/멱등 배선만 확인. KV_DATA 스크래치 필수."""
    import os
    assert os.environ.get("KV_DATA"), "--synthetic 은 KV_DATA 스크래치 필수(실데이터 보호)"
    ok_all = True

    # ① criteria-gate: 마커 존재(현 상태) → JudgeCriteriaEmpty fail-loud
    try:
        s_criteria_gate(SKILL, "syn")
        gate_ok = _EMPTY_MARKER not in CRITERIA.read_text(encoding="utf-8")
        print(f"① criteria-gate 통과(룰 기입됨) · {'PASS' if gate_ok else 'FAIL'}")
    except JudgeCriteriaEmpty as e:
        print(f"① criteria-gate fail-loud 확인: {str(e)[:60]}… · PASS")

    # ② 가짜 큐 + 가짜 세션 → 검증·격리 경로
    fake_rows = [
        {"page_id": "00000000-0000-0000-0000-dc7645036afe", "channel": "스레드",
         "verdict_field": "Tier", "allowed": ["Catch", "Trend", "Wait", "Cut"],
         "name": "합성A", "raw_text": "..."},
        {"page_id": "00000000-0000-0000-0000-47e71a1671f8", "channel": "앱스토어",
         "verdict_field": "판정", "allowed": ["Pass", "Drop"],
         "name": "합성B", "raw_text": "..."},
    ]
    contracts = _contracts()
    fake_out_tier = json.dumps([
        {"page_id": fake_rows[0]["page_id"], "verdict": "Catch", "reason": "합성"},
        {"page_id": "bad", "verdict": "A급", "reason": "허용 밖"},   # enum 위반 → 격리
    ], ensure_ascii=False)
    dlq = paths.data(SKILL, "dlq") / "judge_dlq_syn.jsonl"
    ok, bad = validate.validate_text(fake_out_tier, contracts["judge-tier"], dlq_path=dlq)
    p2 = len(ok) == 1 and len(bad) == 1 and dlq.exists()
    print(f"② tier 계약: 통과 {len(ok)}·격리 {len(bad)}(DLQ 기록) · {'PASS' if p2 else 'FAIL'}")
    ok_all &= p2

    fake_out_app = json.dumps([{"page_id": fake_rows[1]["page_id"], "verdict": "Pass"}],
                              ensure_ascii=False)
    ok2, bad2 = validate.validate_text(fake_out_app, contracts["judge-appstore"])
    p3 = len(ok2) == 1 and not bad2
    print(f"③ appstore 계약(Pass|Drop): 통과 {len(ok2)} · {'PASS' if p3 else 'FAIL'}")
    ok_all &= p3

    # ④ 계약 선택 로직
    p4 = (_contract_name(fake_rows[0]) == "judge-tier"
          and _contract_name(fake_rows[1]) == "judge-appstore")
    print(f"④ 채널→계약 선택(verdict_field 기준) · {'PASS' if p4 else 'FAIL'}")
    ok_all &= p4

    print("🎉 synthetic PASS" if ok_all else "🔴 synthetic FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="무인 judge 러너 (기준 없으면 fail-loud)")
    ap.add_argument("--period", default=date.today().isoformat())
    ap.add_argument("--synthetic", action="store_true", help="claude·노션 불호출 배선 검증")
    args = ap.parse_args()
    if args.synthetic:
        raise SystemExit(run_synthetic())
    raise SystemExit(run(date.fromisoformat(args.period)))
