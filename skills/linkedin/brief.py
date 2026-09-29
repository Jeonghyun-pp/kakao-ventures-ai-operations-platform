"""카드 문안 — 슬랙에 올릴 두 문장과 회사명. 프롬프트가 붙으므로 도메인이다.

## 🔴 왜 One Liner 를 그대로 안 쓰나

노션 자동화가 `One Liner` 를 복사해 Pick 을 만들었는데, 실측 결과 **2~95자**였고
`'--'` · `'CEO'` 같은 것도 있었다. 사람이 그걸 매번 다시 썼다 — 같은 것을 세 번 만든 셈이다.
한 번에 쓸 만한 것을 만든다.

## 🔴 회사명은 필드에 없다 (2026-08-18 실측)

    `회사` 칸 1,046/1,235 채워짐 (85%)
    그런데 헤드라인이 Stealth 인 35건 중 30건이 값을 갖는데 —
        stealth-startup-community   회사가 아니라 플레이스홀더
        divitas-networks            옛 직장
    → **어느 칸을 복사해도 안 된다.** 헤드라인 `Building AI Focus Agent @SampleProduct` 를
      읽어야 `SampleProduct` 가 나온다. 노션 자동화가 원리적으로 못 하는 일이다.

회사명을 제대로 찾는 것이 극초기 중복 문제도 같이 푼다 —
`SampleProduct`(회사)와 `Example Person B`(사람)이 두 행으로 갈라진 게 그 문제였다.
"""
from __future__ import annotations

from pathlib import Path

from kvlib import Fetched, constraints
from kv_transport import claude

_SYSTEM = (
    "너는 벤처캐피털 소싱 담당자를 돕는다. 심사역이 5초 안에 읽고 만날지 판단할 수 있게 쓴다. "
    "설명이나 인사말을 쓰지 말고 요구된 형식만 출력한다."
)

# 회사를 못 찾았을 때 쓰는 표시. 빈칸으로 두면 "못 찾았다"와 "안 찾아봤다"가 구별이 안 된다.
NO_COMPANY = "(미상)"

# 창업 판정 (2026-09-29 · 사용자 결정). 비창업만 카드에서 뺀다 — 준비는 축 B(경력단절)의 대상이다.
PASS = ("창업", "준비")
NOT_FOUNDER = "비창업"
VERDICTS = PASS + (NOT_FOUNDER,)


def _line(p: dict) -> str:
    def flat(s, cap):
        return " ".join(str(s or "").split())[:cap]
    return "\t".join((
        p["key"], flat(p.get("name"), 60), flat(p.get("company"), 60),
        flat(p.get("one_liner"), 120), flat(p.get("headline"), 160),
        flat(p.get("about"), 300), flat(p.get("reason"), 300),
        str(p.get("founding") or ""),
    ))


def write(people: list[dict]) -> Fetched:
    """반환 [{key, verdict, company, brief}]. verdict 는 VERDICTS 중 하나, brief 는 두 문장."""
    if not people:
        return Fetched.measured([], got=0, dlq=0)

    guide = (Path(__file__).parent / "brief.md").read_text(encoding="utf-8")
    batch = constraints.get("linkedin.outreach_batch")
    valid = {p["key"] for p in people}
    good, bad = [], []

    for i in range(0, len(people), batch):
        chunk = people[i:i + batch]
        prompt = (
            f"{guide}\n\n"
            f"# 대상 {len(chunk)}명\n"
            "형식: key⇥이름⇥회사칸⇥한줄소개⇥헤드라인⇥소개글⇥판정이유⇥창업개월\n"
            + "\n".join(_line(p) for p in chunk)
            + f"\n\n# 출력\n정확히 {len(chunk)}줄. `key<TAB>판정<TAB>회사명<TAB>두 문장`. 판정은 {'·'.join(VERDICTS)} 중 하나.\n"
              f"회사명을 못 찾으면 정확히 `{NO_COMPANY}` 라고 쓴다.\n"
        )
        try:
            raw = claude.run_session(prompt, system=_SYSTEM)
        except claude.SessionError as e:
            if not good:
                return Fetched.unknown(f"claude:{e}")
            print(f"  ⚠️  배치 {i // batch + 1} 실패({e}) — 앞 배치는 살린다")
            break

        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "```")):
                continue
            parts = [x.strip() for x in line.split("\t")]
            if len(parts) < 3:
                bad.append({"line": line[:300], "why": "필드 부족"})
                continue
            key = parts[0]
            if key not in valid:
                bad.append({"line": line[:300], "why": "목록에 없는 key"})
                continue
            # 🔴 판정은 위치가 아니라 **값**으로 찾는다 — judge.judge_lines 와 같은 이유(열이 흔들린다)
            vi = next((i for i, x in enumerate(parts[1:3], 1) if x in VERDICTS), None)
            rest = [x for i, x in enumerate(parts[1:], 1) if i != vi]
            if vi is None or len(rest) < 2:
                bad.append({"line": line[:300], "why": "판정 어휘가 없거나 필드 부족"})
                continue
            good.append({"key": key, "verdict": parts[vi], "company": rest[0],
                         "brief": " ".join(rest[1:]).strip()})

    if bad:
        claude.dlq("linkedin", "brief_dlq", bad)
        print(f"  ⚠️  검증 실패 {len(bad)}줄 → DLQ (나머지 {len(good)}줄은 살린다)")
    if not good:
        return Fetched.unknown("검증 통과 0줄")
    seen, uniq = set(), []
    for g in good:
        if g["key"] not in seen:
            seen.add(g["key"]); uniq.append(g)
    return Fetched.measured(uniq, got=len(uniq), dlq=len(bad))
