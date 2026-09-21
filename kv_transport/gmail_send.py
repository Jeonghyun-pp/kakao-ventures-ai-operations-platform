"""지메일 **발송** 통로 — 이 리포에서 메일이 바깥으로 나가는 유일한 자리.

## 🔴 이 파일이 있는 이유는 "여기뿐"을 눈으로 확인할 수 있게 하려는 것이다

`check_intent` A8a 는 발송 엔드포인트 문자열을 **이 파일에만** 허용하고, A8b 는 이 모듈을 import 하는 곳을
`skills/mail/flow.py` 하나로 묶는다. `gmail.py`(읽기·아카이브·초안, 비플랜과 공유)에 넣지 않은 이유다 —
허용 범위가 600줄짜리 공유 파일 전체로 넓어진다.

## 초안 id 가 일회용 토큰이다

여기서는 메일을 짓지 않는다. `gmail.draft_reply()` 가 되읽기까지 끝낸 **초안을 보낼 뿐**이다.
보낸 초안은 사라지므로 같은 id 로 다시 불러도 두 번 나가지 않는다 — `_req` 의 망 재시도가 안전해지고,
"응답을 못 받았는데 나갔나?"는 그 초안이 아직 있는지만 보면 안다. 이중 발송을 사례별 처리가 아니라 구조로 없앤다.

발송이 실패하면 초안은 남는다 — 사람이 보낼 수 있다.
"""
from __future__ import annotations

from kvlib import Fetched

from .gmail import AuthDead, GmailError, _get, _req

_SEND = "/drafts/send"          # 🔴 이 리포에서 이 문자열이 있는 **유일한 줄**이다 (check_intent A8a). 테스트도 이 이름을 가리킨다


def draft_exists(draft_id: str) -> Fetched:
    """그 초안이 아직 있나. 없으면(404) **이미 나갔거나 사람이 지운 것**이다."""
    try:
        _get(f"/drafts/{draft_id}", params={"format": "minimal"})
        return Fetched.measured(True)
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.measured(False) if str(e).startswith("404") else Fetched.unknown(f"gmail:{e}")


def send_draft(draft_id: str) -> Fetched:
    """초안 하나를 보낸다 → 보낸 메시지 id. 🔴 200 은 발송을 뜻하지 않는다 — SENT 라벨로 판정한다."""
    try:
        made = _req("POST", _SEND, json_body={"id": draft_id})
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        left = draft_exists(draft_id)                            # 응답은 못 받았어도 나갔을 수 있다
        if left.is_measured and not left.require():
            return Fetched.measured("", via="draft_gone", error=str(e)[:120])
        return Fetched.unknown(f"gmail:{e}")
    if "SENT" not in (made.get("labelIds") or []):
        return Fetched.unknown(f"readback_mismatch:SENT 라벨이 없다: {str(made)[:120]}")
    return Fetched.measured(made.get("id") or "", thread_id=made.get("threadId"))
