"""앱스토어 개발사 국적 판정 — 확실한 외국만 버리는 3값 분류기.

정책: 판정은 3값(kr / foreign / unknown)이고 버리는 건 foreign 하나뿐이다.
비용이 비대칭이라서다:
  · 한국 앱을 잘못 버림 = 영구 유실. 아무도 그게 없어진 걸 모른다.
  · 외국 앱이 남음     = 사람이 노션에서 거른다. 지금도 하고 있는 일이다.

규칙 L1~L7 이 확정하지 못한 unknown 건은 LLM 에 넘겨 한 번 더 판정한다.
LLM 이 실패하면 착지를 막지 않는다 — 사람이 거르는 게 유실보다 낫다.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

import re

from kvlib import Fetched, connections, constraints
from kv_transport import claude

# ── 문자 체계 ─────────────────────────────────────────────────────────────────
_HANGUL = re.compile(r"[가-힣]")
_KANA = re.compile(r"[぀-ヿ]")
_HAN = re.compile(r"[一-鿿]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_THAI = re.compile(r"[฀-๿]")
_ARABIC = re.compile(r"[؀-ۿ]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")

# ── seller_url 처리 ───────────────────────────────────────────────────────────
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


_FOREIGN_TLD = (
    ".jp", ".cn", ".vn", ".tw", ".hk", ".sg", ".tr", ".th", ".ru", ".br", ".in",
    ".de", ".fr", ".es", ".nl", ".pl", ".uk", ".ua", ".pt", ".se", ".no", ".fi",
    ".dk", ".cz", ".gr", ".ro", ".bg", ".hu", ".il", ".ae", ".pk", ".bd", ".np",
    ".ph", ".my", ".mx", ".ar", ".cl", ".pe", ".eg", ".ma", ".za", ".ch", ".at",
    ".be", ".ie", ".nz", ".au", ".ca", ".kz", ".uz", ".by", ".rs", ".hr", ".si",
    ".sk", ".lt", ".lv", ".ee", ".id",
)

# ── sellerName 법인격 ─────────────────────────────────────────────────────────
_KR_ENTITY = ("주식회사", "(주)", "㈜", "(kr)", "（kr）", "co., ltd(kr)")

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

_FOREIGN_PLACE = (
    "shenzhen", "guangzhou", "hangzhou", "fuzhou", "anhui", "shanghai", "beijing",
    "chengdu", "shandong", "jiangsu", "zhejiang", "hong kong", "hk ", "taipei",
    "ho chi minh", "hanoi", "jakarta", "bangkok", "istanbul", "mumbai", "bengaluru",
)

# ── 한국식 성씨 (VETO 전용) ───────────────────────────────────────────────────
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
    return (urlsplit(url).hostname or "").lower() if url else ""


def _looks_korean_name(seller_name: str) -> bool:
    toks = [t.strip(".,()'\"").lower() for t in re.split(r"[\s,]+", seller_name or "") if t]
    return any(t in _KR_SURNAMES for t in toks)


# ---------------------------------------------------------------- 규칙 분류기

def classify(seller_name: str = "", seller_url: str = "", app_name: str = "",
             description: str = "") -> tuple[str, str]:
    """→ (verdict, reason). verdict ∈ {"kr", "foreign", "unknown"}.

    호출자는 "foreign" 일 때만 버려야 한다. "unknown"은 통과시킨다.
    """
    sn = (seller_name or "").strip()
    padded = " " + sn.lower() + " "
    host = _host(seller_url)

    # L1. 호스팅 플랫폼 도메인은 국적 정보가 없다
    is_platform = any(host == p or host.endswith("." + p) for p in _PLATFORM_HOSTS)

    # L2. seller_url ccTLD
    if host and not is_platform:
        if host.endswith(".kr"):
            return "kr", f"seller_url .kr ({host})"
        for tld in _FOREIGN_TLD:
            if host.endswith(tld):
                return "foreign", f"seller_url {tld} ({host})"

    # L3. sellerName 이 한국임을 명시
    for mark in _KR_ENTITY:
        if mark in padded:
            return "kr", f'seller_name 한국 법인격 "{mark.strip()}"'
    if _HANGUL.search(sn):
        return "kr", "seller_name 한글 표기"

    # L4. sellerName 문자 체계
    if _KANA.search(sn):
        return "foreign", "seller_name 가나(일본)"
    if _HAN.search(sn):
        return "foreign", "seller_name 한자(중화권/일본)"
    if _CYRILLIC.search(sn):
        return "foreign", "seller_name 키릴"

    # L5. 외국 법인격 / 지역명
    for mark in _FOREIGN_ENTITY:
        if mark in padded:
            return "foreign", f'seller_name 외국 법인격 "{mark.strip()}"'
    for place in _FOREIGN_PLACE:
        if place in padded:
            return "foreign", f'seller_name 외국 지역명 "{place.strip()}"'

    # L6. 앱 텍스트가 한글 없이 다른 문자 체계
    blob = f"{app_name or ''} {(description or '')[:400]}"
    if not _HANGUL.search(blob):
        for rx, label in ((_KANA, "가나"), (_CYRILLIC, "키릴"), (_THAI, "타이"),
                          (_ARABIC, "아랍"), (_DEVANAGARI, "데바나가리"), (_HAN, "한자")):
            if rx.search(blob):
                return "foreign", f"앱 텍스트 {label}(한글 0)"

    # L7. 설명의 한글 비율
    min_len = constraints.get("appstore.kr_origin.min_description_length")
    max_ratio = constraints.get("appstore.kr_origin.hangul_foreign_max_ratio")
    desc = description or ""
    if len(desc) >= min_len:
        ratio = len(_HANGUL.findall(desc)) / len(desc)
        if ratio < max_ratio:
            if _looks_korean_name(sn):
                return "unknown", f"한글 {ratio:.0%}지만 한국식 성씨 — veto"
            return "foreign", f"설명 한글 {ratio:.0%} (<{max_ratio:.0%})"

    return "unknown", "판정 신호 없음"


# ---------------------------------------------------------------- LLM 판정

_ORIGIN_SYSTEM = (
    "너는 앱스토어 개발사의 국적을 판정한다. "
    "seller_name(법인 실명)과 app_name, description 을 보고 한국(kr) / 외국(foreign)을 판정한다. "
    "확실하지 않으면 kr 로 판정한다 — 한국 앱을 버리는 것이 외국 앱을 남기는 것보다 나쁘다. "
    "설명이나 인사말을 쓰지 말고 요구된 형식만 출력한다."
)


CRITERIA = Path(__file__).parent / "criteria.md"
FIXTURE = Path(__file__).parent / "fixtures" / "kr_origin_w34.json"      # 고정 정답 199건 — 테스트와 tools/kr_origin_eval.py 가 잰다


def criteria_text() -> str:
    return connections.render(CRITERIA.read_text(encoding="utf-8"))


def llm_judge_origin(unknowns: list[dict], criteria: str) -> Fetched:
    """규칙이 판정하지 못한 앱의 국적을 LLM 으로 판정한다. 기준은 `criteria.md`(사람이 깎는다).

    입력: [{app_id, seller_name, app_name, description}, ...]
    반환: Fetched.measured([{app_id, verdict, reason, raw}, ...])
    """
    if not unknowns:
        return Fetched.measured([])

    lines = []
    for u in unknowns:
        desc = (u.get("description") or "")[:200].replace("\t", " ").replace("\n", " ")
        sn = (u.get("seller_name") or "").replace("\t", " ")
        name = (u.get("app_name") or "").replace("\t", " ")
        lines.append(f"{u['app_id']}\t{sn}\t{name}\t{desc}")

    prompt = (
        f"{criteria}\n\n"
        "# 입력 (app_id · seller_name · app_name · description[:200])\n"
        + "\n".join(lines)
        + f"\n\n# 출력 형식\n정확히 {len(unknowns)}줄. 각 줄은 `app_id<TAB>kr|foreign<TAB>이유(한 문장)`.\n"
          "머리말·꼬리말·코드펜스 금지.\n"
    )
    try:
        raw = claude.run_session(prompt, system=_ORIGIN_SYSTEM)
    except claude.SessionError as e:
        return Fetched.unknown(f"claude:{e}")

    valid_ids = {u["app_id"] for u in unknowns}
    good, bad = [], []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        parts = line.split("\t") if "\t" in line else line.split(None, 2)
        if len(parts) < 2:
            bad.append({"line": line, "why": "필드 부족"})
            continue
        aid = parts[0].strip()
        verdict = parts[1].strip().lower()
        reason = parts[2].strip() if len(parts) > 2 else ""
        if aid not in valid_ids:
            bad.append({"line": line, "why": "목록에 없는 app_id"})
            continue
        if verdict not in ("kr", "foreign"):
            bad.append({"line": line, "why": f"verdict '{verdict}' 이상"})
            continue
        good.append({"app_id": aid, "verdict": verdict, "reason": f"LLM: {reason}", "raw": line})

    if bad:
        claude.dlq("appstore", "kr_origin_dlq", bad)
        print(f"  ⚠️  국적 LLM 검증 실패 {len(bad)}줄 → DLQ")
    if not good:
        return Fetched.unknown(f"국적 LLM 검증 통과 0줄 (원문 {len(raw)}자)")
    return Fetched.measured(good, requested=len(unknowns), got=len(good), dlq=len(bad))


# ---------------------------------------------------------------- HEALTH

def check_health(results: list[tuple[str, str]], total_fresh: int) -> list[str]:
    """과다 삭제나 전량 삭제를 감지한다. 경고 문자열 목록을 반환."""
    if total_fresh == 0:
        return []
    warnings = []
    foreign_count = sum(1 for v, _ in results if v == "foreign")
    ratio = foreign_count / total_fresh

    warn_ratio = constraints.get("appstore.kr_origin.over_deletion_warn_ratio")
    if ratio > warn_ratio:
        warnings.append(
            f"국적 필터 외국 비율 {ratio:.0%} (>{warn_ratio:.0%}) — lookup 장애 의심")
    return warnings
