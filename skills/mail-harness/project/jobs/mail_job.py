"""메일을 kv-core 잡으로 감싸는 스캐폴드 — W3 '어려운 소비자' 검증(결정 A·B·C).

⚠️  스캐폴드다. `--live` 없이는 실 Gmail 을 건드리지 않는다(기본 dry-run = 합성).
⚠️  이 파일은 **발송을 구조적으로 금지한다.** process 는 초안 작성·라우팅까지만 하고,
    발송 경로(send)를 절대 부르지 않는다. 아래 _assert_no_send 참조.
    실 검증은 사람이 깨어서 `--live` 로 돌린다. 밤샘 자동작업은 이 파일을 실행하지 않는다.

## 왜 메일이 '어려운 소비자'인가 (W1 교훈: 가장 요구 많은 소비자로 인터페이스 검증)

    · 잡 경계(A) 가 다르다 — 채널이 아니라 **메일 1통 = 잡** (완전 독립 + 하루 수십 통)
    · 부작용이 비가역 — 발송은 되돌릴 수 없다. 그래서 여기선 발송을 아예 안 한다
    · bplan 중복 초안 사고의 당사자 — 멱등 키(C)가 없으면 재개가 초안을 두 벌 만든다

## 매핑

    잡 경계(A)   = 메일 1통.  job_id = "mail-<message_id>"
    멱등 키(C)   = Gmail message_id.  고유·불변 → 재개 시 이미 처리한 메일 건너뜀
    부작용→커서  = 초안 저장/라우팅 완료 뒤 item_done
    진실(B)      = events.jsonl
    실패(A)      = 한 통 실패는 건너뛰고 계속

## 실 배선에서 채울 곳 (TODO)

    items   — 트리아지 대상 메일 목록 (message_id 포함)
    process — 초안 작성 + 라우팅. **발송 금지.** core/gmail.py 에 발송 경로가 없어야 정상
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import job  # noqa: E402

SKILL = "mail-harness"


def _message_key(mail: dict) -> str:
    """멱등 키 — Gmail message_id. 없으면 처리하지 않는다."""
    mid = (mail.get("message_id") or mail.get("id") or "").strip()
    if not mid:
        raise ValueError("message_id 없음 — 멱등 키가 없어 처리 불가")
    return mid


def _assert_no_send() -> None:
    """구조적 발송 금지 — 어떤 경로로도 send 가 불리면 즉시 멈춘다.

    선언(문서에 '발송 금지')이 아니라 구조(호출되면 예외)로 막는다. W1 교훈 #2.
    """
    raise RuntimeError("이 잡은 발송하지 않는다 — 초안·라우팅까지만. 발송이 필요하면 별도 승인 잡")


def _process_one(mail: dict) -> None:
    """부작용 — 한 통을 트리아지(초안 작성·라우팅). **발송 없음.** 성공해야 커서 전진.

    실 배선(TODO): triage 파이프라인을 message_id 기준으로 한 통 처리.
    발송은 절대 안 한다 — 초안은 Gmail 드래프트로만 남긴다(사람이 검토 후 보냄).
    """
    raise NotImplementedError(
        "실 배선 필요 — 트리아지(초안·라우팅)만 연결. 발송 경로는 연결하지 말 것")


def _collect_mails() -> list[dict]:
    """트리아지 대상 목록. 실 배선(TODO): 인박스에서 미처리 메일 조회."""
    raise NotImplementedError("실 배선 필요 — 미처리 메일 목록(message_id 포함) 반환")


def run_live() -> None:
    """실 실행 — 사람이 깨어서만. 실 Gmail 을 읽고 초안을 만든다. **발송은 없다.**"""
    mails = _collect_mails()
    processed = 0
    for mail in mails:
        jid = job.create(SKILL, {"kind": "mail", "message_id": mail.get("message_id")},
                         job_id=f"mail-{_message_key(mail)}")
        job.run(
            SKILL, jid, [mail],
            key=_message_key,
            process=_process_one,
            validate=lambda done, skipped: done > 0,
            heavy=False,
        )
        processed += 1
    print(f"[mail] {processed}통 처리(초안까지). 발송 0건.")


def run_dry() -> None:
    """dry-run — 합성 메일로 잡 구조만 밟는다. 발송·실 Gmail 없음."""
    drafted: list[str] = []
    fake = [{"message_id": f"msg-{i:03d}"} for i in range(4)]
    for mail in fake:
        jid = job.create(SKILL, {"kind": "mail", "message_id": mail["message_id"], "dry": True},
                         job_id=f"mail-dry-{mail['message_id']}")
        job.run(
            SKILL, jid, [mail],
            key=_message_key,
            process=lambda m: drafted.append(m["message_id"]),   # 부작용 대역: 초안했다 치고 기록
            validate=lambda done, skipped: done > 0,
            heavy=False,
        )
    print(f"[mail dry] 초안(합성) {len(drafted)}통 · 발송 0건")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="메일 잡 (기본 dry-run · 발송 금지)")
    ap.add_argument("--live", action="store_true",
                    help="실 Gmail 읽고 초안 작성 (발송은 하지 않음 — 사람이 깨어서만)")
    args = ap.parse_args()
    (run_live if args.live else run_dry)()
