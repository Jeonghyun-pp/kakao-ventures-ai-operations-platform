"""[W9 재설계 Batch A] 동시성 랩 — **검증 사각 ①** 을 닫는다.

## 왜 있나

합성 12본은 전부 **단일 프로세스 순차 실행**이다. 그래서 W9 리뷰의 high 4건(A-H1·H2·H3·H4)을
원리적으로 못 봤다 — 그 결함들은 "두 주체가 같은 레코드를 동시에 만진다"에서만 나타난다.
실환경의 두 주체는 **10분 launchd 틱**과 **사람의 CLI/슬랙**이고, 둘은 다른 프로세스다.

여기서는 진짜로 프로세스를 띄운다(`sys.executable __file__ --child …`). 스레드가 아니다 —
GIL 아래 스레드는 파일 읽기·쓰기 사이의 창을 실환경만큼 벌리지 못한다.

## 지키는 불변식

    C1  lost update 없음      서로 다른 필드를 동시에 써도 둘 다 살아남는다
    C2  announce ↔ decide     느린 슬랙 POST 중 내려진 결정이 지워지지 않는다
    C3  no-approval-no-execute 실행 중 거부가 유실되지 않는다
    C4  first-decision-wins   동시 승인/거부에서 결정은 하나로 수렴하고 파일은 온전하다

## 수정 전 코드에서의 관측 (2026-08-07 이전, `approval.save(r)` 스냅샷 저장 시절)

    C1 ❌ 나중 쓰기가 앞 쓰기를 통째로 덮어 한쪽 필드가 사라짐
    C2 ❌ 최종 decision=None — 결정이 지워지고 미결로 되돌아감(리뷰 프로브 P6 재현)
    C4 ❌ 두 프로세스가 서로 다른 최종값을 읽음

실행: KV_DATA=<스크래치> python kv-core/tests/_concurrency_lab.py
외부 부작용 0 — gmail·slack 전부 스텁 + KV_NO_SEND.
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kv_core import approval, approvals_tick, gmail, notify, slackbot  # noqa: E402

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


def spawn(role: str, key: str, *args: str) -> subprocess.Popen:
    """자식 프로세스 기동 — 진짜 동시성을 위해 별도 인터프리터를 띄운다."""
    return subprocess.Popen([sys.executable, __file__, "--child", role, key, *args],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


# ══════════════════════════════════════════════════════════════════════
#  자식 역할 — 부모가 --child 로 재호출한다
# ══════════════════════════════════════════════════════════════════════

def child_main(role: str, key: str, rest: list[str]) -> int:
    if role == "slow-announce":
        # 느린 슬랙 POST 를 흉내낸다(실환경 타임아웃 15초). post 가 도는 동안 부모가 결정한다.
        def _slow_post(text: str):
            time.sleep(float(rest[0]) if rest else 1.2)
            return ("1234.5678", "C-TEST")
        slackbot.post = _slow_post
        approvals_tick.slackbot = slackbot
        notify.slack = lambda *a, **k: True
        approvals_tick._announce()
        return 0

    if role == "slow-execute":
        # create_draft 가 초 단위로 걸리는 동안 부모가 거부한다.
        def _slow_draft(to, subject, body_text, **kw):
            time.sleep(float(rest[0]) if rest else 1.2)
            return "draft-slow-1"
        gmail.create_draft = _slow_draft
        gmail.get_draft = lambda did, **kw: {"id": did}
        notify.slack = lambda *a, **k: True
        approvals_tick._execute()
        return 0

    if role == "decide":
        time.sleep(float(rest[1]) if len(rest) > 1 else 0.0)
        r = approval.decide(key, rest[0], by="child")
        print(json.dumps({"decision": (r or {}).get("decision")}, ensure_ascii=False))
        return 0

    if role == "hammer-update":
        # 같은 레코드의 **다른 필드**를 반복 갱신 — lost update 탐지용
        field, n = rest[0], int(rest[1])
        for i in range(n):
            approval.update(key, **{field: i})
        return 0

    print(f"unknown role {role}", file=sys.stderr)
    return 2


if len(sys.argv) > 1 and sys.argv[1] == "--child":
    raise SystemExit(child_main(sys.argv[2], sys.argv[3], sys.argv[4:]))


# ══════════════════════════════════════════════════════════════════════
#  부모 — 시나리오
# ══════════════════════════════════════════════════════════════════════

notify.slack = lambda *a, **k: True
slackbot.post = lambda text: ("1.0", "C-TEST")
slackbot.message_reactions = lambda ch, ts: None

print("=== C1: lost update — 서로 다른 필드를 동시에 써도 둘 다 남는가 ===\n")
# 수정 전(`save(record)` 전체 덮어쓰기): 나중 쓰기가 앞의 필드를 통째로 지웠다.
# 반복 횟수는 **CI 러너에서 실제로 깨진 값(40회)보다 크게** 잡는다. 40회일 때 로컬(맥)은
# 통과하고 GH 러너만 `fieldA=False` 로 실패했다 — 창이 작아진 것이지 없어진 게 아니었고,
# 느린 환경에서만 드러나는 결함을 빠른 환경에서 초록으로 확인하는 것이 바로 W9 의 사고다.
approval.request("cc-1", "judgment", {"text": "t"}, dedup_key="cc-1")
p1 = spawn("hammer-update", "cc-1", "fieldA", "150")
p2 = spawn("hammer-update", "cc-1", "fieldB", "150")
p1.wait(); p2.wait()
# 자식이 죽지 않았는지도 본다 — `atomic_write` 의 tmp 이름이 고정이던 시절 동시 writer 가
# 서로의 tmp 를 rename 해가서 `FileNotFoundError` 로 **크래시**했다(프로브로 재현).
check("C1 동시 writer 가 서로를 크래시시키지 않음",
      p1.returncode == 0 and p2.returncode == 0,
      f"rc={p1.returncode}/{p2.returncode}")
r = approval.load("cc-1")
check("C1 두 필드가 모두 살아남음", r is not None and "fieldA" in r and "fieldB" in r,
      f"fieldA={'fieldA' in (r or {})} fieldB={'fieldB' in (r or {})}")
check("C1 원본 필드 보존", bool(r) and r.get("kind") == "judgment" and r.get("key") == "cc-1")

print("\n=== C2: announce ↔ decide — 느린 POST 중 내려진 결정이 지워지는가 ===\n")
# 수정 전: `undecided()` 스냅샷 → post(15초) → `r["slack_ts"]=…; save(r)` 로 decision 이 소멸.
approval.request("cc-2", "judgment", {"text": "t"}, dedup_key="cc-2")
child = spawn("slow-announce", "cc-2", "1.2")
time.sleep(0.45)                                    # POST 가 진행 중인 창
approval.decide("cc-2", "approved", by="parent-cli")
child.wait()
r = approval.load("cc-2")
check("C2 결정이 살아남음", bool(r) and r.get("decision") == "approved", str((r or {}).get("decision")))
check("C2 slack_ts 도 기록됨", bool(r) and bool(r.get("slack_ts")), str((r or {}).get("slack_ts")))

print("\n=== C3: 실행 중 거부 — no-approval-no-execute 가 경합에서 버티는가 ===\n")
approval.request("cc-3", "mail", {"to": "a@x", "subject": "s", "body": "b"},
                 partner="evan", dedup_key="cc-3")
approval.decide("cc-3", "approved", by="parent")
child = spawn("slow-execute", "cc-3", "1.2")
time.sleep(0.45)                                    # create_draft 진행 중
approval.update("cc-3", decision="rejected", decided_at="race", decided_by="parent-cli")
child.wait()
r = approval.load("cc-3") or {}
check("C3 거부가 유실되지 않음", r.get("decision") == "rejected", str(r.get("decision")))
# 잔여 창: create_draft 도중의 거부는 초안 생성 자체를 막지 못한다(Gmail 호출이 이미 떠났다).
# 그 경우 초안 id 를 `draft_id` 로 확정하면 "거부됐는데 실행완료"가 된다 — 고아 초안으로 분리 표기해야 한다.
orphan = r.get("orphan_draft_id")
check("C3 거부건이 executed 로 확정되지 않음", r.get("status") != "executed", str(r.get("status")))
if r.get("draft_id"):
    check("C3 거부 후 만들어진 초안은 고아로 표기됨", bool(orphan),
          f"draft_id={r.get('draft_id')} orphan={orphan}")

print("\n=== C4: first-decision-wins — 동시 승인/거부가 하나로 수렴하는가 ===\n")
inconsistent = 0
torn = 0
for i in range(8):
    k = f"cc-4-{i}"
    approval.request("cc-4", "judgment", {"text": "t"}, dedup_key=k)
    a = spawn("decide", k, "approved", "0.0")
    b = spawn("decide", k, "rejected", "0.0")
    a.wait(); b.wait()
    try:
        final = (approval.load(k) or {}).get("decision")
    except Exception:                                # noqa: BLE001
        torn += 1
        continue
    outs = []
    for p in (a, b):
        line = (p.stdout.read() or "").strip().splitlines()
        outs += [ln for ln in line if ln.startswith("{")]
    seen = {json.loads(o).get("decision") for o in outs if o}
    if final not in ("approved", "rejected"):
        inconsistent += 1
    elif seen and seen != {final}:
        # 두 프로세스가 서로 다른 최종값을 봤다 = 결정이 하나로 수렴하지 않았다
        inconsistent += 1
check("C4 파일 파손 0", torn == 0, f"torn={torn}")
check("C4 결정이 하나로 수렴 (8회)", inconsistent == 0, f"불일치={inconsistent}")

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 동시성 랩 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 동시성 랩 전부 통과")
