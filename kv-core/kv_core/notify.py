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

import os

from . import secrets

_SUPPRESS_VALUES = {"1", "true", "yes", "on"}


def is_suppressed() -> bool:
    """발송 억제 상태인가 — `KV_NO_SEND` 가 **명시적으로** 켜졌을 때만 참.

    관측 표면(orrery)이 이 함수를 읽어 "🚫 억제됨"을 표시한다. 스위치가 켜진 줄 모르고
    "알림이 안 온다"를 정상으로 오해하는 것이 이 스위치의 최대 위험이었다.
    """
    return str(os.environ.get("KV_NO_SEND", "")).strip().lower() in _SUPPRESS_VALUES


def slack(text: str, *, webhook: str | None = None) -> bool:
    """Slack 메시지 발송. 성공하면 True.

    실패해도 예외를 던지지 않는다 — 호출부가 알림 때문에 죽으면 안 된다.
    """
    import os
    import requests

    # [W9] **발송 킬 스위치** — 테스트가 스텁을 깜빡해도 실 채널로 새지 않게 하는 최후 방어.
    # [W9 Phase0 0-4 정정] 두 가지를 고쳤다:
    #   ① `True`(성공) 반환 → **False**. True 를 주면 호출부(alerts.push)가 "발송됨"으로
    #      기록해 dedup 마커를 태우고, 그날 알림이 영구 유실됐다(B-H1 실증).
    #   ② truthy 판정 → 명시 값만. `KV_NO_SEND=0`·`false`·`no` 가 전부 "켜짐"이었다(직관 정반대).
    if is_suppressed():
        print(f"  🚫 KV_NO_SEND — 발송 안 함(내용): {text[:120]}")
        return False

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
