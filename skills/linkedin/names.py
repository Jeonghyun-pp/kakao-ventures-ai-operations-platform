"""링크드인 이름 → 슬랙에 바로 쓸 한글 이름. 두 벌을 돌려준다.

    전체   `김가람`  리마인드("김가람(Aco) 님께 연락")
    이름   `가람`    연락 문안 인사("안녕하세요 가람님") — 실무 문안이 성을 빼고 부른다

링크드인 이름은 세 모양으로 온다 (2026-09-29 실측: `가람 김` · `Minsoo Kim`).

    한글, 성이 뒤      `가람 김`       → 규칙으로 뒤집는다 (모델 없음)
    영문 표기          `Minsoo Kim`  → claude. Jiwoo=지우/지후, Lee=이/리 처럼 규칙으로 못 정한다
    한국인이 아님       `John Smith`    → 그대로 둔다

🔴 틀린 이름으로 연락이 나가는 게 원문을 보여주는 것보다 나쁘다. 확신이 없으면 `sure=False` 로 돌려주고,
   호출부가 원문을 옆에 적어 사람이 알아보게 한다. 모델이 죽어도 원문으로 돌려준다 — 리마인드는 이름보다 중요하다.
"""
from __future__ import annotations

import re

from kv_transport import claude

_HANGUL = re.compile(r"^[가-힣]+$")
# 두 글자 성. 네 글자 이름에서 성을 뗄 때만 본다 — 목록에 없으면 한 글자 성으로 보고 확신을 내린다
_SURNAME2 = ("남궁", "제갈", "선우", "황보", "독고", "사공", "서문", "동방", "어금", "망절")

_SYSTEM = "너는 링크드인 영문 이름을 한국어 이름 표기로 옮긴다. 설명 없이 요구된 형식만 출력한다."


def _given(full: str) -> tuple[str, bool]:
    """한글 전체 이름 → 성을 뺀 이름."""
    if len(full) == 4:
        return (full[2:], True) if full[:2] in _SURNAME2 else (full[1:], False)
    return (full[1:], True) if len(full) in (2, 3) else (full, False)


def _rule(name: str) -> tuple[str, str, bool] | None:
    """한글만으로 된 이름은 규칙으로. 못 정하면 None(→ 모델)."""
    toks = name.split()
    if not toks or not all(_HANGUL.match(t) for t in toks):
        return None
    if len(toks) == 2 and (len(toks[0]) == 1 or len(toks[1]) == 1):
        first, last = toks                     # `가람 김` · `최 가람` — 한 글자 토막이 성
        sur, given = (last, first) if len(last) == 1 else (first, last)
        return sur + given, given, True
    if len(toks) == 1:
        given, ok = _given(toks[0])
        return toks[0], given, ok
    return "".join(toks), toks[-1], False      # `남궁 민수` / `민수 남궁` — 어느 쪽이 성인지 모른다


def to_korean(people: list[dict]) -> dict[str, tuple[str, str, bool]]:
    """[{key, name, headline}] → {key: (전체, 성 뺀 이름, 확신)}. 모든 key 를 돌려준다."""
    out: dict[str, tuple[str, str, bool]] = {}
    ask = []
    for p in people:
        name = " ".join(str(p.get("name") or "").split())
        r = _rule(name)
        if r:
            out[p["key"]] = r
        else:
            out[p["key"]] = (name, (name.split() or [name])[0], False)   # 모델이 못 답해도 원문은 간다(영문은 first name)
            ask.append((p["key"], name, " ".join(str(p.get("headline") or "").split())[:160]))
    if not ask:
        return out
    prompt = (
        "링크드인 이름을 한국 사람이 슬랙에서 부를 한글 이름으로 옮겨라. 전체(성+이름, 붙여 씀)와 성을 뺀 이름 두 벌.\n"
        "헤드라인에 한글 이름이 있으면 그것을 따른다.\n"
        "한국인 이름이 아니면 전체는 원문 그대로, 이름은 first name, 확신은 N.\n"
        "표기가 여럿 가능하면(예: Jiwoo 지우/지후) 가장 흔한 것을 쓰고 확신은 N.\n\n"
        "형식: key⇥이름⇥헤드라인\n"
        + "\n".join("\t".join(a) for a in ask)
        + f"\n\n# 출력\n정확히 {len(ask)}줄. `key<TAB>전체<TAB>성뺀이름<TAB>Y|N`.\n"
    )
    try:
        raw = claude.run_session(prompt, system=_SYSTEM, tools="")
    except claude.SessionError as e:
        print(f"  ⚠️  이름 변환 실패({e}) — 원문으로 보낸다")
        return out
    asked = {k for k, _, _ in ask}
    for line in raw.splitlines():
        parts = [x.strip() for x in line.split("\t")]
        if len(parts) == 4 and parts[0] in asked and parts[1] and parts[2]:
            out[parts[0]] = (parts[1], parts[2], parts[3].upper() == "Y")
    return out
