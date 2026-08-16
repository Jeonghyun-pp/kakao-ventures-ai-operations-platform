#!/usr/bin/env python3
"""앱스토어 보드 스냅샷 **산출물 판정** — 진공(정당한 0)과 결손(고장난 0)을 가른다.

## 왜 이 파일이 생겼나 (2026-08-16 · 결정 ⑫)

이 판정은 `kv_core/workers.py` 안에 있었다. 254줄 중 85줄(33%)이 앱스토어 전용
지식이었다 — "차트는 84개다", "카지노 유료 차트가 비어 있는 건 정상이다", 파일명 패턴.

그런데 `kv-core` 는 [2]통로 + [3]컨트롤 플레인이고, 그 지식은 **[4] 스킬의 것**이다.
층이 섞이면 두 가지가 깨진다:

    · 애플이 차트를 84 → 90 으로 늘리면 **코어를 고쳐야 한다**(스킬만 고쳐선 안 된다)
    · 재빌드 계획이 *"[3]은 유지, [4]는 재빌드"* 인데 그 말이 참이 아니게 된다

## 🔴 왜 `board.py` 안이 아니라 **별도 파일**인가

원래 이 판정이 코어에 있던 이유가 주석에 남아 있었다:

> `board.py` 는 84차트 중 0개를 저장해도 exit 0 이다 — **rc 를 믿지 않고 산출물을 센다**

2026-07-19 에 앱스토어 수집이 DNS 실패로 전멸했는데 `✅ 0건` + 종료코드 0 으로 성공
보고한 사고가 있었다(전날은 10,112건). 그래서 **결과물을 만든 프로그램이 아닌 다른
프로그램이 세도록** 일부러 분리했다.

`board.py` 안으로 옮기면 **자기가 자기를 채점하게 되어** 그 방어가 사라진다.
그래서 스킬 안으로 옮기되 **파일은 따로 둔다** — 층은 [4]로 돌리고 독립 채점은 지킨다.

## 판정 규칙

    온전한 파일     JSON 배열로 **파싱되는 것**. 빈 배열도 온전하다.
                   0바이트·절단은 무효 (T4: 존재만 세면 0바이트 84개가 통과한다)
    n < MIN_VALID  🔴 실패 — 다음 wake 가 재시도한다
    전량 진공       🔴 실패 — 파일은 다 있는데 내용이 (거의) 전부 비었다 = 수집 고장
    n < EXPECTED   ⚠️ 부분 결손 — 성공이되 알림. 해당 차트는 영구 손실 가능
    빈 차트         **정상 사실**이라 로그에만. 한국 스토어에 유료 카지노 차트가 없는 것은
                   애플의 정답이지 결손이 아니다(그게 매일 경보를 만들던 자리다)

기준일이 **로컬(KST)** 인 이유: `board.py` 가 그 기준으로 파일명을 쓴다. 스케줄러의
period 는 UTC 라 00~09시 KST 에 하루 어긋나므로, UTC 로 세면 매일 아침 오탐이 난다.

## 규약 — 호출부(`kv_core.workers`)가 의존하는 것

    rc 0   정상            (부분 결손이면 stdout JSON 의 `partial` 이 true)
    rc 1   실패 — 재시도    (파일 부족 · 전량 진공)
    stdout **마지막 줄**이 JSON 한 덩어리. `msg` 는 사람이 읽을 문구로 이미 완성돼 있다
           — 호출부가 숫자를 다시 해석하지 않게 하려는 것이다(그게 지식 누수의 시작이다).

실행: python tools/board_health.py --check
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJ.parents[2] / "kv-core"))

from kv_core import paths as _paths  # noqa: E402

SNAP_DIR = _paths.data("net-sourcing", "snapshots")

# 실측(2026-08-10 · 재확인 08-12~16 5일 연속 동일): 84차트 중 83개가 비어있지 않고 총 15,456행.
EXPECTED = 84
MIN_VALID = 80        # 로컬은 primary 후보라 GH 워크플로의 관용치 40 보다 빡빡하게.
                      # 재시도 비용이 0 이다 — 같은 날 파일을 멱등 덮어쓰기 한다.
# 절대 행 수로 임계를 잡으면 스토어 규모·차트 수에 따라 흔들린다. **비어있지 않은 차트의
# 비율**로 본다(규모 무관).
MIN_NONEMPTY_RATIO = 0.5


def scan(day: date | None = None) -> tuple[int, int, list[str]]:
    """(온전한 파일 수, 총 행 수, 정당하게 빈 차트 목록).

    🔴 **파싱 성공 여부**가 진공과 결손을 가른다. 빈 배열은 잘 형성된 응답이고
       0바이트·절단은 그렇지 않다. 예전엔 "비어있지 않은 배열만" 온전하다고 셌는데,
       그러면 `appstore_board_7006_top-paid`(카지노 유료 차트)가 8/4 부터 매일 `[]` 라
       "부분 결손 · 영구 손실 가능" 알림이 **매일** 발동했다.
    """
    d = (day or date.today()).isoformat()
    n = rows = 0
    empty: list[str] = []
    for p in SNAP_DIR.glob(f"appstore_board_*_{d}.json"):
        try:
            if p.stat().st_size == 0:
                continue                       # 0바이트 = 쓰다 만 것
            data = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                continue                       # 배열이 아니면 계약 위반
            n += 1                             # 잘 형성된 응답 — 비어 있어도 온전하다
            rows += len(data)
            if not data:
                empty.append(p.name.replace("appstore_board_", "").replace(".json", ""))
        except (json.JSONDecodeError, OSError):
            continue                           # 절단·파손 = 무효 (임계가 잡는다)
    return n, rows, sorted(empty)


def judge(day: date | None = None) -> dict:
    """산출물 → 판정. 호출부가 그대로 쓸 수 있게 **문구까지 만들어** 돌려준다."""
    n, rows, empty = scan(day)
    nonempty = n - len(empty)
    base = {"valid": n, "expected": EXPECTED, "rows": rows, "empty_charts": empty}

    if n < MIN_VALID:
        return {**base, "ok": False, "partial": False,
                "msg": (f"🔴 appstore-snapshot: 오늘자 온전한 파일 {n}/{EXPECTED} "
                        f"(<{MIN_VALID}) — 실패 처리(다음 wake 재시도)")}
    if rows == 0 or (n and nonempty < n * MIN_NONEMPTY_RATIO):
        # 파일은 다 있는데 내용이 (거의) 전부 비었다 = 수집 자체가 고장난 것.
        # 진공 통과 금지는 **개별**이 아니라 **집계** 수준에서 지킨다.
        return {**base, "ok": False, "partial": False,
                "msg": (f"🔴 appstore-snapshot: 온전한 파일 {n}/{EXPECTED} 이나 비어있지 않은 "
                        f"차트 {nonempty}개(총 {rows}행) — 전량 진공은 결손으로 본다. 실패 처리")}
    if n < EXPECTED:
        return {**base, "ok": True, "partial": True,
                "msg": (f"⚠️ appstore-snapshot: 오늘자 온전한 파일 {n}/{EXPECTED} — "
                        f"{EXPECTED - n}개가 파손/누락(해당 차트는 영구 손실 가능)")}
    tail = (f" · 빈 차트 {len(empty)}개({', '.join(empty[:3])}{'…' if len(empty) > 3 else ''})"
            if empty else "")
    return {**base, "ok": True, "partial": False,
            "msg": f"✅ appstore-snapshot: 온전한 파일 {n}/{EXPECTED} · 총 {rows}행{tail}"}


def main() -> int:
    ap = argparse.ArgumentParser(prog="board_health")
    ap.add_argument("--check", action="store_true", help="판정하고 rc 로 답한다(기본)")
    ap.add_argument("--date", help="YYYY-MM-DD (기본: 오늘 KST)")
    ap.add_argument("--synthetic", action="store_true", help="자체 검증")
    a = ap.parse_args()
    if a.synthetic:
        return _synthetic()
    day = date.fromisoformat(a.date) if a.date else None
    r = judge(day)
    print(r["msg"])
    print(json.dumps(r, ensure_ascii=False))     # **마지막 줄**이 규약이다
    return 0 if r["ok"] else 1


def _synthetic() -> int:
    """판정 경계 4종 — 실 스냅샷 없이 임시 디렉토리로 태운다."""
    import shutil
    import tempfile
    global SNAP_DIR
    fails = []

    def chk(name, cond, detail=""):
        print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
        if not cond:
            fails.append(name)

    def setup(n_full, n_empty, n_broken, rows_each=200):
        d = Path(tempfile.mkdtemp(prefix="boardhealth-"))
        for i in range(n_full):
            (d / f"appstore_board_g{i}_2026-08-16.json").write_text(
                json.dumps([{"id": j} for j in range(rows_each)]), encoding="utf-8")
        for i in range(n_empty):
            (d / f"appstore_board_e{i}_2026-08-16.json").write_text("[]", encoding="utf-8")
        for i in range(n_broken):
            (d / f"appstore_board_b{i}_2026-08-16.json").write_text("", encoding="utf-8")
        return d

    print("=== board_health 판정 경계 ===\n")
    for label, args, want_ok, want_partial in [
        ("84 온전(빈 1 포함) → 정상", (83, 1, 0), True, False),
        ("82/84 → 부분 결손(성공+알림)", (81, 1, 2), True, True),
        ("40/84 → 실패(재시도)", (40, 0, 44), False, False),
        ("84 전부 빈 배열 → 전량 진공은 결손", (0, 84, 0), False, False),
        ("0바이트 84개 → 온전 0 → 실패", (0, 0, 84), False, False),
    ]:
        tmp = setup(*args)
        SNAP_DIR = tmp
        r = judge(date(2026, 8, 16))
        chk(label, r["ok"] == want_ok and r["partial"] == want_partial,
            f"ok={r['ok']} partial={r['partial']} valid={r['valid']} rows={r['rows']}")
        shutil.rmtree(tmp)

    # 🔴 T4 보호 — 0바이트는 "온전한 것"으로 세지 않는다
    tmp = setup(80, 0, 4)
    SNAP_DIR = tmp
    chk("T4 0바이트를 온전한 것으로 세지 않는다", judge(date(2026, 8, 16))["valid"] == 80,
        str(judge(date(2026, 8, 16))["valid"]))
    shutil.rmtree(tmp)

    print()
    if fails:
        print(f"🔴 board_health 합성 실패 {len(fails)}건: {fails}")
        return 1
    print("🎉 board_health 합성 통과")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
