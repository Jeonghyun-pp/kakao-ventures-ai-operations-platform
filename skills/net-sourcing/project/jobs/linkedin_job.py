"""링크드인 채널을 kv-core 잡으로 감싸는 스캐폴드 — W3 첫 적용(결정 A·B·C).

⚠️  스캐폴드다. `--live` 없이는 실 수집/노션을 건드리지 않는다(기본 dry-run = 합성).
    실 워크로드 검증은 사람이 깨어서 `--live` 로 한 번 돌려 인터페이스를 확정한다(결정: 어려운
    소비자로 검증). 밤샘 자동작업은 이 파일을 실행하지 않는다.

## 매핑 — 세 결정이 여기서 어떻게 붙나

    잡 경계(A)   = 링크드인 채널 1개.  job_id = "linkedin-<UTC날짜>"
    커서(A)      = 창업자 1명.  events 의 item_done 한 줄. 폴더 안 만든다
    멱등 키(C)   = LinkedIn URL.  고유·불변·정확 → 재개 시 이미 착지한 사람 건너뜀
    부작용→커서  = 노션 upsert 완료 뒤에 item_done.  (job.run 이 순서를 강제)
    진실(B)      = events.jsonl.  state·cursor 는 재생 결과
    실패(A)      = 한 명 실패는 item_skipped 후 계속. 0명 착지면 validate 가 실패 처리

## 실 배선에서 채울 두 곳 (TODO)

    ① items  — pipeline.collect() 로 수집한 뒤 judge 를 거친 '착지 후보' 리스트
    ② process — notion.stage(cfg, [card]) 로 한 명 착지. stage 는 이미 dedup 키로 upsert 한다
                (core/notion.py: _person_key · existing_dedup_keys · stage)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # project 루트
from kv_core import job  # noqa: E402

SKILL = "net-sourcing"
CHANNEL = "linkedin"


def _linkedin_url(card: dict) -> str:
    """멱등 키 — LinkedIn URL. 없으면 착지 불가(키 없는 항목은 처리하지 않는다)."""
    url = (card.get("linkedin_url") or card.get("url") or "").strip().rstrip("/")
    if not url:
        raise ValueError("linkedin_url 없음 — 멱등 키가 없어 착지 불가")
    return url.lower()


def _land_one(card: dict) -> None:
    """부작용 — 창업자 한 명을 노션에 착지(upsert). **성공해야 커서가 전진한다.**

    실 배선(TODO): from core import notion, config → notion.stage(cfg, [card]).
    stage 는 existing_dedup_keys 로 이미 있는 사람을 upsert 하므로 두 번 와도 안전하다(멱등).
    """
    raise NotImplementedError(
        "실 배선 필요 — core.notion.stage(cfg, [card]) 를 연결하고 --live 로 확정할 것")


def _collect_candidates() -> list[dict]:
    """수집 → judge → 착지 후보. 실 배선(TODO): core.pipeline.collect(cfg, keywords, 'linkedin').

    ⚠️ collect 는 Apify 를 쓴다(회당 ~$10.5). gate.check('apify', ...) 통과 후에만 부른다.
    """
    raise NotImplementedError(
        "실 배선 필요 — core.pipeline.collect + judge 후 후보 리스트를 반환할 것")


def run_live() -> None:
    """실 실행 — 사람이 깨어서만. Apify 과금 + 실 노션 쓰기가 발생한다."""
    jid = job.create(SKILL, {"channel": CHANNEL})
    candidates = _collect_candidates()
    job.run(
        SKILL, jid, candidates,
        key=_linkedin_url,
        process=_land_one,
        validate=lambda done, skipped: done > 0,   # 불변규칙 06: 0명 착지 = 실패
        heavy=True,                                 # 수집은 무거운 잡 → 동시 1개(결정 D)
    )
    print(f"[linkedin] 최종 상태: {job.current_state(SKILL, jid)}")


def run_dry() -> None:
    """dry-run — 합성 후보로 잡 구조만 밟는다. 부작용 없음(노션·Apify 안 건드림)."""
    jid = job.create(SKILL, {"channel": CHANNEL, "dry": True}, job_id="linkedin-dry")
    fake = [{"linkedin_url": f"https://linkedin.com/in/founder{i}"} for i in range(5)]
    landed: list[str] = []
    job.run(
        SKILL, jid, fake,
        key=_linkedin_url,
        process=lambda c: landed.append(c["linkedin_url"]),   # 부작용 대역: 메모리 append
        validate=lambda done, skipped: done > 0,
        heavy=False,
    )
    print(f"[linkedin dry] 상태={job.current_state(SKILL, jid)} · 착지(합성) {len(landed)}명")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="링크드인 채널 잡 (기본 dry-run)")
    ap.add_argument("--live", action="store_true",
                    help="실 수집·노션 착지 (Apify 과금 발생 — 사람이 깨어서만)")
    args = ap.parse_args()
    (run_live if args.live else run_dry)()
