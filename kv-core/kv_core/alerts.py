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


_cfg_warned: set[str] = set()


def cfg_int(section: str, key: str, default: int) -> int:
    """설정 정수 읽기 — **잘못된 값이 알림 체계를 죽이지 않게 한다.**

    [자기리뷰 F2] `escalate_after_sends: '4회'` 같은 값 하나면 YAML 은 통과하고
    `int()` 가 dispatch 안에서 터져 **그 틱의 전 알림이 미발송**된다. 흔적은 print 한 줄뿐
    이라 겉으론 정상 — W9 가 없애려던 바로 그 실패 모드(가짜 침묵)다.
    알림은 안전망이므로 **설정 사고에 죽지 않고 기본값으로 계속 돌아야** 한다. 대신 조용히
    넘기지 않는다: 한 번 loud 로 남기고, 아래 `config_problems()` 가 그 사실 자체를 알린다.
    """
    raw = (config().get(section) or {}).get(key, default)
    try:
        v = int(raw)
        if v < 0:
            raise ValueError("음수")
        return v
    except (TypeError, ValueError):
        k = f"{section}.{key}"
        if k not in _cfg_warned:
            _cfg_warned.add(k)
            print(f"🔴 alerts.yaml {k} 값이 잘못됐다({raw!r}) — 기본값 {default} 으로 계속한다")
        return default


def config_problems() -> list[str]:
    """설정값 검증 — 잘못된 키 목록. CI 선언 정합 검사와 dispatch 가 함께 쓴다."""
    bad: list[str] = []
    spec = (("backoff", "daily_until_days", 2), ("backoff", "sparse_after_days", 7),
            ("backoff", "sparse_every_days", 2), ("backoff", "rare_every_days", 7),
            ("backoff", "escalate_after_sends", 4),
            ("never_succeeded", "grace_periods", 3),
            ("auth_suspect", "consecutive_session_errors", 3),
            ("auth_canary", "unknown_days", 3),
            ("scheduler_silence", "hours", 36))
    for sec, key, _d in spec:
        raw = (config().get(sec) or {}).get(key)
        if raw is None:
            continue                              # 미선언은 기본값 — 정상
        try:
            if int(raw) < 0:
                raise ValueError
        except (TypeError, ValueError):
            bad.append(f"{sec}.{key}={raw!r}")
    return bad


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
    # [자기리뷰 F4] 일자별 파일 — 단일 파일은 회전이 없어 무한히 자라고, 읽는 쪽이 매 틱
    # 전량 read_text 를 한다. 날짜로 나누면 tail 도 전량 읽기도 필요 없어진다.
    # 파일은 **그 이벤트의 시각**으로 이름 짓는다. 예전엔 벽시계 오늘로 지었는데, 읽는 쪽
    # (`_from_events`)은 주입된 `today` 기준으로 파일을 고르므로 둘이 어긋났다 —
    # UTC 자정이 넘어가는 순간 방금 기록한 사실을 못 찾는다(날짜가 바뀌며 실제로 드러났다).
    # 쓰는 쪽과 읽는 쪽이 같은 기준을 쓰게 한다.
    ts = str(meta.pop("ts", "") or datetime.now(timezone.utc).isoformat(timespec="seconds"))
    day = ts[:10] if len(ts) >= 10 else datetime.now(timezone.utc).date().isoformat()
    p = d / f"events-{day}.jsonl"
    _repair_torn_tail(p)
    line = json.dumps({"ts": ts, "kind": kind, **meta}, ensure_ascii=False)
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_events(day: date | None = None, *, lookback_days: int = 2) -> list[dict]:
    """관측 사실 판독 — 파일이 어떻게 나뉘어 있는지 호출부가 몰라도 되게 한다.

    일자별 파일로 바꾸면서(자기리뷰 F4) 경로를 직접 읽던 곳들이 깨졌다. 배치는 언제든
    또 바뀔 수 있으므로 판독 지점을 하나로 둔다.
    """
    out: list[dict] = []
    for f in _event_files(day or _utc_today(), lookback_days):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return out


def observe(kind: str, identity: str, text: str, **meta) -> None:
    """**사실만 기록한다.** 발송 여부는 판단하지 않는다 — 그건 dispatch 의 일이다.

    개별 모듈(gate·workers·approvals_tick)이 부르는 유일한 알림 API. 여기서 발송을
    시도하지 않기 때문에, 그 모듈들은 슬랙 상태·dedup·백오프를 알 필요가 없다.
    발송 실패로 인한 유실도 없다 — 사실은 장부에 남고, 다음 dispatch 가 집어간다.
    """
    append_event(kind, identity=identity, text=text, **meta)


# ─────────────────────────── 발송 정책 (백오프·에스컬레이션) ───────────────────────────

def _policy(ident: str) -> dict:
    """정책 상태 — **저장하는 것은 `last_seen` 하나뿐이다.**

    [W9 자기감사] 처음엔 `first`·`last_sent`·`count` 도 여기 저장했다. 그런데 그 셋은
    **발송 마커(`_alerts/sent/<ident>-<날짜>`)에서 그대로 유도된다** — 마커는 발송에
    성공했을 때만 생기므로 그 자체가 발송 이력의 정본이다.

    파생을 저장하면 진실이 둘이 되고 반드시 어긋난다는 것이 이번 재설계의 진단(R1)인데,
    그 처방을 쓰면서 같은 걸 만들었고 **실데이터에서 실제로 어긋났다**:

        마커에서 유도: first=2026-08-07 · last_sent=2026-08-10 · count=4
        정책 파일 저장: first=2026-08-10 · last_sent=None     · count=0

    그 상태로는 `count=0` 이라 에스컬레이션이 영영 안 뜬다. F5(미래 `last_sent` 가 영구
    침묵을 만든다)도 같은 뿌리였다 — 저장된 사본이 틀려도 아무도 대조하지 않았다.
    이제 유도 불가능한 것(`last_seen`: 후보로 등장했으나 발송하지 않은 날)만 저장한다.
    """
    p = _policy_dir() / f"{_safe_id(ident)}.json"
    if not p.exists():
        return {}
    try:
        st = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {"last_seen": st.get("last_seen")}


def _policy_write(ident: str, st: dict) -> None:
    from .job import atomic_write
    atomic_write(_policy_dir() / f"{_safe_id(ident)}.json",
                 json.dumps({"last_seen": st.get("last_seen")}, ensure_ascii=False))


def sent_days(ident: str) -> list[str]:
    """이 identity 의 **실제 발송일 목록** — 마커가 정본이다(오름차순).

    마커는 `push` 가 발송에 성공했을 때만 남긴다(실패하면 claim 을 반납한다). 그래서
    "언제 몇 번 보냈나"는 계산하면 되는 것이지 따로 적어둘 것이 아니다.
    """
    d = _sent_dir()
    if not d.exists():
        return []
    ident = _safe_id(ident)
    out: list[str] = []
    for p in d.glob(f"{ident}-*"):
        tail = p.name[len(ident) + 1:]
        if len(tail) == 10 and tail[4] == "-" and tail[7] == "-":
            out.append(tail)
    return sorted(out)


def _interval_days(age_days: int) -> int:
    """이 알림이 처음 뜬 지 `age_days` 일 됐을 때, 며칠마다 다시 알릴 것인가.

    [B-M1] 유예만 있고 상한이 없으면 사람이 액션할 때까지 매일 영원히 울린다 —
    alert fatigue 의 교과서 정의다. 그렇다고 아예 멈추면 그건 다시 가짜 침묵이다.
    **간격을 벌리되 끊지는 않는다.**
    """
    if age_days < cfg_int("backoff", "daily_until_days", 2):
        return 1
    if age_days < cfg_int("backoff", "sparse_after_days", 7):
        return cfg_int("backoff", "sparse_every_days", 2)
    return cfg_int("backoff", "rare_every_days", 7)


def _should_send(ident: str, today: date) -> tuple[bool, dict]:
    """백오프 판정. 반환: (보낼까, 저장할 정책 상태).

    발송 이력은 **마커에서 유도**한다(`sent_days`) — 저장된 사본과 대조할 필요가 없다.
    """
    days = [d for d in sent_days(ident) if d <= today.isoformat()]   # 미래 마커는 무시(F5)
    first, last, count = (days[0] if days else None), (days[-1] if days else None), len(days)
    new = {"last_seen": today.isoformat()}
    if last == today.isoformat():
        return False, new                          # 하루 1회
    try:
        age = (today - date.fromisoformat(first)).days if first else 0
    except ValueError:
        age = 0
    if last:
        try:
            gap = (today - date.fromisoformat(last)).days
        except ValueError:
            gap = 99
        if 0 <= gap < _interval_days(age):
            return False, new                      # 백오프 구간
    return True, new


def _history(ident: str, today: date) -> tuple[int, int]:
    """(발송 횟수, 최초 발송 이후 경과일) — 에스컬레이션 문구가 쓰는 값. 전부 유도."""
    days = [d for d in sent_days(ident) if d <= today.isoformat()]
    if not days:
        return 0, 0
    try:
        return len(days), (today - date.fromisoformat(days[0])).days
    except ValueError:
        return len(days), 0


def _touch_seen(ident: str, today: date) -> None:
    """이 identity 가 **오늘 후보로 등장했다**는 사실만 기록한다(발송 여부와 무관).

    [자기리뷰 F3] `_forget` 이 "이틀 조용하면 해결"을 `last_sent` 로 판정했는데, 백오프
    구간(2~7일 간격)의 침묵과 실제 해결이 구분되지 않았다. 그래서 위상에 따라
      · 재발이 최대 5~6일 늦게 알려지거나
      · 간헐 실패(맥 슬립으로 하루 걸러 도는 이 환경의 정상)에는 정책이 매번 지워져
        **간격이 2일에서 멈추고 count 가 리셋돼 에스컬레이션이 한 번도 안 나갔다**
        (30일 시뮬 15회 발송 — 설계 목표 10회 이하를 넘긴다)
    "문제가 아직 있는가"는 발송이 아니라 **후보로 등장했는가**로 봐야 한다.
    """
    st = _policy(ident)
    if st.get("last_seen") == today.isoformat():
        return
    st["last_seen"] = today.isoformat()
    st.setdefault("first", today.isoformat())
    st.setdefault("count", 0)
    _policy_write(ident, st)


def _escalate(text: str, ident: str, today: date) -> str:
    """반복되는 알림에는 **얼마나 오래 반복했는지**를 실어 배경 소음화를 막는다.

    횟수·기간은 마커에서 유도한다(저장된 카운터를 믿지 않는다 — 그게 어긋나서
    에스컬레이션이 영영 안 뜨던 상태였다). 이번 발송분까지 포함해 센다.
    """
    n, age = _history(ident, today)
    n += 1                                         # 지금 보내는 것까지
    if n < cfg_int("backoff", "escalate_after_sends", 4):
        return text
    # 기준을 섞지 않는다 — 여기서 아는 것은 "이 알림을 며칠째 반복했나"이고,
    # "그 문제가 며칠째인가"는 본문(health 의 note)이 이미 말한다.
    return (f"🔁 *같은 알림 {n}회째 · {age}일간 반복* (이후 간격 {_interval_days(age)}일)\n"
            f"{text}")


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
            seen = date.fromisoformat(
                str(st.get("last_seen") or st.get("last_sent") or "1970-01-01"))
        except (json.JSONDecodeError, OSError, ValueError):
            p.unlink(missing_ok=True)
            continue
        gone = (today - seen).days
        if gone < 0:                               # [F5] 미래값 — 이상이므로 즉시 폐기
            p.unlink(missing_ok=True)
        elif gone >= 2:                            # 이틀 연속 후보에 없으면 해결로 본다
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
    n = cfg_int("auth_suspect", "consecutive_session_errors", 3)
    limit = cfg_int("auth_canary", "unknown_days", 3)

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


def _from_scheduler(state) -> list[tuple[str, str]]:
    """[C-H6] 스케줄러 정지 — **10분 틱이 알린다**(스케줄러 자신이 아니라).

    이 신호가 없으면 스케줄러 정지의 MTTD 는 "사람이 요즘 조용하네 할 때까지"였다.
    """
    sc = state.scheduler or {}
    if not sc.get("silent"):
        return []
    return [("scheduler-silent", f"🔴 {sc.get('note')}")]


def _event_files(today: date, lookback_days: int) -> list[Path]:
    """읽을 이벤트 파일들 — 일자별 파일 + 구 단일 파일(호환)."""
    d = paths.data_root() / "_alerts"
    out = [d / f"events-{(today - timedelta(days=i)).isoformat()}.jsonl"
           for i in range(lookback_days + 1)]
    out.append(d / "events.jsonl")               # 구 스키마(단일 파일) — 남아 있으면 함께 읽는다
    return [p for p in out if p.exists()]


def _from_events(today: date, *, lookback_days: int = 2,
                 cap: int = 200) -> list[tuple[str, str]]:
    """최근 관측된 사실 → 알림 후보. **kind 를 가리지 않는다.**

    [B-H3] 예전엔 보상 재푸시가 `gate_block` 전용이었다. dedup 마커가 이미 "오늘 보냈나"를
    알고 있으므로 여기서는 걸러내지 않고 넘긴다.

    [자기리뷰 F1] 창을 **달력일에서 룩백으로** 바꿨다. `startswith(오늘)` 이면 관측과
    dispatch 사이에 UTC 자정이 끼는 순간 그 사실이 **영구 유실**된다 — 다음 dispatch 는
    어제 것을 안 보고, 재관측이 없는 사실(고아 초안·게이트 경고 교차·틱 오류)은 다시
    생기지 않기 때문이다. 실측: 승인 틱 390회 중 1시간 초과 공백 21건, 그중 UTC 자정을
    넘긴 것이 6일간 3건(최대 19.5시간 — 맥 슬립).
    중복 발송은 `push` 의 일자별 마커가 이미 막으므로 룩백은 안전하다.

    [자기리뷰 F4] **필터를 먼저 걸고 상한은 그 뒤에** 씌운다(예전엔 tail 500 이 앞이라
    오래된 것부터 버려졌다). 잘렸으면 **잘렸다는 사실 자체를 후보로 만든다** — 조용한
    절단은 W9 가 없애려던 실패 모드다.
    """
    cutoff = (today - timedelta(days=lookback_days)).isoformat()
    rows: list[tuple[str, str, str]] = []        # (ts, identity, text)
    for f in _event_files(today, lookback_days):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                e = json.loads(ln)
            except json.JSONDecodeError:
                continue
            ts = str(e.get("ts", ""))
            if not (cutoff <= ts[:10] <= today.isoformat()):   # 창 [cutoff, today] — 위아래 모두
                continue
            ident, text = e.get("identity"), e.get("text")
            if ident and text:
                rows.append((ts, str(ident), str(text)))
            elif e.get("kind") == "gate_block":  # 구 스키마(identity/text 없음) 호환
                rows.append((ts, f"gate-block-{e.get('resource', '?')}",
                             f"🔴 게이트 차단: {e.get('resource')} — {e.get('reason')}"))

    rows.sort(key=lambda r: r[0], reverse=True)  # 최신 우선 — 잘려도 신선한 것이 남는다
    out: dict[str, str] = {}
    for _ts, ident, text in rows:
        if ident not in out:
            out[ident] = text                    # 같은 identity 는 **가장 최신** 문구로
        if len(out) >= cap:
            break
    dropped = len({r[1] for r in rows}) - len(out)
    items = sorted(out.items())
    if dropped > 0:
        items.append(("alerts-events-truncated",
                      f"⚠️ 관측 사실이 많아 {dropped}건을 이번 틱에서 생략했다(상한 {cap}) — "
                      f"$KV_DATA/_alerts/events-*.jsonl 확인 요망."))
    return items


# ─────────────────────────── dispatch (유일한 결정 지점) ───────────────────────────

def dispatch(state=None, *, today: date | None = None, send_cap: int = 20) -> list[str]:
    """무엇을 알릴지 결정하고 보낸다. 반환: **실제 발송된** identity 목록.

    [W9 B-F9] 각 원천을 격리한다 — `registry.load()` 는 fail-loud 라 tasks.yaml 오타 하나가
    알림 체계 전체를 정지시켰다(그리고 상위 broad except 가 삼켜 겉으론 정상이었다).
    [B-M2] 격리는 보상 루프에도 적용한다.

    [자기리뷰 F2] **발송 루프 자체도 격리한다.** 예전엔 `_escalate`/`push` 가 `_try` 밖이라
    `alerts.yaml` 값 오타 하나로 dispatch 가 통째로 죽어 **그 틱의 전 알림이 미발송**됐다.
    더 나쁜 것은 `_try` 가 **순회 중인 리스트에 append** 했다는 점이다 — `_should_send` 가
    전역적으로 실패하면 후보가 무한 증식해 루프가 끝나지 않고, 그러면 `heavy_slot("approvals")`
    을 쥔 채 프로세스가 살아 있어(= stale 회수 대상이 아니어서) **이후 모든 틱이 SlotBusy 로
    물러나 승인 리컨실러가 영구 정지**한다. 지금은 새 후보를 `extra` 에 모아 루프 밖에서 처리한다.
    """
    today = today or _utc_today()
    sent: list[str] = []
    candidates: list[tuple[str, str]] = []
    extra: list[tuple[str, str]] = []             # _try 가 만드는 후보 — 순회 중 리스트를 안 건드린다

    def _try(name, fn, *a):
        try:
            return fn(*a)
        except Exception as e:                    # noqa: BLE001
            print(f"🔴 alerts.{name} 실패: {e!r}")
            extra.append((f"alerts-{name}-broken", f"🔴 알림 원천 {name} 실패: {e!r}"))
            return None

    if state is None:
        from . import health
        state = _try("state", health.compute, today)
    if state is not None:
        candidates += _try("from_state", _from_state, state) or []
        candidates += _try("from_auth", _from_auth, state) or []
        candidates += _try("from_scheduler", _from_scheduler, state) or []
    candidates += _try("from_events", _from_events, today) or []

    # 설정 자체가 깨졌다는 사실도 알린다 — 기본값으로 도느라 조용해지면 그게 가짜 침묵이다.
    bad = _try("config_check", config_problems) or []
    if bad:
        extra.append(("alerts-config-broken",
                      f"🔴 alerts.yaml 값 오류 {len(bad)}건 — 기본값으로 동작 중: {', '.join(bad)[:200]}"))

    candidates += extra
    extra = []
    active = {ident for ident, _ in candidates}
    for ident, _text in candidates:
        _try("touch_seen", _touch_seen, ident, today)   # [F3] 후보 등장 자체를 기록

    for ident, text in candidates:
        if len(sent) >= send_cap:
            # 한 틱에 수백 발을 쏘지 않는다. 남은 건 다음 틱이 집는다(마커가 중복을 막는다).
            print(f"⚠️  alerts: 발송 상한 {send_cap} 도달 — 나머지는 다음 틱")
            extra.append(("alerts-send-capped",
                          f"⚠️ 한 틱 발송 상한({send_cap})에 걸렸다 — 알림이 몰리고 있다. "
                          f"후보 {len(candidates)}건. 원인 확인 요망."))
            break
        try:
            ok, st = _should_send(ident, today)
            if not ok:
                continue
            if push(ident, _escalate(text, ident, today), day=today) == "sent":
                sent.append(ident)   # 발송 이력은 마커가 남긴다 — 따로 적지 않는다
        except Exception as e:                    # noqa: BLE001  한 건이 나머지를 죽이지 않는다
            print(f"🔴 alerts: {ident} 발송 실패 — {e!r}")
            extra.append((f"alerts-send-broken-{_safe_id(ident)[:40]}",
                          f"🔴 알림 발송 경로 실패({ident}): {e!r}"))

    for ident, text in extra:                     # 루프 밖에서 한 번만 — 재귀 증식 없음
        try:
            if push(ident, text, day=today) == "sent":
                sent.append(ident)
        except Exception as e:                    # noqa: BLE001
            print(f"🔴 alerts: 브로큰 통지 발송 실패({ident}) — {e!r}")
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
