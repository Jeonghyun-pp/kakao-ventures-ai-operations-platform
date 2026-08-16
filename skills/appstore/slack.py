"""슬랙 통로 — 알림이 나가는 유일한 경로.

## 이 파일이 지키는 [0] 제약

    🔴 억제는 실패가 아니다      KV_NO_SEND 로 막았을 때 True(성공)를 돌려줬더니
                              호출부가 "발송됨"으로 기록해 중복 방지 마커를 태웠고,
                              **그날 알림이 영구 유실**됐다. → 억제는 False 를 반환한다
    🔴 명시 값만 억제로 인정      truthy 판정이면 KV_NO_SEND=0·false·no 가 전부 "켜짐"이었다
    200 ≠ 표시됨               "요청을 받았다"이지 채널에 떴다는 뜻이 아니다(확인 경로 없음 — 사실만 기록)
    webhook 은 ts 를 안 준다     그래서 이모지 반응을 못 읽는다. 승인은 bot 토큰 경로가 따로 필요하다
                              (지금 스킬 1 은 알림만 하므로 webhook 으로 충분하다)
"""
from __future__ import annotations

import requests

from kvlib import constraints, secrets


def _post(text: str) -> bool:
    """반환은 **발송 성공 여부**다. 억제는 False — 성공이 아니다."""
    if secrets.no_send():
        print(f"  🚫 KV_NO_SEND — 억제됨 (보내지 않았다):\n{_indent(text)}")
        return False
    url = secrets.get("SLACK_WEBHOOK_URL", required=False)
    if not url:
        print("  🔴 SLACK_WEBHOOK_URL 이 없다 — 안 보냈다")
        return False
    try:
        r = requests.post(url, json={"text": text},
                          timeout=constraints.get("http.timeout_sec"))
    except Exception as e:  # noqa: BLE001
        print(f"  🔴 슬랙 발송 실패: {type(e).__name__}")
        return False
    if r.status_code != 200:
        print(f"  🔴 슬랙 {r.status_code}: {r.text[:120]}")
        return False
    # 200 은 "받았다"이지 "떴다"가 아니다 — 확인 경로가 없다는 사실을 기록만 해둔다
    return True


def _indent(s: str) -> str:
    return "\n".join("     │ " + ln for ln in s.splitlines())


def notify_owner(msg: str) -> bool:
    return _post(f":inbox_tray: {msg}")


def send_card(rows: list[dict], *, dry_run: bool = False) -> bool:
    """오늘의 앱 카드. 뭘 하는 앱인지 한 줄 + 링크."""
    lines = [f"*오늘의 앱스토어 {len(rows)}개*"]
    for r in rows:
        name = r.get("서비스명") or r.get("App ID")
        dev = r.get("Developer") or ""
        one = (r.get("One Liner") or "").strip().replace("\n", " ")[:90]
        site = r.get("Homepage") or ""
        aid = r.get("App ID")
        lines.append(
            f"\n• *{name}* — {dev}\n  {one}\n  "
            f"<https://apps.apple.com/kr/app/id{aid}|앱스토어>"
            + (f" · <{site}|개발사>" if site else "")
            + f" · <https://www.notion.so/{str(r.get('_page_id','')).replace('-','')}|노션>"
        )
    text = "\n".join(lines)
    if dry_run:
        print(_indent(text))
        return True
    return _post(text)
