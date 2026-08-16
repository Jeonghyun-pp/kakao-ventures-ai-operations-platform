"""Apify 통로 — **돈이 나가는 유일한 경로.**

## 이 파일이 지키는 [0] 제약

    런이 시작되면 과금한다        응답을 못 받아도 돈은 나갔다. 10회 연속 ReadTimeout 에
                              장부는 $0.00 이었지만 실제로는 최대 $4.00 이 나갈 수 있었다
                              → 비용을 **POST 직전에 선반영**한다 (try/finally 가 아니다.
                                예외가 나도 그 줄은 이미 실행됐다)

    모르는 파라미터를 200 과 함께 무시  `maxTotalChargeUsd` 이름이 틀리면 가드가 걸린 줄 알았는데
                              청구서는 그대로였다. 그래서 세 겹으로 막는다:
                              ①액터 하드캡 ②로컬 누적 ③플랫폼 실측 대조.
                              **③이 없으면 ①②는 장식이다**

    실행 옵션은 쿼리 파라미터로   payload(액터 입력)에 넣으면 조용히 무시된다

    월 한도는 플랫폼이 정한다      STARTER 실한도 $29. 게이트가 `pipeline.collect` 한 곳에
                              있었는데 `run_actor` 호출부는 23곳/13파일이었고
                              **게이트를 지나는 건 1곳뿐**이었다
                              → 가드를 호출부가 아니라 **자원 앞**에 둔다

    메모리 미달이면 400          폴백 시 비싼 쪽(8192MB)을 가정해 장부를 보정한다.
                              원래 값으로 계상하면 과소계상이다

    원본을 안 남기면 돈을 다시 쓴다  파싱 버그를 고치려면 재수집해야 한다.
                              → `{slug, payload, items}` 를 **무조건** 저장한다.
                                저장 실패가 수집을 막지 않는다(보험이 본체를 죽이면 안 된다)

## 🔴 skill 꼬리표가 필수다

`run_actor(..., skill=...)` 없이는 호출이 **불가능**하다. 옛 시스템은 $29 를 누가 썼는지
사후에도 알 수 없었다 — 지출 장부가 아예 없었고 `_SPENT` 는 프로세스 안에서만 살았다.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import requests

from kvlib import Fetched, constraints, paths, secrets

_API = "https://api.apify.com/v2"
_NET_ERRORS = (
    requests.exceptions.Timeout,
    requests.exceptions.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)

_spent_this_run = 0.0      # 이 프로세스가 쓴 것 (선반영 포함)
_platform_used: float | None = None
_gate_off = False          # 조회 실패 = fail-open + loud


class BudgetExceeded(RuntimeError):
    """월 천장에 닿았다. **쓰기 전에** 막는다."""


class ActorFailed(RuntimeError):
    pass


# ---------------------------------------------------------------- 장부

def _ledger_path():
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return paths.data("_usage", "apify") / f"{day}.jsonl"


def _record(skill: str, slug: str, usd: float, ok: bool, note: str = "") -> None:
    """🔴 지출 장부. 옛 시스템엔 이게 **없었다** — 프로세스가 죽으면 기록이 사라졌다."""
    row = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "skill": skill, "slug": slug, "usd": round(usd, 4), "ok": ok}
    if note:
        row["note"] = note
    with _ledger_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def spent_today() -> float:
    p = _ledger_path()
    if not p.exists():
        return 0.0
    return sum(json.loads(ln).get("usd", 0) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip())


# ---------------------------------------------------------------- 어드미션

def platform_used() -> Fetched:
    """플랫폼이 아는 이번 달 실사용액. **이게 없으면 나머지 가드는 장식이다.**"""
    global _platform_used, _gate_off
    try:
        r = requests.get(f"{_API}/users/me/limits",
                         headers={"Authorization": f"Bearer {secrets.get('APIFY_TOKEN')}"},
                         timeout=constraints.get("http.timeout_sec"))
        r.raise_for_status()
        d = r.json().get("data", {})
        used = float(d.get("current", {}).get("monthlyUsageUsd", 0))
        _platform_used = used
        _gate_off = False
        return Fetched.measured(used)
    except Exception as e:  # noqa: BLE001
        # fail-open + loud — 조회 실패로 업무를 죽이지 않되, 사실을 표면화한다
        _gate_off = True
        print(f"  🔴 Apify 사용량 조회 실패({type(e).__name__}) — 회당 차단선만 남는다")
        return Fetched.unknown(f"limits:{type(e).__name__}")


def _admit(add_usd: float) -> None:
    """POST **전에** 부른다. 천장 = min(우리 설정, 플랫폼 실한도)."""
    cap = constraints.get("apify.monthly_cap_usd")
    if _platform_used is None:
        platform_used()
    base = _platform_used if _platform_used is not None else spent_today()
    projected = base + _spent_this_run + add_usd
    if not _gate_off and projected > cap:
        raise BudgetExceeded(
            f"월 천장 ${cap} 초과 예상 (현재 ${base:.2f} + 이번 실행 ${_spent_this_run:.2f} "
            f"+ 이번 호출 ${add_usd:.2f} = ${projected:.2f}). 쓰기 전에 막았다."
        )


# ---------------------------------------------------------------- 아카이브

def _archive(slug: str, payload: dict, items: list) -> None:
    """원본 무조건 저장. 실패해도 수집을 막지 않는다 — 보험이 본체를 죽이면 안 된다.

    🔴 경로는 `paths.data()` 다. 옛 리포처럼 `project/data` 심볼릭 링크를 만들지 않는다
    (그 링크로 테스트가 넉 달간 프로덕션에 썼고, CI 가 원리적으로 못 보는 결함이었다).
    """
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S")
        p = paths.data("linkedin", "raw", "_actor") / f"{slug}_{stamp}.json"
        p.write_text(json.dumps({"slug": slug, "payload": payload, "items": items},
                                ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"  ⚠️  원본 저장 실패({type(e).__name__}) — 수집은 계속한다")


# ---------------------------------------------------------------- 호출

def run_actor(slug: str, payload: dict, *, skill: str, est_usd: float,
              memory_mb: int | None = None, timeout: int = 300) -> Fetched:
    """액터 한 번. **skill 은 키워드 필수** — 꼬리표 없는 지출을 불가능하게 만든다.

    est_usd 는 이 호출의 예상 비용이다. 응답 전에 장부에 반영한다.
    """
    global _spent_this_run
    if not skill:
        raise TypeError("skill 꼬리표가 없다 — 누가 쓴 돈인지 모르는 지출을 만들지 않는다")

    _admit(est_usd)

    # 🔴 실행 옵션은 **쿼리 파라미터**로. payload 에 넣으면 액터가 조용히 무시한다.
    params = {"token": secrets.get("APIFY_TOKEN"), "maxTotalChargeUsd": est_usd}
    if memory_mb:
        params["memory"] = memory_mb

    # 🔴 POST **직전** 선반영. try/finally 가 아니다 — 예외가 나도 이 줄은 이미 실행됐다.
    _spent_this_run += est_usd
    _record(skill, slug, est_usd, ok=True, note="pre-charge")

    url = f"{_API}/acts/{slug}/run-sync-get-dataset-items"
    try:
        r = requests.post(url, params=params, json=payload, timeout=timeout)
    except _NET_ERRORS as e:
        _record(skill, slug, 0.0, ok=False, note=f"net:{type(e).__name__}")
        return Fetched.unknown(f"net:{type(e).__name__}")

    # 메모리 미달 400 → 메모리 벗고 1회 재시도. 장부는 **비싼 쪽**을 가정해 보정한다.
    if r.status_code == 400 and "memor" in r.text.lower() and memory_mb:
        fb = constraints.get("apify.fallback_memory_mb")
        extra = est_usd * (fb / memory_mb - 1)
        print(f"  · 메모리 미달 400 — memory 벗고 재시도 (장부 ${extra:.2f} 보정: 비싼 쪽 가정)")
        _admit(extra)
        _spent_this_run += extra
        _record(skill, slug, extra, ok=True, note="memory-fallback")
        params.pop("memory", None)
        try:
            r = requests.post(url, params=params, json=payload, timeout=timeout)
        except _NET_ERRORS as e:
            _record(skill, slug, 0.0, ok=False, note=f"net:{type(e).__name__}")
            return Fetched.unknown(f"net:{type(e).__name__}")

    if r.status_code >= 400:
        _record(skill, slug, 0.0, ok=False, note=f"http{r.status_code}")
        return Fetched.unknown(f"http{r.status_code}:{r.text[:200]}")

    data = r.json()
    items = data if isinstance(data, list) else data.get("items", [])
    _archive(slug, payload, items)

    if not items:
        # 0건은 측정된 결과다. 다만 액터 파손·차단 신호이기도 하므로 시끄럽게 남긴다.
        print(f"  ⚠️  HEALTH: 액터 '{slug}' 0건 반환 — 파손/차단 의심")
    return Fetched.measured(items, slug=slug, spent=est_usd)
