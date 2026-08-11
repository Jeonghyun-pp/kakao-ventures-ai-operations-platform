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
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# 개인이 아닌 역할 주소 — 콜드메일 수신자로 부적절하다(대표에게 닿지 않는다).
_ROLE = re.compile(r"^(noreply|no-reply|donotreply|support|help|info|admin|webmaster|"
                   r"postmaster|abuse|privacy|legal|press|sales|marketing|billing|"
                   r"careers|recruit|hr)@", re.I)
# 이미지·에셋 파일명이 이메일처럼 잡히는 것 방지
_NOT_EMAIL = re.compile(r"\.(png|jpe?g|gif|svg|webp|css|js)$", re.I)

_SKIP_HOSTS = ("linkedin.com", "threads.com", "threads.net", "instagram.com",
               "facebook.com", "twitter.com", "x.com", "youtube.com", "apps.apple.com",
               "play.google.com", "notion.so", "notion.site")

_MAX_BYTES = 400_000
_TIMEOUT = 8


@dataclass
class Contact:
    """어떻게 닿을 것인가. `channel` 이 이 객체의 결론이다."""
    channel: str                     # "email" | "linkedin_dm" | "threads_dm" | "none"
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
    for e in cands:
        e = e.strip().strip(".,;:)»\"'")
        if _NOT_EMAIL.search(e) or _ROLE.match(e):
            continue
        if e.lower() in out:
            continue
        out.append(e)
    return out


def from_text(*texts: str | None) -> list[str]:
    """수집된 원문에 **적혀 있는** 이메일. 만들어내지 않는다."""
    found: list[str] = []
    for t in texts:
        if t:
            found += _EMAIL.findall(str(t))
    return _clean(found)


def _fetch(url: str) -> str:
    import requests
    r = requests.get(url, timeout=_TIMEOUT,
                     headers={"User-Agent": "Mozilla/5.0 (compatible; kv-sourcing/1.0)"})
    if r.status_code != 200:
        return ""
    return r.text[:_MAX_BYTES]


def from_site(url: str | None, *, extra_paths=("/contact", "/about", "/contact-us")) -> tuple[list[str], list[str]]:
    """본인이 걸어둔 홈페이지에서 공개 이메일을 읽는다. 반환: (이메일들, 시도한 URL들).

    소셜·앱스토어 링크는 건너뛴다 — 거기엔 연락처가 없고 우리가 이미 가진 정보다.
    """
    tried: list[str] = []
    if not url:
        return [], tried
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return [], tried
    if not host or any(h in host for h in _SKIP_HOSTS):
        return [], tried

    found: list[str] = []
    for path in ("",) + tuple(extra_paths):
        target = urljoin(url if url.endswith("/") else url + "/", path.lstrip("/")) if path else url
        tried.append(target)
        try:
            html = _fetch(target)
        except Exception:                         # noqa: BLE001  못 읽는 건 정상 결과다
            continue
        if not html:
            continue
        found += _EMAIL.findall(html)
        # mailto: 는 본인이 명시적으로 노출한 것이라 더 신뢰할 만하다 — 앞에 둔다
        found = re.findall(r'mailto:([^"\'>?\s]+)', html) + found
        if _clean(found):
            break                                 # 하나 찾으면 더 두드리지 않는다
    return _clean(found), tried


def resolve(row: dict, *, crawl: bool = True) -> Contact:
    """행 하나 → 연락 방법. **읽기만 한다.**

    `row` 는 judge 큐/노션 행에서 온 dict 로, 다음 키를 (있으면) 본다:
        raw_text · headline · career · one_liner · bio      본문
        homepage · site · url · post_url                    링크
        linkedin_url · handle · author_username             DM 대상
    """
    tried: list[str] = []

    # ① 수집된 원문에 적혀 있는가
    emails = from_text(row.get("raw_text"), row.get("headline"), row.get("career"),
                       row.get("one_liner"), row.get("bio"), row.get("대표 콘텐츠"))
    if emails:
        return Contact("email", emails[0], source="수집 원문", tried=["원문"])

    # ② 본인이 걸어둔 홈페이지
    if crawl:
        for key in ("homepage", "Homepage", "site", "website", "url"):
            site = row.get(key)
            if not site:
                continue
            emails, t = from_site(str(site))
            tried += t
            if emails:
                return Contact("email", emails[0], source=f"홈페이지({key})", tried=tried)

    # ③ 못 찾으면 DM — 실패가 아니라 **정상 결과**다(극초기 창업자는 공개 이메일이 드물다)
    li = row.get("linkedin_url") or row.get("LinkedIn URL") or ""
    if li:
        return Contact("linkedin_dm", handle=str(li), source="이메일 없음 → 링크드인 DM", tried=tried)
    th = row.get("author_username") or row.get("Author Username") or row.get("handle") or ""
    if th:
        return Contact("threads_dm", handle=str(th), source="이메일 없음 → 스레드 DM", tried=tried)
    return Contact("none", source="연락 수단 없음", tried=tried)
