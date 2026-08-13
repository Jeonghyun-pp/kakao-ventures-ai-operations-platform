"""공통 Candidate 스키마 + Adapter 인터페이스.

모든 채널 어댑터는 collect() -> list[Candidate] 를 구현한다. 이후 dedup/judge/notion은
채널을 모른 채 Candidate만 다룬다 (어댑터 ↔ 코어 분리의 핵심).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Candidate:
    """한 개 소싱 후보(정규화된 raw). 수집 단계 산출물 — 아직 판단 전."""
    source: str                              # "앱스토어" | "스레드" | "링크드인" (극초기 DB '발굴' 값과 일치)
    name: str                                # 서비스/법인명 (판단 후 법인명으로 정규화)
    url: str                                 # 대표 링크 (앱스토어/스레드 글/링크드인 프로필)
    one_liner: str = ""                      # 한 줄 설명 (초안, 판단에서 보강)
    founder: str = ""                         # 대표/핸들
    released_at: str = ""                     # 최초 출시일(앱) / 게시일(스레드) / "" (링크드인) — ISO
    raw_text: str = ""                        # 설명·리뷰·글 원문 (판단 에이전트 입력)
    app_id: Optional[str] = None              # 앱스토어 전용 (정확 중복매칭 키)
    ext_id: Optional[str] = None              # 채널 안정 ID (링크드인 URN 등). vanity URL 금지 — URN 사용
    linkedin_url: str = ""                    # [C4] 링크드인 프로필 URL — go-forward dedup 핵심 키(주간 DB 대조)
    homepage: str = ""                        # 회사 홈페이지/서비스 URL — URL 보조 dedup
    adapter: str = ""                         # [C9] provenance 서브소스: "linkedin.harvestapi" | "linkedin.listly" | 채널명
    signals: dict = field(default_factory=dict)  # rank/reviews/rating/followers/chart_new_entry ...

    def dedup_key(self) -> str:
        """대표 중복 판정 키(하위호환). 안정 ID(ext_id/URN)>app_id>정규화 이름 순."""
        if self.ext_id:
            return f"ext:{self.ext_id}"
        if self.app_id:
            return f"app:{self.app_id}"
        return "name:" + _normalize(self.name)

    def match_keys(self) -> list[str]:
        """[2단 dedup] 이 후보가 '기존'과 겹치는지 볼 때 쓰는 후보 키 전부.
        하나라도 기존 집합에 있으면 중복. URL 키(li:/home:)는 정확매칭(go-forward),
        name: 키는 퍼지(레거시 DB 대조·소프트).

        ⚠️ **실제로 도는 건 core/dedup._keys 다**(dict 후보를 받는다). 이 메서드는 그 규칙의
           원본 문서다 — **둘을 반드시 같이 고쳐라.** 한쪽만 고치면 다음 사람이 이걸 읽고
           틀린 규칙을 복원한다.
        """
        keys: list[str] = []
        if self.ext_id:
            # ★ [2026-07-27] 링크드인 URN은 모드마다 달라진다 → 정규화해서 키를 만든다.
            #   안 하면 같은 사람이 `ext:ACoAA…`와 `ext:ACwAA…` 두 키로 갈려 dedup을 통과한다.
            keys.append("ext:" + (canonical_linkedin_urn(self.ext_id) or self.ext_id))
        if self.linkedin_url:
            keys.append("li:" + _normalize_linkedin_url(self.linkedin_url))
        if self.app_id:
            keys.append(f"app:{self.app_id}")
        # 🔴 [2026-07-14] 앱스토어 제외 — homepage가 **개발사 홈페이지**(seller_url)라
        #   같은 개발사의 새 앱이 중복으로 버려진다. 근거·실측은 core/dedup._keys 주석 참조.
        # 🔴 [2026-07-27] 링크드인은 `home:`(=회사 URL, 공동창업자가 공유)과 `name:`(=사람 이름,
        #   흔한 이름 오제외)을 **둘 다 쓰지 않는다.** 근거·실측은 core/dedup._keys 주석 참조.
        #   ⚠️ 이 메서드와 core/dedup._keys 는 **같이 고쳐야 한다**(위 docstring 경고).
        if self.homepage and self.source not in ("앱스토어", "링크드인"):
            keys.append("home:" + _normalize_url(self.homepage))
        if self.name and self.source != "링크드인":
            keys.append("name:" + _normalize(self.name))
        return keys

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _normalize(s: str) -> str:
    """법인명/서비스명 정규화 — 소문자, 공백·괄호·기호 제거."""
    import re
    return re.sub(r"[\s()（）\[\]·・\-_/.,]+", "", (s or "").lower())


def canonical_linkedin_urn(slug: str) -> str:
    """링크드인 **난독화 URN**(`ACwAABq8pMkBt36I…`)에서 사람 고유부만 남긴다. URN이 아니면 "".

    🔴 [2026-07-27 실측] **같은 사람의 URN이 스크래퍼 모드마다 다르다.**
       구조가 `AC?AA` + 멤버ID인코딩(9자) + **세션마다 바뀌는 꼬리**다:
         ACoAABq8pMkB Uyu5gUHx3GXGv2NNOIOK_cb7o_s   ← Full 모드 payload id
         ACwAABq8pMkB t36I8fnw2bZfjGbW3xZp5X8xsl0   ← Short 모드가 URL로 쓰던 값
       3번째 글자(o/w)와 꼬리만 다르고 **앞 12자의 나머지는 같다.**
       이걸 몰라서 dedup이 통과했고, **노션 링크드인 345행 중 84행이 같은 사람의 두 번째 행**이
       됐다(84쌍 전수 검증 · 불일치 0). 정규화 키 = `AC` + `_` + 9자.

    ⚠️ **대소문자를 죽이기 전에** 판정한다 — 멤버ID 인코딩은 대소문자를 구분하므로
       `.lower()` 뒤에 자르면 서로 다른 사람이 같은 키가 될 수 있다.
    """
    import re
    s = (slug or "").strip()
    if re.fullmatch(r"AC[A-Za-z0-9]AA[A-Za-z0-9_\-]{20,}", s):
        return "urn:" + s[:2] + s[3:12]
    return ""


def _normalize_linkedin_url(u: str) -> str:
    """링크드인 프로필 URL 정규화 → 정확매칭 키.
    소문자화 · 프로토콜/서브도메인(www·kr·kr.linkedin) 제거 · /in/<slug> 추출 · 쿼리·트레일링 슬래시 제거.
    난독화 URN 슬러그는 `canonical_linkedin_urn`으로 접는다(모드가 달라도 같은 키가 된다).
    URL이 /in/ 형태가 아니면(회사·post) 일반 URL 정규화로 폴백.

    ⚠️ 남는 한계: **vanity(`/in/haonpark`)와 URN(`/in/ACwAA…`)은 여전히 다른 키다.**
       URN에서 vanity를 유도할 방법이 없다. 지금은 Full 모드가 `publicIdentifier`를 100% 주므로
       (실측 287/287) URN 형태 URL이 애초에 안 생긴다. 다시 생기기 시작하면 **그 사람의 URN을
       노션에 저장해서 대조**하는 수밖에 없다.
    """
    import re
    raw = (u or "").strip()
    if not raw:
        return ""
    m = re.search(r"linkedin\.com/in/([^/?#]+)", raw, re.I)
    if m:
        slug = m.group(1).rstrip("/")
        return "in/" + (canonical_linkedin_urn(slug) or slug.lower())
    return _normalize_url(raw.lower())


def _normalize_url(u: str) -> str:
    """일반 URL 정규화 — scheme·www 제거, 쿼리·프래그먼트·트레일링 슬래시 제거."""
    import re
    s = (u or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.split("?", 1)[0].split("#", 1)[0]
    return s.rstrip("/")


def norm_post_url(u: str) -> str:
    """스레드 글 URL 정규화 → **비교 키**. threads.net/threads.com 도메인 차이까지 흡수.

    ★ 여기 있는 이유: 노션 페이지 본문에 글을 append할 때 **쓰는 쪽(어댑터)과 읽는 쪽(core/notion)이
      반드시 같은 정규화를 써야 한다.** 한 글자만 어긋나면 "이미 붙인 글"을 못 알아보고
      **매일 같은 글이 중복 append돼 본문이 무한히 자란다.** 그래서 어댑터 내부 헬퍼가 아니라
      공용 모듈에 둔다. (원래 adapters/threads.py의 _norm_post_url이었다)
    """
    s = _normalize_url(u)
    return s.replace("threads.net/", "threads.com/")


class Adapter:
    """채널 어댑터 추상 베이스. 수집만 담당(AI 없음, 필터 최소)."""

    source: str = ""

    # 🔴 [2026-08-13 W12 · A-F6] "외부 요청을 안 하는 어댑터"는 **선언해야 한다.**
    #    계측이 0건인 상태는 두 뜻이다 — ⓐ 원래 요청을 안 한다(로컬 파일 파싱)
    #    ⓑ 요청은 했는데 계측이 끊겼거나 전멸했다. 기본값을 False(=요청한다)로 두면
    #    ⓑ 가 시끄럽게 잡히고, ⓐ 인 어댑터만 이 줄을 켜면 된다.
    #    반대로 기본값을 True 로 두면 **새 어댑터가 조용히 계측 밖으로 나간다** —
    #    그게 threads·linkedin 이 계약을 안 지키고도 4개월을 살아남은 방식이다.
    no_fetch: bool = False

    def __init__(self, cfg: dict, keywords: dict):
        self.cfg = cfg
        self.keywords = keywords
        # ★ [2026-07-20] **요청 성적표.** 어댑터는 개별 실패를 격리하는 게 맞다(한 장르가
        #   죽어도 나머지는 살려야 한다). 문제는 그 격리 때문에 **전부 실패해도 '0건'과
        #   구분이 안 된다**는 것이다.
        #   실제 사고(2026-07-19 07:00): 앱스토어 크론이 DNS 실패로 76개 요청이 전멸했는데
        #   `✅ collect appstore: 0건` + **종료코드 0**으로 성공 보고했다. 전날은 10,112건이었다.
        #   버그로그 패턴 ③('돌아가는 척 0을 냄')의 재발이다.
        #   → 어댑터는 시도/실패를 세고, `core/pipeline.py`가 이 수치로 **런의 건강**을 판정한다.
        #   세는 것과 판정하는 것을 분리한 이유: 어댑터마다 "몇 %면 실패인가"가 다를 수 있는데,
        #   그 정책이 어댑터에 흩어지면 채널마다 다른 기준이 조용히 생긴다.
        self.fetch_attempts = 0
        self.fetch_failures = 0

    def _note_fetch(self, ok: bool) -> None:
        """요청 1건의 결과를 기록. 성공/실패 **양쪽 다** 불러야 비율이 맞는다."""
        self.fetch_attempts += 1
        if not ok:
            self.fetch_failures += 1

    def collect(self) -> list[Candidate]:  # noqa: D401
        """raw 후보를 최대한 놓치지 않고 수집해 반환. 하위 클래스가 구현."""
        raise NotImplementedError
