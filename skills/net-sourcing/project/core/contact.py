"""연락처 해석 — 창업자에게 **어떻게 닿을 것인가**를 정한다.

## 왜 있나

W10(발송 자동화)의 마지막 칸이 비어 있었다. 승인 후 만들어지는 Gmail 초안이
`To: deals@example.vc`(우리 계정)로 나가서, 사람이 주소를 찾아 넣어야 보낼 수 있었다.
즉 **"승인 후 사람이 Send"가 성립하지 않았다.**

## 실측이 설계를 정했다 (2026-08-11)

수집된 400행 표본에서:

    이메일이 본문에 이미 있는 비율   스레드 2.8% · 링크드인 4.8%
    외부 URL(홈페이지 등) 이 있는 비율  스레드 9.8% · 링크드인 5.5%

**대부분은 이메일이 없다.** 극초기 창업자는 공개 이메일을 안 두는 게 정상이다.
그래서 이 모듈은 "이메일을 찾는 것"이 아니라 **"닿는 방법을 정하는 것"** 이다 —
이메일이 있으면 메일, 없으면 **링크드인/스레드 DM** 이고, DM 은 사람이 보낸다.

## 탐색 순서 (노션 「그물망 가이드라인」 원문 순서를 따른다)

가이드라인이 사람에게 지시한 순서: *"구글링 → 대표 개인 메일 → 링크드인 → 앱스토어
개인정보 처리방침"*. 이 중 **구글링은 자동화하지 않는다** — `session.py` 의
`_assert_safe_tools` 가 웹검색 계열을 구조적으로 거부하고(ADR 0003), 검색 결과로 이메일을
고르는 것은 오탐 시 **엉뚱한 사람에게 메일이 가는** 비가역 사고다. 사람이 판단할 일이다.

우리가 자동으로 하는 것은 **이미 공개된 곳에 적혀 있는 것을 읽는 것**뿐이다:

    ① 수집된 원문(bio·headline·글)에 적힌 이메일
    ② 본인이 프로필에 걸어둔 홈페이지 → 그 페이지와 /contact·/about 의 이메일
    ③ 없으면 → DM (링크드인 URL·스레드 핸들은 100% 있다)

## 규율

- **추측하지 않는다.** 회사 도메인에서 `contact@`·`hello@` 를 **만들어내지 않는다**.
  적혀 있는 것만 쓴다. 만들어낸 주소로 보내면 되돌릴 수 없다.
- **역할 주소를 개인으로 착각하지 않는다** — `noreply@`·`support@` 등은 거른다.
- 크롤은 공개 페이지 GET 뿐이고 짧은 타임아웃·크기 상한을 둔다. 실패는 조용히 넘기되
  (연락처를 못 찾는 건 정상 결과다) 무엇을 시도했는지는 남긴다.

## 적대 리뷰가 바꾼 것 (2026-08-11)

리뷰가 실제 프로브로 뚫은 것들이다. 전부 **엉뚱한 사람에게 메일이 가는** 축이었다:

    mailto 를 맨 앞에 뒀더니   푸터의 제작 에이전시 `person-094ed0b9b649@example.com` 가 1순위였다
    도메인을 안 봤더니        `acme.io` 창업자를 찾다가 `webagency.co.kr` 을 채택했다
    HTML 전체를 훑었더니       Sentry DSN·GA 스크립트·JSON-LD 기자 메일·댓글이 후보가 됐다
    mailto 를 검증 안 했더니   `mailto:a@x.com,person-48232851fed9@example.com` 통째로 "창업자 메일"이 됐다

그래서 규율을 하나 더한다 — **모호하면 사람에게 넘긴다.** 후보가 여럿이거나 사이트
도메인과 안 맞으면 조용히 최선치를 고르지 않고 `channel="ambiguous"` 로 떨어뜨린다.
이 모듈에서 "가장 그럴듯한 것"을 고르는 것은 비가역 사고의 다른 이름이다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# 개인이 아닌 역할 주소 — 콜드메일 수신자로 부적절하다(대표에게 닿지 않는다).
# [적대리뷰 M1] 로컬파트를 **정규화한 뒤** 토큰으로 본다. `^(no-reply)@` 식 앵커는
# `no_reply`·`do-not-reply`·`support-team` 에 안 걸렸다 — 구분자만 바꾸면 뚫리는 필터였다.
# `contact`·`hello` 는 목록에 아예 없었다(이 모듈이 "만들어내지 않는다"고 못박은 바로 그 둘).
_ROLE_WORDS = frozenset((
    "noreply", "donotreply", "support", "help", "info", "admin", "webmaster", "postmaster",
    "abuse", "privacy", "legal", "press", "sales", "marketing", "billing", "careers",
    "recruit", "hr", "contact", "hello", "team", "office", "ask", "partnership", "pr",
    "ir", "cs", "service", "mail", "master", "official", "welcome", "inquiry", "biz"))


def _is_role(addr: str) -> bool:
    """역할 주소인가. **토큰 단위**로 본다.

    `support-team` 을 통째로 정규화하면 `supportteam` 이 되어 목록에 없다 — 구분자로
    단어를 쪼갠 뒤 각 토큰을 대조해야 `support-team`·`cs.center`·`info_kr` 이 다 걸린다.
    붙여 쓴 형태(`noreply`)를 위해 정규화형도 함께 본다.
    """
    local = addr.split("@", 1)[0].lower()
    norm = re.sub(r"[^a-z]", "", local)                  # no_reply · do-not-reply → noreply
    if norm in _ROLE_WORDS or norm.startswith(("noreply", "donotreply")):
        return True
    return any(tok in _ROLE_WORDS for tok in re.split(r"[^a-z]+", local) if tok)


# 이미지·에셋 파일명이 이메일처럼 잡히는 것 방지
_NOT_EMAIL = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js|ico|woff2?|ttf|mp4)$", re.I)
# [적대리뷰 H2] 스크립트·스타일·주석 구간은 사람이 읽는 연락처가 아니다.
# Sentry DSN(`https://<key>@o44.ingest.sentry.io/1`)·GA 문자열이 여기서 나온다.
_NOISE = re.compile(r"<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->", re.I | re.S)

_SKIP_HOSTS = ("linkedin.com", "threads.com", "threads.net", "instagram.com",
               "facebook.com", "twitter.com", "x.com", "youtube.com", "apps.apple.com",
               "play.google.com", "notion.so", "notion.site")

_MAX_BYTES = 400_000
_TIMEOUT = 8


@dataclass
class Contact:
    """어떻게 닿을 것인가. `channel` 이 이 객체의 결론이다."""
    channel: str                     # "email"|"linkedin_dm"|"threads_dm"|"ambiguous"|"none"
    email: str | None = None
    handle: str | None = None        # DM 대상 (링크드인 URL · 스레드 핸들)
    source: str = ""                 # 어디서 찾았나 — 사람이 검증할 수 있게
    tried: list[str] = field(default_factory=list)

    @property
    def sendable(self) -> bool:
        """메일로 보낼 수 있나. False 면 사람이 DM 으로 가야 한다."""
        return self.channel == "email" and bool(self.email)


def _clean(cands) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()                     # [적대리뷰 L1] 중복 판정은 정규화 키로
    for e in cands:
        e = str(e or "").strip().strip(".,;:)»\"'<>")
        if not _EMAIL.fullmatch(e):            # [적대리뷰 M6] mailto 캡처분도 예외 없이 검증
            continue
        if _NOT_EMAIL.search(e) or _is_role(e):
            continue
        if e.lower() in seen:
            continue
        seen.add(e.lower())
        out.append(e)
    return out


def _reg_domain(host: str) -> str:
    """등록 도메인 근사 — `www.acme.co.kr` → `acme.co.kr`, `mail.acme.io` → `acme.io`.

    공개 접미사 목록을 들이지 않고 2단계(+ 흔한 2단 국가 접미사)로 자른다. 이 함수의
    쓰임은 **차단이 아니라 대조**라, 과하게 잘려도 안전한 쪽(불일치 → 사람에게)으로 틀린다.
    """
    parts = [x for x in str(host or "").lower().split(".") if x]
    if len(parts) < 2:
        return ".".join(parts)
    if len(parts) >= 3 and parts[-2] in ("co", "or", "ne", "go", "ac", "com", "net", "org"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def from_text(*texts: str | None) -> list[str]:
    """수집된 원문에 **적혀 있는** 이메일. 만들어내지 않는다."""
    found: list[str] = []
    for t in texts:
        if t:
            found += _EMAIL.findall(str(t))
    return _clean(found)


def _public_host(url: str) -> str | None:
    """요청해도 되는 호스트인가. 아니면 None.

    [적대리뷰 M3] `homepage` 는 **외부인이 프로필에 심는 값**이다. 프로브가 보여준 것:
    `http://127.0.0.1:8899/internal` 이 그대로 요청됐고 로컬 서비스 응답에서 이메일을 긁었다.
    `http://user:pass@127.0.0.1/` 과 `http://169.254.169.254/`(클라우드 메타데이터)도 통과했다.
    그래서 **스킴을 강제하고 사설·루프백·링크로컬 IP 로 해석되면 거부**한다.
    """
    import ipaddress
    import socket
    try:
        u = urlparse(url)
    except ValueError:
        return None
    if u.scheme not in ("http", "https"):
        return None
    try:
        host = (u.hostname or "").lower()
    except ValueError:
        return None
    if not host or any(h in host for h in _SKIP_HOSTS):
        return None
    try:
        infos = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError, ValueError):
        return None
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return None
        if not ip.is_global:                   # 사설·루프백·링크로컬·예약
            return None
    return host


def _fetch(url: str, *, deadline: float | None = None) -> str:
    """공개 페이지 GET. 적대적인 서버를 전제한다.

    [적대리뷰 M4] 예전 `r.text[:_MAX_BYTES]` 는 **전량 수신 후** 잘랐다 — 80MB 응답에
    RSS +190MB. `timeout=8` 도 소켓 무응답 간격이라 5초마다 4바이트 흘리는 서버에 100초를
    잡혔다. 그동안 `run_pipeline` 은 claude 자원 락을 쥐고 있어 judge·sourcing 이 굶는다.
    그래서 **스트리밍 + 누적 상한 + 총 시간 데드라인**으로 바꾼다.

    [적대리뷰 M3] 리다이렉트는 추종하지 않는다 — 겉보기 외부 도메인이 302 로 내부를
    가리키면 `_public_host` 검사가 최초 URL 에만 걸려 무력화된다.
    """
    import time

    import requests
    r = requests.get(url, timeout=_TIMEOUT, stream=True, allow_redirects=False,
                     headers={"User-Agent": "Mozilla/5.0 (compatible; kv-sourcing/1.0)"})
    with r:
        if r.status_code != 200:
            return ""
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype and not (ctype.startswith("text/") or ctype == "application/xhtml+xml"):
            return ""                          # HTML 이 아니면 읽을 이유가 없다
        buf = bytearray()
        for chunk in r.iter_content(8192):
            buf += chunk
            if len(buf) >= _MAX_BYTES:
                break
            if deadline and time.monotonic() > deadline:
                break
        enc = r.encoding or r.apparent_encoding or "utf-8"
        return bytes(buf).decode(enc, errors="replace")


def _mailto(html: str) -> list[str]:
    """`mailto:` 링크의 주소들. [적대리뷰 M6] 다중 수신자·퍼센트 인코딩을 푼다.

    `mailto:a@x.com,person-48232851fed9@example.com` 을 통째로 채택하면 심사역이 그걸 To 에 붙여넣는 순간
    **의도치 않은 제2 수신자에게 콜드메일이 동시 발송**된다. 비가역이다.
    """
    from urllib.parse import unquote
    out: list[str] = []
    for raw in re.findall(r'mailto:([^"\'>\s]+)', html, re.I):
        addr = unquote(raw.split("?", 1)[0])
        out += re.split(r"[,;]", addr)
    return out


def from_site(url: str | None, *, extra_paths=("/contact", "/about", "/contact-us"),
              budget_sec: float = 20.0) -> tuple[list[str], list[str]]:
    """본인이 걸어둔 홈페이지에서 공개 이메일을 읽는다. 반환: (이메일들, 시도한 URL들).

    소셜·앱스토어 링크는 건너뛴다 — 거기엔 연락처가 없고 우리가 이미 가진 정보다.
    후보 하나에 쓰는 **총 시간**은 `budget_sec` 로 묶는다(경로 4개 × 느린 서버 = 락 점유).
    """
    import time
    tried: list[str] = []
    if not url:
        return [], tried
    if not _public_host(str(url)):
        return [], tried

    deadline = time.monotonic() + budget_sec
    u = urlparse(str(url))
    site_dom = _reg_domain(u.hostname or "")
    found: list[str] = []
    # [적대리뷰 L5] `https://x.com/blog` 에서 `/contact` 는 **루트 기준**이어야 한다 —
    # 예전엔 `/blog/contact` 를 두드리고 진짜 연락처 페이지를 못 봤다.
    root = f"{u.scheme}://{u.netloc}/"
    for path in ("",) + tuple(extra_paths):
        if time.monotonic() > deadline:
            break
        target = str(url) if not path else urljoin(root, path.lstrip("/"))
        if target in tried:
            continue
        tried.append(target)
        try:
            html = _fetch(target, deadline=deadline)
        except Exception:                         # noqa: BLE001  못 읽는 건 정상 결과다
            continue
        if not html:
            continue
        html = _NOISE.sub(" ", html)              # 스크립트·스타일·주석 제거 후 매칭
        found += _mailto(html) + _EMAIL.findall(html)
        if _clean(found):
            break                                 # 하나 찾으면 더 두드리지 않는다

    # [적대리뷰 H2] **사이트 도메인과 일치하는 주소만** 채택한다. 창업자를 찾는데 푸터의
    # 제작 에이전시(`person-094ed0b9b649@example.com`)를 고르는 것이 정확히 이 검사로 막힌다.
    return [e for e in _clean(found)
            if _reg_domain(e.split("@", 1)[1]) == site_dom], tried


def resolve(row: dict, *, crawl: bool = True) -> Contact:
    """행 하나 → 연락 방법. **읽기만 한다.**

    `row` 는 judge 큐/노션 행에서 온 dict 로, 다음 키를 (있으면) 본다:
        raw_text · headline · career · one_liner · bio      본문
        homepage · site · website                           링크
        linkedin_url · handle · author_username             DM 대상
    """
    tried: list[str] = []

    # ① 수집된 원문에 적혀 있는가
    emails = from_text(row.get("raw_text"), row.get("headline"), row.get("career"),
                       row.get("one_liner"), row.get("bio"), row.get("대표 콘텐츠"))
    if len(emails) == 1:
        return Contact("email", emails[0], source="수집 원문", tried=["원문"])
    if len(emails) > 1:
        # [적대리뷰 H2] 후보가 여럿이면 고르지 않는다 — 어느 쪽이 창업자인지 모른다.
        return Contact("ambiguous", source=f"이메일 후보 {len(emails)}건: "
                       + ", ".join(emails[:3]), tried=["원문"])

    # ② 본인이 걸어둔 홈페이지
    if crawl:
        # [적대리뷰 L2] `url` 은 **Post URL**(게시글 링크)이라 홈페이지가 아니다 — 뺐다.
        for key in ("homepage", "Homepage", "site", "website"):
            site = row.get(key)
            if not site or not isinstance(site, str):
                continue
            emails, t = from_site(site)
            tried += t
            if len(emails) == 1:
                return Contact("email", emails[0], source=f"홈페이지({key})", tried=tried)
            if len(emails) > 1:
                return Contact("ambiguous", source=f"홈페이지({key}) 후보 {len(emails)}건: "
                               + ", ".join(emails[:3]), tried=tried)

    # ③ 못 찾으면 DM — 실패가 아니라 **정상 결과**다(극초기 창업자는 공개 이메일이 드물다)
    li = row.get("linkedin_url") or row.get("LinkedIn URL") or ""
    if li:
        return Contact("linkedin_dm", handle=str(li), source="이메일 없음 → 링크드인 DM", tried=tried)
    th = row.get("author_username") or row.get("Author Username") or row.get("handle") or ""
    if th:
        return Contact("threads_dm", handle=str(th), source="이메일 없음 → 스레드 DM", tried=tried)
    return Contact("none", source="연락 수단 없음", tried=tried)
