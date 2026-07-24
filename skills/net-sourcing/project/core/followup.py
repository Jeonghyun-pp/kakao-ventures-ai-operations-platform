"""리텐션(재관찰) 공통 기반 — 스케줄 상태 + 새 글 판정 + 이벤트 로그.

두 채널(스레드·링크드인)이 **한 스케줄러**를 쓴다. 키에 `th:`/`li:` 프리픽스를 붙여 한 파일에
담는 이유: 채널마다 상태 파일을 따로 두면 "이번 주에 누구를 볼 차례인가"가 두 곳에 갈라지고,
합쳐 볼 방법이 없어진다.

## 이 파일이 지키는 것 세 가지

1. **🔴 새 글 판정은 `max(taken_at)`이다. `latestPosts[0]`이 아니다.**
   실측(2026-07-20, n=639명/2,980편): latestPosts가 최신순으로 오는 경우가 **49.8%**뿐이다.
   사실상 동전던지기라 첫 글을 최신으로 쓰면 **절반의 인물에서 오판**한다.
   (키워드 액터도 `sort=recent`인데 배송이 최신순이 아니다 — 이 액터군의 공통 성질로 보인다.)

2. **`next_due`는 저장한다. 매번 재계산하지 않는다.**
   재계산하면 주기 상수를 바꾸는 순간 **전원이 동시에 만기**가 되어 배치 스윕이 부활한다.
   (Coffman: 배치로 몰지 말고 간격을 고르게.) 저장해두면 규칙을 바꿔도 기존 예약은 그대로 흘러간다.

3. **이벤트는 append-only.** 지금 코드는 지문을 덮어써서 **이력이 영구 소실**된다 —
   변화율 모수도 없고, judge에 "무엇이 바뀌었나"도 못 준다. 여기에 쌓아두면 나중에
   자동 판단(A안)으로 갈 때 사람 판정과 대조할 수 있다.

## 주기 (2026-07-20 사용자 확정: **전원 7일 균일**)
실측 근거: latestPosts 4편 창이 덮는 기간의 중앙값이 **7.1일**, 절반(49.7%)이 7일 이하다.
→ 7일보다 느리면 다작자의 글을 구조적으로 놓친다. 대상 ~60명 × $0.005 = **월 $1.30**이라
  층을 나눠 아낄 이유가 없다(원안 3층은 월 $0.6 — 차이 $0.7에 누락을 감수하는 셈).
⚠️ 문헌(Cho & Garcia-Molina 2003)이 **"변화율 비례 배분은 균일보다 못하다"**를 명시적으로
   반증했다 — "많이 쓰는 사람을 더 자주 본다"로 가지 마라. 균일이 맞다.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
STATE = PROJ / "data" / "followup_state.json"
EVENTS = PROJ / "data" / "followup_events.jsonl"

DEFAULT_DAYS = 7                      # 전원 균일(사용자 확정 2026-07-20)


# ── 상태 ────────────────────────────────────────────────────────────────────
def load() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text(encoding="utf-8")) or {}
    except json.JSONDecodeError:
        print("⚠️  followup_state.json이 깨졌다 — 빈 상태로 시작한다(전원 1회 만기).")
        return {}


def save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def key_threads(handle: str) -> str:
    return "th:" + (handle or "").strip().lstrip("@").lower()


def key_linkedin(urn: str) -> str:
    return "li:" + (urn or "").strip()


def is_due(state: dict, key: str, *, today: date | None = None) -> bool:
    """이 사람을 이번에 볼 차례인가. 기록이 없으면 **본다**(모르면 보는 쪽)."""
    rec = state.get(key) or {}
    nd = rec.get("next_due")
    if not nd:
        return True
    try:
        return date.fromisoformat(str(nd)[:10]) <= (today or date.today())
    except (ValueError, TypeError):
        return True


def mark_checked(state: dict, key: str, *, days: int = DEFAULT_DAYS,
                 today: date | None = None, **extra) -> None:
    """관찰 완료 기록 + **다음 예약일을 저장**(재계산 금지 — 모듈 docstring 2번)."""
    d = today or date.today()
    rec = state.setdefault(key, {})
    rec["last_checked"] = d.isoformat()
    rec["next_due"] = (d + timedelta(days=days)).isoformat()
    rec.update({k: v for k, v in extra.items() if v is not None})


# ── 새 글 판정 ──────────────────────────────────────────────────────────────
def newest_post(latest_posts: list | None) -> dict | None:
    """`latestPosts`에서 **가장 최근 글**을 고른다.

    🔴 `latest_posts[0]`을 쓰지 마라 — 최신순 보장이 없다(실측 49.8%만 내림차순).
       반드시 `taken_at` 최대값으로 고른다. 이게 이 함수가 존재하는 유일한 이유다.
    """
    best = None
    for p in (latest_posts or []):
        if not isinstance(p, dict):
            continue
        ts = p.get("taken_at")
        if not isinstance(ts, (int, float)):
            continue
        if best is None or ts > best["ts"]:
            cap = p.get("caption")
            text = (cap.get("text") if isinstance(cap, dict) else cap) or ""
            best = {"ts": int(ts), "id": str(p.get("pk") or p.get("id") or ""),
                    "code": p.get("code") or "", "text": text,
                    "likes": p.get("like_count")}
    return best


def is_new_post(rec: dict, newest: dict | None) -> bool:
    """저장된 마지막 글보다 새로운가. 기록이 없으면 **새 글로 치지 않는다**.

    ⚠️ 첫 관찰에서 True를 내면 전원이 '새 글'로 찍혀 `재검토` 큐가 통째로 터진다.
       첫 관찰은 **기준선을 잡는 것**이지 신호가 아니다.
    """
    if not newest:
        return False
    prev = rec.get("last_post_ts")
    if not prev:
        return False                              # 기준선 수립
    return int(newest["ts"]) > int(prev)


def post_age_days(newest: dict | None) -> float | None:
    if not newest:
        return None
    return (datetime.now(timezone.utc)
            - datetime.fromtimestamp(newest["ts"], timezone.utc)).total_seconds() / 86400


# ── 이벤트 로그 (append-only) ───────────────────────────────────────────────
def record(kind: str, key: str, detail: dict, *, by: str = "session") -> None:
    """관찰 이벤트 1건 추가. **덮어쓰지 않는다** — 이력이 곧 변화율 모수다.

    `by`: "session"(사람이 도는 주 1회) | "cron" | "llm". 나중에 자동 판단(A안)으로 갈 때
    사람 판정과 대조하려면 누가 찍었는지가 남아 있어야 한다.
    """
    try:
        EVENTS.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "kind": kind, "key": key, "by": by, **detail,
            }, ensure_ascii=False) + "\n")
    except Exception as e:                        # 로깅 실패가 수집을 죽이면 안 된다
        print(f"⚠️  이벤트 기록 실패(무해): {e}")
