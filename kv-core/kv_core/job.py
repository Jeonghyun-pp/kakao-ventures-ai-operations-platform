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

import json
import os
import time
from dataclasses import dataclass
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
    "validating": ("completed", "failed"),
    "completed":  (),   # 종단
    "failed":     (),   # 종단
}
_TERMINAL = ("completed", "failed")


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
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())          # 디스크까지 내려가야 크래시에 안전하다
    os.replace(tmp, path)             # 원자적 교체


# ─────────────────────────── 이벤트(진실) ───────────────────────────
# append-only 장부. 이 함수만이 진실을 기록한다. 나머지(state·cursor)는 전부 이걸 재생한 결과다.

def append_event(skill: str, job_id: str, kind: str, **data: Any) -> dict:
    """events.jsonl 에 한 줄 추가한다. **덮어쓰지 않는다 — 중단돼도 앞부분이 남는다.**

    append + fsync 로 쓴다. 마지막 한 줄이 크래시로 반쯤 써질 수는 있으나, 재생 시
    깨진 마지막 줄은 무시한다(rebuild 참조). 앞줄들은 이미 온전하다.
    """
    ev = {"ts": _now_iso(), "kind": kind, **data}
    line = json.dumps(ev, ensure_ascii=False)
    p = _events_path(skill, job_id)
    p.parent.mkdir(parents=True, exist_ok=True)
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
    out: list[dict] = []
    for i, raw in enumerate(p.read_text(encoding="utf-8").splitlines()):
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            # 마지막 줄만 허용. 중간 줄이 깨졌다면 그건 append 규약 위반이라 드러내야 한다.
            if raw and i == len(p.read_text(encoding="utf-8").splitlines()) - 1:
                break
            raise
    return out


# ─────────────────────────── 재생(fold) — events → 파생 상태 ───────────────────────────
# 결정 B: 여기가 "진실을 상태로 바꾸는" 유일한 지점이다. state.json·cursor.json 은 이 결과의 캐시.

@dataclass
class Derived:
    state: str
    done_keys: set[str]          # 부작용까지 완료된 항목(멱등 가드의 근거)
    skipped: list[dict]          # 건너뛴 항목 {key, error}
    request: dict


def rebuild(skill: str, job_id: str) -> Derived:
    """events.jsonl 을 재생해 현재 상태를 계산한다. **파일이 아니라 이벤트가 진실이다.**"""
    state = "requested"
    done: set[str] = set()
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
        elif k == "item_skipped":
            skipped.append({"key": ev.get("key"), "error": ev.get("error")})
    return Derived(state=state, done_keys=done, skipped=skipped, request=request)


def _materialize(skill: str, job_id: str, d: Derived) -> None:
    """파생물(state.json·cursor.json)을 디스크에 캐시한다. 없어도 rebuild 로 복원 가능하다."""
    atomic_write(_state_path(skill, job_id),
                 json.dumps({"state": d.state, "updated": _now_iso()},
                            ensure_ascii=False, indent=2))
    atomic_write(_cursor_path(skill, job_id),
                 json.dumps({"done": len(d.done_keys), "skipped": len(d.skipped),
                             "done_keys": sorted(d.done_keys)},
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

    죽은 프로세스가 남긴 락은 pid 생존 확인으로 회수한다(stale lock reaping).
    """

    def __init__(self, kind: str = "heavy"):
        self.path = _slot_path(kind)
        self.fd: int | None = None

    def __enter__(self):
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if self._stale():
                self.path.unlink(missing_ok=True)
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            else:
                raise SlotBusy(f"heavy 슬롯 점유 중: {self.path}")
        os.write(self.fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        self.path.unlink(missing_ok=True)

    def _stale(self) -> bool:
        """락 파일의 pid 가 이미 죽었으면 stale — 회수 가능."""
        try:
            pid = int(self.path.read_text().strip() or "0")
        except (ValueError, OSError):
            return True
        if pid <= 0:
            return True
        try:
            os.kill(pid, 0)      # 신호 0 = 존재 확인만
            return False         # 살아 있음
        except ProcessLookupError:
            return True          # 죽었음 → stale
        except PermissionError:
            return False         # 남의 프로세스지만 살아 있음


# ─────────────────────────── 제네릭 드라이버 ───────────────────────────

def run(
    skill: str,
    job_id: str,
    items: Iterable[Any],
    *,
    key: Callable[[Any], str],
    process: Callable[[Any], None],
    validate: Callable[[int, int], bool] | None = None,
    heavy: bool = False,
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
        done = set(rebuild(skill, job_id).done_keys)   # 재개 시작점: 이미 끝낸 것들
        skipped = 0
        for item in items:
            k = key(item)
            if k in done:                              # 결정 C-① 가드: 이미 했다
                continue
            try:
                process(item)                          # 부작용(멱등해야 함) — 먼저
            except Exception as e:                     # noqa: BLE001  결정 A: 건너뛰고 계속
                append_event(skill, job_id, "item_skipped", key=k, error=repr(e))
                skipped += 1
                continue
            append_event(skill, job_id, "item_done", key=k)   # 커서 전진 — 부작용 성공 뒤에만
            done.add(k)

        # 검증(불변규칙 06): 0건은 정상이 아니라 실패다
        transition(skill, job_id, "validating")
        ok = validate(len(done), skipped) if validate else (len(done) > 0)
        transition(skill, job_id, "completed" if ok else "failed",
                   done=len(done), skipped=skipped)
        _materialize(skill, job_id, rebuild(skill, job_id))
        return rebuild(skill, job_id)
    finally:
        slot_cm.__exit__(None, None, None)


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
