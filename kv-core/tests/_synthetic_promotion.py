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

적대 리뷰(2026-08-11)가 뚫은 구멍들 — 전부 "게이트가 **열리는** 쪽"으로 틀렸다:

    G7  하나의 발송이 여러 초안의 짝이 될 수 없다(1:1 소비)
    G8  본문을 못 얻은 건은 무수정이 아니라 **측정 불가**다
    G9  날짜·금액·부정 전환은 유사도가 높아도 수정이다
    G10 보낸편지함 조회가 잘렸으면 측정 자체가 무효다
    G11 초안 생성 **이전**에 나간 메일은 짝이 아니다
    G12 자격은 경로별로 본다 — 템플릿 경로 하나로 전체 자격이 생기지 않는다

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


def rec(i: int, body: str = BODY, task: str = "meeting-mail") -> dict:
    return {"key": f"k{i}", "kind": "mail", "draft_id": "d", "task": task,
            "decision": "approved", "executed_at": "2026-08-10",
            "payload": {"subject": f"[카카오벤처스] 팀{i} 미팅 요청의 건", "body": body,
                        "founder_email": "person-093bbe143bc9@example.com"}}


def msg(i: int, body: str) -> dict:
    return {"id": str(i), "to": "person-093bbe143bc9@example.com", "date": "Tue, 11 Aug 2026 09:00:00 +0900",
            "body_ok": True,
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

print("\n=== G7~G12: 게이트가 **열리는 쪽**으로 틀리지 않는다 ===\n")

# G7 같은 제목·수신자로 5건 초안, 실제로 그대로 나간 건 1건뿐
same = [{**rec(0), "key": f"k{i}"} for i in range(5)]
g7 = P.measure(records=same,
               sent=[msg(0, BODY)] + [{**msg(0, "전혀 다른 본문을 새로 썼습니다."), "id": str(i)}
                                      for i in range(1, 5)])
check("G7 발송 1건이 초안 5건의 짝이 되지 않는다", g7.unmodified <= 1,
      f"무수정 {g7.unmodified} (≤1 기대)")
check("G7 짝짓기가 모호하면 자격을 주지 않는다", not g7.eligible and g7.ambiguous_n > 0,
      f"모호 {g7.ambiguous_n}")

# G8 본문이 비었거나 디코드 실패
g8 = P.measure(records=[rec(0, "")] + [rec(i) for i in range(1, 6)],
               sent=[{**msg(0, ""), "body_ok": False}] + [msg(i, BODY) for i in range(1, 6)])
check("G8 빈 본문은 무수정으로 세지 않는다", g8.n == 5 and len(g8.invalid) == 1,
      f"n={g8.n} 측정불가={g8.invalid}")

# G9 유사도는 높은데 의미 토큰이 바뀐 경우 — 사람이 고치는 것 대부분이 이 형태다
SEM = BODY + " 8월 20일 오후 2시, 5억 규모로 논의드리면 좋겠습니다."
sems = [SEM.replace("8월 20일", "8월 27일"), SEM.replace("5억", "50억"),
        SEM.replace("좋겠습니다", "어렵겠습니다"),
        SEM.replace("오후 2시", "오전 10시"), SEM + " 참고 부탁드립니다."]
g9 = P.measure(records=[rec(i, SEM) for i in range(5)],
               sent=[msg(i, b) for i, b in enumerate(sems)])
check("G9 날짜·금액·부정 전환은 유사도가 높아도 수정이다",
      sum(1 for x in g9.pairs if x.modified) >= 4,
      f"수정 {sum(1 for x in g9.pairs if x.modified)}/5 · "
      f"유사도 {[round(x.similarity, 2) for x in g9.pairs]}")

# G10 조회가 상한에서 잘림
g10 = P.measure(records=[rec(i) for i in range(6)],
                sent=[msg(i, BODY) for i in range(6)], truncated=True)
check("G10 조회가 잘리면 rate 100% 여도 자격 없음", g10.rate == 1.0 and not g10.eligible)
check("G10 잘림을 표본 부족이 아니라 **측정 무효**로 보고한다",
      "측정 무효" in g10.report())

# G11 초안 생성보다 **앞선** 발송 — 같은 창업자에게 전에 보낸 다른 메일이 짝이 될 수 없다
g11 = P.measure(records=[rec(i) for i in range(5)],
                sent=[{**msg(i, BODY), "date": "Mon, 03 Aug 2026 09:00:00 +0900"}
                      for i in range(5)])
check("G11 초안 이전에 나간 메일은 짝이 아니다", g11.n == 0 and len(g11.unmatched) == 5,
      f"n={g11.n}")

# G12 템플릿 경로(비플랜 드랍)는 구조적으로 무수정 100% — 그걸로 전체 자격이 생기면 안 된다
g12 = P.measure(
    records=[rec(i, task="bplan-drop") for i in range(6)]
            + [rec(i + 100, task="meeting-mail") for i in range(2)],
    sent=[msg(i, BODY) for i in range(6)] + [msg(i + 100, BODY) for i in range(2)])
check("G12 경로별로 본다 — 쉬운 경로 하나로 전체 자격이 생기지 않는다", not g12.eligible,
      f"by_task={g12.by_task}")
check("G12 경로별 내역을 보여준다", len(g12.by_task) == 2, str(g12.by_task))

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 승격 게이트 계약 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 승격 게이트 계약 전부 통과")
