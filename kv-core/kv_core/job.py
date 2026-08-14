"""잡 러너와 상태 프로토콜 — 기본구조 3단계 "상태는 전부 파일로".

이 규약 하나가 4·6·7·9·11단계를 전부 가능하게 만든다. 파일을 DB의 싼 대체물이 아니라
**서로 다른 프로세스가 상태를 주고받는 프로토콜**로 쓴다. 읽기만 하면 관측이 되고(7단계),
죽음을 판정할 근거가 되고(6단계), 새 기능이 규약만 따르면 관측·복구를 공짜로 얻는다(11단계).

## 완료 판정 — 이 한 줄이 W3 의 전부다

    실행 중인 잡을 `kill -9` 해도, jobs/<id>/ 만 보고 무엇이 어디까지 됐는지 알 수 있다.

## 디렉토리 규약

    $KV_DATA/<skill>/jobs/<id>/
      request.json    무엇을 요청받았나        (한 번 쓰고 안 바꿈)
      events.jsonl    무엇을 수행했나          ← append-only. 유일한 진실
      state.json      지금 어느 단계인가        ← events 재생의 파생물(캐시)
      cursor.json     어디까지 처리했나        ← events 재생의 파생물(캐시)
      artifacts/      산출물
      stderr.log

## 세 결정 (2026-08-02, 박정현) — 이 파일이 그 결정의 구현이다

**결정 A — 잡의 경계는 "채널", 개별 항목은 커서.**
서로 독립적인가 + 몇 개인가로 정한다. net-sourcing 채널은 3개라 채널이 잡,
창업자는 수백이라 커서(폴더를 안 만들고 events 의 item_done 한 줄로 관리).
항목 하나 실패는 **건너뛰고 계속**(item_skipped 남기고 커서 전진). 채널을 멈추지 않는다.
실패가 많이 쌓이면 차단이 아니라 "모듈 재설계 신호"로 본다.

**결정 B — events.jsonl(장부)이 진실. state·cursor 는 파생물.**
추가만 하는 파일은 죽어도 앞부분이 온전하니 못 되돌린다 → 진실.
덮어쓰는 파일은 쓰다 죽으면 반쯤 망가지고 언제든 재생성 가능 → 캐시.
깨어나면 events 를 재생(fold)해 state·cursor 를 **다시 계산**한다. 파생물이 어긋나면 버린다.
일반 이름: write-ahead log. DB 가 크래시에서 살아나는 방식.

**결정 C — 전이 가드 + 멱등 키. 부작용 먼저, 커서는 맨 마지막.**
재개 세계에선 같은 항목이 두 번 시도될 수 있다(at-least-once). 두 겹으로 막는다:
  ① 가드   처리 전에 "이미 했나?"(key ∈ done_keys)를 events 에서 확인 → 있으면 건너뜀
  ② 멱등키  두 번 와도 같은 자리에 쓰게(노션 upsert, 고정 파일명). 부작용 자체를 멱등으로
순서가 핵심: **부작용(노션 upsert)을 완료한 뒤에 item_done 을 기록**한다.
중간에 죽으면 item_done 이 없으니 재실행되고, 멱등키가 중복을 흡수한다.
퍼지 매칭은 키가 아니다 — 두 번 실행하면 결과가 달라져 멱등성의 정반대다.

**결정 D — 동시성은 슬롯으로. heavy 는 동시 1개.**
딥리서치 같은 무거운 잡은 내부적으로 대량 fan-out 한다. heavy 는 배타 슬롯(파일 락)으로
동시 1개만, light 는 제한 없음. 슬롯을 못 잡으면 잡은 queued 로 남고 나중에 재개한다.

## 쓰는 법 (제네릭 드라이버)

    from kv_core import job

    jid = job.create("net-sourcing", {"channel": "linkedin"})
    job.run(
        "net-sourcing", jid,
        items=founders,                       # 처리할 항목들(리스트/이터러블)
        key=lambda f: f["linkedin_url"],      # 멱등 키 (고유·불변·정확)
        process=land_one_founder,             # 부작용: 노션 upsert. 성공 후 커서 전진
        validate=lambda done, skipped: done > 0,   # 불변규칙 06: 0건은 실패
        heavy=False,
    )
    # kill -9 후 같은 인자로 다시 run() 하면 done_keys 를 건너뛰고 이어서 한다.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from . import paths

# ─────────────────────────── 상태머신 ───────────────────────────
# requested → queued → running → validating → completed | failed
STATES = ("requested", "queued", "running", "validating", "completed", "failed")

# 허용 전이만 명시한다. 표에 없는 전이는 거부한다(잘못된 상태 이동을 구조로 막는다).
_ALLOWED: dict[str, tuple[str, ...]] = {
    "requested":  ("queued", "failed"),
    "queued":     ("running", "failed"),
    "running":    ("validating", "running", "failed"),  # running→running: 재개 시 무해한 재진입
    "validating": ("running", "completed", "failed"),   # validating→running: 검증 중 죽은 잡의 재개(검증 재진입)
    "completed":  (),   # 종단
    "failed":     (),   # 종단
}
_TERMINAL = ("completed", "failed")

# 항목 루프 중 파생 캐시(cursor.json)를 N개마다 갱신한다 — 그 사이 크래시하면 최대 N-1개
# 뒤처진다. **events 는 항상 정확하다**(매 항목 fsync). 이건 관측용 캐시의 신선도일 뿐이다.
_CURSOR_FLUSH = 25


# ─────────────────────────── 경로 ───────────────────────────

def job_dir(skill: str, job_id: str, *, create: bool = False) -> Path:
    p = paths.data(skill, "jobs", job_id, create=create)
    return p


def _events_path(skill: str, job_id: str) -> Path:
    return job_dir(skill, job_id) / "events.jsonl"


def _state_path(skill: str, job_id: str) -> Path:
    return job_dir(skill, job_id) / "state.json"


def _cursor_path(skill: str, job_id: str) -> Path:
    return job_dir(skill, job_id) / "cursor.json"


def _request_path(skill: str, job_id: str) -> Path:
    return job_dir(skill, job_id) / "request.json"


# ─────────────────────────── 원자적 쓰기 ───────────────────────────

def atomic_write(path: Path, text: str) -> None:
    """tmp → fsync → rename. **부분 기록된 파일이 절대 남지 않게 한다.**

    비원자적 쓰기 때문에 재실행 시 중복 산출물이 만들어진 적이 있다(bplan). rename 은
    같은 파일시스템에서 원자적이라, 읽는 쪽은 옛 파일 아니면 새 파일만 보고 반쪽을 못 본다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # [W9 자기리뷰] tmp 이름이 **고정**이면 동시 writer 두 개가 같은 파일에 쓰고, 먼저
    # rename 한 쪽 때문에 나머지가 `FileNotFoundError` 로 **크래시**한다(프로브로 재현).
    # 이 함수는 워터마크·잡 상태·승인 레코드·알림 정책이 전부 쓰는 공용 경로다 —
    # "원자적 쓰기"라는 이름이 무색하게 동시성에서 깨지고 있었다. pid 로 분리한다.
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())      # 디스크까지 내려가야 크래시에 안전하다
        os.replace(tmp, path)         # 원자적 교체
    except BaseException:
        tmp.unlink(missing_ok=True)   # 실패해도 tmp 잔재를 남기지 않는다
        raise


# ─────────────────────────── 이벤트(진실) ───────────────────────────
# append-only 장부. 이 함수만이 진실을 기록한다. 나머지(state·cursor)는 전부 이걸 재생한 결과다.

def _repair_torn_tail(p: Path) -> None:
    """크래시로 개행 없이 끝난(반쯤 써진) 마지막 줄을 잘라낸다.

    이 복구가 없으면 다음 append 가 torn 줄 **위에 이어붙어** 파일 중간에 병합줄이 생기고,
    read_events 가 영구 크래시한다(W0-W5 리뷰 H2, 실측 재현). 반쯤 써진 이벤트는 fsync 를
    통과하지 못한 것이라 "기록된 적 없음" — 잘라내는 것이 append-only 규약의 복원이다.
    """
    if not p.exists() or p.stat().st_size == 0:
        return
    with open(p, "rb+") as f:
        f.seek(-1, os.SEEK_END)
        if f.read(1) == b"\n":
            return                                    # 정상 종료 — 손댈 것 없음
        data = p.read_bytes()
        cut = data.rfind(b"\n") + 1                   # 마지막 완전한 줄의 끝(없으면 0)
        f.truncate(cut)
        f.flush()
        os.fsync(f.fileno())


def append_event(skill: str, job_id: str, kind: str, **data: Any) -> dict:
    """events.jsonl 에 한 줄 추가한다. **덮어쓰지 않는다 — 중단돼도 앞부분이 남는다.**

    append + fsync 로 쓴다. 마지막 한 줄이 크래시로 반쯤 써질 수 있으므로, append 전에
    torn 꼬리를 복구해(_repair_torn_tail) "깨질 수 있는 건 마지막 줄뿐" 규약을 유지한다.
    """
    ev = {"ts": _now_iso(), "kind": kind, **data}
    line = json.dumps(ev, ensure_ascii=False)
    p = _events_path(skill, job_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    _repair_torn_tail(p)
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
        os.fsync(f.fileno())
    return ev


def read_events(skill: str, job_id: str) -> list[dict]:
    """events 를 순서대로 읽는다. **깨진 마지막 줄은 조용히 버린다**(크래시로 반쯤 써진 줄).

    앞줄이 깨지는 일은 append+fsync 구조상 없다 — 깨질 수 있는 건 오직 마지막 줄뿐이다.
    """
    p = _events_path(skill, job_id)
    if not p.exists():
        return []
    # ⚠️ 파일을 **한 번만** 읽는다. 예전엔 파손 줄마다 `p.read_text()` 를 두 번 더 불러
    #    전체를 다시 읽었다(줄 수를 세려고). events 가 수천 줄인 잡에서 그건 O(n²) 다.
    lines = p.read_text(encoding="utf-8").splitlines()
    last = len(lines) - 1
    out: list[dict] = []
    for i, raw in enumerate(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            # 마지막 줄만 허용. 중간 줄이 깨졌다면 그건 append 규약 위반이라 드러내야 한다.
            if i == last:
                break
            # 🔴 [2026-08-13 W12 · 리뷰 E-F11] 드러내는 것까지는 설계대로인데 **그 다음이
            #    없었다.** 이 raise 는 `rebuild`·`transition`·`current_state` 를 전부 죽이므로
            #    그 잡은 종단 상태로 갈 수 없다 — **영구 벽돌**이다. `judge_job:416` 의
            #    attempt-suffix 재시도(`rebuild(...).state == "failed"`)도 같은 예외로 죽어
            #    그날 judge 전체가 rc≠0 이 된다. 런북 6본에 이 복구 절차가 없었다.
            #    → 무엇을 하면 되는지 예외가 직접 말한다(격리는 사람이 명시적으로 부른다 —
            #      자동 격리는 "append 규약이 깨졌다"는 신호 자체를 지운다).
            raise json.JSONDecodeError(
                f"{p} 의 {i + 1}번째 줄이 깨졌다(마지막 줄이 아니므로 append 규약 위반이다). "
                f"복구: python -c \"import sys;sys.path.insert(0,'kv-core');"
                f"from kv_core import job;print(job.quarantine_corrupt_events("
                f"'{skill}','{job_id}'))\"  — 깨진 줄을 .quarantine-<ts> 로 옮기고 "
                f"나머지로 재생한다(지우지 않는다)",
                raw, 0) from None
    return out


def quarantine_corrupt_events(skill: str, job_id: str) -> dict:
    """깨진 events 줄을 격리하고 나머지로 재생 가능하게 만든다. **지우지 않는다.**

    🔴 [W12 · E-F11] `read_events` 가 중간 파손에 raise 하는 것은 설계대로다(append 규약
       위반은 드러나야 한다). 문제는 **복구 경로가 없어 그 잡이 영구 벽돌**이 된 것이다.

    ⚠️ 자동으로 부르지 않는다. 사람이 명시적으로 부른다 — 자동 격리는 "장부가 깨졌다"는
       사실을 조용히 지우고, 그러면 원인(append 규약을 깬 코드)을 영영 못 찾는다.

    반환: `{"quarantined": n, "kept": n, "path": <격리 파일>}`.
    """
    p = _events_path(skill, job_id)
    if not p.exists():
        return {"quarantined": 0, "kept": 0, "path": None}
    lines = p.read_text(encoding="utf-8").splitlines()
    good: list[str] = []
    bad: list[str] = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            json.loads(raw)
            good.append(raw)
        except json.JSONDecodeError:
            bad.append(raw)
    if not bad:
        return {"quarantined": 0, "kept": len(good), "path": None}
    qp = p.with_suffix(f".jsonl.quarantine-{int(time.time())}")
    qp.write_text("\n".join(bad) + "\n", encoding="utf-8")   # 원본 보존이 먼저
    atomic_write(p, "\n".join(good) + "\n")
    print(f"🔴 {skill}/{job_id}: events 파손 줄 {len(bad)}개를 격리했다 → {qp.name}\n"
          f"   남긴 줄 {len(good)}개로 재생 가능하다. **격리 파일을 지우지 마라** — "
          f"append 규약을 깬 코드를 찾는 유일한 단서다.")
    return {"quarantined": len(bad), "kept": len(good), "path": str(qp)}


# ─────────────────────────── 재생(fold) — events → 파생 상태 ───────────────────────────
# 결정 B: 여기가 "진실을 상태로 바꾸는" 유일한 지점이다. state.json·cursor.json 은 이 결과의 캐시.

@dataclass
class Derived:
    state: str
    # 🔴 [W12 · E-F12] 이 값은 **전 스테이지의 합집합**이다. 실제 멱등 가드(`_done_keys`)는
    #    `stage` 로 거르는데 여기는 안 걸러서, **같은 이름의 값을 두 함수가 다른 단위로**
    #    계산하고 있었다. 스테이지 간에 같은 키(page_id 등)를 쓰는 파이프라인이면
    #    "s1 에서 끝난 a" 와 "s2 에서 끝난 a" 가 구분되지 않는다.
    #    스테이지 정밀도가 필요하면 `done_by_stage` 를 써라.
    done_keys: set[str]          # 부작용까지 완료된 항목(전 스테이지 합집합)
    skipped: list[dict]          # 건너뛴 항목 {key, error}
    request: dict
    # 스테이지별 완료 키 — `_done_keys` 와 **같은 단위**다. `''` 는 스테이지 미지정(단일 run).
    done_by_stage: dict[str, set[str]] = field(default_factory=dict)


def rebuild(skill: str, job_id: str) -> Derived:
    """events.jsonl 을 재생해 현재 상태를 계산한다. **파일이 아니라 이벤트가 진실이다.**"""
    state = "requested"
    done: set[str] = set()
    by_stage: dict[str, set[str]] = {}
    skipped: list[dict] = []
    request: dict = {}
    for ev in read_events(skill, job_id):
        k = ev.get("kind")
        if k == "created":
            request = ev.get("request", {})
            state = "requested"
        elif k == "state":
            state = ev.get("to", state)
        elif k == "item_done":
            done.add(ev["key"])
            by_stage.setdefault(ev.get("stage", ""), set()).add(ev["key"])
        elif k == "item_skipped":
            skipped.append({"key": ev.get("key"), "error": ev.get("error")})
    return Derived(state=state, done_keys=done, skipped=skipped, request=request,
                   done_by_stage=by_stage)


def _materialize(skill: str, job_id: str, d: Derived) -> None:
    """파생물(state.json·cursor.json)을 디스크에 캐시한다. 없어도 rebuild 로 복원 가능하다."""
    atomic_write(_state_path(skill, job_id),
                 json.dumps({"state": d.state, "updated": _now_iso()},
                            ensure_ascii=False, indent=2))
    # 🔴 [W12 · E-F12] 예전엔 합집합 하나만 적었다. 스테이지가 여럿인 잡에서
    #    "어느 스테이지가 어디까지 갔나"를 못 봤다 — `kill -9` 성공 신호가
    #    *"상태 디렉토리만 보고 무엇이 어디까지 됐는지 안다"* 인데 그 절반이 없었다.
    atomic_write(_cursor_path(skill, job_id),
                 json.dumps({"done": len(d.done_keys), "skipped": len(d.skipped),
                             "done_keys": sorted(d.done_keys),
                             "by_stage": {k: sorted(v) for k, v in
                                          sorted(d.done_by_stage.items())}},
                            ensure_ascii=False, indent=2))


# ─────────────────────────── 생성·전이 ───────────────────────────

def create(skill: str, request: dict, job_id: str | None = None) -> str:
    """잡을 만든다 → job_id. **멱등하다** — 같은 id 로 다시 부르면 기존 잡을 재사용한다.

    이미 events 가 있으면 새로 만들지 않는다(created 를 두 번 남기지 않는다). 재개의 출발점.
    """
    job_id = job_id or _default_id(skill, request)
    job_dir(skill, job_id, create=True)
    (job_dir(skill, job_id) / "artifacts").mkdir(exist_ok=True)
    if not _events_path(skill, job_id).exists():
        atomic_write(_request_path(skill, job_id),
                     json.dumps(request, ensure_ascii=False, indent=2))
        append_event(skill, job_id, "created", request=request)
        _materialize(skill, job_id, rebuild(skill, job_id))
    return job_id


def transition(skill: str, job_id: str, to: str, **meta: Any) -> str:
    """상태를 옮긴다. events 에 먼저 적고, 그 재생 결과로 state.json 을 갱신한다.

    가드(결정 C-①): 이미 그 상태면 no-op. 허용되지 않은 전이면 거부.
    죽으면(결정 B): events 에 남고, state.json 은 다음 rebuild 로 복원된다.
    """
    if to not in STATES:
        raise ValueError(f"알 수 없는 상태: {to}")
    cur = rebuild(skill, job_id).state
    if cur == to:
        return cur                                   # 멱등: 중복 전이는 조용히 무시
    if cur in _TERMINAL:
        return cur                                   # 종단 상태는 더 이상 안 움직인다
    if to not in _ALLOWED.get(cur, ()):
        raise ValueError(f"허용되지 않은 전이: {cur} → {to}")
    append_event(skill, job_id, "state", **{"from": cur, "to": to, **meta})
    _materialize(skill, job_id, rebuild(skill, job_id))
    return to


def current_state(skill: str, job_id: str) -> str:
    return rebuild(skill, job_id).state


# ─────────────────────────── 동시성 슬롯 (결정 D) ───────────────────────────
# heavy 잡은 동시 1개. 파일 락(O_CREAT|O_EXCL)으로 배타 획득한다. light 는 슬롯을 안 쓴다.

class SlotBusy(Exception):
    """heavy 슬롯이 이미 점유됨 — 잡은 queued 로 남고 나중에 재개한다."""


def _slot_path(kind: str) -> Path:
    return paths.data("_locks", create=True) / f"{kind}.lock"


class heavy_slot:
    """heavy 배타 슬롯. `with heavy_slot():` 로 감싼다. 못 잡으면 SlotBusy.

    ## 🔴 [2026-08-13 W12 · 리뷰 E-F1] pid 파일 락 → `fcntl.flock`

    옛 구현은 "락 파일이 존재하면 점유"였고, 죽은 프로세스의 락은 pid 생존 확인 +
    `MAX_HOLD_SEC` 나이로 **회수**했다. 그 회수 경로에 동시 1개 계약을 깨는 구멍이 있었다:

        A 획득 → 맥이 6시간 자다 깸(A 는 아직 살아 있다) → B 가 유령 락으로 보고 회수
        → B 가 같은 경로에 자기 락 생성 → **A 가 정상 종료하며 `path.unlink()`**
        → 그 경로는 이미 B 의 락이다 → 슬롯이 무주공산 → C·D·… 무제한 진입

    실측 재현(2026-08-13): A 획득 → mtime 을 7시간 전으로 → B 회수 성공 → `A.__exit__()`
    → 락 파일 사라짐 → C 진입 성공. **B 와 C 가 동시 보유.**
    스케줄러 락에서도 같았다 — 같은 task 를 N 개 스케줄러가 동시에 돌린다.

    `__exit__` 이 소유권을 확인하고 지우게 하는 것으로는 못 닫는다: 파일을 읽고 unlink
    하는 사이가 원자적이지 않고, POSIX 에 "inode 가 일치하면 unlink" 가 없다.

    **그래서 커널에 맡긴다.** `flock` 은 fd(정확히는 open file description)에 붙으므로
    프로세스가 죽으면 자동 해제된다 — 회수(stale reaping)라는 개념 자체가 필요 없어지고,
    위 시나리오가 **표현 불가능**해진다. `approval.py:222` 가 A-H3 때 이미 도달한 결론인데
    `job.heavy_slot` 만 옛 방식에 남아 있었다.

    ## 무엇이 달라지나

    · 락 파일을 **지우지 않는다.** 파일이 곧 락의 대상이고, 존재 여부는 점유와 무관하다.
      (`_locks/*.lock` 이 남지만 0바이트급이고, `approval` 도 같은 방식이다.)
    · pid 재사용·유령 락 문제가 사라진다 — 커널이 안다.
    · **오래 잡고 있는 락은 회수하지 않고 경고만 한다.** 회수가 위 결함의 원인이었고,
      회수가 필요했던 이유(죽은 프로세스 판별 불가)는 flock 이 없앴다. 남는 경우는
      "살아 있는데 멈춘 프로세스"뿐인데, 그건 회수해서 이중 실행을 만드는 것보다
      **시끄럽게 알리고 사람이 죽이는 편**이 맞다(불변규칙 06).
    """

    def __init__(self, kind: str = "heavy"):
        self.path = _slot_path(kind)
        self.fd: int | None = None

    def __enter__(self):
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:                      # BlockingIOError 포함 — 다른 fd 가 쥐고 있다
            held = self._held_info()
            os.close(fd)
            self._warn_if_long(held)
            raise SlotBusy(f"heavy 슬롯 점유 중: {self.path.name}{held}")
        self.fd = fd
        # 락은 커널이 쥔다. 파일 내용은 **사람이 읽을 진단 정보**일 뿐이다 —
        # 이 값으로 소유권을 판정하지 않는다(그게 옛 결함의 뿌리였다).
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()} {time.time():.0f}".encode())
        except OSError:
            pass                             # 진단 정보 기록 실패가 락을 막으면 안 된다
        return self

    def __exit__(self, *exc):
        if self.fd is None:
            return
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)
            self.fd = None
        # 🔴 **unlink 하지 않는다.** 파일이 곧 락의 대상이다. 지우면 그 사이 다른
        #    프로세스가 만든 파일을 지우게 되고, 그게 정확히 이 클래스가 앓던 결함이다.

    # 이만큼 잡고 있으면 **경고한다**(회수하지 않는다). heavy 잡의 최장 런타임
    # (judge 3시간)보다 넉넉히 위. 회수가 옛 결함의 원인이었고, 회수가 필요했던 이유
    # (죽은 프로세스를 판별 못 함)는 flock 이 없앴다.
    MAX_HOLD_SEC = 6 * 3600

    def _held_info(self) -> str:
        """점유자 진단 정보 — 로그에 붙일 문자열. 판정에는 쓰지 않는다."""
        try:
            raw = self.path.read_text().strip().split()
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return ""
        pid = raw[0] if raw else "?"
        return f" (pid={pid} · {age / 3600:.1f}시간째)"

    def _warn_if_long(self, held: str) -> None:
        """오래 잡힌 락은 시끄럽게 알린다 — 회수는 하지 않는다.

        옛 구현은 `MAX_HOLD_SEC` 를 넘으면 **회수**했고, 그 회수가 동시 1개 계약을 깼다.
        지금은 두 가지가 다르다: ① 죽은 프로세스는 커널이 이미 풀었으므로 여기 안 온다
        ② 여기 오는 건 "살아 있는데 오래 잡고 있다"뿐이고, 그건 사람이 볼 일이다.
        """
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return
        if age > self.MAX_HOLD_SEC:
            print(f"🔴 락 {self.path.name} 이 {age / 3600:.1f}시간째 잡혀 있다{held}.\n"
                  f"   프로세스가 살아 있다는 뜻이다(죽었으면 커널이 풀었다) — 멈춰 있는지\n"
                  f"   확인하고 필요하면 죽여라. **자동 회수하지 않는다**: 회수는 같은 슬롯에\n"
                  f"   두 잡을 동시에 넣는 경로였다(W12 E-F1).")


class resource_slots:
    """여러 자원 락을 한꺼번에 배타 획득 — **이름 정렬 순서로 잡아 데드락을 막는다**(결정 3, D′).

    동시성의 진짜 기준은 CPU 무게가 아니라 **공유 자원**이다(인박스·시트 A1·노션 DB). 한 잡이
    [gmail-inbox, notion-bplan] 둘을 건드리면 둘 다 배타로 잡아야 다른 잡과 안 겹친다.
    여러 락을 제각각 순서로 잡으면 교착(잡1: A→B, 잡2: B→A). 그래서 **항상 이름 정렬 순**으로.
    하나라도 못 잡으면 이미 잡은 것을 전부 풀고 SlotBusy — 부분 점유를 남기지 않는다.

    자원명으로 키잉된 배타 락은 heavy_slot 을 그대로 재사용한다
    (2026-08-13 W12 부터 `fcntl.flock` — 프로세스가 죽으면 커널이 푼다. 회수 없음).
    """

    def __init__(self, names: Iterable[str]):
        self.names = sorted({n for n in names if n})
        self._slots: list[heavy_slot] = []

    def __enter__(self):
        acquired: list[heavy_slot] = []
        try:
            for name in self.names:
                s = heavy_slot(kind=name)
                s.__enter__()
                acquired.append(s)
        except SlotBusy:
            for s in reversed(acquired):
                s.__exit__(None, None, None)
            raise
        self._slots = acquired
        return self

    def __exit__(self, *exc):
        for s in reversed(self._slots):
            s.__exit__(None, None, None)
        return False


# ─────────────────────────── 항목 루프 (재사용 코어) ───────────────────────────
# run() 과 run_pipeline() 의 스테이지가 공유하는 항목 처리 코어. 결정 A(건너뛰고 계속)·
# C(가드+부작용 먼저·커서 마지막)가 여기 한 곳에 있다.

def _done_keys(skill: str, job_id: str, stage: str = "") -> set[str]:
    """events 에서 이 stage 의 item_done 키만 모은다(스테이지별 커서 분리)."""
    out: set[str] = set()
    for ev in read_events(skill, job_id):
        if ev.get("kind") == "item_done" and ev.get("stage", "") == stage:
            out.add(ev["key"])
    return out


def process_items(
    skill: str,
    job_id: str,
    items: Iterable[Any],
    *,
    key: Callable[[Any], str],
    process: Callable[[Any], None],
    stage: str = "",
    watermark_reader: Callable[[], "set[str]"] | None = None,
) -> tuple[int, int]:
    """항목 루프 — 재개 가능·멱등. 반환 (done 개수, skipped 개수).

    stage: '' 면 단일 스테이지(run). run_pipeline 스테이지는 자기 이름을 줘서 커서를 분리한다.
    watermark_reader: 외부 커서 스테이지(bplan A1 등)용(결정 4c). 주면 "이미 한 것"의 근거를
        events 가 아니라 이 콜백(외부 진실)에서 얻는다. 없으면 events 의 done_keys.
    """
    done = set(watermark_reader()) if watermark_reader is not None else _done_keys(skill, job_id, stage)
    skipped = 0
    n_done = 0
    for item in items:
        k = key(item)
        if k in done:                                  # 결정 C-① 가드: 이미 했다
            continue
        try:
            process(item)                              # 부작용(멱등해야 함) — 먼저
        except Exception as e:                         # noqa: BLE001  결정 A: 건너뛰고 계속
            append_event(skill, job_id, "item_skipped", key=k, stage=stage, error=repr(e))
            skipped += 1
            continue
        append_event(skill, job_id, "item_done", key=k, stage=stage)   # 커서 전진 — 성공 뒤에만
        done.add(k)
        # 🔴 [W12 · E-F12] 캐시를 **항목 루프 중에도** 갱신한다. 예전엔 `_materialize` 가
        #    create·transition·잡 종료 때만 돌아서, `kill -9` 직후 `cursor.json` 이
        #    `done: 0` 인데 events 엔 완료 2건이 있었다(실측). 상태 디렉토리만 보고
        #    판단하라는 규약(BASIC-STRUCTURE:207)이 3개 파일 중 1개에서 거짓이었다.
        #    매 항목마다 쓰면 비싸므로 N개마다 — 크래시 시 최대 N-1개 뒤처진다.
        n_done += 1
        if n_done % _CURSOR_FLUSH == 0:
            _materialize(skill, job_id, rebuild(skill, job_id))
    if n_done:
        _materialize(skill, job_id, rebuild(skill, job_id))   # 루프 종료 시 마지막 반영
    return len(done), skipped


# ─────────────────────────── 제네릭 드라이버 (단일 스테이지) ───────────────────────────

def run(
    skill: str,
    job_id: str,
    items: Iterable[Any],
    *,
    key: Callable[[Any], str],
    process: Callable[[Any], None],
    validate: Callable[[int, int], bool] | None = None,
    heavy: bool = False,
    watermark_reader: Callable[[], "set[str]"] | None = None,
) -> Derived:
    """잡을 실행한다. **재개 가능하고 멱등하다.** 세 결정이 여기서 함께 작동한다.

    A(건너뛰고 계속): 항목 하나가 예외를 던지면 item_skipped 를 남기고 다음으로 간다.
    B(events 가 진실): 진행은 전부 events 로 기록되고, 재개 시 그걸 재생해 이어간다.
    C(가드+멱등키+순서): key(item) 이 이미 done 이면 건너뛴다(가드). process() 가
       성공적으로 **끝난 뒤에** item_done 을 남긴다(부작용 먼저, 커서 마지막).
    D(슬롯): heavy=True 면 배타 슬롯을 잡는다. 못 잡으면 queued 로 남기고 SlotBusy.

    process(item) 은 **그 자체로 멱등**해야 한다(노션 upsert 등). 재개로 두 번 불릴 수 있다.
    """
    d = rebuild(skill, job_id)
    if d.state in _TERMINAL:
        return d                                  # 이미 끝난 잡은 다시 안 돈다(멱등)

    # 큐 상태로 표시하고 슬롯 확보(D)
    if d.state == "requested":
        transition(skill, job_id, "queued")

    slot_cm = heavy_slot() if heavy else _null_ctx()
    try:
        slot_cm.__enter__()
    except SlotBusy:
        # 슬롯을 못 잡으면 queued 로 남긴다 — 사람/스케줄러가 나중에 다시 run() 한다.
        append_event(skill, job_id, "queued_wait", reason="heavy 슬롯 점유 중")
        return rebuild(skill, job_id)

    try:
        transition(skill, job_id, "running")
        done_n, skipped = process_items(skill, job_id, items, key=key, process=process,
                                        watermark_reader=watermark_reader)

        # 검증(불변규칙 06): 0건은 정상이 아니라 실패다
        transition(skill, job_id, "validating")
        ok = validate(done_n, skipped) if validate else (done_n > 0)
        transition(skill, job_id, "completed" if ok else "failed",
                   done=done_n, skipped=skipped)
        _materialize(skill, job_id, rebuild(skill, job_id))
        return rebuild(skill, job_id)
    except Exception as e:                            # noqa: BLE001
        # 실패는 이벤트를 남기고 전파한다(불변규칙 06) — run_pipeline 과 달리 failed 로
        # 전이하지 않는다: 잡 단위 재시도(재개)가 run() 의 계약이라 종단시키면 안 된다.
        append_event(skill, job_id, "run_error", error=repr(e))
        raise
    finally:
        slot_cm.__exit__(None, None, None)


# ─────────────────────────── 다단계 파이프라인 드라이버 ───────────────────────────
# 순서형 잡(collect→judge→land, sync→scoring→drop). 스테이지 완료마다 stage_done 을 찍어
# 재개 시 완료 스테이지를 건너뛴다(결정 5, 스테이지 레벨 체크포인트). 항목 레벨(process_items)과
# 두 겹으로 재개된다 — 스테이지가 통째로 끝났으면 스킵, 스테이지 중간에 죽었으면 그 스테이지의
# done_keys 로 항목을 이어간다.

@dataclass
class Stage:
    """파이프라인 한 단계. run(skill, job_id)이 부작용을 수행하고 실패 시 예외를 던진다.

    스테이지 안에서 항목 루프가 필요하면 process_items(skill, job_id, ..., stage=name)을 부른다.
    """
    name: str
    run: Callable[[str, str], None]


def _done_stages(skill: str, job_id: str) -> set[str]:
    return {ev.get("stage") for ev in read_events(skill, job_id)
            if ev.get("kind") == "stage_done"}


def run_pipeline(
    skill: str,
    job_id: str,
    stages: "list[Stage]",
    *,
    resources: Iterable[str] | None = None,
) -> Derived:
    """다단계 잡 — 순서형. 각 스테이지 완료 시 stage_done, 재개 시 완료 스테이지 스킵.

    스테이지는 **순서 의존**(land 는 collect 결과가 있어야 한다). 한 스테이지가 예외를 던지면
    이후를 진행하지 않고 잡을 failed 로 둔다 — 다음 cadence 에 새 잡으로 재시도된다(워터마크가
    last_success 면). kill-9 로 죽으면 stage_done 이 없으니 재개 시 그 스테이지부터 다시 한다.

    resources: 이 잡이 건드리는 자원 이름들 → 배타 락(결정 3). 겹치는 자원의 잡끼리만 배타.
    """
    d = rebuild(skill, job_id)
    if d.state in _TERMINAL:
        # 🔴 [2026-08-14] 여기서 **조용히** 돌려주던 것이 실사고를 만들었다.
        #    `bplan_sync_job.py --relabel-only`(dry) 가 잡을 completed 로 만들었고, 곧바로
        #    `--apply` 를 돌리자 같은 job_id 라 여기서 즉시 반환됐다. 호출부는 `→ completed`
        #    를 찍었다 — **아무것도 안 했는데 성공과 글자 하나 다르지 않았다.** 42건이 그대로
        #    남았고, 사람이 Gmail 을 다시 세어보고서야 알았다.
        #    "안 한 것"과 "해서 끝난 것"은 다른 사실이다(불변규칙 05 · 근원 ⑤).
        print(f"↩️  {skill}/{job_id}: 이미 {d.state} 인 잡 — **아무 스테이지도 실행하지 않았다.**\n"
              f"    다시 돌리려면 새 job_id 로 만들어라(같은 id 는 설계상 1회다).")
        return d
    if d.state == "requested":
        transition(skill, job_id, "queued")

    # 🔴 [2026-08-13 W12 · 리뷰 E-F7] 잡 락은 **자원 이름 기준**이라 "같은 잡의 이중 실행"을
    #    막는 장치가 아니었다. `resources` 를 안 넘긴 잡은 **어떤 배타 락도 없어서** 같은
    #    job_id 를 두 프로세스가 동시에 돌릴 수 있었다. 실측(진짜 프로세스 2개):
    #    부작용이 겹쳐 실행되고 `events.jsonl` 에 `completed` **뒤에** `stage_done` 이 붙었다 —
    #    상태머신상 불가능한 순서인데 `rebuild` 는 그걸 접어도 completed 를 내므로
    #    **재생으로도 이상이 안 드러난다.**
    #
    #    실환경의 두 주체는 10분 launchd 틱과 사람의 CLI 다(정본 4-1 R4). 운영자가 런북대로
    #    손으로 잡을 돌리는 동안 스케줄러 틱이 겹치면 노션 기입·초안이 중복으로 나간다.
    #
    #    **잡 이중 실행은 자원 겹침과 다른 축이다.** job_id 자체를 락으로 잡는다.
    #    자원 락보다 **먼저** 잡는다 — 순서를 뒤집으면 자원을 쥔 채 잡 락을 기다려 데드락이 난다.
    job_lock = heavy_slot(f"job-{skill}-{job_id}")
    try:
        job_lock.__enter__()
    except SlotBusy:
        # ⚠️ 여기서 `append_event` 를 부르지 **않는다.** 그 잡의 events 는 지금 다른
        #    프로세스가 쓰는 중이고, `append_event` 는 쓰기 전에 `_repair_torn_tail` 로
        #    파일 끝을 truncate 한다 — 상대가 쓰는 도중이면 그 줄을 자를 수 있다.
        #    **장부는 실행 중인 인스턴스의 것이다.** 물러나는 쪽은 화면으로만 말한다.
        print(f"⚠️  {skill}/{job_id} 이 이미 실행 중이다 — 이번 호출은 물러난다(이중 실행 방지).")
        return rebuild(skill, job_id)

    lock_cm = resource_slots(resources) if resources else _null_ctx()
    try:
        lock_cm.__enter__()
    except SlotBusy:
        append_event(skill, job_id, "queued_wait", reason="자원 락 점유 중",
                     resources=sorted({n for n in (resources or []) if n}))
        job_lock.__exit__()
        return rebuild(skill, job_id)

    try:
        transition(skill, job_id, "running")
        done = _done_stages(skill, job_id)
        for st in stages:
            if st.name in done:                         # 완료 스테이지 스킵(재개)
                continue
            st.run(skill, job_id)                       # 부작용 — 실패 시 예외
            append_event(skill, job_id, "stage_done", stage=st.name)

        transition(skill, job_id, "validating")
        finished = _done_stages(skill, job_id)
        ok = all(st.name in finished for st in stages)
        transition(skill, job_id, "completed" if ok else "failed", stages=len(finished))
        _materialize(skill, job_id, rebuild(skill, job_id))
        return rebuild(skill, job_id)
    except Exception as e:                              # noqa: BLE001  스테이지 실패 = 이후 중단
        append_event(skill, job_id, "pipeline_error", error=repr(e))
        transition(skill, job_id, "failed", error=repr(e))
        return rebuild(skill, job_id)
    finally:
        # 잡은 순서의 **역순**으로 푼다(자원 → 잡). 잡 락을 먼저 풀면 그 틈에 다른
        # 프로세스가 잡 락을 얻고 자원 락에서 막혀, 아무도 못 도는 창이 생긴다.
        lock_cm.__exit__(None, None, None)
        job_lock.__exit__()


# ─────────────────────────── 유틸 ───────────────────────────

class _null_ctx:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _now_iso() -> str:
    # UTC 로 기록한다 — 타임존 경계 버그(2026-08-02 계층 A 사고)를 잡에서 되풀이하지 않는다.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _default_id(skill: str, request: dict) -> str:
    """기본 job_id — 채널 + UTC 날짜. 같은 날 같은 채널은 같은 잡(재개됨)."""
    ch = request.get("channel") or request.get("kind") or "job"
    day = time.strftime("%Y-%m-%d", time.gmtime())
    return f"{ch}-{day}"
