"""착지 스키마 가드 — **행이 사는 DB에 없는 칸을 보내지 않는다.**

## 왜 이 파일이 생겼나

2026-08-13 전수 감사 리뷰 B(F5). 폭발 반경이 가장 컸던 결함이고, **코드가 그 함정을
주석으로 정확히 서술해놓고 4칸만 가드한** 자리다.

`land()` 는 두 가지를 서로 다른 출처에서 가져온다:

    props  = _build_landing_properties(cfg, cand, **_fetch_schema(cfg)**, run_id)   ← 타깃 DB 스키마
    pid    = seen[key]  ← existing_landing_keys 가 **전 착지 DB**를 훑어 만든 것

전 DB 를 훑는 건 의도다 — 기본 DB 에 있는 기존 인원이 '처음 보는 사람'이 되어 중복
재생성되는 걸 막으려고 2026-07-15 에 그렇게 정했다. 그 결과 **`pid` 는 타깃과 다른 DB 의
행일 수 있는데, `props` 는 늘 타깃 DB 스키마로 만들어진다.**

라이브 실측(2026-08-13, 착지 DB 4개):

    base 54칸 · appstore 21칸 · threads 41칸 · linkedin 37칸
    타깃(base)으로 만든 props 8칸을 appstore 행에 보내면 없는 칸 5개
      ['Author Verified','Is Quote Post','Is Reply','LinkedIn URL','Status'] → 노션 400

## 400 하나의 폭발 반경 (이게 이 파일의 존재 이유다)

    PATCH 400 → failed += 1
      → pipeline.land: `if failed: return`  → _commit_hooks 미호출
        → 앱스토어 기준선 · 스레드 로테이션 커서 · 링크드인 seen-store **전부 미확정**
          → seen-store 가 안 갱신되니 다음 실행에 **같은 사람이 같은 profile_changed 로**
            올라와 **같은 400** → 사람이 코드를 고칠 때까지 안 풀린다(자가 증식)
            → 그동안 매 실행이 링크드인을 재수집한다($1.6~$5.2/회 · 월 상한 $29)

즉 링크드인 한 사람 때문에 앱스토어 200건의 커밋이 정지하고, 그 상태가 매일 반복된다.

## 검사하는 것

    L1  행이 타깃과 같은 DB → 칸이 그대로 나간다 (가드가 정상 갱신을 죽이지 않는다)
    L2  행이 다른 DB → 없는 칸만 걸러지고 나머지는 나간다 (조용히 버리지 않고 로그를 남긴다)
    L3  스캔에 없던 pid = "이 행의 칸을 **모른다**" → 보내지 않고 **그 행만** 실패로 센다
    L4  이번 실행에서 방금 만든 행 → 타깃 스키마가 정답이므로 정상 갱신된다
    L5  라이브 스키마들이 실제로 서로 다르다 (같아지면 L1~L3 가 공허해진다 · 네트워크 있을 때만)

L3 의 선택지가 셋이었다 — ⓐ 아무것도 안 보낸다(갱신이 조용히 증발) ⓑ 전부 보낸다(위 연쇄)
ⓒ 모른다고 말한다. **ⓒ 를 골랐다**: ⓐ·ⓑ 는 이 상태의 빈도를 영영 못 알려주는데, ⓒ 는 며칠
로그를 보면 빈도가 나오고 그때 진짜 처방을 정할 수 있다(불변규칙 06 · R5 진공/결손 구분).

실행: KV_DATA=<스크래치> python kv-core/tests/_synthetic_landing_schema.py
HTTP 를 대역으로 갈아끼운다 — 노션 실호출 0(L5 만 예외이고, 토큰이 없으면 건너뛴다).
"""
from __future__ import annotations

import os

os.environ.setdefault("KV_NO_SEND", "1")

import sys  # noqa: E402
from pathlib import Path  # noqa: E402

assert os.environ.get("KV_DATA"), "KV_DATA 스크래치 필수"

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "skills/net-sourcing/project"))

from core import notion  # noqa: E402

FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAIL.append(name)


# ══════════════════════════════════════════════════════════════════════
# 대역 — 노션에 나가는 모든 것을 가로챈다
# ══════════════════════════════════════════════════════════════════════
SENT: list[tuple[str, str, dict]] = []


class _Resp:
    def __init__(self, code: int = 200, body: dict | None = None):
        self.status_code, self._b, self.text = code, body or {}, ""

    def json(self) -> dict:
        return self._b


def _fake_write(method, url, token, payload=None, **kw):
    SENT.append((method, url, payload or {}))
    return _Resp(200, {"id": "created-1"})


TARGET = {f"C{i}": {"type": "rich_text"} for i in range(10)}
TARGET.update({"이름": {"type": "title"}, "Last Seen": {"type": "date"},
               "재검토": {"type": "checkbox"}})
NARROW = {"C0": {"type": "rich_text"}, "이름": {"type": "title"}}   # 다른 DB 행 — 칸이 적다

notion._write = _fake_write
notion._token = lambda: "tok"
notion._fetch_schema = lambda cfg, tok: TARGET
notion._db_id = lambda cfg: "target-db"
notion._assert_person_schema = lambda *a: None
notion._build_landing_properties = lambda cfg, c, sch, rid: {
    k: {"rich_text": []} for k in ("C0", "C1", "C2")}
notion._merge_patch = lambda pid, props, tp: dict(props)
notion._landing_key = lambda c: c.get("k")
notion.existing_post_urls = lambda tok, pid: set()
notion.append_career_blocks = lambda *a: False


def _land(seen: dict, rowprops: dict, cands: list[dict]):
    SENT.clear()
    notion._ROW_PROPS.clear()
    notion._ROW_PROPS.update(rowprops)
    notion.existing_landing_keys = lambda cfg: dict(seen)
    ok, failed = notion.land({"notion": {}}, cands, "run-1")
    patches = [p for m, _u, p in SENT if m == "PATCH"]
    keys = sorted(patches[0]["properties"]) if patches else None
    return ok, failed, keys


def _changed(k: str = "a"):
    return [{"k": k, "name": "홍길동", "signals": {"profile_changed": True}}]


# ══════════════════════════════════════════════════════════════════════
print("=== L1~L4: 크로스-DB PATCH 가드 ===\n")

ok, failed, keys = _land({"a": "pid-1"}, {"pid-1": TARGET}, _changed())
check("L1 같은 DB 행은 칸이 그대로 나간다(가드가 정상 갱신을 죽이지 않는다)",
      keys == ["C0", "C1", "C2"] and failed == 0, f"보낸 칸={keys} failed={failed}")

ok, failed, keys = _land({"a": "pid-2"}, {"pid-2": NARROW}, _changed())
check("L2 다른 DB 행은 없는 칸만 걸러진다", keys == ["C0"] and failed == 0,
      f"보낸 칸={keys} (C1·C2 는 그 행에 없다) failed={failed}")

ok, failed, keys = _land({"a": "unknown-pid"}, {}, _changed())
check("L3 칸을 모르는 행은 보내지 않고 그 행만 실패로 센다",
      keys is None and failed == 1 and ok == 0,
      f"PATCH={keys} failed={failed} — 조용한 증발(ⓐ)도 400 연쇄(ⓑ)도 아니다")

# L4 — 같은 배치에 같은 사람이 두 번. 두 번째는 방금 만든 행을 갱신한다.
#      그 pid 는 착지 DB 스캔 시점엔 없었으므로 _ROW_PROPS 에 없다 → L3 로 오분류되면 안 된다.
SENT.clear()
notion._ROW_PROPS.clear()
notion.existing_landing_keys = lambda cfg: {}
ok, failed = notion.land(
    {"notion": {}},
    [{"k": "dup", "name": "신규", "signals": {}},
     {"k": "dup", "name": "신규", "signals": {"profile_changed": True}}],
    "run-1")
_p = [p for m, _u, p in SENT if m == "PATCH"]
_c = [p for m, _u, p in SENT if m == "POST"]
# ⚠️ `land` 는 `ok + patched` 를 반환한다(생성과 갱신의 합). 처음엔 `ok == 1` 로 단언했다가
#    거짓 실패를 냈다 — 여기선 생성 1 + 갱신 1 = 2 가 정답이다.
#    핵심 단언은 반환값이 아니라 **행이 두 번 만들어지지 않았다 + 갱신이 실패로 안 셌다** 이다.
check("L4 이번 실행에서 방금 만든 행은 정상 갱신된다(모른다로 오분류 안 함)",
      len(_c) == 1 and len(_p) == 1 and failed == 0 and ok == 2,
      f"생성 POST {len(_c)}회 · 갱신 PATCH {len(_p)}회 · 반환 {ok} · failed={failed}")

# ══════════════════════════════════════════════════════════════════════
print("\n=== L5: 라이브 스키마가 실제로 서로 다르다 ===\n")
# 4개 DB 의 칸 구성이 같아지면 위 검사들이 "잡을 것이 없는" 상태가 된다.
# 그건 결함이 사라진 게 아니라 **검사가 공허해진** 것이므로 사실로 확인한다.
_tok = os.environ.get("NOTION_TOKEN")
if not _tok:
    print("  ⏭️  건너뜀 — NOTION_TOKEN 없음(CI 러너). L1~L4 는 위에서 이미 돌았다.")
else:
    import yaml  # noqa: E402
    import requests  # noqa: E402

    _cfg = yaml.safe_load(
        (_REPO / "skills/net-sourcing/project/config.yaml").read_text(encoding="utf-8"))
    _H = {"Authorization": f"Bearer {_tok}", "Notion-Version": "2022-06-28"}
    _sizes = {}
    for _db in notion._scan_landing_dbs(_cfg):
        _r = requests.get(f"https://api.notion.com/v1/databases/{_db}", headers=_H, timeout=30)
        if _r.status_code == 200:
            _sizes[_db[:8]] = len(_r.json()["properties"])
    check("L5 착지 DB 를 2개 이상 읽었다", len(_sizes) >= 2, str(_sizes))
    check("L5 착지 DB 들의 칸 구성이 서로 다르다(같으면 L1~L4 가 공허하다)",
          len(set(_sizes.values())) > 1, str(_sizes))

# ══════════════════════════════════════════════════════════════════════
print()
if FAIL:
    print(f"🔴 착지 스키마 계약 실패 {len(FAIL)}건: {', '.join(FAIL)}")
    raise SystemExit(1)
print("✅ 착지 스키마 계약 통과 — 행이 사는 DB에 없는 칸은 나가지 않는다")
