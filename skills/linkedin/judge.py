"""링크드인 판정 — 프롬프트가 붙어 있으므로 도메인이다(통로가 아니다)."""
from __future__ import annotations

from pathlib import Path

from kvlib import Fetched
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
        (p["headline"] or "")[:120], (p["about"] or "")[:200],
    ))


def judge_people(people: list[dict]) -> Fetched:
    """반환 [{key, tier, reason}]. 한 줄씩 되읽어 검증하고 어긋난 줄만 DLQ 로."""
    criteria = (Path(__file__).parent / "criteria.md").read_text(encoding="utf-8")
    valid = {p["key"] for p in people}
    good, bad, drift = [], [], []

    for i in range(0, len(people), BATCH):
        chunk = people[i:i + BATCH]
        prompt = (
            f"{criteria}\n\n"
            f"# 판정 대상 {len(chunk)}명\n"
            "형식: key⇥축⇥이름⇥회사⇥직함⇥재직개월⇥창업개월⇥공백개월⇥학부시작연도⇥헤드라인⇥소개\n"
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
                good.append({"key": key, "tier": parts[ti],
                             "reason": " ".join(parts[ti + 1:]).strip()})

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
