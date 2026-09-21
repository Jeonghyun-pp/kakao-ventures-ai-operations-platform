"""콜드 메시지 초안 — 링크드인으로 보낼 글. 프롬프트가 붙으므로 도메인이다.

## 🔴 메일이 아니라 링크드인 메시지다

노션의 그물망 문안은 **메일용**이다 — 제목 줄, 회사 소개 문단, BCC 규칙이 들어 있다.
링크드인엔 그게 안 맞는다.

    메일        제목 있음 · 여러 문단 · BCC · 길이 제한 없음
    링크드인     제목 없음 · 짧아야 함 · BCC 개념 없음
                🔴 연결 요청 메모는 300자 상한 (플랫폼이 정한다)

그래서 **두 벌을 만든다.** 상대와 연결돼 있는지 우리는 알 수 없으므로,
심사역이 상황에 맞는 쪽을 골라 쓴다.

## 🔴 우리가 보내지 않는다

심사역 개인 계정으로 나가는 메시지다. 그 계정에 우리가 못 들어간다 —
메일 트리아지가 hello@ 초안을 만들 수 있는 것과 다르다.
그래서 **글만 써서 슬랙에 올리고, 사람이 복사해서 보낸다.**
"""
from __future__ import annotations

from pathlib import Path

from kvlib import Fetched, constraints
from kv_transport import claude

_SYSTEM = (
    "너는 벤처캐피털 심사역이 창업자에게 처음 보내는 링크드인 메시지를 쓴다. "
    "과장하지 않고, 상대가 실제로 한 일을 근거로 쓴다. "
    "설명이나 인사말을 쓰지 말고 요구된 형식만 출력한다."
)


def _line(p: dict) -> str:
    """한 사람을 한 줄로. 탭·줄바꿈은 지운다 — TSV 를 깨뜨린다."""
    def flat(s, cap):
        return " ".join(str(s or "").split())[:cap]
    return "\t".join((
        p["key"], flat(p.get("name"), 60), flat(p.get("one_liner"), 120),
        flat(p.get("headline"), 120), flat(p.get("reason"), 300),
    ))


def write(people: list[dict], sender: str) -> Fetched:
    """반환 [{key, short, long}]. `sender` 는 발신할 심사역 성함."""
    if not people:
        return Fetched.measured([], got=0, dlq=0)

    cap = constraints.get("linkedin.connect_note_chars")
    batch = constraints.get("linkedin.outreach_batch")
    guide = (Path(__file__).parent / "outreach.md").read_text(encoding="utf-8")
    valid = {p["key"] for p in people}
    good, bad = [], []

    for i in range(0, len(people), batch):
        chunk = people[i:i + batch]
        prompt = (
            f"{guide}\n\n"
            f"# 발신자\n{sender} 심사역\n\n"
            f"# 대상 {len(chunk)}명\n"
            "형식: key⇥이름⇥한줄소개⇥헤드라인⇥우리가 주목한 이유\n"
            + "\n".join(_line(p) for p in chunk)
            + f"\n\n# 출력\n정확히 {len(chunk)}줄. `key<TAB>짧은버전<TAB>긴버전`.\n"
              f"짧은버전은 공백 포함 {cap}자 이내. 줄바꿈 대신 문장을 이어 쓴다.\n"
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
            short = parts[1]
            # 🔴 상한을 넘으면 자르지 않고 DLQ 로 보낸다. 잘라내면 문장이 끊긴 채
            #    심사역에게 가고, 그걸 그대로 보내면 상대가 반쪽짜리 메시지를 받는다.
            if len(short) > cap:
                bad.append({"line": line[:300], "why": f"짧은버전이 {len(short)}자 (상한 {cap})"})
                continue
            good.append({"key": key, "short": short, "long": " ".join(parts[2:]).strip()})

    if bad:
        claude.dlq("linkedin", "outreach_dlq", bad)
        print(f"  ⚠️  검증 실패 {len(bad)}줄 → DLQ (나머지 {len(good)}줄은 살린다)")
    if not good:
        return Fetched.unknown("검증 통과 0줄")
    seen, uniq = set(), []
    for g in good:
        if g["key"] not in seen:
            seen.add(g["key"]); uniq.append(g)
    return Fetched.measured(uniq, got=len(uniq), dlq=len(bad))
