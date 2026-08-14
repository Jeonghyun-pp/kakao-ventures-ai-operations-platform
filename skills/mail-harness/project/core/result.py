"""하네스 결과 마커 — **생산자와 소비자가 보는 한 곳.**

## 왜 별도 모듈인가

`harness.py` 는 각 단계를 서브프로세스로 돌리고 그 stdout 에서 이 마커를 찾아 결과를 얻는다.
즉 **문자열 하나에 대한 합의**가 두 프로세스 사이에 걸려 있다. 합의가 깨지면:

    result is None and name not in ("triage", "apply")  →  status: "fail"
                                                            ^^^^^^^^^^^^^^
그 단계는 **성공해도 실패로 보고된다**(rc=0 인데 fail). 그리고 다이제스트의 해당 칸이 빈다.

🔴 2026-08-14 실측: W12 재빌드가 `bplan/main.py` 의 `emit_result` 를 새 잡으로 옮기지
   않았다. `harness.py:140,172` 는 새 잡을 `bplan_sync`·`bplan_drop` 이름으로 부르므로
   위 조건에 정확히 걸린다 — **두 단계가 항상 fail 로 찍히고**(note: `rc=0`),
   `rec["bplan_sync"]`·`rec["bplan_drop"]` 이 None 이 되어 다이제스트에서 비플랜 숫자가
   통째로 사라진다. 잡 자체는 멀쩡히 돌았는데도.

   같은 계열의 사고를 그날 이미 하나 고쳤다(sync 가 안 붙인 라벨을 drop 이 찾던 것 →
   `core/labels.py`). **한 프로세스의 출력이 다른 프로세스의 입력일 때, 그 약속은
   양쪽이 import 하는 한 곳에 있어야 한다.** 여기가 그 자리다.

## 계약

    생산자  emit("bplan-sync", ok=True, created=3, ...)   → stdout 한 줄
    소비자  PATTERN.finditer(out) 의 **마지막** 매치를 쓴다(단계가 여러 번 찍을 수 있다)

`ok` 는 특별하다 — `harness.py` 가 `result.get("ok", True)` 로 읽어 실패 판정에 쓴다.
성공 시 굳이 안 넣어도 되지만, **넣는 편이 낫다**(빠뜨린 것과 참인 것이 구분된다).
"""
from __future__ import annotations

import json
import re
import sys

MARKER = "@@HARNESS_RESULT@@"
PATTERN = re.compile(re.escape(MARKER) + r" (\{.*\})")


def emit(kind: str, **summary) -> None:
    """결과 한 줄을 stdout 에 찍는다. **flush 한다** — 서브프로세스가 죽어도 남아야 한다."""
    print(f"{MARKER} {json.dumps({'kind': kind, **summary}, ensure_ascii=False)}",
          flush=True)


def parse_last(out: str) -> dict | None:
    """출력에서 **마지막** 결과 줄. 없으면 None — 그 자체가 신호다(위 독스트링 참조)."""
    m = None
    for m in PATTERN.finditer(out):
        pass
    if m is None:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        # 조용히 None 을 주면 "안 찍었다"와 "찍었는데 깨졌다"가 같아진다. 다른 사실이다.
        print(f"🔴 결과 줄을 못 읽었다: {e} — {m.group(1)[:200]}", file=sys.stderr)
        return {"ok": False, "halted": f"결과 파싱 실패: {e}"}
