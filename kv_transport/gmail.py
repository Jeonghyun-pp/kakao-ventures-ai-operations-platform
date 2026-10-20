"""Gmail 통로 — 지메일에 닿는 유일한 경로. **읽기 · 아카이브 · 초안까지. 발송은 없다.**

## 🔴 이 파일에 발송이 없는 것은 다짐이 아니라 구조다

    할 수 있다   읽기 · 인박스 라벨 떼기(아카이브) · 초안 만들기
    없다        messages/send · drafts/send · smtplib — **어떤 형태의 발송도**

경계가 여기인 이유: 아카이브는 되돌릴 수 있고 초안은 나가지 않는다.
**회사 이름으로 바깥에 나가는 순간만 사람이 누른다.**

스코프로는 못 막는다. 토큰이 가진 건 `gmail.modify` 하나인데 **그 스코프에 send 가 포함된다**
(옛 리포도 같은 자리에 *"이 스코프는 send 를 막지 않는다"* 고 적어뒀다). 인박스 라벨을
떼려면 modify 가 필요하고, modify 보다 좁으면서 라벨을 바꿀 수 있는 스코프는 없다.
그러니 **막는 자리는 코드뿐이고, 그래서 `check_intent` 의 A8 이 존재한다.**

`POST /drafts` 는 초안 생성이고 `POST /drafts/{id}/send` 가 발송이다. URL 이 접미사 하나
차이라서, A8 은 `/send` 를 잡되 `/drafts` 는 통과시킨다.

## 왜 구글 SDK 를 안 쓰나

두 검사가 SDK 를 못 본다.

    A8   `svc.users().messages().send()` 는 동적 속성 체인이라 AST 가 못 짚는다.
         URL 문자열이면 짚을 수 있다.
    A1   NET_MODULES 에 requests 는 있지만 googleapiclient·google.auth 는 없다.
         🔴 SDK 를 쓰면 통로 **밖**에서 지메일을 불러도 A1 이 조용히 통과한다.

검사를 통과하려고 SDK 를 피하는 게 아니라, SDK 를 쓰면 검사가 무력해진다.

## 이 파일이 지키는 [0] 제약

    access_token 은 1시간         만료 60초 전에 갱신한다. 만료 후 갱신은 401 한 번을 낭비한다
    200 ≠ 토큰 받음               응답에 access_token 키가 없으면 실패다. 상태코드로 판정 안 한다
    재인증은 자동화 불가            refresh_token 이 죽으면 브라우저 로그인이 필요하다.
                                  조용히 넘어가지 말고 그 사실을 말하고 멈춘다
    list 는 id 만 준다             본문·헤더를 받으려면 건마다 get 을 한 번 더 쳐야 한다 (N+1)
    본문이 text/plain 이 아닐 수 있다  MIME 트리를 걸어야 한다. payload.body 만 보면 빈 문자열이다
"""
from __future__ import annotations

import base64
import json
import re
import time
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape as _html_escape

import requests

from kvlib import Fetched, constraints, secrets

_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_TOKEN_URL = "https://oauth2.googleapis.com/token"

_NET_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)


class GmailError(RuntimeError):
    pass


class AuthDead(GmailError):
    """refresh_token 이 더 이상 안 먹는다 — 사람이 브라우저로 재인증해야 한다."""


# ---------------------------------------------------------------- 인증

_token: dict = {}          # {"value": str, "expires_at": float}


def _creds() -> dict:
    """$KV_CONFIG 의 token.json. 스킬로 복사하지 않는다.

    옛 시스템은 이 파일이 4벌이었는데 refresh_token 이 전부 같았다 — 물리적 사본일 뿐인데
    재인증은 4번 해야 했다. 경로의 정본은 .env 의 GMAIL_TOKEN_FILE 하나다.
    """
    path = secrets.get("GMAIL_TOKEN_FILE")
    try:
        return json.loads(open(path, encoding="utf-8").read())
    except FileNotFoundError as e:
        raise AuthDead(f"토큰 파일이 없다: {path}") from e
    except json.JSONDecodeError as e:
        raise AuthDead(f"토큰 파일이 깨졌다: {path} — {e}") from e


def _access_token() -> str:
    """refresh_token → access_token. 만료 60초 전에 미리 바꾼다.

    🔴 상태코드로 성공을 판정하지 않는다. 구글은 잘못된 grant 에도 200 을 주는 경우가 있고,
       우리가 필요한 건 상태코드가 아니라 access_token 문자열이다.
    """
    lead = constraints.get("gmail.token_refresh_lead_sec")
    if _token and _token["expires_at"] - lead > time.time():
        return _token["value"]

    c = _creds()
    if not c.get("refresh_token"):
        raise AuthDead("refresh_token 이 없다 — 브라우저 재인증이 필요하다")

    try:
        r = requests.post(_TOKEN_URL, data={
            "client_id": c["client_id"],
            "client_secret": c["client_secret"],
            "refresh_token": c["refresh_token"],
            "grant_type": "refresh_token",
        }, timeout=constraints.get("http.timeout_sec"))
    except _NET_ERRORS as e:
        raise GmailError(f"토큰 서버에 못 닿았다: {type(e).__name__}") from e

    body = {}
    try:
        body = r.json()
    except ValueError:
        pass

    tok = body.get("access_token")
    if not tok:
        err = body.get("error", "")
        # invalid_grant = 토큰 폐기·만료·비밀번호 변경. 재시도해도 안 된다.
        if err == "invalid_grant" or r.status_code in (400, 401):
            raise AuthDead(
                f"refresh_token 이 거부됐다 ({err or r.status_code}). "
                f"**브라우저 로그인으로 재인증해야 한다 — 자동으로 못 고친다.**"
            )
        raise GmailError(f"토큰 갱신 실패 {r.status_code}: {str(body)[:200]}")

    _token.update(value=tok, expires_at=time.time() + float(body.get("expires_in") or 3600))
    return tok


def auth_check() -> Fetched:
    """토큰이 살아 있나. 인박스를 부르기 **전에** 이걸 먼저 본다.

    죽었으면 여기서 멈춰야 한다. 나중에 401 로 알면 "메일 0통"과 구별이 안 된다.
    """
    try:
        _access_token()
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.unknown(f"gmail:{e}")
    return Fetched.measured(True)


# ---------------------------------------------------------------- HTTP

def _req(method: str, path: str, *, params: dict | None = None,
         json_body: dict | None = None) -> dict:
    """지메일 요청 한 번. 재시도 계층은 노션 통로와 같다.

    🔴 method 를 받지만 이 파일이 실제로 쓰는 건 GET · POST 둘뿐이고,
       POST 가 가는 곳은 `/messages/{id}/modify` · `/drafts` · `/labels`(분류 라벨 만들기, 2026-09-29) 세 곳뿐이다.
       발송 엔드포인트는 이 파일 어디에도 없다 — A8 이 그걸 센다.
    """
    net_backoff = constraints.get("http.retry_backoff_sec")
    url = f"{_API}{path}"
    net_try = 0
    other_try = 0

    while True:
        try:
            r = requests.request(method, url,
                                 headers={"Authorization": f"Bearer {_access_token()}"},
                                 params=params, json=json_body,
                                 timeout=constraints.get("http.timeout_sec"))
        except _NET_ERRORS as e:
            if net_try < len(net_backoff):
                w = net_backoff[net_try]
                net_try += 1
                print(f"  · 지메일 망 오류 {type(e).__name__} — {w}s 후 재시도 ({net_try}/{len(net_backoff)})")
                time.sleep(w)
                continue
            raise GmailError(f"망 계열로 끝까지 실패: {type(e).__name__} {path}") from e

        if r.status_code == 401:
            # 토큰이 도중에 죽었다. 캐시를 버리고 한 번만 다시 받아본다.
            if other_try == 0:
                other_try += 1
                _token.clear()
                continue
            raise AuthDead(f"401 이 반복된다: {path} — 재인증이 필요하다")
        if r.status_code == 429 or 500 <= r.status_code < 600:
            other_try += 1
            if other_try > 4:
                raise GmailError(f"{r.status_code} 가 계속된다: {path}")
            wait = float(r.headers.get("Retry-After") or 2 ** other_try)
            print(f"  · {r.status_code} — {wait}s 후 재시도")
            time.sleep(wait)
            continue
        if r.status_code == 403:
            raise GmailError(
                f"403 {path} — 스코프를 먼저 의심하라. 토큰이 gmail.modify 를 갖고 있는지 확인."
            )
        if r.status_code >= 400:
            raise GmailError(f"{r.status_code} {path}: {r.text[:300]}")

        return r.json() if r.content else {}


def _get(path: str, *, params: dict | None = None) -> dict:
    return _req("GET", path, params=params)


# ---------------------------------------------------------------- 본문 파싱

def _decode(part: dict) -> str:
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _body_text(payload: dict) -> str:
    """MIME 트리를 걸어 본문을 얻는다. text/plain 우선, 없으면 html 태그 제거.

    🔴 `payload["body"]["data"]` 만 보면 multipart 메일이 통째로 빈 문자열이 된다.
       인바운드 메일 대부분이 multipart 다.
    """
    plain: list[str] = []
    html: list[str] = []

    def walk(part: dict) -> None:
        mt = part.get("mimeType", "")
        if mt == "text/plain":
            plain.append(_decode(part))
        elif mt == "text/html":
            html.append(_decode(part))
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload or {})
    if any(plain):
        return "\n".join(plain)
    text = re.sub(r"<br\s*/?>", "\n", "\n".join(html), flags=re.I)
    text = re.sub(r"</(p|div|tr|li)>", "\n", text, flags=re.I)
    return re.sub(r"<[^>]+>", " ", text)


def _addrs(value: str) -> list[str]:
    return re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", value or "")


def _normalize(m: dict) -> dict:
    """메시지 하나를 우리가 쓰는 필드로 접는다.

    🔴 안 쓰는 건 담지 않는다. 옛 dict 는 `labels`·`snippet` 을 담았는데 아무도 안 읽었다 —
       담아두면 "쓰는 데이터"를 셀 때 거짓말이 된다.
    """
    payload = m.get("payload") or {}
    h = {x["name"].lower(): x["value"] for x in payload.get("headers") or []}
    to_all = _addrs(h.get("to", "")) + _addrs(h.get("cc", "")) + _addrs(h.get("bcc", ""))
    return {
        "id": m["id"],
        "thread_id": m.get("threadId"),
        "message_id_header": h.get("message-id"),
        "subject": h.get("subject", ""),
        "sender": (_addrs(h.get("from", "")) or [""])[0].lower(),
        "sender_raw": h.get("from", ""),
        "to": to_all,
        "recipient_count": len(to_all),
        "date": h.get("date", ""),
        # 🔴 서버가 받은 시각(epoch ms). `date` 헤더는 **발신자가 쓴 값**이라 순서 판단에
        #    쓸 수 없다 — 틀리거나, 형식이 제각각이거나, 일부러 미래로 적혀 있을 수 있다.
        #    "같은 사람의 어느 메일이 최신인가" 를 이걸로 정한다.
        "internal_date": int(m.get("internalDate") or 0),
        "list_unsubscribe": bool(h.get("list-unsubscribe")),
        "body": _body_text(payload),
    }


# ---------------------------------------------------------------- 읽기


def message(msg_id: str) -> Fetched:
    """메시지 하나를 정규화해 돌려준다. 초안의 인용문·헤더를 **실행 시점에** 다시 읽는 데 쓴다."""
    try:
        return Fetched.measured(_normalize(_get(f"/messages/{msg_id}", params={"format": "full"})))
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.unknown(f"gmail:{e}")


def attachments(msg_id: str) -> Fetched:
    """첨부 파일 `[{name, mime, data(bytes)}]`. 읽기만 한다 — 메일로 온 덱을 판정이 읽을 때 쓴다(drive.text 의 메일 링크)."""
    try:
        m = _get(f"/messages/{msg_id}", params={"format": "full"})
        out = []

        def walk(part: dict) -> None:
            body = part.get("body") or {}
            if part.get("filename") and body.get("attachmentId"):
                raw = _get(f"/messages/{msg_id}/attachments/{body['attachmentId']}").get("data", "")
                out.append({"name": part["filename"], "mime": part.get("mimeType", ""),
                            "data": base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))})
            for sub in part.get("parts") or []:
                walk(sub)

        walk(m.get("payload") or {})
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.unknown(f"gmail:{e}")
    return Fetched.measured(out)


def thread_after(thread_id: str, msg_id: str) -> Fetched:
    """그 메시지 **뒤에** 스레드에 붙은 메시지 수. **초안은 세지 않는다.**

    0 이 아니면 계획을 세운 뒤 대화가 진행됐다는 뜻이다 —
    사람이 이미 답장했거나 상대가 또 보냈다. 그 위에 초안을 얹으면
    같은 사람에게 두 번 답장하게 된다.

    🔴 DRAFT 라벨을 빼는 이유 (08-18 실측):
       우리가 만든 초안이 같은 스레드에 붙으므로, 안 빼면 **우리 행동이 우리를 막는다.**
       ① 이유가 거짓말이 된다 — "사람이 답장했다"가 아니라 우리가 초안을 만든 것이다
       ② 제휴 건은 포워드 + 거절 초안 **둘**이 필요한데, 재시도 시 첫 초안 때문에
          두 번째가 영영 안 만들어진다. 조용히 절반만 되는 상태가 된다
       SENT 는 뺀 게 아니라 남긴다 — 사람이 실제로 보냈다면 그게 우리가 찾던 신호다.
    """
    try:
        res = _get(f"/threads/{thread_id}", params={"format": "minimal"})
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.unknown(f"gmail:{e}")
    msgs = res.get("messages") or []
    order = [m["id"] for m in msgs]
    if msg_id not in order:
        return Fetched.unknown(f"스레드에 그 메시지가 없다: {msg_id}")
    after = [m for m in msgs[order.index(msg_id) + 1:]
             if "DRAFT" not in (m.get("labelIds") or [])]
    return Fetched.measured(len(after), drafts_ignored=len(msgs) - 1 - order.index(msg_id) - len(after))


def labels() -> Fetched:
    """{라벨이름: id}. 라벨이 실제로 있는지 확인하는 데 쓴다."""
    try:
        res = _get("/labels")
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.unknown(f"gmail:{e}")
    return Fetched.measured({l["name"]: l["id"] for l in res.get("labels") or []})


def search_ids(q: str, *, limit: int = 500) -> Fetched:
    """질의에 맞는 메시지 번호. 본문을 안 받으므로 싸다.

    🔴 페이지네이션한다. 상한에 닿았는데 다음 장이 있으면
       partial 로 표시한다. "전부"와 "여기까지"를 구별하지 않으면 뒤쪽이 조용히 빠진다.
    """
    ids: list[str] = []
    token = None
    try:
        while len(ids) < limit:
            p = {"q": q, "maxResults": min(500, limit - len(ids))}
            if token:
                p["pageToken"] = token
            res = _get("/messages", params=p)
            ids += [m["id"] for m in res.get("messages") or []]
            token = res.get("nextPageToken")
            if not token:
                break
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.unknown(f"gmail:{e}")
    truncated = bool(token) and len(ids) >= limit
    return Fetched.measured(ids[:limit], partial=truncated, truncated=truncated, limit=limit, q=q)


def thread_tail(thread_id: str, msg_id: str) -> Fetched:
    """그 메시지 **뒤에** 스레드에 붙은 메시지들 `[{id, labels}]`(초안 제외). 사람이 답장·전달했는지는 SENT 라벨로 본다(reconcile, 2026-10-01)."""
    try:
        res = _get(f"/threads/{thread_id}", params={"format": "minimal"})
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.unknown(f"gmail:{e}")
    msgs = res.get("messages") or []
    order = [m["id"] for m in msgs]
    if msg_id not in order:
        return Fetched.unknown(f"스레드에 그 메시지가 없다: {msg_id}")
    return Fetched.measured([{"id": m["id"], "labels": m.get("labelIds") or []} for m in msgs[order.index(msg_id) + 1:]
                             if "DRAFT" not in (m.get("labelIds") or [])])


def thread_before(thread_id: str, msg_id: str) -> Fetched:
    """그 메시지 **앞에** 스레드에 있던 메시지들 `[{id, labels}]`(초안 제외), 오래된 순.
    "폼이 안 된다"는 답장엔 사업 내용이 없고 덱은 첫 메일에 붙어 있다(2026-10-20 실측) — 폼 오류 접수가 앞 메일을 읽는다."""
    try:
        res = _get(f"/threads/{thread_id}", params={"format": "minimal"})
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.unknown(f"gmail:{e}")
    msgs = res.get("messages") or []
    order = [m["id"] for m in msgs]
    if msg_id not in order:
        return Fetched.unknown(f"스레드에 그 메시지가 없다: {msg_id}")
    return Fetched.measured([{"id": m["id"], "labels": m.get("labelIds") or []} for m in msgs[:order.index(msg_id)]
                             if "DRAFT" not in (m.get("labelIds") or [])])


def headers_of(msg_id: str, names: list[str]) -> Fetched:
    """헤더 몇 개만(metadata). 본문을 안 받는다 — 제목만 필요할 때 full 은 낭비다."""
    try:
        res = _get(f"/messages/{msg_id}",
                   params=[("format", "metadata")] + [("metadataHeaders", n) for n in names])
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.unknown(f"gmail:{e}")
    h = {x["name"].lower(): x["value"] for x in (res.get("payload") or {}).get("headers") or []}
    return Fetched.measured({n: h.get(n.lower(), "") for n in names},
                            labels=res.get("labelIds") or [], thread_id=res.get("threadId"))


# ---------------------------------------------------------------- 쓰기 (발송은 없다)

def archive(msg_id: str) -> Fetched:
    """인박스 라벨만 뗀다. **삭제가 아니라 아카이빙이라 되돌릴 수 있다.**

    멱등하다 — 이미 인박스에 없으면 성공으로 친다. 사람이 먼저 치웠을 수 있고,
    그건 실패가 아니라 목적이 이미 달성된 것이다.

    🔴 쓴 것은 되읽는다. 200 은 저장을 뜻하지 않는다 — 노션에서 배운 것과 같다.
    """
    try:
        _req("POST", f"/messages/{msg_id}/modify", json_body={"removeLabelIds": ["INBOX"]})
        got = _get(f"/messages/{msg_id}", params={"format": "minimal"})
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.unknown(f"gmail:{e}")
    if "INBOX" in (got.get("labelIds") or []):
        return Fetched.unknown(f"readback_mismatch:{msg_id} 가 아직 인박스에 있다")
    return Fetched.measured(True)


def create_label(name: str) -> Fetched:
    """라벨을 만든다(분류 라벨 kv/<카테고리>, 2026-09-29). 사용자 라벨이라 지워도 메일은 안 없어진다.

    🔴 쓴 것은 되읽는다 — labels() 에 그 이름이 있어야 성공이다. 반환 = 라벨 id.
    """
    try:
        _req("POST", "/labels", json_body={"name": name, "labelListVisibility": "labelShow", "messageListVisibility": "show"})
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.unknown(f"gmail:{e}")
    have = labels()
    if not have.is_measured or name not in have.require():
        return Fetched.unknown(f"readback_mismatch:라벨 {name} 이 목록에 없다")
    return Fetched.measured(have.require()[name])


def relabel(msg_id: str, *, add: list[str], remove: list[str]) -> Fetched:
    """라벨을 붙이고 뗀다. **삭제가 아니라 라벨 이동이라 되돌릴 수 있다.**

    비플랜 sync 가 접수 메일을 담당자 라벨로 옮기고 인박스에서 빼는 데 쓴다 —
    drop 이 그 라벨 안에서 원본을 찾는다. 라벨 id 는 `labels()` 로 얻는다(이름이 아니다).

    🔴 쓴 것은 되읽는다. 붙일 것이 안 붙었거나 뗄 것이 남아 있으면 실패다.
    """
    try:
        _req("POST", f"/messages/{msg_id}/modify",
             json_body={"addLabelIds": add, "removeLabelIds": remove})
        got = _get(f"/messages/{msg_id}", params={"format": "minimal"})
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.unknown(f"gmail:{e}")
    live = set(got.get("labelIds") or [])
    missing = [l for l in add if l not in live]
    lingering = [l for l in remove if l in live]
    if missing or lingering:
        return Fetched.unknown(f"readback_mismatch:{msg_id} 안 붙음={missing} 안 떨어짐={lingering}")
    return Fetched.measured(True)


def _to_gmail_html(text: str) -> str:
    """Gmail 초안 에디터가 긴 text/plain 을 중간에서 접고 빈 줄을 먹는다.

    사람이 손으로 쓸 때의 <div> 구조를 흉내낸 html 파트를 같이 넣어야
    초안을 열었을 때 문단이 보존된다. (옛 시스템에서 검증된 것)
    """
    return "".join("<div><br></div>" if l == "" else f"<div>{_html_escape(l)}</div>"
                   for l in text.split("\n"))


def _draft(msg: dict, body: str, to: str, *, subject: str) -> Fetched:
    """초안 하나를 만들고 되읽어 확인한다. **보내지 않는다.**

    🔴 수신자를 호출부가 명시한다. 헤더에서 추측하지 않는다 —
       웹플로우 메일에 답장했다가 no-reply 로 가서 550 반송된 적이 있다(2026-07-10).
    """
    if not to:
        return Fetched.unknown("수신자가 비어 있다 — 추측하지 않는다")

    mime = MIMEMultipart("alternative")
    mime["To"] = to
    mime["Subject"] = subject
    if msg.get("message_id_header"):
        mime["In-Reply-To"] = msg["message_id_header"]
        mime["References"] = msg["message_id_header"]
    mime.attach(MIMEText(body, "plain", "utf-8"))
    mime.attach(MIMEText(_to_gmail_html(body), "html", "utf-8"))
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()

    try:
        message = {"raw": raw}
        if msg.get("thread_id"):
            message["threadId"] = msg["thread_id"]
        made = _req("POST", "/drafts", json_body={"message": message})
        did = made.get("id")
        if not did:
            return Fetched.unknown(f"초안 id 가 안 왔다: {str(made)[:120]}")
        got = _get(f"/drafts/{did}", params={"format": "metadata"})
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.unknown(f"gmail:{e}")

    if got.get("id") != did:
        return Fetched.unknown(f"readback_mismatch:초안 {did} 를 되읽지 못했다")
    hdrs = {h["name"].lower(): h["value"]
            for h in ((got.get("message") or {}).get("payload") or {}).get("headers") or []}
    if to.lower() not in (hdrs.get("to") or "").lower():
        return Fetched.unknown(f"readback_mismatch:수신자가 다르다 (원함 {to}, 저장 {hdrs.get('to')})")
    return Fetched.measured(did)


def forward_draft(msg: dict, to: list[str], intro: str, attachments: list[dict] | None = None) -> Fetched:
    """원래 메일을 포워드하는 **초안**(Fwd: 제목 · 인사말 + 원문 머리 + 본문 · 첨부 그대로). 보내지 않는다. 되읽어 수신자를 확인한다.
    🔴 수신자는 호출부가 명시한다(명부의 회사 메일). 원래 메일 스레드에 붙여 hello 쪽에서도 이어서 보인다."""
    to = [t for t in to if t]
    if not to:
        return Fetched.unknown("수신자가 비어 있다 — 추측하지 않는다")
    subj = msg.get("subject") or ""
    head = ("---------- Forwarded message ---------\n"
            f"From: {msg.get('sender_raw') or msg.get('sender') or ''}\nDate: {msg.get('date') or ''}\n"
            f"Subject: {subj}\nTo: {', '.join(msg.get('to') or [])}\n")
    body = f"{intro.rstrip()}\n\n{head}\n{msg.get('body') or ''}"
    mime = MIMEMultipart("mixed")
    mime["To"] = ", ".join(to)
    mime["Subject"] = subj if subj.lower().startswith(("fwd:", "fw:")) else f"Fwd: {subj}"
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body, "plain", "utf-8"))
    alt.attach(MIMEText(_to_gmail_html(body), "html", "utf-8"))
    mime.attach(alt)
    for a in attachments or []:
        main, _, sub = (a.get("mime") or "application/octet-stream").partition("/")
        part = MIMEBase(main or "application", sub or "octet-stream")
        part.set_payload(a["data"])
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=a["name"])
        mime.attach(part)
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode()
    try:
        message = {"raw": raw}
        if msg.get("thread_id"):
            message["threadId"] = msg["thread_id"]
        made = _req("POST", "/drafts", json_body={"message": message})
        did = made.get("id")
        if not did:
            return Fetched.unknown(f"초안 id 가 안 왔다: {str(made)[:120]}")
        got = _get(f"/drafts/{did}", params={"format": "metadata"})
    except AuthDead as e:
        return Fetched.unknown(f"auth_dead:{e}")
    except GmailError as e:
        return Fetched.unknown(f"gmail:{e}")
    hdrs = {h["name"].lower(): h["value"] for h in ((got.get("message") or {}).get("payload") or {}).get("headers") or []}
    if got.get("id") != did or any(t.lower() not in (hdrs.get("to") or "").lower() for t in to):
        return Fetched.unknown(f"readback_mismatch:초안 {did} 수신자 {hdrs.get('to')}")
    return Fetched.measured(did)


def draft_reply(msg: dict, body: str, to: str, subject: str | None = None) -> Fetched:
    """`subject` 를 주면 그걸 쓴다.

    🔴 홈페이지 폼 회신이 이 인자를 쓴다. 폼 메일의 제목은 문의자가 쓴 게 아니라
       "New form submission on Webflow for 2023_kakaoventures" 라는 알림 제목이다.
       그대로 답장하면 문의자는 **자기가 뭘 물었는지도 모를 제목**을 받는다
       (실측 2026-09-02: 초안 2장이 그렇게 나갔다). 문의자가 쓴 제목을 넘겨받는다.
    """
    s = subject if subject is not None else (msg.get("subject") or "")
    return _draft(msg, body, to, subject=s if s.startswith("Re: ") else f"Re: {s}")
