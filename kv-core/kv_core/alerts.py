"""알림 — **사실 기록(observe)과 발송 결정(dispatch)의 분리.** [W8 · W9 재설계 Batch C]

## 왜 갈랐나

W9 이전에는 발송 지점이 **14곳**이었고 각자 자기 정책을 가졌다.
`dedup`·`백오프`·`보상 재푸시`·`킬 스위치 해석`·`에스컬레이션` 이 호출부마다 달랐다. 그래서:

- **보상 재푸시가 `gate_block` 한 kind 에만** 있었다 — 7개 push 호출부 중 반환값을 쓰는
  곳이 하나뿐이라, 나머지는 슬랙이 5xx 인 순간 claim 을 반납하고 **흔적 없이 사라졌다**.
  appstore 부분결손("해당 차트는 영구 손실 가능")이 그렇게 유실될 수 있었다.
- **백오프가 없어** never-succeeded 가 사람이 액션할 때까지 매일 영원히 같은 문장을 보냈다
  (실측: judge 알림이 8/7~8/10 4일 연속. 30일 시뮬은 27일 연속).
- 킬 스위치를 `notify.slack` 은 `True`, `slackbot.post` 는 `None`(실패) 로 해석했다.

## 지금의 규약

    observe(kind, identity, text, **meta)   개별 모듈이 부르는 것. **사실만 남긴다.**
                                            발송 여부를 판단하지 않는다.
    dispatch(state)                          알릴지 말지를 정하는 **유일한 지점.**
                                            정책(dedup·백오프·에스컬레이션·보상)이 여기 모인다.
    push(identity, text)                     실제 발송 프리미티브. dedup 마커의 원자 선점.

**판정은 여기 없다.** "무엇이 문제인가"는 `health.compute()` 가 답하고, 여기는 "그걸 지금
사람에게 알릴 것인가"만 답한다.

## dedup 이 구조인 이유
같은 실패가 틱마다 울리면 늑대소년이 된다(T3 사고의 교훈). 마커 파일을 **O_EXCL 로 원자
선생성** → 성공한 프로세스만 발송 — 동시 틱의 이중 발송을 락 없이 구조로 차단.

## 보상이 구조인 이유
마커는 **발송에 성공했을 때만** 남는다. 실패하면 claim 을 반납하므로 다음 틱이 같은
identity 를 다시 시도한다. 그래서 `dispatch` 는 "오늘 관측된 사실"을 매 틱 전부 훑는다 —
특정 kind 만 보상하는 특례가 없다.

의존 방향: alerts → {health, notify, paths}. gate·workers·tick → alerts (단방향, 순환 0).
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import notify, paths

_cfg: dict | None = None


def config(force: bool = False) -> dict:
    global _cfg
    if _cfg is None or force:
        import yaml
        p = paths.repo_root() / "config" / "alerts.yaml"
        _cfg = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _cfg or {}


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _sent_dir() -> Path:
    return paths.data_root() / "_alerts" / "sent"


def _policy_dir() -> Path:
    return paths.data_root() / "_alerts" / "policy"


_ID_BAD = __import__("re").compile(r"[^A-Za-z0-9._-]")


def _safe_id(identity: str) -> str:
    """[W9 B-F8] identity 는 파일명이 된다 — 경로 문자가 있으면 push 가 raise 하고,
    그 raise 가 호출부에서 터져 틱 나머지를 죽였다. 위생 처리.

    [B-L1] 100자 절단만 하면 접미사가 다른 긴 identity 두 개가 같은 파일로 뭉친다
    (한쪽 알림이 다른 쪽 dedup 마커에 먹힌다). 절단할 때는 **해시를 붙여** 충돌을 없앤다.
    """
    safe = _ID_BAD.sub("_", str(identity))
    if len(safe) <= 100:
        return safe
    import hashlib
    return safe[:88] + "-" + hashlib.sha1(str(identity).encode()).hexdigest()[:8]


# ─────────────────────────── 발송 프리미티브 ───────────────────────────

def push(identity: str, text: str, *, day: date | None = None) -> str:
    """즉시 푸시 — identity 당 하루 1회. 반환: "sent" | "deduped" | "suppressed" | "failed".

    [W9 B-F2] **claim(동시성) 과 delivered(dedup) 를 분리**한다. 예전엔 마커를 먼저 만들고
    발송했는데, 웹훅이 실패해도 마커가 남아 ①당일 재시도를 막고 ②다음 날은 today 필터가
    배제해 **보상 재푸시가 실행 가능한 경로 자체가 없었다**(실증: 영구 유실).
    이제 발송 실패 시 claim 을 되돌려 다음 틱이 다시 시도한다.

    [W9 B-F6] bool 이 "dedup 스킵"과 "발송 실패" 둘 다였다 — 장부가 거짓말을 했다. 3값으로.
    """
    ident = _safe_id(identity)
    d = _sent_dir()
    d.mkdir(parents=True, exist_ok=True)
    marker = d / f"{ident}-{day or _utc_today()}"
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)   # claim (동시 틱 배타)
        os.close(fd)
    except FileExistsError:
        return "deduped"                          # 오늘 이미 발송됨 — no-op
    if notify.slack(text):
        marker.write_text(f'{{"delivered":true,"ts":"{datetime.now(timezone.utc).isoformat()}"}}',
                          encoding="utf-8")
        return "sent"
    marker.unlink(missing_ok=True)                # 미발송 → claim 반납(다음 틱 재시도 가능)
    # [W9 Phase0 0-5] **억제 판정은 발송 경계(notify.slack)에만 두고, 여기서는 결과를 해석한다.**
    # 처음엔 이 함수 맨 앞에서 is_suppressed() 를 검사했는데, 그러면 notify.slack 을 스텁으로
    # 갈아끼운 테스트까지 억제돼 알림 로직 자체가 검증 불가가 됐다(_synthetic_alerts 4건 전멸).
    # 킬 스위치의 목적은 "스텁을 깜빡했을 때"의 방어다 — 스텁이 걸렸으면 방어할 대상이 없다.
    if notify.is_suppressed():
        print(f"  🚫 알림 억제됨(KV_NO_SEND) — {identity}: {text[:80]}")
        return "suppressed"
    return "failed"


# ─────────────────────────── 사실 기록 (observe) ───────────────────────────

def append_event(kind: str, **meta) -> None:
    """알림 이벤트 장부 — $KV_DATA/_alerts/events.jsonl (torn-tail 규율 = job.py 와 동일)."""
    from .job import _repair_torn_tail
    d = paths.data_root() / "_alerts"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "events.jsonl"
    _repair_torn_tail(p)
    line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       "kind": kind, **meta}, ensure_ascii=False)
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def observe(kind: str, identity: str, text: str, **meta) -> None:
    """**사실만 기록한다.** 발송 여부는 판단하지 않는다 — 그건 dispatch 의 일이다.

    개별 모듈(gate·workers·approvals_tick)이 부르는 유일한 알림 API. 여기서 발송을
    시도하지 않기 때문에, 그 모듈들은 슬랙 상태·dedup·백오프를 알 필요가 없다.
    발송 실패로 인한 유실도 없다 — 사실은 장부에 남고, 다음 dispatch 가 집어간다.
    """
    append_event(kind, identity=identity, text=text, **meta)


# ─────────────────────────── 발송 정책 (백오프·에스컬레이션) ───────────────────────────

def _policy(ident: str) -> dict:
    p = _policy_dir() / f"{_safe_id(ident)}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _policy_write(ident: str, st: dict) -> None:
    from .job import atomic_write
    atomic_write(_policy_dir() / f"{_safe_id(ident)}.json",
                 json.dumps(st, ensure_ascii=False))


def _interval_days(age_days: int) -> int:
    """이 알림이 처음 뜬 지 `age_days` 일 됐을 때, 며칠마다 다시 알릴 것인가.

    [B-M1] 유예만 있고 상한이 없으면 사람이 액션할 때까지 매일 영원히 울린다 —
    alert fatigue 의 교과서 정의다. 그렇다고 아예 멈추면 그건 다시 가짜 침묵이다.
    **간격을 벌리되 끊지는 않는다.**
    """
    b = config().get("backoff") or {}
    if age_days < int(b.get("daily_until_days", 2)):
        return 1
    if age_days < int(b.get("sparse_after_days", 7)):
        return int(b.get("sparse_every_days", 2))
    return int(b.get("rare_every_days", 7))


def _should_send(ident: str, today: date) -> tuple[bool, dict]:
    """백오프 판정. 반환: (보낼까, 갱신될 정책 상태)."""
    st = _policy(ident)
    first = st.get("first") or today.isoformat()
    last = st.get("last_sent")
    count = int(st.get("count", 0))
    new = {"first": first, "last_sent": last, "count": count}
    try:
        age = (today - date.fromisoformat(first)).days
    except ValueError:
        age = 0
    if last == today.isoformat():
        return False, new                          # 하루 1회
    if last:
        try:
            gap = (today - date.fromisoformat(last)).days
        except ValueError:
            gap = 99
        if gap < _interval_days(age):
            return False, new                      # 백오프 구간
    new.update({"last_sent": today.isoformat(), "count": count + 1})
    return True, new


def _escalate(text: str, st: dict, today: date) -> str:
    """반복되는 알림에는 **얼마나 오래 방치됐는지**를 실어 보낸다.

    같은 문장을 N번 받으면 사람은 그것을 배경 소음으로 처리한다. 문장이 자라야 눈에 띈다.
    """
    n = int(st.get("count", 1))
    try:
        age = (today - date.fromisoformat(st.get("first", today.isoformat()))).days
    except ValueError:
        age = 0
    if n < int((config().get("backoff") or {}).get("escalate_after_sends", 4)):
        return text
    return (f"🔁 *{age}일째 미해결* (같은 알림 {n}회째 · 이후 간격 "
            f"{_interval_days(age)}일)\n{text}")


def _forget(active: set[str], today: date) -> None:
    """더 이상 발동하지 않는 알림의 정책 상태를 지운다.

    이게 없으면 문제가 해결된 뒤 재발했을 때 **백오프가 남아 있어 늦게 알린다**.
    복구를 감지하는 별도 신호가 없으므로 "오늘 안 울렸다"를 복구로 본다.
    """
    d = _policy_dir()
    if not d.exists():
        return
    keep = {_safe_id(a) for a in active}
    for p in d.glob("*.json"):
        if p.stem in keep:
            continue
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
            last = date.fromisoformat(str(st.get("last_sent") or "1970-01-01"))
        except (json.JSONDecodeError, OSError, ValueError):
            p.unlink(missing_ok=True)
            continue
        if (today - last).days >= 2:               # 이틀 조용하면 해결로 본다
            p.unlink(missing_ok=True)


# ─────────────────────────── 알림 원천 ───────────────────────────

def _from_state(state) -> list[tuple[str, str]]:
    """상태에서 나오는 알림 — **판정하지 않는다.** health 가 이미 정한 것을 문구로 만든다."""
    out: list[tuple[str, str]] = []
    for t in state.alertable_tasks():
        kind = "never-succeeded" if t.status == "never_bad" else "task-fail"
        out.append((f"{kind}-{t.name}", f"🔴 {t.name}: {t.note}"))
    return out


def _from_auth(state) -> list[tuple[str, str]]:
    """[B-H4] 인증 **미확인**을 침묵시키지 않는다.

    예전 `_auth_suspect` 는 "최근 호출이 전부 실패"만 봤고, 호출이 0이면 아무 말도 하지
    않았다. 그런데 무인 실행은 claude 인증에 의존한다 — 확인된 적이 없다는 사실 자체가
    알릴 값어치가 있다. "실패했다"와 "모른다"를 **다른 문장으로** 낸다.
    """
    a = state.auth or {}
    n = int((config().get("auth_suspect") or {}).get("consecutive_session_errors", 3))
    limit = int((config().get("auth_canary") or {}).get("unknown_days", 3))

    # ① 열화 — 연속 실패. [W9 B-F7] **오늘 발생분이 포함될 때만** 발동한다(어제 실패로
    #    오늘 또 울리면 identity 가 날짜별이라 dedup 이 못 막는다 = alert fatigue 의 재생산).
    if a.get("consecutive_failures", 0) >= n and a.get("failure_today"):
        return [("auth-suspect-claude",
                 f"🔴 claude 세션 연속 {a['consecutive_failures']}회 실패 — 인증 만료 의심. "
                 f"맥에서 `claude` 로그인 상태 확인 요망.")]

    # ② 미확인 [B-H4] — 실패가 아니라 **아무도 확인하지 않은 상태**. 예전엔 이 경우가
    #    통째로 침묵이라 "인증 만료"와 "며칠째 안 부름"이 관측적으로 동일했다.
    if a.get("never_confirmed") and a.get("calls_seen"):
        return [("auth-unknown-claude",
                 "🔴 claude 세션 성공 기록이 없다(호출은 있었다) — 인증 상태 확인 요망.")]
    unknown = a.get("unknown_days")
    if unknown is not None and unknown >= limit:
        return [("auth-unknown-claude",
                 f"⚠️ claude 세션을 {unknown}일째 성공적으로 부른 적이 없다 — "
                 f"무인 실행이 인증에 의존하는데 그 상태가 미확인이다.")]
    return []


def _from_events(today: date) -> list[tuple[str, str]]:
    """오늘 관측된 사실 → 알림 후보. **kind 를 가리지 않는다.**

    [B-H3] 예전엔 보상 재푸시가 `gate_block` 전용이었다. 그래서 appstore 부분결손이나
    승인 실행 실패는 슬랙이 한 번 5xx 를 주면 **재시도 경로 자체가 없었다**.
    dedup 마커가 이미 "오늘 보냈나"를 알고 있으므로, 여기서는 걸러내지 않고 전부 넘긴다.
    """
    ev = paths.data_root() / "_alerts" / "events.jsonl"
    if not ev.exists():
        return []
    out: dict[str, str] = {}
    iso = today.isoformat()
    for ln in ev.read_text(encoding="utf-8").splitlines()[-500:]:   # [B-F16] tail 만
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if not str(e.get("ts", "")).startswith(iso):
            continue
        ident, text = e.get("identity"), e.get("text")
        if ident and text:
            out[str(ident)] = str(text)            # 같은 identity 는 마지막 문구로
        elif e.get("kind") == "gate_block":        # 구 스키마(identity/text 없음) 호환
            out[f"gate-block-{e.get('resource', '?')}"] = \
                f"🔴 게이트 차단: {e.get('resource')} — {e.get('reason')}"
    return sorted(out.items())


# ─────────────────────────── dispatch (유일한 결정 지점) ───────────────────────────

def dispatch(state=None, *, today: date | None = None) -> list[str]:
    """무엇을 알릴지 결정하고 보낸다. 반환: **실제 발송된** identity 목록.

    [W9 B-F9] 각 원천을 격리한다 — `registry.load()` 는 fail-loud 라 tasks.yaml 오타 하나가
    알림 체계 전체를 정지시켰다(그리고 상위 broad except 가 삼켜 겉으론 정상이었다).
    [B-M2] 격리는 **보상 루프에도** 적용한다 — 예전엔 그 루프가 `_try` 밖이라 디스크 오류
    하나로 scan() 전체가 죽었다.
    """
    today = today or _utc_today()
    sent: list[str] = []
    candidates: list[tuple[str, str]] = []

    def _try(name, fn, *a):
        try:
            return fn(*a)
        except Exception as e:                    # noqa: BLE001
            print(f"🔴 alerts.{name} 실패: {e!r}")
            candidates.append((f"alerts-{name}-broken", f"🔴 알림 원천 {name} 실패: {e!r}"))
            return None

    if state is None:
        from . import health
        state = _try("state", health.compute, today)
    if state is not None:
        candidates += _try("from_state", _from_state, state) or []
        candidates += _try("from_auth", _from_auth, state) or []
    candidates += _try("from_events", _from_events, today) or []

    active = {ident for ident, _ in candidates}
    for ident, text in candidates:
        ok, st = _try("policy", _should_send, ident, today) or (False, {})
        if not ok:
            continue
        if push(ident, _escalate(text, st, today), day=today) == "sent":
            sent.append(ident)
            _try("policy_write", _policy_write, ident, st)
    _try("forget", _forget, active, today)
    return sent


def scan() -> list[str]:
    """구 이름 — `dispatch()` 의 별칭. 호출부(approvals_tick)가 옮겨갈 때까지 유지."""
    return dispatch()


def _task_failures(state=None) -> list[tuple[str, str]]:
    """구 이름 — 상태 기반 알림 후보. 테스트·진단 스크립트가 부른다."""
    if state is None:
        from . import health
        state = health.compute()
    return _from_state(state)
