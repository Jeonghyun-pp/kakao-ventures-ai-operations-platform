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
    "validating": ("running", "completed", "failed"),   # validating→running: 검증 중 죽은 잡의 재개(검증 재진입)
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
            if not self._stale():
                raise SlotBusy(f"heavy 슬롯 점유 중: {self.path}")
            # stale 회수 — unlink 로 경쟁하면 두 프로세스가 서로의 새 락을 지워 둘 다 진입할 수
            # 있다(리뷰 D4). rename 은 원자적이라 **한 프로세스만** 소유권을 선점한다.
            claim = self.path.with_suffix(f".reap-{os.getpid()}")
            try:
                os.rename(self.path, claim)
            except FileNotFoundError:
                raise SlotBusy(f"heavy 슬롯 회수 경쟁 중 — 물러난다: {self.path}")
            claim.unlink(missing_ok=True)
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                # 회수 직후 제3자가 정당하게 선점 — SlotBusy 로 변환해 조용한 크래시를 막는다.
                raise SlotBusy(f"heavy 슬롯 점유 중(회수 직후 선점됨): {self.path}")
        os.write(self.fd, str(os.getpid()).encode())
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        self.path.unlink(missing_ok=True)

    # 락이 이 시간을 넘기면 pid 가 살아 있어도 회수한다. [W9 A-L13]
    # heavy 잡의 최장 런타임(judge 3시간)보다 넉넉히 위. 유령 락으로 시스템이 영구
    # 정지하는 것보다, 아주 드물게 이중 실행을 감수하는 편이 낫다 — 이중 실행은
    # 멱등 설계가 흡수하지만 영구 정지는 아무도 모른다(A-H3·A-H4 가 그 실증이었다).
    MAX_HOLD_SEC = 6 * 3600

    def _stale(self) -> bool:
        """회수 가능한 락인가 — pid 생존 **그리고** 나이를 함께 본다.

        [W9 A-L13] pid 만 보면 두 가지가 샌다.
          ① 빈 락파일(O_EXCL 성공 후 write 전 찰나)을 제3자가 보면 `int("")` → 0 → stale
             로 판정해 **정당한 락을 뺏는다**. 생성 직후 몇 초는 보호한다.
          ② 맥 슬립·SIGKILL 로 죽은 뒤 그 pid 가 **재사용**되면 영원히 not-stale 이다.
             그러면 모든 진입이 SlotBusy 로 조용히 물러난다(A-H3 실증: decide 영구 no-op).
        """
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return True
        try:
            raw = self.path.read_text().strip()
        except OSError:
            return True
        if not raw:
            # ① 쓰기 전 찰나 — 아주 최근이면 정당한 락으로 본다.
            return age > 5
        try:
            pid = int(raw)
        except ValueError:
            return True
        if pid <= 0:
            return True
        if age > self.MAX_HOLD_SEC:
            # ② pid 재사용 방어 — 이만큼 오래 잡고 있는 정상 잡은 없다.
            print(f"⚠️  락 {self.path.name} 이 {age/3600:.1f}시간째 — 유령 락으로 보고 회수한다")
            return True
        try:
            os.kill(pid, 0)      # 신호 0 = 존재 확인만
            return False         # 살아 있음
        except ProcessLookupError:
            return True          # 죽었음 → stale
        except PermissionError:
            return False         # 남의 프로세스지만 살아 있음


class resource_slots:
    """여러 자원 락을 한꺼번에 배타 획득 — **이름 정렬 순서로 잡아 데드락을 막는다**(결정 3, D′).

    동시성의 진짜 기준은 CPU 무게가 아니라 **공유 자원**이다(인박스·시트 A1·노션 DB). 한 잡이
    [gmail-inbox, notion-bplan] 둘을 건드리면 둘 다 배타로 잡아야 다른 잡과 안 겹친다.
    여러 락을 제각각 순서로 잡으면 교착(잡1: A→B, 잡2: B→A). 그래서 **항상 이름 정렬 순**으로.
    하나라도 못 잡으면 이미 잡은 것을 전부 풀고 SlotBusy — 부분 점유를 남기지 않는다.

    자원명으로 키잉된 배타 락은 heavy_slot 을 그대로 재사용한다(O_EXCL·stale reaping 검증됨).
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
        return d                                        # 이미 끝난 잡은 다시 안 돈다
    if d.state == "requested":
        transition(skill, job_id, "queued")

    lock_cm = resource_slots(resources) if resources else _null_ctx()
    try:
        lock_cm.__enter__()
    except SlotBusy:
        append_event(skill, job_id, "queued_wait", reason="자원 락 점유 중",
                     resources=sorted({n for n in (resources or []) if n}))
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
        lock_cm.__exit__(None, None, None)


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
