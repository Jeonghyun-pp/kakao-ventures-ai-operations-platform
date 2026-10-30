"""스타트업 DB 대조 — 카드 후보의 회사가 이미 우리 DB(포트폴리오 포함)에 있으면 카드에서 뺀다. 프롬프트는 known.md(다른 표기).

[사용자 피드백 2026-10-21] "만났던 팀들이 넘 많이 나오네요"(예시랩·김창업). 그전 중복 방지는 링크드인 쪽 기록끼리
(극초기 링크 URL·체크·장부)만이라 스타트업 DB 에 있는 팀이 그대로 나갔다. 비플랜 DB 는 대조하지 않는다 [사용자 결정 2026-10-21].
포트폴리오는 따로 DB 가 없다 — 스타트업 DB 의 `패밀리` 체크다.

🔴 노션은 넓게 찾고(`contains`), 맞음은 코드가 정한다. 10/21 실측(카드 197장): 단순 contains 는 METAL → "Sun Metalon",
   SECA → "Visecare" 처럼 이름 일부만 겹친 회사를 걸었다. 표기를 정규화해 **같거나, 그 표기로 시작하고 꼬리가 짧을 때**만 맞음
   (`예시랩` → `예시랩AI` · `SECA` → `SECA Labs`).
🔴 못 읽으면 실패를 돌려준다 — 호출부(card)가 멈춘다. 못 거른 채 내보내면 오늘 같은 일이 또 난다(극초기 대조와 같은 원칙).
"""
from __future__ import annotations

import re
from pathlib import Path

from kv_transport import claude, notion
from kvlib import Fetched, connections

STARTUP_DB = connections.get("notion.startup_database_id")
# 꼬리 허용은 영문 복수형뿐(`step up` ↔ `step ups`) — 글자 수로 열면 `persly` 가 `perslyon` 을 건다. 한글엔 꼬리를 안 준다 — `예시랩` 가 `예시랩온` 을 걸면 남의 회사를
#    "이미 본 팀"으로 영구히 Legacy 에 보낸다(잘못 빼는 쪽이 한 장 더 보는 쪽보다 비싸다). `예시랩AI` 는 덩어리 나누기가 잡는다.
PLURAL = ("s", "es")
MAX_ALIASES = 3
_SYSTEM = "너는 회사명의 한글·영문 표기를 알려준다. 지어내지 않는다. 설명 없이 요구된 형식만 출력한다."
_LEGAL = re.compile(r"주식회사|\(주\)|㈜|\b(?:inc|corp|corporation|ltd|llc|co)\b\.?", re.I)
_PAREN = re.compile(r"[(\[（](.*?)[)\]）]")
_RUN = re.compile(r"[가-힣]+|[0-9a-z]+(?:[ .&\-]+[0-9a-z]+)*")       # 한글 덩어리 · 영문 덩어리(띄어쓴 영문은 한 덩어리)
_LATIN = re.compile(r"^[0-9a-z]+$")
_NO_COMPANY = re.compile(r"^\(?미상\)?$|^스텔스$|stealth", re.I)


def norm(s: str) -> str:
    """대조용 — 법인 꼬리·공백·문장부호를 지운 소문자. 한글·영문·숫자만 남는다."""
    return re.sub(r"[^0-9a-z가-힣]", "", _LEGAL.sub("", str(s or "").lower()))


def usable(a: str) -> bool:
    """너무 짧은 표기는 대조하지 않는다 — `AI`·`랩` 하나로 수백 행이 걸린다."""
    return len(re.findall(r"[가-힣]", a)) >= 2 or len(a) >= 3


def forms(title: str) -> set[str]:
    """DB 제목의 표기들 — 괄호 밖과 안을 따로(`예시랩(ExampleLab)` → 예시랩 · persly), 한글과 영문을 따로
    (`예시랩AI` → 예시랩 · ai · `스텝업 STEP UPS` → 스텝업 · stepups), 띄어쓴 영문은 앞에서부터 단어 단위로(`SECA Labs` → seca · secalabs).
    한 제목에 두 표기를 나란히 적거나 일반어를 붙이는 일이 흔하다. 맞음은 이 중 하나와 **같을 때**다."""
    parts = [_PAREN.sub(" ", title), title, *_PAREN.findall(title)]
    runs = [r for p in parts for r in _RUN.findall(_LEGAL.sub(" ", p.lower()))]
    heads = [" ".join(w[:i]) for r in runs for w in [re.split(r"[ .&\-]+", r)] for i in range(1, len(w) + 1)]
    out = {norm(x) for x in parts + runs + heads}
    return {f for f in out if f}


def matches(aliases: list[str], title: str) -> bool:
    keys = [a for a in (norm(x) for x in aliases) if usable(a)]
    return any(f == a or (_LATIN.match(f) and f.startswith(a) and f[len(a):] in PLURAL)
               for f in forms(title) for a in keys)


def aliases(companies: list[str]) -> dict[str, list[str]]:
    """{회사명: [회사명, 다른 표기…]}. claude 가 죽어도 원래 이름은 있다 — 대조는 계속한다."""
    out = {c: [c] for c in companies}
    if not companies:
        return out
    guide = (Path(__file__).parent / "known.md").read_text(encoding="utf-8")
    try:
        raw = claude.run_session(f"{guide}\n\n# 회사 {len(companies)}개\n" + "\n".join(companies) + "\n", system=_SYSTEM, tools="")
    except claude.SessionError as e:
        print(f"  ⚠️  회사명 다른 표기 실패({e}) — 원래 이름으로만 대조")
        return out
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split("\t") if p.strip()]
        if parts and parts[0] in out:
            out[parts[0]] = list(dict.fromkeys([parts[0], *parts[1:1 + MAX_ALIASES]]))
    return out


def lookup(names: list[str]) -> Fetched:
    """스타트업 DB 에서 이 표기들과 맞는 행 [{title, family, page_id}]."""
    keys = [a for a in names if usable(norm(a))]
    if not keys:
        return Fetched.measured([])
    f = notion.query(STARTUP_DB, ["회사명", "패밀리"], page_size=50, max_pages=2,
                     filter={"or": [{"property": "회사명", "title": {"contains": a.strip()}} for a in keys]})
    if not f.is_measured:
        return f
    return Fetched.measured([{"title": str(r.get("회사명") or "").strip(), "family": bool(r.get("패밀리")), "page_id": r["_page_id"]}
                             for r in f.require() if matches(keys, str(r.get("회사명") or ""))])


def check(people: list[tuple[str, str]]) -> Fetched:
    """[(key, 회사명)] → {key: {"aliases": [...], "hits": [...]}}. 회사 미상·스텔스는 대조하지 않는다(hits 빈 값).
    노션을 하나라도 못 읽으면 전체 실패 — 호출부가 멈춘다."""
    cos = list(dict.fromkeys(c for _, c in people if c and not _NO_COMPANY.search(c)))
    al = aliases(cos)
    out = {}
    for k, c in people:
        if c not in al:
            out[k] = {"aliases": [], "hits": []}
            continue
        f = lookup(al[c])
        if not f.is_measured:
            return Fetched.unknown(f"스타트업 DB 를 못 읽었다({c}): {f.reason}")
        out[k] = {"aliases": al[c], "hits": f.require()}
    return Fetched.measured(out)


def label(hits: list[dict]) -> str:
    return " · ".join(h["title"] + ("(패밀리)" if h["family"] else "") for h in hits[:3])
