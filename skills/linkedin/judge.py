"""링크드인 판정 — 프롬프트가 붙어 있으므로 도메인이다(통로가 아니다)."""
from __future__ import annotations

from pathlib import Path

from kvlib import Fetched
from kv_transport import claude

TIERS = ("Catch", "Trend", "Wait", "Cut")
BATCH = 25          # claude 한 번에 넣는 인원. 넘으면 나눠 부른다

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
        (p["headline"] or "")[:120], (p["about"] or "")[:200],
    ))


def judge_people(people: list[dict]) -> Fetched:
    """반환 [{key, tier, reason}]. 한 줄씩 되읽어 검증하고 어긋난 줄만 DLQ 로."""
    criteria = (Path(__file__).parent / "criteria.md").read_text(encoding="utf-8")
    valid = {p["key"] for p in people}
    good, bad = [], []

    for i in range(0, len(people), BATCH):
        chunk = people[i:i + BATCH]
        prompt = (
            f"{criteria}\n\n"
            f"# 판정 대상 {len(chunk)}명\n"
            "형식: key⇥축⇥이름⇥회사⇥직함⇥재직개월⇥창업개월⇥공백개월⇥헤드라인⇥소개\n"
            + "\n".join(_line(p) for p in chunk)
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
            parts = line.split("\t")
            if len(parts) < 3:
                bad.append({"line": line, "why": "필드 부족"})
                continue
            key, tier, reason = parts[0].strip(), parts[1].strip(), parts[2].strip()
            if key not in valid:
                bad.append({"line": line, "why": "목록에 없는 key"})
            elif tier not in TIERS:
                bad.append({"line": line, "why": f"모르는 Tier '{tier}'"})
            else:
                good.append({"key": key, "tier": tier, "reason": reason})

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
