"""처리 이력 — 같은 메일에 초안을 두 번 만들지 않기 위해.

액션을 **실행한 직후 즉시** 기록하고, 매 실행은 기록된 id를 건너뛴다.

## 🔴 [2026-08-13 W12] 이 파일은 자기가 피하겠다던 결함을 형태만 바꿔 갖고 있었다

원래 독스트링은 *"bplan-automation의 알려진 결함(비원자적 쓰기 → 재실행 시 중복 생성)을
여기서 되풀이하지 않는다"* 였다. 그런데 실제로는 셋 다 같은 계열이었다:

    ① `except Exception: return {}`  — 손상을 **조용히** 삼킨다. 파일이 깨지면 전 메일이
       '미처리'로 보이고 **초안이 전부 다시 만들어진다**(= 중복 생성. 피하겠다던 그것이다).
    ② `write_text()`                 — 원자적이 아니다. 쓰다 죽으면 반쯤 망가진 파일이 남고
       ①이 그걸 `{}` 로 만든다. 두 결함이 맞물려 **재실행 = 전량 중복**이 된다.
    ③ 락 없는 read-merge-write       — 두 프로세스가 겹치면 한쪽 기록이 통째로 사라진다
       (`scheduler.update_watermark` 가 W12 에 flock 으로 고친 것과 같은 형태).

`mail-triage` 는 2026-08-12 에 배선돼 **매일 무인으로 돈다**. 지금은 분류만 하지만
컷오버 후엔 초안까지 가므로, 그때 이 셋이 그대로면 중복 초안이 나간다.

세 가지를 `kv_core` 의 기존 규율로 바꾼다 — 새로 만들지 않는다. 같은 문제를 이미 세 번
풀었고 그 답이 플랫폼에 있다.
"""
from __future__ import annotations

import fcntl
import json
import time
from datetime import datetime, timezone

from kv_core import job, paths

STORE = paths.data("mail-harness", "triage") / "processed.json"

# 프로세스 안에서 캐시한다. `seen()` 이 메시지마다 파일 전체를 읽던 것을 없앤다(O(n²)).
# 쓰기 때 무효화하므로 같은 프로세스 안에서는 항상 최신이다.
_CACHE: dict | None = None


def load(*, refresh: bool = False) -> dict:
    """처리 이력. **손상을 조용히 삼키지 않는다.**

    부재 = `{}`(정상, 첫 실행) · 손상 = 🔴 + `.corrupt-<ts>` 로 **보존** 후 `{}`.
    손상을 조용히 `{}` 로 돌려주면 그 순간 **전 메일이 미처리로 보인다** — 그게
    이 파일이 막겠다고 선언한 바로 그 사고다.
    """
    global _CACHE
    if _CACHE is not None and not refresh:
        return _CACHE
    if not STORE.exists():
        _CACHE = {}
        return _CACHE
    try:
        _CACHE = json.loads(STORE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        bak = STORE.with_suffix(f".json.corrupt-{int(time.time())}")
        try:
            STORE.rename(bak)                      # 지우지 않는다 — 복구의 유일한 근거
        except OSError:
            bak = None
        print(f"🔴 트리아지 처리이력 손상: {e}\n"
              f"   **전 메일이 '미처리'로 보인다** — 이 상태로 진행하면 이미 처리한 메일에\n"
              f"   초안이 다시 만들어진다. 원본 보존: {bak or '보존 실패'}\n"
              f"   복구: 보존 파일에서 살릴 수 있는 항목을 골라 되돌려라.")
        _CACHE = {}
    except OSError as e:
        print(f"🔴 트리아지 처리이력을 못 읽는다: {e} — 마운트·권한을 확인하라. "
              f"이 상태로 진행하면 전 메일이 재처리된다.")
        _CACHE = {}
    return _CACHE


def mark(msg_id: str, category: str, action: str, detail: str = "") -> None:
    """처리 기록 — **flock + 원자적 쓰기.**

    ⚠️ 읽기-병합-쓰기를 락 안에서 한다. 밖에서 읽고 쓰면 두 프로세스가 겹칠 때
       한쪽 기록이 통째로 사라지고(lost update), 사라진 그 메일은 다음 실행에
       **초안을 다시 받는다.**
    """
    global _CACHE
    STORE.parent.mkdir(parents=True, exist_ok=True)
    lock = STORE.with_suffix(".lock")
    with open(lock, "a+") as lf:
        fcntl.flock(lf.fileno(), fcntl.LOCK_EX)    # 프로세스가 죽으면 커널이 푼다
        try:
            data = load(refresh=True)              # 락 안에서 다시 읽는다
            data[msg_id] = {
                "category": category,
                "action": action,
                "detail": detail,
                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            # `write_text` 가 아니라 atomic_write — 쓰다 죽어도 반쪽 파일이 안 남는다.
            job.atomic_write(STORE, json.dumps(data, ensure_ascii=False, indent=2))
            _CACHE = data
        finally:
            fcntl.flock(lf.fileno(), fcntl.LOCK_UN)


def seen(msg_id: str) -> bool:
    return msg_id in load()
