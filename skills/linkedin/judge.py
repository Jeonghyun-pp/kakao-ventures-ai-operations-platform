"""링크드인 판정 — 프롬프트가 붙어 있으므로 도메인이다(통로가 아니다)."""
from __future__ import annotations

from pathlib import Path

from kvlib import Fetched, connections, judgments
from kv_transport import claude

TIERS = ("Catch", "Trend", "Wait", "Cut")
# claude 한 번에 넣는 인원. 넘으면 나눠 부른다.
# 🔴 작게 잡으면 호출 수가 늘어 쿼터 차단선(일 50회)에 닿는다 —
#    700명이면 25명씩은 28회, 50명씩은 14회다. 한 줄이 400자 남짓이라 50명도 넉넉하다.
BATCH = 50

_SYSTEM = (
    "너는 벤처캐피털 소싱 담당자를 돕는다. 주어진 링크드인 프로필을 네 등급으로 나눈다. "
    "설명이나 인사말을 쓰지 말고 요구된 형식만 출력한다."
)


def _line(p: dict) -> str:
    """한 사람을 한 줄로. 축을 반드시 실어야 축별 기준이 적용된다."""
    return "\t".join(str(x or "") for x in (
        p["key"], p["axis"], p["name"], p["company"], p["title"],
        f"재직{p['tenure']}" if p["tenure"] is not None else "재직?",
        f"창업{p['founding']}" if p["founding"] is not None else "창업?",
        f"공백{p['gap']}" if p["gap"] is not None else "공백?",
        f"학부{p['undergrad']}" if p.get("undergrad") else "학부미상",
        (p["headline"] or "")[:120], (p["about"] or "")[:200], (p.get("past") or "")[:200],
    ))


CRITERIA = Path(__file__).parent / "criteria.md"


def criteria_text() -> str:
    return connections.render(CRITERIA.read_text(encoding="utf-8"))


def sha(criteria: str) -> str:
    return judgments.criteria_sha(_SYSTEM, criteria)


def judge_people(people: list[dict], *, criteria: str | None = None) -> Fetched:
    """반환 [{key, tier, reason, raw}]. 한 줄씩 되읽어 검증하고 어긋난 줄만 DLQ 로."""
    return judge_lines([_line(p) for p in people], {p["key"] for p in people}, criteria or criteria_text())


def judge_lines(lines: list[str], valid, criteria: str) -> Fetched:
    """모델이 보는 것은 **줄**이다 — collect 가 만든 줄이든 judgments 에 저장된 줄이든 같다(replay 가 그걸 쓴다)."""
    valid = set(valid)
    good, bad, drift = [], [], []

    for i in range(0, len(lines), BATCH):
        chunk = lines[i:i + BATCH]
        prompt = (
            f"{criteria}\n\n"
            f"# 판정 대상 {len(chunk)}명\n"
            "형식: key⇥축⇥이름⇥회사⇥직함⇥재직개월⇥창업개월⇥공백개월⇥학부시작연도⇥헤드라인⇥소개⇥이전경력\n"
            + "\n".join(chunk)
            + f"\n\n# 출력\n정확히 {len(chunk)}줄. `key<TAB>Tier<TAB>이유 한 문장`.\n"
        )
        try:
            raw = claude.run_session(prompt, system=_SYSTEM)
        except claude.SessionError as e:
            if not good:
                return Fetched.unknown(f"claude:{e}")
            print(f"  ⚠️  배치 {i // BATCH + 1} 실패({e}) — 앞 배치 결과는 살린다")
            break

        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "```")):
                continue
            parts = [x.strip() for x in line.split("\t")]
            if len(parts) < 3:
                bad.append({"line": line, "why": "필드 부족"})
                continue
            # 🔴 위치가 아니라 **값**으로 Tier 를 찾는다.
            #    실측(08-17): claude 가 입력의 축 칸을 출력에 그대로 옮겨
            #    `key⇥축⇥Tier⇥이유` 로 왔고, 위치로 읽던 파서가 116줄(20%)을 버렸다.
            #    판정 내용은 멀쩡했다 — 파서가 틀린 것이다.
            #    출력이 soft 인 이상 열 위치는 언제든 흔들린다. 값으로 찾으면 안 흔들린다.
            ti = next((i for i, x in enumerate(parts[1:], 1) if x in TIERS), None)
            key = parts[0]
            if key not in valid:
                bad.append({"line": line, "why": "목록에 없는 key"})
            elif ti is None:
                bad.append({"line": line, "why": f"Tier 어휘가 줄 안에 없다: {parts[1:3]}"})
            else:
                if ti != 1:
                    drift.append(ti)          # 예상 위치가 아니었다 — 조용히 넘기지 않는다
                good.append({"key": key, "verdict": parts[ti],
                             "reason": " ".join(parts[ti + 1:]).strip(), "raw": line})

    if drift:
        print(f"  ⚠️  Tier 가 예상 위치(2번째)가 아닌 줄 {len(drift)}개 — 값으로 찾아 흡수했다. "
              f"프롬프트를 손볼 신호다")
    if bad:
        claude.dlq("linkedin", "judge_dlq", bad)
        print(f"  ⚠️  검증 실패 {len(bad)}줄 → DLQ (나머지 {len(good)}줄은 살린다)")
    if not good:
        return Fetched.unknown("검증 통과 0줄")
    seen, uniq = set(), []
    for g in good:                       # 같은 key 가 두 번 오면 첫 것만
        if g["key"] not in seen:
            seen.add(g["key"]); uniq.append(g)
    return Fetched.measured(uniq, got=len(uniq), dlq=len(bad))


# ================================================================ 국적 (2026-09-28 · 사용자 결정)
#
# 🔴 **거주지가 아니라 국적이다.** 한국에 사는 외국인은 빼고, 해외에 사는 한국인은 남긴다.
#    거주지·학교 문자열로는 못 가른다(09-28 재고 192명 실측: 서울 거주 외국인 ~20명이
#    `KR-거주` 로, 교환학생 한 줄뿐인 외국인이 `KR-학교` 로 통과했다). 그래서 claude 가 본다.
# 🔴 모르면 한국인 쪽이다 — 판정이 비거나 흔들린 사람은 태그를 안 단다(모르는 건 감점이 아니다).
FOREIGN = "외국인"
_NAT_SYSTEM = "너는 벤처캐피털 소싱 담당자를 돕는다. 설명이나 인사말을 쓰지 말고 요구된 형식만 출력한다."
_NAT = ("한국인", FOREIGN)
_NAT_RULES = """# 국적 판정
각 사람이 한국인인지 외국인인지 가른다. **거주지가 아니라 국적·뿌리다.**
- 해외에 사는 한국인, 한국계 교포(예: Minsu Kim, Minsoo Kim)는 **한국인**이다.
- 한국에 사는 외국인(예: 서울 거주 Enrique Cubero, KAIST 유학생 Arslan Kenbayev)은 **외국인**이다.
- 한국 학교가 교환·연수·여름학기·단기과정 한 줄뿐이면 한국인 근거가 아니다.
- 근거: 이름(한국식 이름·한글 병기), 한국 학부, 한국 회사 경력, 소개 글의 언어.
- 확신이 없으면 **한국인**으로 둔다 — 한국인을 버리는 비용이 외국인 한 명 더 보는 비용보다 크다."""


def nationality_line(key: str, name: str, location: str, schools: str, work: str) -> str:
    return "\t".join(str(x or "").replace("\t", " ").replace("\n", " ")[:200]
                     for x in (key, name, location, schools, work))


def nationality(lines: dict[str, str]) -> Fetched:
    """{key: 줄} → {key: "한국인"|"외국인"}. 판정 못 받은 key 는 **빠진다** — 호출부가 한국인으로 취급한다."""
    out: dict[str, str] = {}
    keys = list(lines)
    for i in range(0, len(keys), BATCH):
        chunk = keys[i:i + BATCH]
        prompt = (f"{_NAT_RULES}\n\n# 대상 {len(chunk)}명\n형식: key⇥이름⇥거주지⇥학력⇥경력·헤드라인\n"
                  + "\n".join(lines[k] for k in chunk)
                  + f"\n\n# 출력\n정확히 {len(chunk)}줄. `key<TAB>한국인` 또는 `key<TAB>외국인`. 다른 말 없이.\n")
        try:
            raw = claude.run_session(prompt, system=_NAT_SYSTEM, tools="")
        except claude.SessionError as e:
            if not out:
                return Fetched.unknown(f"claude:{e}")
            print(f"  ⚠️  국적 배치 {i // BATCH + 1} 실패({e}) — 앞 배치 결과는 살린다")
            break
        for line in raw.splitlines():
            parts = [x.strip() for x in line.strip().split("\t")]
            v = next((x for x in parts[1:] if x in _NAT), None)       # 위치가 아니라 값으로 — judge_lines 와 같은 이유
            if parts and parts[0] in lines and v and parts[0] not in out:
                out[parts[0]] = v
    return Fetched.measured(out, got=len(out))
