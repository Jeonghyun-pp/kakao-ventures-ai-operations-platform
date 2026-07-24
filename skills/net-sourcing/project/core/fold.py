"""글-후보 → **인물-후보** 접기. 스레드 전용(다른 채널은 그대로 통과).

## 왜 필요한가
스레드는 지금까지 **글 1행**으로 착지했다. 한 사람이 글 3개를 쓰면 노션에 3행이 생기고,
다음 주에 또 쓰면 또 생긴다. 그래서
  · "이번 주 몇 명 발굴했나"를 **셀 수가 없다** (행 수 ≠ 사람 수)
  · 같은 사람이 keyword 층과 reply 층에 **둘 다** 걸려도 두 행으로 흩어져 아무도 못 본다
    — 여러 층에 동시에 걸린 사람이야말로 가장 강한 신호인데.
  · 꾸준히 만드는 사람과 한 번 올리고 마는 사람이 구분이 안 된다.

## 접는 위치가 왜 judge **뒤**인가 (중요)
judge의 L0 정규식(`_THIRD`/`_FIRST`/`_OWN_LINK`)과 LLM 홍보 분류기는 **글 하나**를 보고 만들어졌다.
사람을 먼저 접어서 judge에 넘기면, 최신 글이 우연히 홍보 리셰어인 사람은 **이틀 전 런칭 글까지
통째로** 죽는다. 글 단위로 판단하고, 살아남은 글만 사람으로 접는다.
(raw 파일·cut 로그·_health()도 전부 글 단위로 남아 디버깅이 유지된다)
"""
from __future__ import annotations

from adapters.base import norm_post_url

# 대표 층(select `Sourcing Method` 한 칸) 우선순위. 발굴력이 강한 순.
# 여러 층에 걸린 사람은 `Sourcing Layers`(multi_select)에 전부 기록되고, 이 select엔 대표 하나만 간다.
_LAYER_RANK = ["keyword_search", "reply_crawl", "following_graph", "watchlist"]

# 🔴 층 이름이 두 벌 있다. **노션 옵션과 맞는 건 sourcing_method 쪽이다.**
#      signals.layer            = keyword | watchlist | following_graph | replies
#      signals.sourcing_method  = keyword_search | watchlist | following_graph | reply_crawl  ← 정본
#   layer를 그대로 쓰면 'keyword'·'replies'가 노션 옵션에 없어서 `_known_options_only`가
#   **조용히 버린다** — 착지는 성공하고 Sourcing Layers만 전부 빈칸이 된다. 이 스킬의 단골 실패다.
_LAYER_ALIAS = {"keyword": "keyword_search", "replies": "reply_crawl"}


def _layer_of(sig: dict) -> str:
    """이 후보가 어느 층에서 왔는가 — 노션 옵션 어휘로 정규화해서 돌려준다."""
    v = sig.get("sourcing_method") or sig.get("layer") or ""
    return _LAYER_ALIAS.get(v, v)


def _handle(c: dict) -> str:
    s = c.get("signals") or {}
    h = s.get("author_username") or c.get("founder") or ""
    return str(h).strip().lstrip("@").lower()


def _is_post(url: str) -> bool:
    """이 후보가 **본문에 붙일 실제 글**인가.

    스레드 글 URL은 `/@handle/post/{id}` 꼴이다. 프로필 URL(`/@handle`)은 글이 아니다
    (팔로잉그래프 stub — "발견했지만 최근 글 없음").

    ⚠️ **URL이 없으면 글로 치지 않는다.** URL이 중복 판정의 유일한 키이기 때문이다
       (노션 본문 블록의 링크와 대조한다). 키 없는 글을 붙이면 다음 실행에 "이미 붙었나"를
       알 수 없어 **매일 다시 붙고 본문이 무한히 자란다.** 본문 텍스트는 Raw Text 칸에 남는다.
    """
    return "/post/" in (url or "")


def by_author(cands: list[dict]) -> list[dict]:
    """스레드 후보를 작성자별 인물-후보 1건으로 접는다. 나머지 채널은 손대지 않는다."""
    threads = [c for c in cands if c.get("source") == "스레드"]
    others = [c for c in cands if c.get("source") != "스레드"]
    if not threads:
        return cands

    # 핸들 없는 후보는 버린다 — 안 버리면 전부 빈 키로 뭉쳐 **한 행에 몰린다**.
    orphan = [c for c in threads if not _handle(c)]
    if orphan:
        print(f"🔴 fold: 작성자를 못 뽑은 스레드 후보 {len(orphan)}건 폐기 "
              f"— 액터 출력 스키마가 바뀌었을 수 있다(_post_to_candidate 폴백 확인)")
    threads = [c for c in threads if _handle(c)]

    # 본문은 있는데 글 URL이 없는 후보 = 액터 출력 스키마가 어긋난 것. 조용히 넘기지 않는다.
    lost = [c for c in threads
            if (c.get("raw_text") or "").strip() and not _is_post((c.get("url") or ""))
            and "/@" in (c.get("url") or "") and "/post/" not in (c.get("url") or "")
            and (c.get("signals") or {}).get("layer") != "following_graph"]
    if lost:
        print(f"🔴 fold: 본문은 있는데 글 URL이 없는 후보 {len(lost)}건 — 본문에 못 붙인다"
              f"(중복 판정 키가 URL이라서). 액터의 URL 필드명을 _post_to_candidate에서 확인하라.")

    people: dict[str, dict] = {}
    for c in threads:
        h = _handle(c)
        p = people.setdefault(h, {"posts": [], "cands": []})
        p["cands"].append(c)
        url = (c.get("url") or "").strip()
        # ★ 글이 **아닌** 후보는 posts에 넣지 않는다.
        #   팔로잉그래프의 person_stub은 "발견했지만 최근 글이 없는 사람"이라 url이 **프로필 URL**이다.
        #   이걸 글로 세면 post_count=1이 되고(거짓), 본문에 프로필 링크만 달린 **유령 블록**이 붙는다.
        #   post_count=0 = "발견했지만 아직 아무 말도 안 한 사람" — 그 자체가 의미 있는 정보다.
        if not _is_post(url):
            continue
        p["posts"].append({
            "url": url,
            "key": norm_post_url(url),
            "text": c.get("raw_text") or "",
            "posted_at": c.get("released_at") or "",
            "layer": _layer_of(c.get("signals") or {}),
            "query": (c.get("signals") or {}).get("query") or "",
        })

    out: list[dict] = []
    for h, p in people.items():
        # 같은 글이 여러 쿼리에 잡혀 중복으로 들어올 수 있다(burbn 실측: 247건 중 141건이 중복).
        seen, posts = set(), []
        for post in p["posts"]:
            k = post["key"]
            if k and k in seen:
                continue
            if k:
                seen.add(k)
            posts.append(post)
        posts.sort(key=lambda x: x.get("posted_at") or "")       # 시간순(오래된 것 먼저)
        newest = posts[-1] if posts else {}

        sigs = [(c.get("signals") or {}) for c in p["cands"]]
        layers = sorted({_layer_of(s) for s in sigs} - {""})
        keywords = sorted({k for s in sigs for k in (s.get("matched_keyword") or []) if k})
        primary = next((l for l in _LAYER_RANK if l in layers), (layers[0] if layers else ""))

        base = p["cands"][0]
        bsig = base.get("signals") or {}
        # 최신 글을 쓴 후보의 신호(좋아요/댓글 등)를 대표값으로. 글이 없으면(stub) 첫 후보.
        top = max(p["cands"], key=lambda c: (c.get("released_at") or "")) if p["cands"] else base
        tsig = top.get("signals") or {}

        # 🔴 [2026-07-13] 예전엔 `dict(bsig)` — **첫 후보의 신호만** 가져왔다. 그게 틀렸다:
        #   한 사람의 글이 여러 액터에서 온다. 키워드 액터(igview)는 **bio를 안 준다**.
        #   meta 액터(워치리스트·팔로잉글)는 **준다**. 첫 글이 키워드에서 왔으면, 나중 글에
        #   붙어온 bio를 **버렸다** — 이미 돈 주고 산 데이터를.
        #   결과: `Author Headline`이 635행 중 **1행**만 찼다. 그 1행의 bio는 "동탄 산도 전문점"이었다
        #   — bio 한 줄이면 그 빵집을 즉시 걸러낼 수 있었다. 사람이 "뭘 하는 곳인지 모르겠다"고 한
        #   이유가 이것이다. → **아무 후보한테서든 값이 있으면 살린다.**
        def _first(key: str):
            for s in sigs:
                v = s.get(key)
                if v not in (None, "", [], {}):
                    return v
            return None

        merged = dict(bsig)
        for k in ("author_headline", "followers", "is_verified", "author_display_name"):
            v = _first(k)
            if v is not None:
                merged[k] = v

        merged.update({
            "layer": primary,
            "sourcing_method": primary,          # select 한 칸 — 대표 층
            "sourcing_layers": layers,           # multi_select — 걸린 층 **전부**
            "matched_keyword": keywords,
            "post_count": len(posts),
            "posts": posts,                      # ← 페이지 본문에 붙일 원본
            "author_username": h,
            # ⚠️ 위 _first 로 살려둔 값을 여기서 빈 문자열로 덮어쓰면 안 된다(그 버그를 방금 고쳤다).
            "author_display_name": (tsig.get("author_display_name")
                                    or merged.get("author_display_name") or ""),
            "likes": tsig.get("likes"),
            "comments": tsig.get("comments"),
            "shares": tsig.get("shares"),
            "is_reply": tsig.get("is_reply"),
            "is_quote": tsig.get("is_quote"),
            "content_type": tsig.get("content_type"),
            "media_type": tsig.get("media_type"),
            "match_reason": " / ".join(dict.fromkeys(
                s.get("match_reason") for s in sigs if s.get("match_reason")))[:2000],
            "query": tsig.get("query") or "",
        })

        out.append({
            "source": "스레드",
            "name": h,
            # ★ 글 URL이 아니라 **프로필 URL** — 사람당 불변이라 착지 키로 쓸 수 있다.
            "url": f"https://www.threads.com/@{h}",
            "one_liner": (top.get("one_liner") or ""),
            "founder": h,
            "released_at": newest.get("posted_at") or "",        # = 마지막 활동일(Posted At)
            "raw_text": (newest.get("text") or "")[:2000],
            "app_id": None,
            "ext_id": None,
            "linkedin_url": "",
            "homepage": "",
            "adapter": base.get("adapter") or "threads",
            "signals": merged,
        })

    print(f"· fold: 스레드 글 {len(threads)}건 → **인물 {len(out)}명** "
          f"(글 여러 개 쓴 사람은 1행에 누적)")
    return others + out
