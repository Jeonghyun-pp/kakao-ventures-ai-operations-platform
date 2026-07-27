"""Slack 단일 경로.

## 왜 필요한가

통합 전에는 두 방식이 공존했다 — webhook(mail-harness)과 bot 토큰(bplan).
알림 경로가 둘이면 "왜 안 왔지"를 두 군데서 찾아야 한다.

## 설계 원칙 — 알림 실패가 파이프라인을 죽이지 않는다

webhook이 없거나 발송이 실패해도 **예외를 던지지 않고 False를 반환**한다.
알림은 관측 경로이지 실행 경로가 아니다. 다만 **조용히 넘어가지도 않는다** —
실패는 stdout에 남긴다(불변 규칙 06: 실패를 삼키는 예외 처리를 두지 않는다).

## 주의 — 발송 성공은 전달 성공이 아니다

`requests.post`가 200을 줘도 그것은 Slack이 **요청을 받았다**는 뜻이다.
채널에 실제로 떴는지는 별개 사건이다(불변 규칙 05). 중요한 알림은 표시 여부를
따로 확인할 경로가 필요하다.
"""
from __future__ import annotations

from . import secrets


def slack(text: str, *, webhook: str | None = None) -> bool:
    """Slack 메시지 발송. 성공하면 True.

    실패해도 예외를 던지지 않는다 — 호출부가 알림 때문에 죽으면 안 된다.
    """
    import requests

    url = webhook or secrets.get("SLACK_WEBHOOK_URL", required=False)
    if not url:
        print("  ⚠️  SLACK_WEBHOOK_URL 없음 — 알림을 건너뛴다")
        return False
    try:
        r = requests.post(url, json={"text": text}, timeout=15)
    except Exception as e:                       # noqa: BLE001 — 알림 실패는 삼키되 남긴다
        print(f"  ⚠️  Slack 발송 예외: {type(e).__name__}: {e}")
        return False
    if r.status_code != 200:
        print(f"  ⚠️  Slack 발송 실패 status={r.status_code} body={r.text[:200]}")
        return False
    return True
