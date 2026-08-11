"""[W10] 승격 게이트 계약 — 무수정률 측정이 **정직한가**.

이 게이트는 이 시스템에서 가장 비가역적인 행위(메일 발송)를 켜는 문턱이다.
그래서 두 방향의 거짓을 다 막아야 한다:

    너무 엄격  서명·줄바꿈 차이를 '수정'으로 세면 무수정률이 0 에 수렴해 **영영 안 열린다**
    너무 관대  실제 문장 변경을 놓치면 **준비 안 된 채로 열린다** — 회수 불가

    G1  서명·인용·공백 차이는 수정이 아니다
    G2  실제 문장 추가/삭제는 수정이다
    G3  n < 5 면 비율과 무관하게 자격 없음
    G4  비율 미달이면 n 과 무관하게 자격 없음
    G5  둘 다 충족해야 자격 — 그리고 **이 모듈은 켜지 않는다**(재서 보고만)
    G6  보낸 기록을 못 찾은 건은 '무수정'으로 세지 않는다(침묵을 성공으로 오독 금지)

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_promotion.py
외부 호출 0 — records·sent 를 주입한다(네트워크·시간 의존 없음).
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import ast  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import promotion as P  # noqa: E402

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


BODY = ("안녕하세요, 대표님. 카카오벤처스입니다. 저희가 신규 서비스에 관심을 갖고 지켜보던 중 "
        "인상적으로 접하게 되어 연락드렸습니다. 미팅을 요청드립니다. 감사합니다.")


def rec(i: int, body: str = BODY) -> dict:
    return {"key": f"k{i}", "kind": "mail", "draft_id": "d", "executed_at": "2026-08-10",
            "payload": {"subject": f"[카카오벤처스] 팀{i} 미팅 요청의 건", "body": body}}


def msg(i: int, body: str) -> dict:
    return {"id": str(i), "to": "person-093bbe143bc9@example.com", "date": "",
            "subject": f"[카카오벤처스] 팀{i} 미팅 요청의 건", "body": body}


# ══════════════════════════════════════════════════════════════════════
print("=== G1: 서명·인용·공백은 수정이 아니다 ===\n")
noise = [
    BODY + "\n--\n카카오벤처스 드림",                       # 서명
    BODY.replace(". ", ".\n\n"),                             # 줄바꿈만 다름
    BODY + "\n\n> 이전 메일 인용\n> 두 번째 줄",              # 인용부
    "  " + BODY + "   ",                                     # 앞뒤 공백
    BODY.replace(" ", "  "),                                 # 연속 공백
]
v = P.measure(records=[rec(i) for i in range(5)],
              sent=[msg(i, b) for i, b in enumerate(noise)])
check("G1 서명·줄바꿈·인용·공백 차이 5건이 전부 무수정", v.unmodified == 5,
      f"무수정 {v.unmodified}/5 · 유사도 {[round(p.similarity, 3) for p in v.pairs]}")

print("\n=== G2: 실제 문장 변경은 수정이다 ===\n")
edits = [
    BODY + " 지난번 데모를 인상 깊게 봤습니다.",              # 추가
    BODY.replace("미팅을 요청드립니다.", ""),                 # 삭제
    BODY.replace("카카오벤처스입니다", "카카오벤처스 김철수입니다"),  # 치환
]
v2 = P.measure(records=[rec(i) for i in range(3)],
               sent=[msg(i, b) for i, b in enumerate(edits)])
check("G2 추가·삭제·치환 3건이 전부 수정으로 잡힘",
      sum(1 for p in v2.pairs if p.modified) == 3,
      f"수정 {sum(1 for p in v2.pairs if p.modified)}/3")
check("G2 무엇이 바뀌었는지 보여준다(사람이 룰을 고칠 수 있게)",
      all(p.diff_sample for p in v2.pairs if p.modified))

print("\n=== G3/G4/G5: 자격 판정은 두 조건을 **모두** 본다 ===\n")
# 표본 부족 — 비율은 100% 지만 n=3
v3 = P.measure(records=[rec(i) for i in range(3)],
               sent=[msg(i, BODY) for i in range(3)])
check("G3 n<5 면 비율 100% 여도 자격 없음", v3.rate == 1.0 and not v3.eligible,
      f"n={v3.n} rate={v3.rate}")
# 비율 미달 — n 은 충분
v4 = P.measure(records=[rec(i) for i in range(10)],
               sent=[msg(i, BODY if i < 8 else BODY + " 덧붙임.") for i in range(10)])
check("G4 비율 미달이면 n 충분해도 자격 없음", v4.n == 10 and not v4.eligible,
      f"n={v4.n} rate={v4.rate * 100:.0f}%")
# 둘 다 충족
v5 = P.measure(records=[rec(i) for i in range(6)],
               sent=[msg(i, BODY + "\n--\n카카오벤처스 드림") for i in range(6)])
check("G5 둘 다 충족하면 자격 있음", v5.eligible, f"n={v5.n} rate={v5.rate * 100:.0f}%")

# 이 모듈이 무언가를 **켜지 않는지** 구조로 확인 — 자격 판정과 실행은 분리돼야 한다
src = Path(P.__file__).read_text(encoding="utf-8")
writers = []
for node in ast.walk(ast.parse(src)):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr in ("send", "create_draft", "write_text", "atomic_write", "update"):
            writers.append(f"{node.func.attr}@L{node.lineno}")
check("G5 승격 모듈은 아무것도 켜거나 쓰지 않는다(재서 보고만)", not writers, str(writers))

print("\n=== G6: 대조 실패를 성공으로 세지 않는다 ===\n")
# 초안 6건 중 2건은 보낸 기록이 없다(아직 안 보냄) — 그 2건이 무수정으로 세어지면 안 된다
v6 = P.measure(records=[rec(i) for i in range(6)],
               sent=[msg(i, BODY) for i in range(4)])
check("G6 못 찾은 건은 대조 모수에서 빠진다", v6.n == 4, f"n={v6.n} (4 기대)")
check("G6 못 찾은 건을 별도로 보고한다", len(v6.unmatched) == 2, str(v6.unmatched))
check("G6 그래서 표본 부족으로 자격 없음", not v6.eligible)

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 승격 게이트 계약 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 승격 게이트 계약 전부 통과")
