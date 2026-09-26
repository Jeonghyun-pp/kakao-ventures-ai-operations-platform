"""슬랙 통로 — 슬랙에 닿는 유일한 경로. **게시 · 반응 조회.**

## webhook 에서 bot API 로 옮겼다 (2026-08-18)

옛 독스트링이 이렇게 적어뒀었다:

    webhook 은 ts 를 안 준다 → 그래서 이모지 반응을 못 읽는다.
    승인은 bot 토큰 경로가 따로 필요하다 (지금 스킬 1 은 알림만 하므로 webhook 으로 충분하다)

그 신호가 왔다. 신뢰 경로 건을 슬랙에 올리고 **❌ 를 누르면 아카이브**해야 하는데,
어떤 메시지에 달린 반응인지 알려면 게시할 때 받은 `ts` 가 있어야 한다.

**병행하지 않고 교체했다.** 두 경로를 두면 "어느 쪽으로 나갔나"가 두 진실원이 된다.

## 이 파일이 지키는 [0] 제약

    🔴 억제는 실패가 아니다      KV_NO_SEND 로 막았을 때 True(성공)를 돌려줬더니
                              호출부가 "발송됨"으로 기록해 중복 방지 마커를 태웠고,
                              **그날 알림이 영구 유실**됐다. → 억제는 미상을 반환한다
    🔴 명시 값만 억제로 인정      truthy 판정이면 KV_NO_SEND=0·false·no 가 전부 "켜짐"이었다
    🔴 슬랙은 실패해도 200        HTTP 200 인데 본문이 {"ok": false, "error": "..."} 다.
                              상태코드로 판정하면 "안 떴는데 떴다"가 된다
    봇은 채널 멤버여야 한다       아니면 게시도 조회도 not_in_channel 이다.
                              `check()` 가 그걸 미리 확인한다
    반응은 conversations.history 로  reactions.list 는 reactions:read 가 필요한데 없다.
                              실측(08-18): history 응답의 message.reactions 로 읽힌다
"""
from __future__ import annotations

import requests

from kvlib import Fetched, constraints, secrets

_API = "https://slack.com/api"

# 🔴 게시 허용 채널 — **스킬이 등록한다.** 통로가 명단을 들고 있지 않다.
#    노션의 register_writable 과 같은 규율이다. 등록 안 된 채널엔 안 쓴다.
_CHANNELS: dict[str, str] = {}


class SlackError(RuntimeError):
    pass


class NotRegistered(SlackError):
    """등록 안 된 채널 키다."""


def register(key: str, channel_id: str) -> None:
    prev = _CHANNELS.get(key)
    if prev and prev != channel_id:
        raise SlackError(f"'{key}' 가 이미 {prev} 로 등록돼 있다 (새로: {channel_id})")
    _CHANNELS[key] = channel_id


register("test", "C000E8E4F75")     # review_test — 모든 스킬의 보고·health 채널. 스킬은 자기 채널(net·bplan)만 등록한다


def channel_id(key: str) -> str:
    if key not in _CHANNELS:
        raise NotRegistered(f"등록 안 된 채널: '{key}' (등록된 것: {sorted(_CHANNELS)})")
    return _CHANNELS[key]


def registered() -> dict[str, str]:
    return dict(_CHANNELS)


# ---------------------------------------------------------------- HTTP

def _call(method: str, path: str, **kw) -> dict:
    """슬랙 API 한 번.

    🔴 상태코드가 아니라 본문의 `ok` 로 판정한다. 슬랙은 실패해도 200 을 준다 —
       not_in_channel·channel_not_found·missing_scope 전부 200 이다.
    """
    token = secrets.get("SLACK_BOT_TOKEN")
    try:
        r = requests.request(method, f"{_API}{path}",
                             headers={"Authorization": f"Bearer {token}"},
                             timeout=constraints.get("http.timeout_sec"), **kw)
    except Exception as e:  # noqa: BLE001
        raise SlackError(f"망: {type(e).__name__}") from e
    try:
        body = r.json()
    except ValueError as e:
        raise SlackError(f"{r.status_code} 응답이 JSON 이 아니다: {r.text[:120]}") from e
    if not body.get("ok"):
        raise SlackError(f"{path} → {body.get('error')} (HTTP {r.status_code})")
    return body


def _indent(s: str) -> str:
    return "\n".join("     │ " + ln for ln in s.splitlines())


# ---------------------------------------------------------------- 게시

def post(key: str, text: str, *, thread_ts: str | None = None,
         unfurl: bool = False, dry_run: bool = False) -> Fetched:
    """채널에 올리고 **ts 를 돌려준다.** 나중에 반응을 찾으려면 이게 있어야 한다.

    `thread_ts` 를 주면 그 메시지의 스레드에 단다 (만기 예고에 쓴다).

    🔴 `unfurl` 기본값이 False 인 이유 (08-18 실측): 슬랙이 링크를 자동으로 펼쳐
       미리보기를 붙이는데, 링크드인은 "1촌 500명 이상 · 10억 명의 회원이 있는
       전문가 커뮤니티" 같은 보일러플레이트를 사람마다 5줄씩 붙인다.
       우리가 두 문장으로 요약해 올리는 의미가 사라진다.
    """
    ch = channel_id(key)
    if dry_run:
        print(_indent(text))
        return Fetched.unknown("dry_run", channel=key)
    if secrets.no_send():
        # 🔴 억제는 성공이 아니다. 미상으로 돌려줘야 호출부가 "보냈다"로 기록하지 않는다.
        print(f"  🚫 KV_NO_SEND — 억제됨 (보내지 않았다):\n{_indent(text)}")
        return Fetched.unknown("suppressed:KV_NO_SEND", channel=key)
    body = {"channel": ch, "text": text,
            "unfurl_links": unfurl, "unfurl_media": unfurl}
    if thread_ts:
        body["thread_ts"] = thread_ts
    try:
        res = _call("POST", "/chat.postMessage", json=body)
    except SlackError as e:
        return Fetched.unknown(f"slack:{e}", channel=key)
    return Fetched.measured(res["ts"], channel=key, channel_id=ch)


def reactions(key: str, ts: str) -> Fetched:
    """그 메시지에 달린 반응. `[{name, users, count}]` · 없으면 빈 배열(측정된 0).

    🔴 reactions.list 는 reactions:read 가 없어 못 쓴다. conversations.history 를
       그 ts 하나만 잘라 읽는다 — 실측(08-18)으로 반응이 딸려 오는 걸 확인했다.
    """
    ch = channel_id(key)
    try:
        res = _call("GET", "/conversations.history",
                    params={"channel": ch, "latest": ts, "oldest": ts,
                            "inclusive": "true", "limit": 1})
    except SlackError as e:
        return Fetched.unknown(f"slack:{e}", channel=key)
    msgs = res.get("messages") or []
    if not msgs:
        return Fetched.unknown(f"그 메시지를 못 찾았다: ts={ts}", channel=key)
    return Fetched.measured(msgs[0].get("reactions") or [], ts=ts)


def replies(key: str, parent_ts: str) -> Fetched:
    """스레드 답글 전체. 각 답글의 `reactions` 가 같이 온다.

    🔴 `conversations.history` 는 **최상위 메시지만** 준다. 스레드 답글에 달린 반응은
       거기서 안 보인다. 사람별 판단을 스레드 답글로 받기로 했으므로 이 경로가 필요하다.
       배치 한 번에 한 번만 부르면 되니 사람 수만큼 부르지 않는다.
    """
    ch = channel_id(key)
    try:
        res = _call("GET", "/conversations.replies",
                    params={"channel": ch, "ts": parent_ts, "limit": 200})
    except SlackError as e:
        return Fetched.unknown(f"slack:{e}", channel=key)
    msgs = res.get("messages") or []
    # 첫 항목은 부모 메시지다 — 답글만 돌려준다
    return Fetched.measured([m for m in msgs if m.get("ts") != parent_ts],
                            parent=parent_ts, n=len(msgs))


def check() -> Fetched:
    """등록된 채널마다 봇이 **실제로 들어가 있는지** 확인한다.

    🔴 안 들어가 있으면 게시도 조회도 not_in_channel 로 실패한다. 그런데 그 실패는
       실행 중에야 드러나므로, 미리 한 번에 확인할 자리가 필요하다.
       (conversations.info 는 channels:read 가 필요해 못 쓴다. history 로 대신 확인한다.)
    """
    if not _CHANNELS:
        return Fetched.unknown("등록된 채널이 없다 — 스킬이 register 를 안 불렀다")
    bad = {}
    for key, ch in _CHANNELS.items():
        try:
            _call("GET", "/conversations.history", params={"channel": ch, "limit": 1})
        except SlackError as e:
            bad[key] = str(e)
    if bad:
        return Fetched.unknown(f"접근 불가 채널 {len(bad)}개: {bad}")
    return Fetched.measured(sorted(_CHANNELS))


# ---------------------------------------------------------------- 편의

def notify_owner(key: str, msg: str) -> Fetched:
    return post(key, f":inbox_tray: {msg}")


def send_text(key: str, text: str, *, dry_run: bool = False) -> bool:
    """서식은 스킬이 만든다 — 그건 도메인이다. 반환은 **발송 성공 여부**.

    억제·실패는 전부 False 다. dry_run 은 화면에만 찍고 True.
    """
    if dry_run:
        print(_indent(text))
        return True
    return post(key, text).is_measured


# ---------------------------------------------------------------- 이력·파일 (2026-09-26, 투심 레인)

def history(key: str, *, limit: int = 200) -> Fetched:
    """채널 **최상위** 메시지(새것부터). `files`·`thread_ts`·`reply_count`·`user`·`reactions` 가 딸려 온다."""
    ch = channel_id(key)
    try:
        res = _call("GET", "/conversations.history", params={"channel": ch, "limit": limit})
    except SlackError as e:
        return Fetched.unknown(f"slack:{e}", channel=key)
    return Fetched.measured(res.get("messages") or [], channel=key)


def thread(key: str, parent_ts: str) -> Fetched:
    """부모 + 답글 전부(부모 포함). `replies()` 는 답글만 준다 — 부모에 붙은 파일까지 보려면 이것."""
    ch = channel_id(key)
    try:
        res = _call("GET", "/conversations.replies", params={"channel": ch, "ts": parent_ts, "limit": 200})
    except SlackError as e:
        return Fetched.unknown(f"slack:{e}", channel=key)
    return Fetched.measured(res.get("messages") or [], parent=parent_ts)


def files_of(msgs: list[dict]) -> list[dict]:
    """메시지들에 붙은 **사람이 올린** 파일 메타 `[{id, name, url, ts, user}]`. 봇 파일(우리 초안)은 뺀다. 망에 안 닿는다."""
    return [{"id": f.get("id"), "name": f.get("name") or f.get("title") or f.get("id"), "url": f["url_private"], "ts": m.get("ts"), "user": m.get("user")}
            for m in msgs if not m.get("bot_id") and m.get("subtype") != "bot_message"
            for f in m.get("files") or [] if f.get("url_private")]


def download(url: str, dest) -> Fetched:
    """`url_private` 를 받아 dest 에 쓴다. 🔴 스코프(files:read)가 없으면 200 으로 **로그인 HTML** 이 온다 — 크기가 아니라 종류로 판정한다."""
    token = secrets.get("SLACK_BOT_TOKEN")
    try:
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=constraints.get("http.timeout_sec"))
    except Exception as e:  # noqa: BLE001
        return Fetched.unknown(f"망: {type(e).__name__}")
    ctype = r.headers.get("content-type", "")
    if r.status_code != 200 or ctype.startswith("text/html"):
        return Fetched.unknown(f"다운로드 실패 HTTP {r.status_code} {ctype[:30]} — files:read 스코프를 의심하라")
    dest.write_bytes(r.content)
    return Fetched.measured(dest, bytes=len(r.content))


def upload(key: str, path, *, thread_ts: str | None = None, title: str | None = None) -> Fetched:
    """파일을 채널(스레드)에 올린다 — files.getUploadURLExternal → PUT → completeUploadExternal. 반환은 file id.
    files:write 스코프가 필요하다. 억제·실패는 미상으로."""
    ch = channel_id(key)
    if secrets.no_send():
        print(f"  🚫 KV_NO_SEND — 억제됨 (파일 안 올림): {path}")
        return Fetched.unknown("suppressed:KV_NO_SEND", channel=key)
    data = path.read_bytes()
    try:
        step1 = _call("POST", "/files.getUploadURLExternal", data={"filename": path.name, "length": len(data)})
        put = requests.post(step1["upload_url"], data=data, timeout=constraints.get("http.timeout_sec") * 4)
        if put.status_code != 200:
            return Fetched.unknown(f"업로드 PUT HTTP {put.status_code}", channel=key)
        body = {"files": [{"id": step1["file_id"], "title": title or path.name}], "channel_id": ch}
        if thread_ts:
            body["thread_ts"] = thread_ts
        _call("POST", "/files.completeUploadExternal", json=body)
    except SlackError as e:
        return Fetched.unknown(f"slack:{e}", channel=key)
    except Exception as e:  # noqa: BLE001
        return Fetched.unknown(f"망: {type(e).__name__}", channel=key)
    return Fetched.measured(step1["file_id"], channel=key, channel_id=ch)
