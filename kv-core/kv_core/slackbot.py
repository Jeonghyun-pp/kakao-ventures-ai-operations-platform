"""슬랙 봇 경로 — **양방향(승인 이모지)** 전용. [W8]

webhook(notify.py)은 ts 를 돌려주지 않아 반응 조회가 불가능하다 — 승인 메시지는
bot 의 chat.postMessage(→ts)로 보내고, 반응은 conversations.history 로 읽는다
(응답 메시지 객체에 reactions 배열이 포함 — `channels:history` 스코프로 충분, 실측 확인).

토큰(`SLACK_BOT_TOKEN`)·채널(`SLACK_APPROVAL_CHANNEL`)이 없으면 **loud 스킵** —
승인은 CLI(`python -m kv_core.approval approve`)로 동작한다(CLI-only 모드).
notify 규약 상속: 이 모듈은 절대 raise 하지 않는다 — 실패는 None + stdout.
"""
from __future__ import annotations

from . import secrets

_API = "https://slack.com/api"

# ✅/❌ 의 슬랙 리액션 이름 + 흔한 별칭
APPROVE_EMOJI = {"white_check_mark", "heavy_check_mark"}
REJECT_EMOJI = {"x", "negative_squared_cross_mark"}


class _Suppressed:
    """킬 스위치로 **의도적으로 안 보낸** 상태 — 실패(None)와 구분한다. [W9 Phase0 0-6]"""
    def __repr__(self) -> str:
        return "SUPPRESSED"

    def __bool__(self) -> bool:
        return False


SUPPRESSED = _Suppressed()


def _auth() -> tuple[str, str] | None:
    token = secrets.get("SLACK_BOT_TOKEN", required=False)
    channel = secrets.get("SLACK_APPROVAL_CHANNEL", required=False)
    if not token or not channel:
        print("⚠️  slackbot: SLACK_BOT_TOKEN/SLACK_APPROVAL_CHANNEL 미설정 — CLI-only 모드 "
              "(승인은 python -m kv_core.approval approve <key>)")
        return None
    return token, channel


def post(text: str) -> tuple[str, str] | None:
    """승인 카드 발송 → (ts, channel). 실패/미설정 → None + loud."""
    import requests
    from . import notify
    # [W9 Phase0 0-6] 억제는 **실패가 아니다** — 예전엔 None(실패)을 돌려줘서 `_announce` 가
    # 전건 실패로 집계하고 `slackbot-down` 경보를 매일 띄웠다(A-M10). 센티널로 구분한다.
    if notify.is_suppressed():
        print(f"  🚫 KV_NO_SEND — 슬랙 카드 발송 안 함: {text[:80]}")
        return SUPPRESSED
    auth = _auth()
    if auth is None:
        return None
    token, channel = auth
    try:
        r = requests.post(f"{_API}/chat.postMessage",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"channel": channel, "text": text}, timeout=15)
        d = r.json()
    except Exception as e:                        # noqa: BLE001
        # [W9 B-F5] 일시(타임아웃·비JSON 429)와 영구를 구분해 로그에 남긴다 — 호출부가
        # 재시도 가치를 판단할 수 있게. (반환 계약은 None 유지 — 호출부는 실패만 알면 된다)
        print(f"⚠️  slackbot.post 예외(일시적일 수 있음): {type(e).__name__}: {e}")
        return None
    if not d.get("ok"):
        err = d.get("error")
        permanent = err in ("invalid_auth", "account_inactive", "channel_not_found",
                            "not_in_channel", "is_archived")
        print(f"{'🔴' if permanent else '⚠️'}  slackbot.post 실패: {err}"
              + (" (영구 — 토큰/채널 설정 확인 필요)" if permanent else " (일시적일 수 있음)"))
        return None
    ts, ch = d.get("ts"), d.get("channel")        # [W9 B-F10] ok:true 인데 필드 결손 → raise 금지
    if not ts or not ch:
        print(f"⚠️  slackbot.post: ok 인데 ts/channel 결손 — {str(d)[:120]}")
        return None
    return ts, ch


def message_reactions(channel: str, ts: str) -> list[dict] | None:
    """메시지 1건의 reactions 배열. **None = 조회 실패 ≠ 반응 0개** (규칙 05 — 구분한다)."""
    import requests
    token = secrets.get("SLACK_BOT_TOKEN", required=False)
    if not token:
        # [W9 B-F11] post 는 loud 인데 여기만 silent 였다 — 수거가 조용히 멈춘 상태가
        # "반응 없음"과 같은 로그를 남겼다(불변규칙 06 비대칭).
        print("⚠️  slackbot: SLACK_BOT_TOKEN 미설정 — 반응 수거 불가(CLI 승인은 가능)")
        return None
    try:
        r = requests.get(f"{_API}/conversations.history",
                         headers={"Authorization": f"Bearer {token}"},
                         params={"channel": channel, "latest": ts, "oldest": ts,
                                 "inclusive": "true", "limit": 1}, timeout=15)
        d = r.json()
    except Exception as e:                        # noqa: BLE001
        print(f"⚠️  slackbot.message_reactions 예외: {type(e).__name__}: {e}")
        return None
    if not d.get("ok"):
        print(f"⚠️  slackbot.message_reactions 실패: {d.get('error')}")
        return None
    msgs = d.get("messages") or []
    if not msgs:
        return None                               # 메시지를 못 찾음 — 조회 실패로 취급
    return msgs[0].get("reactions", [])
