"""극초기 스타트업 Notion DB read/write. bplan-automation의 Notion 클라이언트 패턴 재활용.

DB: collection://00000000-0000-0000-0000-e3dc926767d1 (확인됨)
필드 매핑·'발굴' 허용값: references/notion-schema.md

토큰은 환경변수 NOTION_TOKEN(bplan 값 재사용). 해당 integration을 Notion에서 극초기 DB에
"연결(공유)"해야 200이 뜬다(integration 토큰은 공유된 리소스만 접근).

property 타입은 코드에 하드코딩하지 않고 GET /v1/databases/{id} 로 실제 스키마를 읽어
그 타입에 맞춰 payload를 만든다 → schema 문서의 "url/text" 모호성·필드 부재에 안전.
"""
from __future__ import annotations

import os
import re
from datetime import date

import requests

from adapters.base import _normalize, _normalize_linkedin_url, _normalize_url, norm_post_url

API = "https://api.notion.com/v1"


def _token() -> str | None:
    return os.environ.get("NOTION_TOKEN")


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }


# 노션 공식 한도 = **평균 3 req/s**. 07-11엔 415행을 무지연 순차 POST 했고, retry/backoff 코드가
# 리포 전체에 0줄이었다. 429가 나면 그 행은 print만 되고 사라지는데, seen-store는 그와 무관하게
# '봤음'으로 확정돼 **다시는 안 잡힌다**(조용한 영구 유실).
_RATE_SLEEP = 0.34            # ≈3 req/s
_MAX_RETRY = 4

# 🔴 [2026-07-21] 네트워크 오류(DNS·연결 불가) 백오프는 429/5xx와 **분리한다**.
# 07-18 크론: 수집(10,112건)은 성공했는데 착지 직전 노션 쓰기가 `gaierror`로 죽어 **229건 유실**.
# 원인은 절전 해제 직후 DNS가 아직 안 올라온 상태인데, 당시 백오프가 1/2/4s = **7초**뿐이라
# 망이 붙기 전에 재시도를 다 소진했다. 수집 경로(`adapters/_util.get_with_retry`)는 같은 날
# 15/30/60s = 105초로 강화됐는데 **쓰기 경로만 안 고쳐져** 파이프라인 뒷단에서 똑같이 죽었다.
# → 두 경로의 정책을 맞춘다. 429는 Retry-After가 오므로 짧게(1s 기반) 유지.
_NET_BACKOFF = 15.0           # 연결/DNS 실패 전용: 15 → 30 → 60s (총 105초, 수집 경로와 동일)


# ══════════════════════════════════════════════════════════════════════
# 전송층 — **kv_core.notion 이 정본이다.** 여기는 얇은 위임뿐.
# ══════════════════════════════════════════════════════════════════════
# 🔴 [2026-08-13 W12 · 리뷰 B-F1] 이 두 함수는 `kv_core/notion.py` 와 **동작이 완전히
#    동일한 복제본**이었다(로컬 서버로 429/500/Retry-After/연결거부 전 시나리오를 나란히
#    때려 확인 — 재시도 횟수·간격·총 소요가 전부 같았다).
#
#    ⚠️ 감사 브리핑에 내가 "kv_core 쪽은 RATE_SLEEP 0 · MAX_RETRY 0 = 규율 없음"이라고
#       적어 넘겼는데 **틀렸다.** `kv_core` 는 상수에 언더스코어를 안 쓴다(`RATE_SLEEP`).
#       `_RATE_SLEEP` 으로 grep 한 인공물이었다. 리뷰가 실측으로 뒤집어 왔다.
#
#    진짜 문제는 "어느 쪽이 약한가"가 아니라 **정본이 아무에게도 안 쓰인다**는 것이었다 —
#    `kv_core.notion` 임포터가 리포 전체에 **0건**이었다. 그래서 노션을 부르는 13개 모듈이
#    각자 클라이언트를 만들었고 **그중 11본에 백오프가 0줄**이다.
#    새 파일을 열면 임포트할 정본이 안 보인다 — 검색해도 용례가 안 나오니까.
#
#    복제본을 지우고 위임한다. 동작이 같으므로 교체 위험이 0 이고, 앞으로 kv_core 를
#    고치면 여기도 같이 고쳐진다(방금 `ChunkedEncodingError` 를 그렇게 고쳤다 — B-F6).
def _write(method: str, url: str, token: str, payload: dict):
    """노션 쓰기 — `kv_core.notion.write` 에 위임(정본). 동작 동일."""
    from kv_core import notion as _kv
    return _kv.write(method, url, token, payload)


def _read(url: str, token: str, params: dict | None = None):
    """노션 읽기 — `kv_core.notion.read` 에 위임(정본). 동작 동일. → `_write` 주석 참조."""
    from kv_core import notion as _kv
    return _kv.read(url, token, params)


# ============================ 페이지 본문 블록 (인물 1행의 핵심) ============================
# 스레드는 **사람 1행**이고, 그 사람의 글은 페이지 **본문**에 시간순으로 쌓인다.
# 프로퍼티 텍스트 칸은 2,000자 제한이라 글 여러 개를 못 담지만 본문은 사실상 무제한이다.
# (이 리포에 블록을 쓰는 코드가 원래 0줄이었다 — 모든 행의 본문이 비어 있었다)
_MAX_CHILDREN = 100           # 노션: children 배열 1회 최대 100개
_MAX_TEXT = 1900              # 노션: rich_text 항목당 content 2,000자
# 글 1개 = 블록 1~2개(heading_3 + 본문이 있으면 paragraph). 글 단위로 자르면 블록은 자동으로
# 상한 안에 든다. **블록 단위로 자르면 청크 경계가 글을 반으로 쪼개거나 중복 append를 만든다.**
_MAX_POSTS_PER_REQ = _MAX_CHILDREN // 2      # = 50글 (최악의 경우 100블록)


def _clip16(text: str, limit: int = 2000) -> str:
    """노션 길이 제한에 맞춰 자른다. **파이썬 문자 수가 아니라 UTF-16 코드유닛 기준**이다.

    🔴 [2026-07-27 실측] `text[:2000]`으로 잘랐는데 노션이 400을 냈다:
         "content.length should be ≤ 2000, instead was 2003"
       원인: 노션(JS)은 **UTF-16 코드유닛**을 센다. 이모지 같은 보조평면 문자는 **2로 계산**된다.
       `Example Person M`의 bio에 `🦖🦹🎮` 3개가 있어 2000자가 2003유닛이 됐다.
       → 링크드인 495건 중 **7건이 통째로 착지 실패**했다(이모지 쓰는 사람이 정확히 우리가 찾는
         인구다 — 게임·디자인·인디 개발자). 로그엔 400이 찍혔지만 조용히 넘어갔으면 못 봤다.

    ⚠️ 새로 rich_text/title에 값을 넣을 땐 **반드시 이걸 통과시켜라.** `[:N]`은 안전하지 않다.
    """
    s = text or ""
    if len(s.encode("utf-16-le")) // 2 <= limit:
        return s
    # 유닛 기준으로 뒤에서 줄인다(문자 경계는 파이썬 슬라이스가 보장한다)
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(s[:mid].encode("utf-16-le")) // 2 <= limit:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo]


def _rt_chunks(text: str) -> list[dict]:
    """긴 본문을 1,900자 단위 rich_text 배열로. 안 쪼개면 2,000자 초과 시 400이 난다."""
    s = (text or "").strip()
    if not s:
        return []
    # 🔴 청크도 **UTF-16 유닛**으로 잘라야 한다 — 1,900 파이썬 문자가 이모지 때문에
    #   2,000유닛을 넘으면 그 블록만 400이 나고 본문이 통째로 안 붙는다.
    out, rest = [], s
    while rest and len(out) < 10:
        piece = _clip16(rest, _MAX_TEXT)
        if not piece:
            break
        out.append({"type": "text", "text": {"content": piece}})
        rest = rest[len(piece):]
    return out


def _blocks_for_posts(posts: list[dict]) -> list[dict]:
    """글 리스트(시간순) → 블록 배열. 글 1개 = heading_3(날짜·층, 원문 링크) + paragraph(본문).

    ★ heading_3의 링크 href가 **중복 append 방지의 유일한 근거**다. 이 href를 norm_post_url로
      정규화한 값이 곧 비교 키다 — 읽는 쪽(existing_post_urls)이 같은 함수를 쓴다.
    """
    out: list[dict] = []
    for p in posts:
        url = (p.get("url") or "").strip()
        when = (p.get("posted_at") or "")[:10]
        layer = p.get("layer") or ""
        kw = p.get("query") or ""
        label = " · ".join(x for x in (when, layer, (f"「{kw}」" if kw else "")) if x) or "post"
        head = {"type": "text", "text": {"content": label}}
        # 액터 출력은 신뢰하지 않는다 — 상대경로('/@u/post/x')나 빈 문자열이 오면 노션이 400을 내고
        # **그 청크 전체**(다른 사람 글까지)가 날아간다. http(s)만 링크로 건다.
        if url.startswith(("http://", "https://")):
            head["text"]["link"] = {"url": url}
        out.append({"object": "block", "type": "heading_3",
                    "heading_3": {"rich_text": [head]}})
        body = _rt_chunks(p.get("text") or "")
        if body:
            out.append({"object": "block", "type": "paragraph",
                        "paragraph": {"rich_text": body}})
    return out


_CAREER_HEAD = "경력 · 학력"


def _blocks_for_career(career: str, stamp: str) -> list[dict]:
    """경력 타임라인 → 블록. heading_3에 **날짜 도장**을 찍어 append-only로 쌓는다.

    왜 덮어쓰지 않나: 경력은 시간이 지나면 바뀌는데(창업·이직), 덮으면 **"언제 무엇이 바뀌었나"가
    영구 소실**된다. `core/followup.py`가 지문에 대해 같은 원칙을 적는다("이벤트는 append-only").
    그리고 우리가 찾는 신호가 바로 그 **변화**다.
    """
    out: list[dict] = [{"object": "block", "type": "heading_3", "heading_3": {
        "rich_text": [{"type": "text", "text": {"content": f"{_CAREER_HEAD} · {stamp}"}}]}}]
    # paragraph 하나에 rich_text 2,000자 제한이 있어 문단 단위로 쪼갠다(문장을 안 자르려고 줄 단위로).
    buf: list[str] = []
    size = 0
    for line in career.split("\n"):
        if size + len(line) > 1800 and buf:
            out.append({"object": "block", "type": "paragraph",
                        "paragraph": {"rich_text": _rt_chunks("\n".join(buf))}})
            buf, size = [], 0
        buf.append(line)
        size += len(line) + 1
    if buf:
        out.append({"object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": _rt_chunks("\n".join(buf))}})
    return out


def existing_career(token: str, page_id: str) -> str | None:
    """본문에 이미 쌓인 경력 텍스트 전부(중복 append 방지용). 읽기 실패면 None."""
    out, cursor = [], None
    for _ in range(20):
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = _read(f"{API}/blocks/{page_id}/children", token, params)
        if r.status_code != 200:
            return None
        d = r.json()
        for b in d.get("results", []):
            t = b.get("type") or ""
            rt = (b.get(t) or {}).get("rich_text") or []
            out.append("".join(x.get("plain_text", "") for x in rt))
        if not d.get("has_more"):
            break
        cursor = d.get("next_cursor")
    return "\n".join(out)


def append_career_blocks(token: str, page_id: str, career: str, stamp: str) -> bool:
    """경력을 본문에 append. **이미 같은 내용이 있으면 붙이지 않는다**(멱등).

    🔴 본문을 못 읽으면 **붙이지 않는다.** 읽기 딸꾹질 한 번에 같은 경력이 두 번 쌓이면
       판단 에이전트가 같은 사람을 두 번 읽는다(스레드 글 append와 같은 규율).
    """
    if not career:
        return True
    have = existing_career(token, page_id)
    if have is None:
        print(f"  · 본문 조회 실패 page={page_id[:8]} — 경력 추가만 건너뛴다")
        return False
    probe = career.split("\n")[0][:80]
    if probe and probe in have:
        return True                       # 같은 타임라인이 이미 있다 → no-op
    blocks = _blocks_for_career(career, stamp)
    for i in range(0, len(blocks), _MAX_CHILDREN):
        r = _write("PATCH", f"{API}/blocks/{page_id}/children", token,
                   {"children": blocks[i:i + _MAX_CHILDREN]})
        if r.status_code != 200:
            print(f"  ✗ 경력 append 실패 page={page_id[:8]} {r.status_code} {r.text[:160]}")
            return False
    return True


def existing_post_urls(token: str, page_id: str) -> set[str] | None:
    """페이지 본문에 **이미 붙어 있는** 글 URL 집합(정규화). 조회 실패 시 **None**.

    🔴 실패를 빈 set으로 돌려주면 안 된다 — "붙은 글이 하나도 없다"로 오해되어
       매일 전량이 재append되고 본문이 무한히 자란다. None이면 호출부가 append를 **건너뛴다.**
    """
    urls: set[str] = set()
    cursor = None
    for _ in range(20):                       # 페이지당 최대 2,000블록이면 충분
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        r = _read(f"{API}/blocks/{page_id}/children", token, params)
        if r.status_code != 200:
            print(f"  · 본문 조회 실패 page={page_id[:8]} status={r.status_code}")
            return None
        d = r.json()
        for b in d.get("results", []):
            rt = (b.get(b.get("type") or "") or {}).get("rich_text") or []
            for run in rt:
                href = run.get("href") or ((run.get("text") or {}).get("link") or {}).get("url")
                if href:
                    urls.add(norm_post_url(href))
        if not d.get("has_more"):
            return urls
        cursor = d.get("next_cursor")
    return urls


def append_post_blocks(token: str, page_id: str, posts: list[dict]) -> tuple[int, int]:
    """글을 페이지 본문에 append. (붙인 글 수, 실패한 청크 수).

    **글 단위로 청크를 나눈다** — 블록 단위로 나누면 경계가 한 글의 heading과 본문을 갈라놓는다.
    """
    if not posts:
        return 0, 0
    done = failed = 0
    for i in range(0, len(posts), _MAX_POSTS_PER_REQ):
        chunk = posts[i:i + _MAX_POSTS_PER_REQ]
        blocks = _blocks_for_posts(chunk)
        if not blocks:
            continue
        r = _write("PATCH", f"{API}/blocks/{page_id}/children", token, {"children": blocks})
        if r.status_code == 200:
            done += len(chunk)
        else:
            failed += 1
            print(f"  ✗ 본문 append 실패 page={page_id[:8]} status={r.status_code} {r.text[:160]}")
    return done, failed


def db_for(cfg: dict, source: str) -> str:
    """채널별 착지 DB — `notion.channel_db_ids[{source}]`, 없으면 기본 `notion.db_id`.

    🔴 [2026-07-14] **DB를 채널별로 나눈다.** 근거:
      · 단위가 다르다 — 1행=앱 / 1행=사람. 한 DB에 섞이면 "총 N행"이 의미 없는 숫자다.
      · 판단 흐름이 다르다 — 앱스토어는 판단 없는 보드(Tier 없음), 사람 채널은 Tier 대상.
        같은 빈칸인데 앱스토어는 '원래 맞는 것', 사람은 '아직 안 한 것'이라 뜻이 정반대였다.
      · 물량이 압도한다 — 앱스토어가 하루 수십~수백 행씩 쌓여 사람 채널을 묻는다.
      · 속성 49개 중 앱스토어가 쓰는 건 **23개뿐**이다(나머지는 전부 사람용 빈칸).
    ⚠️ 통합 DB의 유일한 명분이던 **채널 간 교차 dedup은 원래 작동하지 않았다**(실측):
       키가 전부 채널 전용이다 — app:=앱스토어, li:=링크드인, name:=레거시DB 대조용,
       스레드는 name 키를 아예 안 만든다. **나눠도 잃는 게 0이다.**
    """
    m = (cfg.get("notion", {}).get("channel_db_ids") or {})
    raw = str(m.get(source) or cfg["notion"]["db_id"])
    return raw.split("://", 1)[-1]


def landing_dbs(cfg: dict) -> list[str]:
    """착지에 쓰이는 **모든** DB(기본 + 채널별). dedup ①단이 전부를 읽어야 한다 —
    하나라도 빠뜨리면 그 DB에 이미 있는 사람/앱을 **다시 착지시킨다**(중복 행)."""
    n = cfg.get("notion", {})
    ids = [str(n.get("db_id", ""))] + [str(v) for v in (n.get("channel_db_ids") or {}).values()]
    out: list[str] = []
    for i in ids:
        i = i.split("://", 1)[-1]
        if i and not i.endswith("TODO") and i not in out:
            out.append(i)
    return out


def _db_id(cfg: dict) -> str:
    """config 의 notion.db_id 에서 collection:// 접두어를 벗겨 raw uuid 반환."""
    raw = str(cfg["notion"]["db_id"])
    return raw.split("://", 1)[-1]


def _title_prop_name(schema: dict) -> str:
    """스키마에서 title 타입 property 이름을 찾는다(법인(서비스))."""
    for name, meta in schema.items():
        if meta.get("type") == "title":
            return name
    return "법인(서비스)"


def _fetch_schema(cfg: dict, token: str) -> dict:
    """GET database → {property_name: {"type": ...}} 매핑."""
    # [W12 · B-F3] 자기 파일의 규율(_read)을 우회하고 있었다 — 백오프·페이싱 0.
    r = _read(f"{API}/databases/{_db_id(cfg)}", token)
    if r.status_code != 200:
        raise RuntimeError(
            f"Notion DB 조회 실패 status={r.status_code} body={r.text[:300]}\n"
            "→ integration('plan')을 대상 DB에 연결(공유)했는지 확인 (references/notion-schema.md)")
    return r.json().get("properties", {})


# --- dedup 조회 ---
def weekly_url_keys(cfg: dict) -> set[str]:
    """[C4/①] 주간 소싱 DB에서 LinkedIn URL·홈페이지 URL 정확매칭 키 집합을 반환.

    go-forward dedup의 핵심 — 주간 DB에 LinkedIn URL이 쌓일수록 정확도 상승.
    키 규칙은 core.dedup._keys 와 동일: 'li:'+정규화 프로필URL / 'home:'+정규화 URL.

    TODO[D1/D3]: 주간 DB 확정(=그물망 소싱 DB에 '주차' 속성) + 'LinkedIn URL' property 신설 후,
    아래 PROP 이름을 실제 스키마에 맞춰 확정. weekly_db_id 미설정이면 빈 집합(=①단 skip).
    """
    # 🔴 [2026-07-14] DB를 채널별로 나눈 뒤로는 **착지 DB 전부**를 읽어야 한다.
    #   하나라도 빠뜨리면 그 DB에 이미 있는 앱/사람을 **다시 착지시킨다**(중복 행).
    #   weekly_db_id 는 하위호환으로 남긴다(기본 DB와 같으면 landing_dbs가 중복 제거한다).
    dbs = landing_dbs(cfg)
    wid_legacy = str(cfg.get("notion", {}).get("weekly_db_id", "") or "").split("://", 1)[-1]
    if wid_legacy and not wid_legacy.endswith("TODO") and wid_legacy not in dbs:
        dbs.append(wid_legacy)
    if not dbs:
        return set()
    token = _token()
    if not token:
        return set()
    # 🔴 HOME_PROP은 "홈페이지"였다 — 착지 DB의 실제 property 이름은 **"Homepage"**다.
    #    props.get("홈페이지")가 항상 None이라 'home:' 키가 **단 한 개도** 만들어진 적이 없다.
    #    경고조차 없었다(반복버그 ①: 키 이름 추측 + ④: 조용히 실패).
    #
    # 🔴 [2026-07-14] **APP_PROP이 아예 없었다.** 착지 DB에 `App ID` 칸이 멀쩡히 있는데 읽지 않아
    #    'app:' 키가 **0개**였다(실측: 이 함수가 만든 485개 키의 종류가 {li, home}뿐).
    #    앱스토어의 **유일한 정확 고유키를 dedup에서 안 쓰고 있었다.** 그 공백을 'home:'(= 개발사
    #    홈페이지)가 메우고 있었는데, 그건 앱이 아니라 **개발사** 단위라 오탐을 냈다:
    #      "한강 수온 측정 앱"이 같은 개발사(gonilab.blogspot.com)의 기존 앱 "글자수 세기" 때문에
    #      **조용히 버려졌다.** 앱을 여러 개 내는 1인 개발자 = 정확히 우리가 찾는 사람인데.
    #    → app: 키를 여기서 만들고, appstore 후보의 home: 키는 core/dedup._keys 에서 뺀다.
    LI_PROP, HOME_PROP, APP_PROP = "LinkedIn URL", "Homepage", "App ID"
    keys: set[str] = set()
    for wid in dbs:
        url = f"{API}/databases/{wid}/query"
        payload: dict = {"page_size": 100}
        n0 = len(keys)
        while True:
            # [W12 · B-F3] 이 while 루프가 이 스킬에서 **노션 요청 수가 가장 많은 구간**이다
            #   (착지 DB 4개 전수 × 100행 페이지). 그런데 백오프가 0줄이었다.
            r = _write("POST", url, token, payload)
            if r.status_code != 200:
                # 🔴 조용히 넘기면 그 DB의 앱/사람이 **전부 다시 착지한다.** 소리를 낸다.
                print(f"🔴 착지 DB {wid[:8]} query 실패 status={r.status_code} — "
                      f"이 DB에 대한 ①단 dedup이 **꺼진다**(중복 행 위험).")
                break
            data = r.json()
            for page in data.get("results", []):
                props = page.get("properties", {})
                li = (props.get(LI_PROP) or {}).get("url")
                if li:
                    keys.add("li:" + _normalize_linkedin_url(li))
                home = (props.get(HOME_PROP) or {}).get("url")
                if home:
                    keys.add("home:" + _normalize_url(home))
                # App ID 는 rich_text 다(number 아님) — 빈 배열이면 스킵.
                app = "".join(t.get("plain_text", "")
                              for t in (props.get(APP_PROP) or {}).get("rich_text", [])).strip()
                if app:
                    keys.add(f"app:{app}")
            if not data.get("has_more"):
                break
            payload["start_cursor"] = data.get("next_cursor")
            if not payload["start_cursor"]:
                break
        print(f"· ①단 착지DB {wid[:8]}: 키 {len(keys) - n0}개")
    return keys


def existing_dedup_keys(cfg: dict) -> set[str]:
    """②단 — **레거시 DB**(극초기 · 스타트업)의 제목(대표이사·회사명)으로 name: 키 집합 반환.

    ⚠️ 이 함수는 착지 DB(db_id)를 읽으면 안 된다. 착지 DB의 제목은 채널마다 의미가 달라
    (스레드 = 작성자 핸들) 자기 자신과 대조하는 순간 "이미 착지한 사람의 새 글"이 영구 차단된다.
    실제로 그렇게 돼 있었고, 스레드 raw 247건이 dedup 생존 0건이 됐다.
    대조 대상은 **우리가 이미 아는 회사/대표**가 적힌 레거시 2개 DB뿐이다.

    레거시 DB엔 LinkedIn URL property가 없어 이름 기반 소프트 매칭만 가능하다.
    조회 실패(미공유·id 오류)는 경고 후 skip — ②단이 비어도 URL 기반 ①단·착지키가 남는다.
    """
    token = _token()
    if not token:
        print("⚠️  NOTION_TOKEN 없음 — Notion 대조 생략(배치 내 중복만 제거). sync엔 토큰 필요.")
        return set()

    name_props = cfg.get("notion", {}).get("dedup_name_props") or []
    nc = cfg.get("notion", {})

    # 🔴 [2026-07-21] 대조원을 **config 리스트**로 바꿨다. 예전엔 cfg 키 2개
    #    (`earlystage_db_id`,`startup_db_id`)를 **코드에 하드코딩**해 읽었고, 그래서
    #    **본 스타트업 DB와 Bplan 2.0이 대조에서 통째로 빠져 있었다**(실측: 사용자가
    #    "Example Person J는 스타트업 DB에 있다"고 지적해 발각. 우리 대조는 그 DB를 볼 수조차 없었다).
    #    게다가 `startup_db_id`라는 이름이 실제로는 **Bplan 3.0**을 가리켜 혼동을 키웠다.
    #    → 이제 `notion.dedup_db_ids`에 {label,id}를 추가하면 **코드 수정 없이** 대조원이 는다.
    targets: list[tuple[str, str]] = []
    for ent in (nc.get("dedup_db_ids") or []):
        if isinstance(ent, dict) and ent.get("id"):
            targets.append((str(ent.get("label") or ent["id"])[:40], str(ent["id"])))
        elif isinstance(ent, str) and ent:
            targets.append((ent[:40], ent))
    if not targets:                                    # 하위호환 — 리스트가 없으면 옛 키
        for cfg_key in ("earlystage_db_id", "startup_db_id"):
            raw = str(nc.get(cfg_key) or "")
            if raw and not raw.endswith("TODO"):
                targets.append((cfg_key, raw))

    keys: set[str] = set()
    ok, failed, empty = 0, [], []
    for label, raw in targets:
        db = raw.split("://", 1)[-1]
        if not db or db.endswith("TODO"):
            continue
        got = _title_keys(db, token, label, name_props)
        # ⚠️ **빈 DB와 조회 실패를 구분한다.** 둘 다 0키라서 예전엔 같이 '실패'로 셌는데,
        #    Bplan 2.0은 정상 조회되는 **0행 DB**다. 이걸 실패로 부르면 매 실행마다 가짜
        #    🔴가 뜨고, 진짜 실패(스타트업 DB 404)가 그 소음에 묻힌다 — 경보 피로.
        if got is None:
            failed.append(label)
        elif got:
            ok += 1
            keys |= got
        else:
            empty.append(label)
            ok += 1

    # 🔴 **조용히 실패하지 않는다.** 대조 DB를 설정해뒀는데 키가 0개면 ②단은 전면 사망이고,
    #    그건 "이미 아는 사람 제외"가 **0% 작동한다**는 뜻이다. 그런데 예전 로그는 그 뒤에
    #    "· dedup: 465 → 460 신규(기존 5건 제거)"라는 성공형 문장을 찍어 사망을 가렸다.
    if targets and not keys:
        print("🔴 ②단 dedup 0키 — **'이미 아는 사람 제외'가 작동하지 않는다.** "
              "대조 DB가 404이거나 인티그레이션에 공유되지 않았다. 아래 dedup 숫자는 "
              "'우리가 지난번에 착지시킨 것'만 뺀 값이다(KV가 아는 회사/대표는 안 빠졌다).")
    elif failed:
        # ★ 부분 실패도 소리를 낸다. 4개 중 1개가 404면 그 DB에 있는 회사는 **전부 통과**한다.
        #   전체가 죽었을 때만 경고하면 이 구멍이 성공 로그에 묻힌다(이번 사고가 정확히 그 모양).
        print(f"🔴 ②단 대조원 {len(failed)}/{len(targets)}개 **실패** → {', '.join(failed)} "
              f"— 이 DB들에 있는 회사/대표는 걸러지지 않는다(인티그레이션 공유 확인 필요).")
    if ok:
        tail = f" (그중 빈 DB {len(empty)}개: {', '.join(empty)})" if empty else ""
        print(f"· ②단 대조원 {ok}/{len(targets)}개 정상 · 총 {len(keys)}키{tail}")
    return keys


def _title_keys(db: str, token: str, label: str, name_props: list[str] | None = None) -> set[str] | None:
    """한 DB의 페이지들 → name: 키 집합.

    title(회사명)뿐 아니라 **사람 이름 property**(대표명 등)도 읽는다.
    ★ 이게 없으면 링크드인 채널은 ID를 고쳐도 영영 안 걸린다 —
      착지 행의 제목은 **사람 이름**(97/97 실측)인데 대조 DB의 제목은 **회사명**이라
      축이 아예 다르다. Bplan DB의 '대표명' 같은 rich_text를 읽어야 비로소 대조가 성립한다.
    """
    r = _read(f"{API}/databases/{db}", token)          # [W12 · B-F3]
    if r.status_code != 200:
        print(f"⚠️  대조 DB '{label}' 조회 실패 status={r.status_code} — ②단 dedup에서 제외")
        return None          # ★ None=조회 실패 / set()=정상인데 0건. 호출부가 이 둘을 구분한다.
    schema = r.json().get("properties", {})
    title_name = _title_prop_name(schema)
    # 설정된 이름 property 중 이 DB에 실제로 있는 것만 (없는 이름을 읽어봐야 조용히 빈다)
    #
    # 🔴 [2026-07-21] **정확일치를 버리고 정규화 매칭으로 바꿨다.** 스타트업 DB의 사람이름
    #    property명이 `✅ 대표이사`인데, 정확일치라 `"대표이사" in schema`가 False였다 —
    #    이모지·공백 하나 때문에 **대표이사 대조가 영영 0건**이 된다. 조용히 빈다는 게 이 모양이다.
    #    (같은 부류: dedup_name_props에 "대표"를 넣으면 "대표전화" 같은 것도 걸릴 수 있어
    #     선택된 property명을 아래에서 **로그로 찍는다** — 잘못 걸리면 눈에 보이게.)
    def _pn(s: str) -> str:
        return re.sub(r"[^0-9a-z가-힣]", "", str(s).lower())

    wanted = [_pn(t) for t in (name_props or []) if _pn(t)]
    person_props = [p for p in schema if any(w in _pn(p) for w in wanted)]

    keys: set[str] = set()
    n_title = n_person = 0
    payload: dict = {"page_size": 100}
    while True:
        # [W12 · B-F3] 레거시 대조 DB 4개 전수 페이지네이션 — 두 번째로 요청이 많은 구간.
        q = _write("POST", f"{API}/databases/{db}/query", token, payload)
        if q.status_code != 200:
            print(f"⚠️  대조 DB '{label}' query 실패 status={q.status_code} — 부분 결과로 진행")
            return keys
        data = q.json()
        for page in data.get("results", []):
            props = page.get("properties", {})
            name = _plain(props.get(title_name))
            if name:
                keys.add("name:" + _normalize(name))
                n_title += 1
            for pp in person_props:
                person = _plain(props.get(pp))
                for one in _split_names(person):
                    keys.add("name:" + _normalize(one))
                    n_person += 1
        if not data.get("has_more"):
            break
        payload["start_cursor"] = data.get("next_cursor")
        if not payload["start_cursor"]:
            break
    extra = f" + 사람이름 {n_person}건({', '.join(person_props)})" if person_props else ""
    print(f"· ②단 대조 '{label}': 회사명 {n_title}건{extra}")
    return keys


def _split_names(raw: str) -> list[str]:
    """'홍길동, 김철수' / '홍길동·김철수' → ['홍길동', '김철수']. 공동대표 표기 흡수."""
    import re
    if not raw:
        return []
    parts = re.split(r"[,/·&]|\s+및\s+", raw)
    return [p.strip() for p in parts if p.strip()]


# --- 적재 ---
def stage(cfg: dict, judged: list[dict]) -> int:
    """판단 카드를 극초기 DB에 신규 로우(미검토)로 적재. 적재 건수 반환.

    필드 매핑(references/notion-schema.md):
      법인(서비스)=name · 한 줄 설명=one_liner · 링크=url · 발굴=source
      논의 포인트=raw_text(요약 전 원문 truncate) · Status=cfg.notion.unreviewed_status
      발굴자=cfg.discoverer_user_id(유효할 때만) · 다운로드수/평점=signals(있으면)
    발송·확정 없음 — 사람 검수 대기.
    """
    token = _token()
    if not token:
        raise RuntimeError("NOTION_TOKEN 없음 — .env 확인 (bplan 값 재사용)")
    if not judged:
        print("· 적재할 후보 없음")
        return 0

    schema = _fetch_schema(cfg, token)
    db_id = _db_id(cfg)
    url = f"{API}/pages"
    ok = 0
    for card in judged:
        props = _build_properties(cfg, card, schema)
        payload = {"parent": {"database_id": db_id}, "properties": props}
        r = _write("POST", url, token, payload)      # 429 백오프 + 3req/s 준수
        if r.status_code == 200:
            ok += 1
        else:
            name = card.get("candidate", {}).get("name", "?")
            print(f"  ✗ 적재 실패 [{name}] status={r.status_code} {r.text[:200]}")
    return ok


def _build_properties(cfg: dict, card: dict, schema: dict) -> dict:
    """판단 카드 → Notion properties. 스키마에 존재하고 타입이 맞는 필드만 넣는다."""
    c = card.get("candidate", {})
    signals = c.get("signals", {}) or {}
    one_liner = card.get("one_liner") or c.get("one_liner", "")
    discussion = _discussion_text(card)

    intended = [
        (_title_prop_name(schema), c.get("name", "")),   # 법인(서비스) (title)
        ("한 줄 설명", one_liner),
        ("링크", c.get("url", "")),
        ("발굴", c.get("source", "")),
        ("논의 포인트", discussion),
        ("Tier", card.get("tier_suggestion", "")),
        ("영역", card.get("area", [])),
        ("Status", cfg.get("notion", {}).get("unreviewed_status", "")),
        ("발굴자", _user_id(cfg.get("discoverer_user_id", ""))),
        ("다운로드수", signals.get("downloads")),
        ("평점", signals.get("rating")),
    ]

    props: dict = {}
    for name, value in intended:
        if name not in schema:
            continue
        value = _known_options_only(name, schema[name], value)
        formatted = _prop_value(schema[name].get("type", ""), value)
        if formatted is not None:
            props[name] = formatted
    return props


_OPTION_WARNED: set[str] = set()


def _known_options_only(name: str, prop: dict, value):
    """select/multi_select 는 **스키마에 이미 있는 옵션만** 통과시킨다.

    노션은 없는 값을 쓰면 옵션을 조용히 새로 만든다. keywords.yaml 을 건드릴 때마다
    공유 DB에 옵션이 쌓이고(현재 Matched Keyword 옵션 18개 중 13개가 이미 고아),
    필터·그룹핑이 못 쓰게 된다. Genre 는 GENRE_NAMES 로 막아뒀지만 나머지는 무방비였다.

    ⚠️ 버리되 **조용히 버리지 않는다** — 버린 값을 실행당 한 번 경고로 찍는다.
       그 값이 정당하면 노션에서 옵션을 먼저 만들어야 한다는 신호다.
    """
    ptype = prop.get("type", "")
    if ptype not in ("select", "multi_select"):
        return value
    allowed = {o.get("name") for o in (prop.get(ptype) or {}).get("options", [])}
    if not allowed:
        return value
    vals = value if isinstance(value, list) else ([value] if value else [])
    kept = [v for v in vals if v in allowed]
    for v in vals:
        if v not in allowed and (tag := f"{name}:{v}") not in _OPTION_WARNED:
            _OPTION_WARNED.add(tag)
            print(f"⚠️  '{name}' 옵션 없음 → 값 '{v}' 버림. 노션에 옵션을 먼저 만들어야 한다.")
    return kept if isinstance(value, list) else (kept[0] if kept else "")


def _discussion_text(card: dict) -> str:
    """논의 포인트 = pros/cons(있으면) + raw_text 원문. judge 패스스루면 raw_text만."""
    parts = []
    for tag, items in (("+", card.get("pros")), ("-", card.get("cons"))):
        for it in items or []:
            parts.append(f"{tag} {it}")
    raw = card.get("candidate", {}).get("raw_text", "")
    if raw:
        parts.append(raw)
    return "\n".join(parts)


def _user_id(raw: str) -> str:
    """user://<uuid> 또는 <uuid> → uuid. TODO placeholder면 빈 문자열."""
    raw = str(raw or "")
    if not raw or raw.endswith("TODO"):
        return ""
    return raw.split("://", 1)[-1]


def _prop_value(ptype: str, value):
    """property 타입별 payload 값. 빈 값이면 None 반환(→ payload에서 생략)."""
    if ptype == "title":
        text = str(value or "").strip()
        if not text:
            return None
        return {"title": [{"text": {"content": _clip16(text)}}]}
    if ptype in ("rich_text", "text"):     # REST=rich_text, 데이터소스 스키마=text (둘 다 허용)
        text = str(value or "").strip()
        if not text:
            return None
        return {"rich_text": [{"text": {"content": _clip16(text)}}]}
    if ptype == "url":
        text = str(value or "").strip()
        if not text:
            return None
        return {"url": text}
    if ptype == "date":
        iso = _iso_date(value)
        if not iso:
            return None
        return {"date": {"start": iso}}
    if ptype == "select":
        text = str(value or "").strip()
        if not text:
            return None
        return {"select": {"name": text[:100]}}
    if ptype == "multi_select":
        vals = value if isinstance(value, list) else ([value] if value else [])
        vals = [str(v).strip()[:100] for v in vals if str(v).strip()]
        if not vals:
            return None
        return {"multi_select": [{"name": v} for v in vals]}
    if ptype == "number":
        if value is None or value == "":
            return None
        try:
            return {"number": float(value)}
        except (TypeError, ValueError):
            return None
    if ptype == "people":
        uid = str(value or "").strip()
        if not uid:
            return None
        return {"people": [{"id": uid}]}
    if ptype == "checkbox":
        return {"checkbox": bool(value)}
    return None


# ==================================================================================
#  land — 소싱 raw 를 그물망 소싱 DB(36c0ac54)에 Channel 태그로 전량 착지.
#  판단(타깃/Notes)은 별도 '판단 에이전트' 단계. 여기서는 채널별 정리까지만.
# ==================================================================================

# Candidate.source → DB Channel(select) 값
CHANNEL_LABEL = {"스레드": "Threads", "링크드인": "LinkedIn", "앱스토어": "App Store"}
# 앱스토어 차트 코드 → 노션 `Chart`(select) 표기. 어댑터의 CHART_LABEL과 값이 같아야 한다.
CHART_KO = {"top-free": "무료", "top-paid": "유료", "top-grossing": "매출", "new": "최신출시"}

# 변화 감지 PATCH에서 **제외**할 칸 — 사람이 쓰거나 '최초'를 기록하는 칸이다.
# (Status = 심사역이 바꾼 값 · Captured At/주차 = 최초 발굴 시점 · Run ID = 최초 실행)
_PATCH_EXCLUDE = {
    "Status",        # 심사역이 바꿔둔 상태('Contacted')를 'New'로 되돌리면 안 된다
    "Captured At",   # **최초** 발굴일. 오늘로 덮으면 "언제 처음 잡았나"가 소실된다
    "주차",           # 최초 발굴 주차
    "Run ID",        # 최초 발굴 회차
    # ★ Post URL — 인물 1행 전환의 함정.
    #   기존 99행은 글-1행 시절의 것이라 Post URL에 **원본 글 링크**가 들어 있다(발굴 근거).
    #   인물 행의 url은 프로필 URL이라, 갱신 때 덮으면 그 근거가 영구 소실된다.
    #   게다가 그 옛 글은 수집창(2일) 밖이라 본문에도 안 붙는다 — 그냥 사라진다.
    "Post URL",
    # ★ 팔로업 필드 (2026-07-17) — 셋 다 **전용 경로만** 쓴다. 생성/병합 payload에 실리면 안 된다:
    #   재검토 checkbox가 _build… 를 타면 falsy도 {"checkbox": false}로 직렬화돼(_prop_value)
    #   **사람이 해제해 둔 체크를 코드가 되살리거나 켜진 것을 끈다.** 켜기는 코드, 끄기는 사람.
    "Last Seen",     # land()의 병합 지점이 직접 주입
    "Last Checked",  # tools/recheck.py · backfill_bio.py 만 쓴다
    "재검토",         # land() 병합 지점 + recheck 가 켜기만 한다
}


def _iso_date(value) -> str | None:
    """ISO 날짜/시각 문자열의 앞부분만 취해 Notion date.start 로. 파싱 불가면 None."""
    s = str(value or "").strip()
    if not s:
        return None
    # 'YYYY-MM-DD...' 또는 'YYYY-MM-DDTHH:MM:SS...' 를 그대로 통과(Notion이 파싱).
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s
    return None


def _person_key(cand: dict) -> str:
    """스레드 **인물** 행의 착지 키. 핸들이 사람의 유일한 안정 ID다.

    글 URL을 키로 쓰면 같은 사람이 글을 쓸 때마다 새 행이 생긴다(=지금까지의 동작).
    핸들을 키로 쓰면 그 사람의 기존 행을 찾아 **갱신 + 본문에 글 추가**할 수 있다.
    """
    if cand.get("source") != "스레드":
        return ""
    s = cand.get("signals") or {}
    h = str(s.get("author_username") or cand.get("founder") or "").strip().lstrip("@").lower()
    return f"threads:{h}" if h else ""


def _url_key(u: str) -> str:
    """URL 착지키 — **층①(dedup)과 같은 정규화**를 통과시킨다.

    🔴 [2026-08-13 W12 · 리뷰 B-F4] 여기가 `.strip()` 하나였다. 같은 사람의 URL 5변형이
       층①에서는 1키인데 층②(착지)에서는 **5키**가 됐다(실측):

           https://www.linkedin.com/in/hong          층① in/hong
           https://linkedin.com/in/hong              층① in/hong
           http://www.linkedin.com/in/hong/          층① in/hong
           https://www.linkedin.com/in/hong?originalSubdomain=kr
           https://kr.linkedin.com/in/hong

       평소엔 층①이 먼저 걸러서 안 드러난다. 그런데 `dedup.py:31-35` 가 **변화 감지
       후보는 층①을 일부러 통과시킨다**(`profile_changed`) — 그 경로에서는 **층②가
       유일한 방어**다. 링크드인 URL 은 액터·모드에 따라 `kr.` · 쿼리 · 트레일링 슬래시가
       붙었다 떨어졌다 하므로, 변형 하나에 그 사람이 '처음 보는 사람'이 되어 **인물 행이
       새로 생긴다.** 2026-07-27 에 링크드인 345행 중 84행이 같은 사람의 두 번째 행이 된
       사고와 같은 형태인데, 그때 고친 건 층①뿐이었다.
    """
    from adapters.base import _normalize_linkedin_url, _normalize_url
    raw = (u or "").strip()
    if not raw:
        return ""
    return _normalize_linkedin_url(raw) if "linkedin.com" in raw.lower() else _normalize_url(raw)


def _landing_key(cand: dict) -> str | None:
    """착지 dedup 키 — 앱스토어=app:{id}, 스레드=threads:{핸들}(인물), 그 외=정규화 URL."""
    if cand.get("app_id"):
        return f"app:{cand['app_id']}"
    return _person_key(cand) or _url_key(cand.get("url") or "") or None


def _scan_landing_dbs(cfg: dict) -> list[str]:
    """층② dedup이 스캔할 **모든** 착지 DB(기본 + 채널별). 대상 DB가 맨 앞(pid 우선).

    🔴 [2026-07-15] 스레드·링크드인도 채널별 DB로 나눈 뒤, 층②(`existing_landing_keys`)가
       대상 DB **하나만** 보면 기본 DB에 있는 기존 635/344명이 '처음 보는 사람'이 되어 새 DB에
       **중복 재생성**된다(사용자 결정 '신규만'의 전제가 무너진다). weekly_url_keys(층①)와 똑같이
       전 착지 DB를 스캔해야 한다. base(=weekly_db_id)는 cfg2에서 db_id가 대상 DB로 바뀌어도
       weekly_db_id로 여전히 도달한다. dict.fromkeys로 대상 DB를 맨 앞에 두어 pid 충돌 시 우선.
    """
    dbs = landing_dbs(cfg)
    wid = str(cfg.get("notion", {}).get("weekly_db_id", "") or "").split("://", 1)[-1]
    if wid and not wid.endswith("TODO"):
        dbs.append(wid)
    return list(dict.fromkeys(dbs))


def _query_pages(cfg: dict, token: str, db: str | None = None):
    """소싱 DB 전 행을 페이지네이션하며 yield. db 미지정이면 cfg의 기본 DB."""
    url = f"{API}/databases/{db or _db_id(cfg)}/query"
    payload: dict = {"page_size": 100}
    while True:
        r = _write("POST", url, token, payload)
        if r.status_code != 200:
            raise RuntimeError(f"Notion query 실패 status={r.status_code} body={r.text[:300]}")
        data = r.json()
        for page in data.get("results", []):
            yield page
        if not data.get("has_more") or not data.get("next_cursor"):
            return
        payload["start_cursor"] = data["next_cursor"]


def _is_threads_row(props: dict) -> bool:
    ch = ((props.get("Channel") or {}).get("select") or {}).get("name")
    if ch == "Threads":
        return True
    return "threads." in ((props.get("Post URL") or {}).get("url") or "")


# 기존 행의 property 원본 — 갱신 시 **덮어쓰지 말고 합쳐야 하는** 값들 때문에 들고 있는다.
# (existing_landing_keys가 이미 전 행의 property를 받아오므로 추가 API 호출 0회)
_ROW_PROPS: dict[str, dict] = {}


def _existing_multi(pid: str, name: str) -> list[str]:
    props = _ROW_PROPS.get(pid) or {}
    return [o["name"] for o in ((props.get(name) or {}).get("multi_select") or [])]


def _merge_patch(pid: str, props: dict, n_posts_total: int | None) -> dict:
    """갱신 payload를 **기존 값과 합친다.**

    🔴 이걸 안 하면 갱신이 곧 파괴다:
      · `Post Count` — 이번 실행이 본 글 수(수집창 2일)로 덮어써서, 3주간 5편 쓴 사람이 1이 된다.
        인물 1행을 만든 이유("꾸준한 빌더 vs 한 번 올린 사람")가 통째로 무너진다.
        → 본문에 **실제로 쌓인 글 수**가 정답이다.
      · `Sourcing Layers`/`Matched Keyword` — 월요일 keyword, 수요일 reply로 걸린 사람이
        수요일 갱신 뒤 [reply_crawl]만 남는다. "여러 층에 걸린 사람이 강한 신호"가 실행 간에 파괴된다.
        → 합집합.
    """
    out = {k: v for k, v in props.items() if k not in _PATCH_EXCLUDE}
    for name in ("Sourcing Layers", "Matched Keyword", "Korea Signal"):
        if name not in out:
            continue
        new = [o["name"] for o in (out[name].get("multi_select") or [])]
        merged = sorted(set(new) | set(_existing_multi(pid, name)))
        out[name] = {"multi_select": [{"name": n} for n in merged]}
    if n_posts_total is not None and "Post Count" in out:
        out["Post Count"] = {"number": n_posts_total}
    return out


def existing_landing_keys(cfg: dict) -> dict[str, str]:
    """그물망 소싱 DB를 query해 {착지키: page_id} 반환.

    page_id 까지 들고 오는 이유: 기존 행을 **PATCH**해야 하기 때문(스레드 인물 행의 글 누적,
    링크드인 프로필 변화감지). 예전엔 set이라 page_id가 없어 갱신 경로 자체가 죽은 코드였다.

    ★ 스레드는 `Author Username`을 읽어 `threads:{핸들}` 키를 만든다 — **API 호출 추가 0회**
      (query가 이미 모든 property를 준다). 덕분에 **기존 글-행 99개가 그대로 인물 행이 된다** —
      마이그레이션도, 중복 정리도 필요 없다.
    """
    token = _token()
    if not token:
        print("⚠️  NOTION_TOKEN 없음 — DB 대조 생략(배치 내 중복만 제거).")
        return {}
    keys: dict[str, str] = {}
    _ROW_PROPS.clear()
    n_threads = n_rows = 0
    dup_handles: dict[str, int] = {}
    # 🔴 [2026-07-15] 대상 DB **하나**가 아니라 전 착지 DB를 스캔한다. 기존 사람이 기본 DB에
    #   있으면 그 pid를 찾아 **거기에 갱신·글누적**한다(중복 재생성 방지 + 이관 없이도 누적 유지).
    for db in _scan_landing_dbs(cfg):
        try:
            pages = list(_query_pages(cfg, token, db=db))
        except RuntimeError as e:
            # 조용히 넘기면 그 DB의 사람이 전부 '신규'가 되어 중복 재생성된다. 소리를 낸다.
            print(f"🔴 착지 대조 DB {db[:8]} query 실패 — 이 DB의 기존 행이 dedup에서 빠진다"
                  f"(중복 행 위험): {e}")
            continue
        for page in pages:
            props = page.get("properties", {})
            pid = page.get("id", "")
            n_rows += 1
            _ROW_PROPS[pid] = props
            app_id = _plain(props.get("App ID"))
            if app_id:
                keys.setdefault(f"app:{app_id}", pid)
            post_url = (props.get("Post URL") or {}).get("url")
            if post_url:
                # [W12 · B-F4] 쓰는 쪽(`_landing_key`)과 **같은 정규화**를 거쳐야 만난다.
                # 둘 중 하나만 고치면 키가 어긋나 전원이 '신규'가 된다.
                keys.setdefault(_url_key(post_url), pid)
            if _is_threads_row(props):
                h = _plain(props.get("Author Username")).strip().lstrip("@").lower()
                if h:
                    if f"threads:{h}" in keys:
                        dup_handles[h] = dup_handles.get(h, 1) + 1
                    keys.setdefault(f"threads:{h}", pid)   # 첫 행이 그 사람의 정본 행이 된다
                    n_threads += 1
    print(f"· 기존 착지 대조: {n_rows}행 (스레드 행 {n_threads}개 → 인물 "
          f"{n_threads - sum(v - 1 for v in dup_handles.values())}명)")
    if dup_handles:
        # 글-1행 시절의 잔재. 첫 행만 갱신되고 나머지는 그대로 남는다(데이터 손실은 없다).
        print(f"  · 같은 사람의 옛 글-행이 여럿인 케이스 {len(dup_handles)}명 "
              f"— 첫 행을 정본으로 쓴다. 나머지는 노션에서 수동 정리 가능(내용 손실 없음).")
    # 🔴 [2026-07-14] 이 경보는 **스레드를 착지하는 DB에서만** 의미가 있다.
    #   채널별 DB로 나눈 뒤, 앱스토어 DB에는 스레드 행이 없는 게 **정상**인데도 매번 울렸다.
    #   거짓 경보는 진짜 경보를 죽인다 — 이 스킬의 반복 실패 패턴이라 즉시 막는다.
    #   (pipeline이 cfg에 `_landing_source`를 심어 "지금 어느 채널을 착지 중인지" 알려준다.)
    src = (cfg.get("notion", {}) or {}).get("_landing_source")
    if n_rows and not n_threads and src in (None, "", "스레드"):
        # 이걸 놓치면 **매일 전원이 새 행으로 다시 생긴다**(하루 ~100행씩 중복 증식).
        print("🔴 스레드 행이 하나도 인물키로 안 잡혔다 — Channel 옵션명이나 Author Username을 확인하라. "
              "이 상태로 착지하면 같은 사람이 매일 새 행으로 쌓인다.")
    return keys


def landed_threads_handles(cfg: dict) -> set[str]:
    """이미 노션에 착지한 **스레드 작성자 핸들** 전부.

    팔로잉그래프의 "처음 보는 계정" 판정에 쓴다. 지금까지 이 판정이 워치리스트 10명만 보고 있어서,
    **이미 DB에 있는 89명을 '처음 보는 사람'으로 세고 그 사람 글을 다시 사고 있었다.**
    """
    token = _token()
    if not token:
        return set()
    out: set[str] = set()
    # 🔴 [2026-07-15] 스레드가 채널별 DB로 분리된 뒤엔 **전 착지 DB**를 봐야 한다.
    #   기본 DB만 보면 새 스레드 DB에 착지한 사람을 '처음 보는 계정'으로 세어 글을 다시 산다.
    for db in _scan_landing_dbs(cfg):
        try:
            pages = list(_query_pages(cfg, token, db=db))
        except RuntimeError:
            continue
        for page in pages:
            props = page.get("properties", {})
            if _is_threads_row(props):
                h = _plain(props.get("Author Username")).strip().lstrip("@").lower()
                if h:
                    out.add(h)
    return out


def watch_roster_handles(cfg: dict) -> list[str]:
    """워치리스트 팔로업 대상 = **사람이 고른 사람들** (Tier ∈ notion_tiers 또는 타깃 ✅).

    ★ [2026-07-17 팔로업] 워치리스트 층의 용도 재정의와 한 몸이다. 이 층은 실측 빌더 0/6이었는데,
      원인은 층이 아니라 **명단**이었다 — 발굴용(트리거 자동등록)으로 채워져 아무나 들어갔다.
      글 단위 추적은 인당 비용이 bio의 5배라(그리고 mode:user는 구조적으로 낡은 글 68%를 산다)
      "만나고 싶은 소수"에게만 경제성이 성립한다. 그 명단은 judge/사람이 이미 만들어 놓았다:
      Tier=Catch(judge)와 타깃 체크(사람). 어댑터는 그걸 **소비만** 한다(품질 판단 아님).

    조회 대상 = base + 스레드 채널 DB **둘뿐** — 앱스토어 DB엔 Tier/타깃 자체가 없어(19칸)
    필터가 400을 내고, 링크드인 DB 사람은 스레드 핸들이 없다. 실패는 조용히 넘기지 않고
    경고를 찍되 빈 목록을 반환한다(호출부가 큐레이션+자동 명단으로 계속 간다 — graceful).
    """
    token = _token()
    if not token:
        return []
    ro = ((cfg.get("channels") or {}).get("threads") or {}).get("watchlist_roster") or {}
    ors: list[dict] = [{"property": "Tier", "select": {"equals": t}}
                       for t in (ro.get("notion_tiers") or ["Catch"])]
    if ro.get("notion_target_checkbox", True):
        ors.append({"property": "타깃", "checkbox": {"equals": True}})
    if not ors:
        return []

    ncfg = cfg.get("notion") or {}
    dbs = [str(ncfg.get("db_id", "")).split("://", 1)[-1]]
    th = (ncfg.get("channel_db_ids") or {}).get("스레드")
    if th:
        dbs.append(str(th).split("://", 1)[-1])

    out: list[str] = []
    seen: set[str] = set()
    for db in dict.fromkeys(d for d in dbs if d):
        url = f"{API}/databases/{db}/query"
        payload: dict = {"filter": {"or": ors}, "page_size": 100}
        while True:
            r = _write("POST", url, token, payload)
            if r.status_code != 200:
                print(f"⚠️ watch_roster: DB {db[:8]}… 조회 실패 status={r.status_code} — 이 DB 건너뜀")
                break
            d = r.json()
            for page in d.get("results", []):
                props = page.get("properties", {})
                if not _is_threads_row(props):
                    continue                      # base DB의 링크드인/앱스토어 행 배제
                h = _plain(props.get("Author Username")).strip().lstrip("@").lower()
                if h and h not in seen:
                    seen.add(h)
                    out.append(h)
            if not d.get("has_more") or not d.get("next_cursor"):
                break
            payload["start_cursor"] = d["next_cursor"]
    return out


def _plain(prop: dict | None) -> str:
    """rich_text/title property → 첫 plain_text. 값 없으면 빈 문자열."""
    if not prop:
        return ""
    for k in ("rich_text", "title"):
        arr = prop.get(k)
        if arr:
            return arr[0].get("plain_text", "")
    return ""


def land(cfg: dict, candidates: list[dict], run_id: str) -> tuple[int, int]:
    """raw 후보를 그물망 소싱 DB에 Channel 태그로 착지. **(성공, 실패) 반환.**

    - 스키마에 존재하고 타입이 맞는 필드만 넣는다(스키마 변동에 안전).
    - App ID / Post URL 로 DB·배치 내 중복 제거(재실행해도 안 겹침).
    - Status = cfg.notion.land_status (기본 'New'). 타깃/Notes 는 판단 단계에서.

    ★ **실패 건수를 반환하는 이유**: 예전엔 int만 돌려줘서 호출부(pipeline.land)가 실패를 알 수
      없었고, 행 하나가 429로 죽어도 그대로 _commit_hooks가 돌아 seen-store에 '봤음'이 박혔다.
      = 착지 못 한 사람이 다음 실행에 다시 안 잡힌다(조용한 영구 유실).
    """
    token = _token()
    if not token:
        raise RuntimeError("NOTION_TOKEN 없음 — .env 확인. 통합을 그물망 소싱 DB에 공유했는지도 확인.")
    if not candidates:
        print("· 착지할 후보 없음")
        return 0, 0

    schema = _fetch_schema(cfg, token)
    db_id = _db_id(cfg)
    seen = existing_landing_keys(cfg)
    _assert_person_schema(schema, candidates)
    # ★ 본문 append 실패는 **행 실패와 다르다.** append는 멱등이라 다음 실행에 저절로 재시도된다
    #   (existing_post_urls가 못 붙은 글을 '없음'으로 보므로). 그런데 이걸 failed에 섞으면
    #   pipeline이 _commit_hooks를 건너뛰어 **모든 채널의 커서/seen-store가 영구 정지**한다
    #   (워치리스트가 같은 12명만 반복, 앱스토어 신규진입 기준선 동결). 따로 센다.
    # ★ 팔로업 표기 (2026-07-17): 재조우로 새 정보가 착지하는 순간을 Last Seen/재검토로 표면화.
    #   롤백 = config `followup.enabled: false` 한 줄 (코드 revert 불필요).
    fu = cfg.get("followup") or {}
    fu_on = bool(fu.get("enabled"))

    ok = skipped = patched = failed = appended = block_failed = touched = careers = 0
    # 🔴 [2026-08-13 W12] 이번 실행에서 **방금 만든** 행의 pid. `_ROW_PROPS` 미스를 두 경우로
    #    가르는 데 쓴다(아래 PATCH 직전의 스키마 가드 참조). 같은 배치에 같은 사람이 두 번
    #    나오면 두 번째는 방금 만든 행을 갱신하는데, 그 행은 스캔 시점엔 없었으므로
    #    `_ROW_PROPS` 에 없다 — 그건 "모른다"가 아니라 "타깃 DB 스키마가 정답"인 경우다.
    created_now: set[str] = set()
    for cand in candidates:
        key = _landing_key(cand)
        props = _build_landing_properties(cfg, cand, schema, run_id)
        posts = ((cand.get("signals") or {}).get("posts") or []) if _person_key(cand) else []

        if key and key in seen:
            pid = seen[key]
            new_posts: list[dict] = []
            total_posts: int | None = None
            body_ok = True

            if posts and pid:
                # ★ 인물 행 — 새 글만 본문에 덧붙인다.
                #   수집 창이 2일이라 **같은 글이 어제도 오늘도 온다.** 이미 붙은 글을 빼지 않으면
                #   본문이 매일 같은 글로 무한히 자란다. 노션 본문을 진실로 삼는다(로컬 상태 아님).
                have = existing_post_urls(token, pid)
                if have is None:
                    # 본문을 못 읽었다 → **붙이지는 않는다**(중복 폭주 방지).
                    # 하지만 프로퍼티 갱신까지 건너뛰면 안 된다 — 읽기 딸꾹질 하나로 그 사람의
                    # Posted At·Headline이 몇 주씩 낡은 채 남는다. append만 포기한다.
                    body_ok = False
                    print(f"  · 본문 조회 실패 [{cand.get('name', '?')}] — 글 추가만 건너뛴다(다음 실행에 재시도)")
                else:
                    new_posts = [p for p in posts if p.get("key") and p["key"] not in have]
                    total_posts = len(have) + len(new_posts)   # ★ 누적 글 수의 정답

            changed = (cand.get("signals") or {}).get("profile_changed")
            if body_ok and not new_posts and not changed:
                # 인물 행인데 새 글이 없거나, 링크드인인데 프로필이 안 바뀐 경우 = 진짜 중복.
                # touch_on_duplicate=true 일 때만 Last Seen 단독 갱신(재검토는 안 켠다 — 변화가 없다).
                # 기본 off: 중복 인원수만큼 PATCH가 늘어 레이트리밋(0.34s/호출)을 먹는다.
                if fu_on and fu.get("touch_on_duplicate") and "Last Seen" in (_ROW_PROPS.get(pid) or {}):
                    tr = _write("PATCH", f"{API}/pages/{pid}", token, {"properties": {
                        "Last Seen": {"date": {"start": date.today().isoformat()}}}})
                    if tr.status_code == 200:
                        touched += 1  # 실패해도 행 실패로 안 센다 — 표기는 베스트에포트, 커밋을 막지 않는다
                skipped += 1
                continue

            # ★ 갱신은 **사람이 건드린 칸을 덮지 않고, 누적 값을 합친다.**
            #   생성용 props를 그대로 PATCH하면 심사역이 바꿔둔 Status가 'New'로 되돌아가고,
            #   최초 발굴일이 오늘로 덮이고, Post Count가 이번 2일치로 붕괴한다. 그건 갱신이 아니라 파괴다.
            patch_props = _merge_patch(pid, props, total_posts)

            # ═══ 이 행이 실제로 가진 칸 ═══════════════════════════════════════════
            # 🔴 [2026-08-13 W12 · 리뷰 B-F5] 바로 아래 주석이 이 함정을 **정확히 서술해놓고
            #    4칸만 가드했다**(Last Seen·재검토·변화 요약·재검토 사유). `_merge_patch` 가
            #    만든 나머지 11칸은 그대로 나갔다.
            #
            #    왜 위험한가: `seen` 은 **전 착지 DB**를 훑어 만든다(기본 DB 포함 — 기존 635/344명이
            #    '처음 보는 사람'으로 중복 재생성되는 걸 막으려고 의도적으로 그렇게 했다).
            #    그래서 `pid` 는 **타깃과 다른 DB의 행**일 수 있다. 그런데 `props` 는 타깃 DB
            #    스키마(`_fetch_schema(cfg)`)로 만들어졌다. 실측 칸 수: base 54 · appstore 21 ·
            #    threads 41 · linkedin 37 — 링크드인용 PATCH 를 base 행에 보내면 없는 칸 5개
            #    (창업 개월·공백 개월·본업 공백·과거 스텔스·대표 콘텐츠) → **400**.
            #
            #    400 하나의 폭발 반경: failed += 1 → `pipeline.land:426` 이 `if failed: return`
            #    → `_commit_hooks` 미호출 → **앱스토어 기준선·스레드 로테이션 커서·링크드인
            #    seen-store 가 전부 미확정.** 그리고 seen-store 가 안 갱신되니 다음 실행에도 같은
            #    사람이 같은 `profile_changed` 로 올라와 **같은 400**이 난다 — 사람이 코드를
            #    고칠 때까지 안 풀리고, 그동안 매 실행이 링크드인을 재수집한다($1.6~$5.2/회).
            _rp = _ROW_PROPS.get(pid)
            if not _rp:
                if pid in created_now:
                    # 이번 실행에서 방금 만든 행이다 — 스캔 시점엔 없었을 뿐, 타깃 DB에 산다.
                    # "모른다"가 아니라 "타깃 스키마가 정답"인 경우다.
                    _rp = schema
                else:
                    # 🔴 **모른다.** 스캔이 실패한 DB의 행이거나(그 경우 위에서 이미 소리를 냈다)
                    #    예상 못 한 경로로 온 pid다. 여기서 갈림길이 셋인데 —
                    #      ⓐ 아무것도 안 보낸다 → 갱신이 **조용히 증발**한다(에러 없음, 로그는 성공,
                    #         그 행만 옛 데이터로 남는다). 실패를 삼키는 쪽이라 불변규칙 06 위반.
                    #      ⓑ 전부 보낸다 → 위의 400 연쇄. 지금까지의 동작이다.
                    #      ⓒ 모른다고 말한다 → 그 행 **하나만** 실패로 세고 시끄럽게 보고.
                    #    ⓒ 를 고른다. ⓐ·ⓑ 는 "이 상태가 얼마나 자주 일어나는지"를 영영 못 알려주는데,
                    #    ⓒ 는 며칠 로그를 보면 빈도가 나오고 그때 진짜 처방을 정할 수 있다.
                    failed += 1
                    print(f"  ✗ 갱신 보류 [{cand.get('name', '?')}] — 이 행({pid[:8]})의 실제 칸을 "
                          f"모른다(착지 DB 스캔에 없었다). 잘못된 칸을 보내면 PATCH 전체가 400 이 "
                          f"나고 그 한 건이 전 채널 커밋을 정지시킨다 — 보내지 않고 실패로 센다.")
                    continue
            row_props = _rp

            # ★ 팔로업 표기 — 이 병합이 곧 "재조우로 새 정보가 왔다"는 뜻이다(스레드 새 글·링크드인
            #   프로필 변화 둘 다 이 지점을 지난다). Last Seen=오늘, 재검토=켬.
            #   🔴 켜기만 한다 — 끄는 건 사람이다(운영 규약). 그래서 _PATCH_EXCLUDE에도 들어 있다.
            if fu_on:
                if "Last Seen" in row_props:
                    patch_props["Last Seen"] = {"date": {"start": date.today().isoformat()}}
                if "재검토" in row_props:
                    patch_props["재검토"] = {"checkbox": True}
                # ★ [2026-07-27] **왜 켰는지도 같이 남긴다.** 이유 없는 `재검토 ✅`는
                #   사람이 프로필을 다시 열어보게 만들어 자동 감지의 의미를 없앤다
                #   (실측: 오늘 감지된 8명이 전부 `변화 요약` 빈칸이었다).
                #   ⚠️ 사람이 적어둔 요약을 덮지 않는다 — **비어 있을 때만** 쓴다.
                _cs = (cand.get("signals") or {}).get("change_summary") or ""
                if not _cs:
                    # 🔴 변화는 감지됐는데 이유가 없다 = **조용히 사라짐**([[net-bugs-log]] 패턴 2).
                    #   요약은 어댑터(`_change_summary`)가 만드는 게 정본이라 여기서 다시 계산하지
                    #   않는다(로직 2벌은 어긋난다). 대신 **소리를 낸다** — 재검토 ✅만 켜지고
                    #   이유가 빈 행은 사람이 프로필을 다시 열어야 하고, 그러면 감지의 의미가 없다.
                    print(f"  🔴 변화 감지인데 change_summary가 비었다: {cand.get('name')} "
                          f"(raw가 _change_summary 도입 전이면 재수집 전까지 이 칸은 빈다)")
                if _cs:
                    # 🔴 [2026-07-28] **덮어쓰지 않고 앞에 붙인다.** 예전엔 "비어 있을 때만" 썼는데
                    #   (사람 메모 보호 목적) 그 부작용으로 **두 번째 변화가 통째로 사라졌다** —
                    #   노션 칸은 1차 그대로였고 이벤트 로그에도 안 남았다.
                    #   우리가 찾는 게 '변화'인데 그 이력이 1회성으로 끝나면 궤적을 못 본다
                    #   (`PM → Pre-founder → Founder`는 한 시점보다 훨씬 강한 신호다).
                    #   `core/followup.py`가 적어둔 원칙("이벤트는 append-only")을 land도 지킨다.
                    prev = _plain((_ROW_PROPS.get(pid) or {}).get("변화 요약"))
                    if _cs not in prev:              # 같은 raw로 재실행해도 중복 append 안 됨
                        line = f"{date.today().isoformat()[5:]} {_cs}"
                        merged = line + (" / " + prev if prev else "")
                        if "변화 요약" in row_props:
                            # ⚠️ 노션 rich_text 2,000유닛 상한 — 여기서 잘려도 **이벤트 로그가 정본**이다.
                            patch_props["변화 요약"] = {
                                "rich_text": [{"text": {"content": _clip16(merged, 1900)}}]}
                        # ★ append-only 이벤트 로그. 노션 칸이 잘려도 여기엔 전부 남는다.
                        #   공백형 축의 정밀도("몇 명이 실제로 창업했나")를 재는 **유일한 수단**이다.
                        try:
                            from core import followup as _fu
                            _fu.record("li_profile_change",
                                       _fu.key_linkedin(str(cand.get("ext_id") or "")),
                                       {"name": cand.get("name"), "summary": _cs,
                                        "from": (cand.get("signals") or {}).get("changed_from", ""),
                                        "page": pid}, by="land")
                        except Exception as e:       # 로그 실패가 착지를 막으면 안 된다
                            print(f"  · 이벤트 기록 실패(무시): {e}")
                    if "재검토 사유" in row_props:
                        patch_props["재검토 사유"] = {"select": {"name": "프로필변화"}}
            # ═══ 그 행에 실제로 있는 칸만 남긴다 ═════════════════════════════════
            # 위에서 `row_props` 를 확정했다(모르면 이미 continue 했다). 코드가 이 데이터를
            # 줄곧 손에 들고 있었는데 4칸에만 썼다 — 나머지에도 쓴다.
            _drop = sorted(set(patch_props) - set(row_props))
            if _drop:
                patch_props = {k: v for k, v in patch_props.items() if k in row_props}
                # 조용히 버리지 않는다. 이 줄이 자주 뜨면 "행이 사는 DB"와 "타깃 DB"가 갈리는
                # 빈도가 높다는 뜻이고, 그건 착지 대상 결정 자체를 다시 볼 신호다.
                print(f"  · 칸 {len(_drop)}개 제외 [{cand.get('name', '?')}] — 이 행이 사는 DB에 "
                      f"없다: {_drop}")
            if not patch_props:
                # 남는 게 없으면 PATCH 자체가 무의미하다(노션은 빈 properties 에 400 을 준다).
                skipped += 1
                print(f"  · 갱신 생략 [{cand.get('name', '?')}] — 보낼 수 있는 칸이 하나도 없다")
                continue

            r = _write("PATCH", f"{API}/pages/{pid}", token, {"properties": patch_props})
            if r.status_code != 200:
                failed += 1
                print(f"  ✗ 갱신 실패 [{cand.get('name', '?')}] status={r.status_code} {r.text[:200]}")
                continue
            patched += 1
            # ★ [2026-07-27] 경력 타임라인도 본문에 쌓는다(멱등 — 같은 내용이면 no-op).
            #   프로필이 바뀐 사람만 여기 온다 = **바뀐 경력이 날짜 도장과 함께 append**된다.
            _c = (cand.get("signals") or {}).get("career") or ""
            if _c and pid and append_career_blocks(token, pid, _c, date.today().isoformat()):
                careers += 1
            if new_posts:
                n, nf = append_post_blocks(token, pid, new_posts)
                appended += n
                block_failed += nf
            continue

        # --- 신규 생성 ---
        # 본문(children)을 생성 payload에 함께 실어 요청 1회를 아낀다.
        # ★ **블록이 아니라 글을 자른다.** 글 1개는 블록 1~2개다(본문이 비면 heading만 1개).
        #   블록을 [:100]으로 자르고 "그러니 글 50개"라고 가정하면, 본문 없는 글이 섞였을 때
        #   100블록이 60글까지 덮고 → 남은 것을 posts[50:]로 다시 붙여 **10글이 중복 append**된다.
        #   글을 먼저 자르면 블록 수는 자동으로 상한 안에 든다(글당 최대 2블록).
        head, rest = posts[:_MAX_POSTS_PER_REQ], posts[_MAX_POSTS_PER_REQ:]
        body = {"parent": {"database_id": db_id}, "properties": props}
        if head:
            body["children"] = _blocks_for_posts(head)
        r = _write("POST", f"{API}/pages", token, body)
        if r.status_code != 200:
            failed += 1
            print(f"  ✗ 착지 실패 [{cand.get('name', '?')}] status={r.status_code} {r.text[:200]}")
            continue
        ok += 1
        appended += len(head)
        # ★ 생성된 page_id를 **반드시 받아 둔다.** 예전엔 seen[key]=""로 버렸다 —
        #   그러면 처음 보는 사람의 본문은 영원히 비고, 같은 배치에 그 사람이 또 나오면 행이 또 생긴다.
        pid = r.json().get("id", "")
        if key:
            seen[key] = pid
        # [W12] 방금 만든 행이라고 표시한다 — 같은 배치에 이 사람이 또 나오면 위 갱신 경로로
        # 가는데, 그 pid 는 착지 DB 스캔 시점엔 없었으므로 `_ROW_PROPS` 에 없다. 표시가 없으면
        # 스키마 가드가 "모른다"로 보고 정상 갱신을 실패로 센다.
        if pid:
            created_now.add(pid)
        if rest and pid:
            n, nf = append_post_blocks(token, pid, rest)
            appended += n
            block_failed += nf
        # ★ 경력 타임라인 — 생성 payload에 안 싣고 따로 붙인다. 글(children)과 섞으면
        #   100블록 상한 계산이 얽히고, 실패 시 어느 쪽이 죽었는지 구분이 안 된다.
        _c = (cand.get("signals") or {}).get("career") or ""
        if _c and pid and append_career_blocks(token, pid, _c, date.today().isoformat()):
            careers += 1

    if patched:
        print(f"· 기존 행 갱신 {patched}건 (스레드=새 글 추가 · 링크드인=프로필 변화)"
              + (" — Last Seen·재검토 표기" if fu_on else ""))
    if touched:
        print(f"· 순수중복 Last Seen 갱신 {touched}건 (touch_on_duplicate)")
    if appended:
        print(f"· 페이지 본문에 글 {appended}편 누적")
    if careers:
        print(f"· 페이지 본문에 경력 타임라인 {careers}건 적재(링크드인)")
    if skipped:
        print(f"· 중복 {skipped}건 skip (새 글/변화 없음)")
    if block_failed:
        print(f"· 본문 append 실패 {block_failed}청크 — **다음 실행에 자동 재시도**된다"
              f"(멱등이므로 커밋을 막지 않는다)")
    if failed:
        print(f"🔴 착지 실패 {failed}건 — 이 사람들은 노션에 없다. seen-store를 확정하지 않는다"
              f"(다음 실행에 재수집).")
    return ok + patched, failed


def _assert_person_schema(schema: dict, candidates: list[dict]) -> None:
    """인물 행을 쓰려는데 필요한 칸이 DB에 없으면 **크게 알린다.**

    없으면 `if name not in schema: continue`가 조용히 버리고, 로그는 "✅ 700건 착지"를 찍는다.
    인물 1행 재설계의 산출물(Post Count·Sourcing Layers)이 통째로 빈칸이 되는데 아무도 모른다.
    → tools/notion_migrate.py 를 안 돌린 상태를 여기서 잡는다.
    """
    if not any((c.get("signals") or {}).get("posts") for c in candidates):
        return
    need = ["Post Count", "Sourcing Layers", "Author Username",
            # 팔로업 필드(2026-07-17) — 없으면 재조우 표기가 조용히 무시된다
            "Last Seen", "Last Checked", "재검토"]
    miss = [n for n in need if n not in schema]
    if miss:
        print(f"🔴 노션 DB에 {miss} 가 없다 — 인물 행의 핵심 칸이 **조용히 버려진다.**")
        print(f"   → ./.venv/bin/python tools/notion_migrate.py --apply 를 먼저 돌려라.")


def _build_landing_properties(cfg: dict, cand: dict, schema: dict, run_id: str) -> dict:
    """raw Candidate dict → 그물망 소싱 DB properties(채널 인지). 스키마 교집합만."""
    s = cand.get("signals", {}) or {}
    src = cand.get("source", "")
    channel = CHANNEL_LABEL.get(src, "")
    is_appstore = src == "앱스토어"
    released = cand.get("released_at", "")

    intended = [
        (_title_prop_name(schema), cand.get("name", "")),   # 서비스명 (title)
        ("Channel", channel),
        ("Post URL", cand.get("url", "")),
        ("LinkedIn URL", cand.get("linkedin_url", "")),      # [C4] go-forward dedup 키
        ("주차", _iso_week(run_id)),                          # [D1] 주간 관리(ISO 주차)
        ("Raw Text", _clip16(cand.get("raw_text", "") or "")),
        ("Captured At", run_id_date(run_id)),
        ("Status", cfg.get("notion", {}).get("land_status", "New")),
        ("Run ID", run_id),
        ("Sourcing Method", s.get("sourcing_method")),      # select — 대표 층 하나
        # ★ 인물 1행 구조에서 생긴 두 칸. 여러 층/여러 글에 걸린 사람이 강한 신호인데
        #   지금까지는 행이 흩어져서 볼 수가 없었다.
        ("Sourcing Layers", s.get("sourcing_layers") or []),  # multi_select — 걸린 층 **전부**
        ("Post Count", s.get("post_count")),                  # number — 본문에 쌓인 글 수
        ("Search Mode", s.get("search_mode")),
        ("Search Type", s.get("search_type")),
        ("Match Reason", s.get("match_reason")),
        ("API Query", s.get("query")),
        ("Matched Keyword", s.get("matched_keyword") or []),
        ("Korea Signal", s.get("korea_signal") or []),
        # 소셜(스레드/링크드인) 필드
        ("Author Display Name", s.get("author_display_name")),
        ("Author Username", s.get("author_username") or (cand.get("founder") if not is_appstore else "")),
        ("Author Headline", s.get("author_headline") or s.get("headline")),
        ("Author Verified", s.get("author_verified")),
        # 팔로워 수 — 진성 빌더(수백~수천) vs 인플루언서/마케터(수십만+)를 가르는 신호.
        ("Followers", s.get("followers")),
        ("Likes", s.get("likes") if s.get("likes") is not None else s.get("reactions")),
        ("Comments", s.get("comments")),
        ("Shares", s.get("shares")),
        ("Content Type", s.get("content_type")),
        ("Media Type", s.get("media_type")),
        ("Is Reply", s.get("is_reply")),
        ("Is Quote Post", s.get("is_quote")),
        ("Posted At", "" if is_appstore else released),
        # 앱스토어 필드
        ("App ID", cand.get("app_id")),
        ("Chart Rank", s.get("rank")),
        # ★ [2026-07-14] 어느 차트에서 왔나(무료/유료/매출/최신출시). 예전엔 Match Reason
        #   **문장 안에만** 있어서 노션에서 "매출 차트만" 같은 필터를 걸 수 없었다.
        ("Chart", CHART_KO.get(s.get("chart"), "")),
        ("Genre", _as_list(s.get("genre"))),
        ("Developer", cand.get("founder") if is_appstore else ""),
        ("Release Date", released if is_appstore else ""),
        ("Entry Type", s.get("entry_type")),
        ("Artwork", s.get("artwork")),

        # ↓ 수집은 하는데 착지에서 통째로 버려지던 값들. (스키마에 없으면 아래 루프가 알아서 건너뛴다)
        #   판단 에이전트의 입력이 될 것들이라 여기서 끊기면 그 에이전트가 볼 게 없다.
        ("Tenure Months", s.get("tenure_months")),   # ★ 극초기를 재는 유일한 하드 팩트(링크드인)
        # ★ [2026-07-27] Tenure Months의 **짝**. 하나만 보면 판단이 뒤집힌다:
        #     tenure 2 / 창업 2   → 갓 시작한 창업자        (우리가 찾는 사람)
        #     tenure 2 / 창업 84  → 7년차가 법인을 하나 더  (같은 tenure, 전혀 다른 사람)
        #   271명 실측: 창업직함 기준으로 고치니 극초기 판정이 17명 뒤집혔다(탈락 14·편입 3).
        ("창업 개월", s.get("founder_since_months")),
        # ★ 시제. **끝난** 경력의 stealth = 이미 회사를 공개했다 = 더는 스텔스가 아니다.
        #   안 가르면 공개한 사람이 stealth 축에 영원히 남는다(실측 19/271).
        ("과거 스텔스", s.get("past_stealth")),
        # ★ featured(대표 콘텐츠) — 회사가 이미 투자를 받았는지가 여기 문장으로 있을 때가 있다.
        #   payload에 회사 나이·투자단계가 **없어서** tenure로 대리 추정하던 구멍을 메운다.
        #   ⚠️ 있으면 사실, 없으면 **미확인**이다(부정이 아니다) — 그래서 체크박스가 아니라 텍스트다.
        ("대표 콘텐츠", s.get("featured")),
        # ★ 공백 개월(사수 요구사항 2026-07-24). **0과 빈칸의 뜻이 다르다** —
        #   0 = 진행 중인 일이 있다(확인됨) · 빈칸 = 경력 날짜가 없어 판정 불가(미확인).
        ("공백 개월", s.get("gap_months")),
        # ★ 짝. 공백 0 / 본업공백 6 = "파트타임 하나 걸고 6개월째 본업 없음"(EIR 등) = 신호.
        ("본업 공백", s.get("fulltime_gap_months")),
        # ★ [2026-07-27] 링크드인은 `Homepage`가 0% 사용 중이라, **회사 LinkedIn URL**을 싣는다.
        #   payload에 회사의 나이·투자단계가 없으므로 판단 에이전트가 회사를 보려면 이 URL이 필요하다.
        ("Homepage", cand.get("homepage") or s.get("company_url") or s.get("seller_url")),  # 앱스토어 651/1086 유실 중
        ("Rating", s.get("rating")),                 # 1083/1086 유실 중
        ("Review Count", s.get("review_count")),     # 811/1086 유실 중
        ("One Liner", cand.get("one_liner")),        # 1083/1086 유실 중
        ("Tier", s.get("tier")),                     # judge가 세팅하는데 읽는 곳이 0곳이었다
        ("AI Reason", s.get("ai_reason") or s.get("tier_reason")),
        # ★ 팔로잉그래프 귀속. 예전엔 Match Reason **문장 안에만** 있어서 정규식 없이는 못 쟀다
        #   → 가장 값나가는 노브(허브 계정)가 **측정 불가**였다. 이제 multi_select로 집계된다.
        ("Seed", _as_list(s.get("seed"))),
        # ★ 링크드인 검색축. `Seed`와 같은 이유로 구조화한다 — 예전엔 Match Reason 문장 안에만
        #   있어서 "새 기준(극초기 필터)으로 발굴된 사람만 보기"가 노션에서 불가능했다.
        ("Axis", _as_list(s.get("label")) if s.get("engine") == "profile_search" else []),
    ]

    props: dict = {}
    for name, value in intended:
        if name not in schema:
            continue
        value = _known_options_only(name, schema[name], value)
        formatted = _prop_value(schema[name].get("type", ""), value)
        if formatted is not None:
            props[name] = formatted
    return props


# (_OPTION_WARNED / _known_options_only 는 위(392~)에 정의돼 있다. 여기 있던 **동일한 복사본**을
#  제거했다 — 두 벌이 있으면 뒤엣것이 앞엣것을 덮어써서, 앞을 고쳐도 아무 일이 안 일어난다.)


def _as_list(value) -> list:
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def run_id_date(run_id: str) -> str:
    """Run ID('YYYY-MM-DDThhmmss...') 앞 10자를 Captured At 날짜로."""
    return run_id[:10] if run_id and len(run_id) >= 10 else ""


def _iso_week(run_id: str) -> str:
    """Run ID 날짜 → ISO 주차 라벨 'YYYY-Www' (주간 select 값). 파싱 불가면 빈 문자열."""
    from datetime import date
    s = run_id_date(run_id)
    try:
        y, w, _ = date.fromisoformat(s).isocalendar()
        return f"{y}-W{w:02d}"
    except ValueError:
        return ""
