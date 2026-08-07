"""Slack **단방향 알림** 경로 (webhook).

## 경로가 둘인 이유 [W8 정정]

webhook 은 발송만 가능하고 ts 를 돌려주지 않는다 — 반응(이모지) 조회가 불가능하다.
그래서 **양방향(승인 카드·이모지 수거)은 kv_core.slackbot**(bot 토큰, chat.postMessage→ts)이
맡는다. 역할이 겹치지 않는다: 알림=여기(webhook), 승인=slackbot(bot). "왜 안 왔지"의
탐색 지점: 다이제스트·경보 → 이 파일 / 승인 카드 → slackbot.py.

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
    import os
    import requests

    # [W9] **발송 킬 스위치** — KV_NO_SEND=1 이면 실발송 없이 stdout 에만 남긴다.
    # 왜 프로덕션 코드에 있나: 테스트가 스텁을 깜빡하면 실 채널에 가짜 경보가 간다.
    # 두 번 데였다(T3 게이트 테스트 · W9 dispatch 테스트) — 스텁은 기억이고 이건 구조다.
    # 합성 테스트 전부가 이 변수를 켜고, 메타 검증이 그 사실을 단언한다.
    if os.environ.get("KV_NO_SEND"):
        print(f"  🚫 KV_NO_SEND — 발송 안 함(내용): {text[:120]}")
        return True

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
