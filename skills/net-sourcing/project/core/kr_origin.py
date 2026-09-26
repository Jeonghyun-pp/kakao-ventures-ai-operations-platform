"""앱스토어 개발사 국적 판정 — **확실한 외국만 버리는 3값 분류기**.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔴 이건 예전에 걷어낸 `kr_signal`의 부활이 **아니다.** 읽는 필드가 다르다.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  옛 kr_signal은 `artistName`(=차트에 보이는 개발사 표시명)에 정규식을 걸었다. 거기엔
  **법인격이 안 보인다** — "Quvarte"만 보고 한국인지 튀르키예인지 맞히라는 문제였고,
  그래서 424개 중 225개 오탐(절반이 베트남·중국·터키) / 좁히면 123개 중 100개 미탐이 났다.
  당연하다. 신호가 없는 필드였다.

  이 모듈은 `sellerName`(=**법인 실명**)을 읽는다. 같은 앱이 이렇게 갈린다:
      artistName "Quvarte"  →  sellerName "Quvarte Teknoloji **Anonim Sirketi**"  (튀르키예)
      artistName "Netflowers Inc."          seller_url "netflowers.**co.jp**"      (일본)
      artistName "Up Planet Inc."           seller_url "pickplay.**co.kr**"        (한국)
  이 필드는 **줄곧 받아오고 있었는데 버려지고 있었다**(rank_lookup.enrich 의 setdefault가
  SSR이 이미 채운 developer 를 덮지 못해 sellerName 이 통째로 유실됐다). 그걸 살린 게 전부다.

━━━ 정책: 판정은 3값이고, **버리는 건 'foreign' 하나뿐이다.** ━━━━━━━━━━━━━━━━━━━━━━━━
  비용이 비대칭이라서다:
    · 한국 앱을 잘못 버림 = **영구 유실**. 아무도 그게 없어진 걸 모른다.
    · 외국 앱이 남음     = 사람이 노션에서 거른다. 지금도 하고 있는 일이다.
  그래서 애매하면 통과시킨다. 이 모듈의 목표는 "한국을 찾는 것"이 아니라
  **"확실한 외국을 빼는 것"**이다. 이름을 kr_signal이 아니라 kr_origin으로 둔 이유다.

━━━ 실측 (2026-W34 노션 착지분 204건 · 사람이 App Store 판매자 정보로 라벨링) ━━━━━━━━━
  라벨: 한국 73 / 외국 131
    L1~L6 (seller 신호)      : foreign 44건 — 오탐 0
    L7   (설명 한글비율<5%)   : foreign 65건 — 오탐 2 → 성씨 veto가 2건 모두 구제
    최종 : 외국 131건 중 **109건 제거(83%)** · 한국 73건 중 **유실 0건**
  재측정: `python tools/kr_origin_eval.py` (fixtures/kr_origin_w34.json 고정 라벨)
  🔴 규칙을 고치면 **반드시 이 eval을 돌리고 유실 0건을 확인**해라. 감으로 고치지 마라.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# ── 문자 체계 ─────────────────────────────────────────────────────────────────
_HANGUL = re.compile(r"[가-힣]")
_KANA = re.compile(r"[぀-ヿ]")
_HAN = re.compile(r"[一-鿿]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_THAI = re.compile(r"[฀-๿]")
_ARABIC = re.compile(r"[؀-ۿ]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# ── seller_url 처리 ───────────────────────────────────────────────────────────
# 🔴 호스팅 플랫폼은 **국적 정보가 0이다.** 먼저 걸러내지 않으면 사고가 난다:
#   실측 오탐 - "마이 레시피 노트"(한국) 의 seller_url 이 …supabase.**co** 라서
#   `.co`(콜롬비아)로 읽혀 외국 판정이 났다. 개발자가 랜딩페이지를 안 만들고
#   백엔드 URL을 그대로 넣은 것뿐인데 한국 앱이 지워질 뻔했다.
_PLATFORM_HOSTS = (
    "github.io", "github.com", "gitlab.io", "vercel.app", "netlify.app", "netlify.com",
    "pages.dev", "workers.dev", "web.app", "firebaseapp.com", "supabase.co",
    "notion.site", "notion.so", "blogspot.com", "wordpress.com", "wixsite.com",
    "weebly.com", "webflow.io", "carrd.co", "framer.website", "framer.app",
    "google.com", "docs.google.com", "sites.google.com", "linktr.ee", "bio.link",
    "chatgpt.site", "gumroad.com", "substack.com", "medium.com", "apple.com",
    "facebook.com", "instagram.com", "youtube.com", "x.com", "twitter.com",
    "gitbook.io", "readme.io", "glitch.me", "replit.app", "onrender.com",
)

_KR_TLD = (".kr",)

# 🔴 여기 넣어도 되는 건 **바니티로 안 쓰이는 ccTLD 뿐이다.**
#   뺀 것들과 이유:
#     .co(supabase.co·범용 축약) .it(duru.**it** — 한국 "두루"였다) .io .ai .me .tv
#     .app .dev .to .cc .ly .la .st .sh .gg .fm .am .is  → 전부 국적과 무관하게 팔린다.
#   .in 은 남겼다(실측상 인도 실사용 — forkai.in). 오탐 나면 여기서 빼라.
_FOREIGN_TLD = (
    ".jp", ".cn", ".vn", ".tw", ".hk", ".sg", ".tr", ".th", ".ru", ".br", ".in",
    ".de", ".fr", ".es", ".nl", ".pl", ".uk", ".ua", ".pt", ".se", ".no", ".fi",
    ".dk", ".cz", ".gr", ".ro", ".bg", ".hu", ".il", ".ae", ".pk", ".bd", ".np",
    ".ph", ".my", ".mx", ".ar", ".cl", ".pe", ".eg", ".ma", ".za", ".ch", ".at",
    ".be", ".ie", ".nz", ".au", ".ca", ".kz", ".uz", ".by", ".rs", ".hr", ".si",
    ".sk", ".lt", ".lv", ".ee", ".id",
)

# ── sellerName 법인격 ─────────────────────────────────────────────────────────
# 한국임을 **명시**하는 마커. 실측: 애플이 판매자명에 "(KR)"을 붙여주는 계정이 있다
#   (`AZAI(KR)`, `DayLab(KR)`).
_KR_ENTITY = ("주식회사", "(주)", "㈜", "(kr)", "（kr）", "co., ltd(kr)")

# 외국 법인격. **소문자·양옆 공백 패딩된 문자열**에 대해 검사한다(부분일치 사고 방지).
_FOREIGN_ENTITY = (
    "anonim sirketi", "anonim şirketi", " a.s.", " a.ş.", "limited sirketi",
    "pte. ltd", "pte ltd", "pty ltd", "pty. ltd", "sdn bhd", "sdn. bhd",
    "private limited", "pvt. ltd", "pvt ltd", "pvt. limited",
    "limited liability company", "limited liability co",
    " srl", " s.r.l", " sas ", " sasu", " slu", " s.l.", " s.a.", " sac ",
    " sa de cv", " gmbh", " mbh", " ug ", " bv ", " b.v.", " nv ", " n.v.",
    " aps ", " a/s", " oy ", " ab ", "ltda", " lda.", " d.o.o", "sp. z o.o",
    " s.p.a", "k.k.", ", kk", " fze", " fz-llc", " fzc", " llp", " lp ",
    "株式会社", "合同会社", "有限会社", "有限公司", "股份", "科技有限公司",
)

# 중국 지역명이 사명에 박힌 경우(로마자라 한자 규칙에 안 걸린다). 실측: Shenzhen Huopu
# Software Co., Ltd. / Guangzhou Ciyou / Fuzhou Qingyue / Anhui Xiangyu / Hangzhou VelocAI.
_FOREIGN_PLACE = (
    "shenzhen", "guangzhou", "hangzhou", "fuzhou", "anhui", "shanghai", "beijing",
    "chengdu", "shandong", "jiangsu", "zhejiang", "hong kong", "hk ", "taipei",
    "ho chi minh", "hanoi", "jakarta", "bangkok", "istanbul", "mumbai", "bengaluru",
)

# ── 한국식 성씨 (VETO 전용) ───────────────────────────────────────────────────
# 🔴 이 목록은 **한국이라고 단정하는 데 절대 쓰지 않는다.** L7(설명 한글비율) 이 외국으로
#   판정하려 할 때 그걸 **막기만** 한다. 이유: Lee/Han/Song/Wu/Do/Yang 은 중국·베트남과
#   충돌한다 — 이게 옛 kr_signal 이 죽은 바로 그 지점이다. 하지만 veto로 쓰면 충돌 비용이
#   "외국 앱 몇 개가 노션에 남는다"뿐이다.
#   실측(W34): veto가 6건 발동 — 한국 2건(FlyReady·Snick) 구제 / 외국 4건 유입. 남는 장사다.
_KR_SURNAMES = frozenset("""
kim gim lee yi rhee ri park bak pak choi choe jung jeong chung joung kang gang
cho jo joh yoon yun youn jang chang lim im rim han hahn oh oo seo suh sur
shin sin hwang whang ahn an song sung seong ryu ryoo yoo yu you hong jeon jun
chun ko koh go goh moon mun son sohn baek paek back nam noh no roh ha hah
heo hur huh joo ju chu koo ku goo gu min na ra do doh woo wu gil kil
chae chai bae pae bai kwon gwon shim sim yang ryang bang pang byun byeon pyun
hyun hyeon jin chin gwak kwak cheon chon uhm eom um pyo tak ok wi wee
sunwoo namgung hwangbo seok suk yeo yuh ban pan mo myung myong
""".split())


def _host(url: str) -> str:
    if not url:
        return ""
    try:
        return (urlparse(url if "://" in url else "https://" + url).hostname or "").lower()
    except Exception:  # noqa: BLE001 - URL이 쓰레기여도 판정만 포기하면 된다
        return ""


def _looks_korean_name(seller_name: str) -> bool:
    """sellerName 의 토큰 중 하나라도 한국식 성씨 로마자면 True. **veto 전용.**"""
    toks = [t.strip(".,()'\"").lower() for t in re.split(r"[\s,]+", seller_name or "") if t]
    return any(t in _KR_SURNAMES for t in toks)


def _hangul_ratio(text: str) -> float:
    text = text or ""
    return len(_HANGUL.findall(text)) / len(text) if text else 0.0


# L7이 성립하려면 설명이 **충분히 길어야** 한다. 짧은 문자열의 비율은 노이즈다.
_MIN_DESC_LEN = 40
_HANGUL_FOREIGN_MAX = 0.05


def classify(seller_name: str = "", seller_url: str = "", app_name: str = "",
             description: str = "") -> tuple[str, str]:
    """→ (verdict, reason). verdict ∈ {"kr", "foreign", "unknown"}.

    호출자는 **"foreign" 일 때만 버려야 한다.** "unknown"은 통과시킨다(위 정책 참조).
    """
    sn = (seller_name or "").strip()
    padded = " " + sn.lower() + " "
    host = _host(seller_url)

    # ── L1. 호스팅 플랫폼 도메인은 국적 정보가 없다 → 도메인 판정 자체를 건너뛴다
    is_platform = any(host == p or host.endswith("." + p) for p in _PLATFORM_HOSTS)

    # ── L2. seller_url ccTLD (가장 강한 신호)
    if host and not is_platform:
        if host.endswith(_KR_TLD):
            return "kr", f"seller_url .kr ({host})"
        for tld in _FOREIGN_TLD:
            if host.endswith(tld):
                return "foreign", f"seller_url {tld} ({host})"

    # ── L3. sellerName 이 한국임을 명시
    for mark in _KR_ENTITY:
        if mark in padded:
            return "kr", f'seller_name 한국 법인격 "{mark.strip()}"'
    if _HANGUL.search(sn):
        return "kr", "seller_name 한글 표기"

    # ── L4. sellerName 문자 체계
    if _KANA.search(sn):
        return "foreign", "seller_name 가나(일본)"
    if _HAN.search(sn):
        return "foreign", "seller_name 한자(중화권/일본)"
    if _CYRILLIC.search(sn):
        return "foreign", "seller_name 키릴"

    # ── L5. 외국 법인격 / 지역명
    for mark in _FOREIGN_ENTITY:
        if mark in padded:
            return "foreign", f'seller_name 외국 법인격 "{mark.strip()}"'
    for place in _FOREIGN_PLACE:
        if place in padded:
            return "foreign", f'seller_name 외국 지역명 "{place.strip()}"'

    # ── L6. 앱 텍스트가 **한글 없이** 다른 문자 체계
    #   🔴 "한글 없이" 가드가 필수다. 없으면 일본어 학습 앱("링귀지", 한국 개발사)이
    #      본문의 가나 예문 때문에 일본 앱으로 판정된다. 실측 오탐 1건이 정확히 이거였다.
    blob = f"{app_name or ''} {(description or '')[:400]}"
    if not _HANGUL.search(blob):
        for rx, label in ((_KANA, "가나"), (_CYRILLIC, "키릴"), (_THAI, "타이"),
                          (_ARABIC, "아랍"), (_DEVANAGARI, "데바나가리"), (_HAN, "한자")):
            if rx.search(blob):
                return "foreign", f"앱 텍스트 {label}(한글 0)"

    # ── L7. 설명의 한글 비율
    #   🔴 설명이 없거나 짧으면 **판정하지 않는다.** lookup 이 레이트리밋/장애로 설명을 못
    #      가져오면 description="" → 비율 0 → 전량 foreign → **그날 수집이 통째로 증발한다.**
    #      "검증 못 한 것은 버리지 않는다"(어댑터의 출시일 미상 정책과 같은 원칙, 방향만 반대).
    desc = description or ""
    if len(desc) >= _MIN_DESC_LEN:
        if _hangul_ratio(desc) < _HANGUL_FOREIGN_MAX:
            if _looks_korean_name(sn):
                return "unknown", f"한글 {_hangul_ratio(desc):.0%}지만 한국식 성씨 — veto"
            return "foreign", f"설명 한글 {_hangul_ratio(desc):.0%} (<{_HANGUL_FOREIGN_MAX:.0%})"

    return "unknown", "판정 신호 없음"
