"""메일 분류 — 규칙이 못 잡은 것만 claude 가 본다. 프롬프트가 붙으므로 도메인이다.

## 🔴 옛 시스템은 이 분류기가 꺼져 있었다

`ANTHROPIC_API_KEY` 가 없어서 `llm.enabled: false` 였고, 그래서 **규칙이 못 잡은 건
전부 UNKNOWN 이 되어 사람에게 갔다.** 분류기가 있다고 문서에 적혀 있는데 동작하지 않는,
정확히 "선언은 있는데 발동하지 않는 가드"였다.

우리는 `claude -p` 구독 세션으로 부른다 — API 키도, 돈도 안 든다.

## claude 는 규칙을 못 덮는다

규칙이 확정한 건 여기 오지도 않는다. claude 는 **남은 것만** 본다.
확실한 것(비플랜 접수·웹플로우·사칭·대량발송·광고)의 판정 근거를 모델의 그날 컨디션에
맡기지 않는다.
"""
from __future__ import annotations

from pathlib import Path

from kvlib import Fetched, constraints
from kv_transport import claude

from .rules import JUDGE_CATEGORIES

_SYSTEM = (
    "너는 카카오벤처스 공용 메일함의 분류기다. 각 메일을 정확히 하나의 카테고리로 분류한다. "
    "설명이나 인사말을 쓰지 말고 요구된 형식만 출력한다."
)


def _line(m: dict) -> str:
    """메일 하나를 한 줄로. 탭·줄바꿈은 지운다 — TSV 를 깨뜨린다."""
    sub_cap = constraints.get("gmail.subject_chars_for_judge")
    body_cap = constraints.get("gmail.body_chars_for_judge")

    def flat(s: str, cap: int) -> str:
        return " ".join((s or "").split())[:cap]

    return "\t".join((m["id"], flat(m.get("sender_raw") or m["sender"], 200),
                      flat(m["subject"], sub_cap), flat(m.get("body") or "", body_cap)))


def judge_mail(msgs: list[dict]) -> Fetched:
    """반환 [{id, category, reason}]. 한 줄씩 되읽어 검증하고 어긋난 줄만 DLQ 로."""
    if not msgs:
        return Fetched.measured([], got=0, dlq=0)

    criteria = (Path(__file__).parent / "criteria.md").read_text(encoding="utf-8")
    batch = constraints.get("gmail.judge_batch")
    valid = {m["id"] for m in msgs}
    good, bad, drift = [], [], []

    for i in range(0, len(msgs), batch):
        chunk = msgs[i:i + batch]
        prompt = (
            f"{criteria}\n\n"
            f"# 분류 대상 {len(chunk)}통\n"
            "형식: id⇥발신⇥제목⇥본문\n"
            + "\n".join(_line(m) for m in chunk)
            + f"\n\n# 출력\n정확히 {len(chunk)}줄. `id<TAB>카테고리<TAB>이유 한 문장`.\n"
        )
        try:
            raw = claude.run_session(prompt, system=_SYSTEM)
        except claude.SessionError as e:
            if not good:
                return Fetched.unknown(f"claude:{e}")
            print(f"  ⚠️  배치 {i // batch + 1} 실패({e}) — 앞 배치 결과는 살린다")
            break

        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "```")):
                continue
            parts = [x.strip() for x in line.split("\t")]
            if len(parts) < 2:
                bad.append({"line": line[:300], "why": "필드 부족"})
                continue
            # 🔴 위치가 아니라 **값**으로 카테고리를 찾는다.
            #    링크드인에서 실측(08-17): claude 가 입력 칸을 출력에 옮겨 열이 밀렸고,
            #    위치로 읽던 파서가 116줄(20%)을 버렸다. 판정은 멀쩡했고 파서가 틀렸다.
            ci = next((j for j, x in enumerate(parts[1:], 1) if x in JUDGE_CATEGORIES), None)
            mid = parts[0]
            if mid not in valid:
                bad.append({"line": line[:300], "why": "목록에 없는 id"})
            elif ci is None:
                bad.append({"line": line[:300], "why": f"카테고리 어휘가 줄 안에 없다: {parts[1:3]}"})
            else:
                if ci != 1:
                    drift.append(ci)
                good.append({"id": mid, "category": parts[ci],
                             "reason": " ".join(parts[ci + 1:]).strip()})

    if drift:
        print(f"  ⚠️  카테고리가 예상 위치(2번째)가 아닌 줄 {len(drift)}개 — 값으로 찾아 흡수했다. "
              f"프롬프트를 손볼 신호다")
    if bad:
        claude.dlq("mail", "judge_dlq", bad)
        print(f"  ⚠️  검증 실패 {len(bad)}줄 → DLQ (나머지 {len(good)}줄은 살린다)")
    if not good:
        return Fetched.unknown("검증 통과 0줄")

    seen, uniq = set(), []
    for g in good:
        if g["id"] not in seen:
            seen.add(g["id"]); uniq.append(g)
    return Fetched.measured(uniq, got=len(uniq), dlq=len(bad))
