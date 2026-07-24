"""어댑터 공용 유틸 — HTML strip + list-diff 스냅샷 헬퍼(appstore·disquiet 공유).

신규 의존성 없이 stdlib만 사용. 스냅샷은 data/snapshots/{prefix}_{key}_{date}.json 규약.
"""
from __future__ import annotations

import json
import re
from datetime import date, timedelta
from html import unescape
from pathlib import Path

SNAP_DIR = Path(__file__).resolve().parent.parent / "data" / "snapshots"

_TAG = re.compile(r"<[^>]+>")

# ── 네트워크 재시도 ───────────────────────────────────────────────────────────
# 🔴 [2026-07-20] **앱스토어 크론이 이틀 연속(07-19·07-20) 전멸했다.**
#   증상: 07:00 실행에서 76개 요청 전부 `Failed to resolve 'itunes.apple.com'`.
#   그런데 같은 기계에서 낮에 재시도하면 **0.4초에 200**이 온다 → 도메인·차단 문제가 아니다.
#   원인: **맥이 깨어나는 중에 launchd가 먼저 발화**해 네트워크(DNS)가 아직 안 올라온 상태.
#   마지막 성공 수집은 07-18(10,112건)이고, 신규진입은 diff라 **안 본 날은 영영 못 본다.**
#   → 첫 요청이 DNS/연결 오류면 망이 올라올 때까지 물러섰다 다시 친다.
#
#   ⚠️ 재시도 대상은 **연결 계층 오류뿐**이다. 404·429·5xx는 재시도하지 않는다 —
#      그건 망 문제가 아니라 상대 서버의 답이고, 여기서 눌러 담으면 진짜 고장이 가려진다.
_RETRY_EXC = ("ConnectionError", "Timeout", "ConnectTimeout", "ReadTimeout")

# 🔴 **회로 차단기 — 재시도가 크론을 몇 시간 붙잡는 걸 막는다.**
#   처음 구현엔 이게 없었고, 76개 요청이 각각 105초씩 물러서서 **총 2시간+**가 걸렸다
#   (실제로 돌려보고 10분 타임아웃에 걸려서야 발견했다 — 테스트만 짰으면 못 봤다).
#   망이 정말 죽었으면 그 사실은 **한 번이면 안다.** 한 번 끝까지 기다려보고, 그래도 죽어 있으면
#   나머지 요청은 즉시 실패시킨다. 어차피 상위(_note_fetch/CollectDegraded)가 전멸을 잡아낸다.
#   성공이 한 번이라도 나오면 차단기를 푼다(일시적 딸꾹질이었다는 뜻).
_circuit_open = False


def reset_circuit() -> None:
    """프로세스 안에서 재시도 차단기를 푼다(테스트·장시간 실행용)."""
    global _circuit_open
    _circuit_open = False


def get_with_retry(url: str, *, tries: int = 4, backoff: float = 15.0, **kw):
    """requests.get + **연결 오류에만** 지수 백오프 재시도. 15s → 30s → 60s (총 ~105s 대기).

    절전 해제 직후를 노린 값이다. 더 늘리면 크론이 오래 붙잡히고, 줄이면 망이 못 올라온다.
    마지막 시도까지 실패하면 **예외를 그대로 올린다** — 삼키면 '조용히 0건'으로 돌아간다.

    차단기가 열려 있으면(= 앞서 망이 죽은 게 확인됨) 재시도 없이 1회만 시도한다.
    """
    global _circuit_open
    import time

    import requests
    effective = 1 if _circuit_open else max(1, tries)
    last: Exception | None = None
    for i in range(effective):
        try:
            r = requests.get(url, **kw)
            if _circuit_open:
                print("· 연결 복구됨 — 재시도 차단기를 푼다.")
                _circuit_open = False
            return r
        except Exception as e:                      # noqa: BLE001 — 타입명으로 선별한다
            if type(e).__name__ not in _RETRY_EXC:
                raise                               # 연결 문제가 아니면 즉시 올린다
            last = e
            if i == effective - 1:
                break
            wait = backoff * (2 ** i)
            print(f"· 연결 실패({type(e).__name__}) — {wait:.0f}s 후 재시도 "
                  f"({i + 2}/{effective}). 절전 해제 직후면 망이 곧 올라온다.")
            time.sleep(wait)
    if effective > 1 and not _circuit_open:
        # 끝까지 기다려도 안 되면 망이 죽은 것으로 본다. 나머지 요청은 빨리 실패시킨다.
        _circuit_open = True
        print("🔴 재시도를 다 써도 연결이 안 된다 — 이후 요청은 **재시도 없이** 즉시 실패시킨다"
              "(전멸 판정은 상위에서 한다). 망 복구 후 다시 돌려라.")
    raise last if last else RuntimeError("get_with_retry: 알 수 없는 실패")


def strip_html(s: str) -> str:
    """태그·HTML 엔티티 제거(naver <b>, disquiet 카드 등). stdlib만."""
    if not s:
        return ""
    return unescape(_TAG.sub("", s)).strip()


# --- list-diff 스냅샷 (appstore에서 승격, prefix/key 인자화) ---
def _snap_path(prefix: str, key: str, d: date) -> Path:
    return SNAP_DIR / f"{prefix}_{key}_{d.isoformat()}.json"


def save_snapshot(prefix: str, key: str, ids: list[str]) -> None:
    """[1단계] 오늘 스냅샷을 **.pending 으로만** 저장. 확정은 land 성공 후 commit_snapshots().

    왜 2단계인가: 스냅샷은 "이미 본 app_id" 기준선이다. collect 시점에 확정해 버리면,
    land가 실패한 날(노션 403 등) 수집만 되고 착지 안 된 앱들이 **내일부터 '이미 본 것'**이 돼
    영영 안 올라온다. 조용한 영구 유실이다. (linkedin seen-store와 같은 패턴으로 통일)
    load_seen_ids 는 `*.json` 만 glob 하므로 pending 은 기준선에 안 잡힌다.
    """
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    p = _snap_path(prefix, key, date.today())
    p.with_suffix(p.suffix + ".pending").write_text(json.dumps(ids), encoding="utf-8")


def commit_snapshots(prefix: str) -> int:
    """[2단계] land 성공 후 호출 — pending 스냅샷을 확정. 확정한 파일 수 반환."""
    if not SNAP_DIR.exists():
        return 0
    n = 0
    for pend in SNAP_DIR.glob(f"{prefix}_*.json.pending"):
        pend.replace(pend.with_suffix(""))     # .json.pending → .json
        n += 1
    return n


def load_seen_ids(prefix: str, key: str, also_prefixes: tuple[str, ...] = ()) -> set[str]:
    """**누적 seen-set** — 오늘 이전의 모든 스냅샷을 합집합. "한 번도 차트에 없던 것"이 신규진입.

    lookback 방식(load_prev_snapshot)을 대체한다. 그 방식은 "target 날짜에 가장 가까운 파일 하나"만
    골랐는데, 가드가 없어 명목 14일 lookback이 실제로는 2일로 조용히 동작했다(2026-07-11 실측).
    게다가 하루라도 스냅샷을 거르면 baseline이 흔들려 신규진입 판정이 요동친다.

    누적 방식은 이 문제가 정의상 사라진다: 과거 어느 스냅샷에도 없던 app_id만 신규.
    스냅샷이 쌓일수록 강해지고, 하루 걸러도 0건이 되지 않는다. (linkedin seen-store와 같은 패턴)
    """
    if not SNAP_DIR.exists():
        return set()
    today = date.today()
    seen: set[str] = set()
    for pf in (prefix, *also_prefixes):
        for f in SNAP_DIR.glob(f"{pf}_{key}_*.json"):
            try:
                d = date.fromisoformat(f.stem.split("_")[-1])
            except ValueError:
                continue
            if d >= today:                    # 오늘 저장분은 baseline에서 제외(자기 자신 비교 방지)
                continue
            try:
                seen |= set(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
    return seen


def load_prev_snapshot(prefix: str, key: str, lookback_days: int,
                       also_prefixes: tuple[str, ...] = ()) -> set[str]:
    """lookback_days 이전(±여유) 가장 가까운 스냅샷 로드. 없으면 빈 set.

    also_prefixes: 같은 key의 다른 접두어 스냅샷도 baseline 후보로 포함(브리지).
    예) appstore는 이미 쌓인 'appstore_board_{key}_*'(전체 차트 보드 스냅샷, 동일 app_id 배열
    형식)을 재사용해 자체 히스토리가 없어도 신규진입 판정을 즉시 켠다.
    """
    if not SNAP_DIR.exists():
        return set()
    target = date.today() - timedelta(days=lookback_days)
    best, best_gap = None, 10 ** 9
    for pf in (prefix, *also_prefixes):
        for f in SNAP_DIR.glob(f"{pf}_{key}_*.json"):
            try:
                d = date.fromisoformat(f.stem.split("_")[-1])
            except ValueError:
                continue
            gap = abs((d - target).days)
            if gap < best_gap:
                best, best_gap = f, gap
    if best is None:
        return set()
    return set(json.loads(best.read_text(encoding="utf-8")))
