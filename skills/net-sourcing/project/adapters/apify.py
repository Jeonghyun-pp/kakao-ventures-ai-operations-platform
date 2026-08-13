"""Apify 공용 헬퍼 — run-sync-get-dataset-items 호출 1함수.

Threads·LinkedIn·X가 동일 패턴(액터를 동기 실행하고 dataset item 배열을 그대로 받음)이라
여기로 추출한다. 액터마다 다른 것은 slug와 payload 입력 스키마·매핑뿐.

[프록시] configure(cfg)로 config.apify.proxy 를 로드 → run_actor가 payload에
proxyConfiguration을 주입. 기본 none = 주입 안 함(현행 동작 불변). residential=유료($8/GB).
[헬스체크] 액터가 0건 반환하면 경고 — Meta doc_id 주간 로테이션 등으로 조용히 깨지는 것 감지.

🔴 [비용 가드 2026-07-13] **이 파일에 비용 상한이 한 줄도 없었다.** 그래서 07-10에 burbn 6런이
   $5.94를 태워 무료 한도($5)를 넘겼고, 그때부터 스레드·링크드인이 전부 403으로 죽었다.
   이제 세 겹으로 막는다:
     ① 액터측 하드캡 — `maxTotalChargeUsd` 쿼리 파라미터. 액터가 스스로 멈춘다(가장 강함).
     ② 클라이언트 예산 — `_SPENT` 추정 누적이 `budget_usd`를 넘으면 이후 run_actor를 차단.
     ③ 실측 대조 — usage_usd()로 실행 전후 실제 과금을 읽어 추정과 비교해 출력.
   ③이 없으면 ①②는 **장식**이다: 파라미터 이름이 틀리면 Apify는 200을 주고 그냥 무시한다.

⚠️ 메모리는 **아무 값이나 못 넣는다.** Apify는 actor-start를 **메모리 GB당 1회** 과금하므로
   낮출수록 싸지만, 액터가 선언한 minMemoryMbytes 미만이면 400이 나고 그 채널이 통째로 0건이 된다.
   실측(2026-07-13, GET /v2/acts/{slug}/builds/default):
     burbn~threads-search-scraper          min=4096 max=8192   → **4GB 밑으로 못 내린다** (start $0.16/런)
     futurizerush~meta-threads-scraper     min=4096 max=4096   → 4GB인데 start 단가가 1/8 ($0.02/런)
     futurizerush~threads-replies-scraper  min=512  max=1024   → 1GB ($0.005/런)
     harvestapi~linkedin-profile-search    min=128  max=512    → start 미과금(page당 과금)
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

import requests

RUN = "https://api.apify.com/v2/acts/{slug}/run-sync-get-dataset-items"
LIMITS = "https://api.apify.com/v2/users/me/limits"

# 모듈 전역 프록시 설정(configure로 채움). None=주입 안 함.
_PROXY: dict | None = None

# 액터별 실행 옵션(config.apify.actors). {slug: {memory_mb, max_items, max_charge_usd, price_per_item, start_usd}}
_OPTS: dict[str, dict] = {}
_DEFAULT_MEM = 1024
# 메모리 거부 폴백 시 가정할 값. 액터 기본 메모리는 응답에 없고, 우리가 넣은 값보다 크다는
# 것만 안다(그래서 거부됐다). 실측 최대치가 8192MB 라 **모르면 비싼 쪽**으로 가정한다.
# 액터별로 알면 config 의 `max_memory_mb` 가 이 값을 이긴다.
_FALLBACK_MEM = 8192

# 이번 프로세스에서 쓴 것으로 **추정**되는 금액(달러). 실측이 아니라 단가×아이템수다.
_SPENT = 0.0

# 🔴 [2026-07-13] budget_usd는 **폭주 차단선**이지 예산이 아니다. 둘을 같은 숫자로 두지 마라.
#   실행 **전** 게이트(preflight_max_usd)는 추정치를 보고 막으므로 **공짜다** → 빡빡해도 된다.
#   실행 **중** 이 가드가 걸리면 이미 쓴 돈은 못 돌려받고 뒤쪽 채널만 0건이 된다 → **손해다.**
#   그래서 추정($9.79)에 딱 붙이면 안 되고, 정상 변동은 통과시키고 사고만 잡는 높이여야 한다.
_BUDGET = 0.0          # 0 = 무제한(가드 없음)

# ══════════════════════════════════════════════════════════════════════
# 월 천장 — **여기가 유일한 결정 지점이다** (chokepoint)
# ══════════════════════════════════════════════════════════════════════
# 🔴 [2026-08-13 W12 · 리뷰 A-F3] 월 게이트가 `pipeline.collect` 한 곳에만 있었다.
#    `run_actor` 호출부는 **23곳 / 13파일**인데 게이트를 지나는 건 그중 1곳뿐이었다.
#    나머지는 도구다 — 그중 둘은 스킬 문서가 **주 1회 정식 실행**이라 적은 커맨드고
#    (`main.py recheck`·`recheck-linkedin`, 회당 $1.6~$5.2), `land` 안에서 무조건 도는
#    `tools/backfill_bio.py` 도 게이트 밖이었다(설계상 $0 스테이지인데 돈을 쓴다).
#
#    ADR 0002 결정 ②("공유 자원은 결정 지점을 하나로")를 claude 축에만 이행한 상태였다.
#    `session.py` 가 claude 에 대해 한 것을 apify 에 한다 — **가드를 호출부가 아니라 자원
#    앞에 둔다.** 그러면 "게이트 부르는 걸 잊는다"가 표현 불가능해진다.
#
# ⚠️ 매 호출마다 Apify API 를 치면 안 된다(`threads.py` 는 루프에서 부른다).
#    **한 번 읽고 로컬 증분으로 추적**하되, 추정이 틀릴 수 있으므로 누적 추정이
#    `_REPROBE_USD` 만큼 늘 때마다 다시 읽어 드리프트를 그 금액으로 묶는다.
_LIVE_USED: float | None = None      # 마지막으로 읽은 **실제** 월 누적
_LIVE_CAP: float | None = None       # 유효 천장(우리 설정 ∩ 플랫폼 한도 중 낮은 쪽)
_PROBED_AT_SPENT = 0.0               # 그때의 _SPENT — 이후 증분을 더해 현재값을 추정
_GATE_OFF = False                    # 조회 실패 = fail-open. 사실을 표면화하고 통과시킨다
_REPROBE_USD = 1.0
# 사람이 `--force-budget` 로 강행한 경우. 가드를 자원 앞으로 내리면서 **사람 우회로도 같이
# 내려와야** 한다 — 안 그러면 강행을 승인한 사람이 돈을 쓴 뒤 런 중간에 막힌다(더 나쁘다).
_FORCE = False


# ══════════════════════════════════════════════════════════════════════
# 수집 건강 계측 — **여기가 유일한 계측 지점이다** (chokepoint)
# ══════════════════════════════════════════════════════════════════════
# 🔴 [2026-08-13 W12 · 리뷰 A-F6] `base.py:150-158` 이 계약을 명시했다 —
#    *"어댑터는 시도/실패를 세고, core/pipeline.py 가 이 수치로 런의 건강을 판정한다."*
#    착지 4어댑터 중 **appstore 하나만** 그 계약을 지켰다. threads(외부요청 7곳)·
#    linkedin(4곳)은 `_note_fetch` 를 **한 번도** 부르지 않았다.
#
#    귀결: 2026-07-19 앱스토어 사고(76요청 전멸 → `✅ 0건` + 종료코드 0)를 막으려고 만든
#    장치인데, **정작 그 사고가 재발하면 돈이 나가는 두 채널에서는 발화하지 않는다.**
#    Apify 쿼터 초과·doc_id 로테이션·429 로 threads 7개 층이 전멸해도 `✅ collect threads:
#    0건` + exit 0 로 끝나고 `health.compute` 는 ✅ 를 낸다.
#
#    F3(예산 관문)과 같은 처방이다 — **어댑터가 "부르는 걸 잊는" 것을 표현 불가능하게** 한다.
#    `pipeline` 이 어댑터 실행 전후로 이 값을 스냅샷해 그 어댑터의 계수에 더한다
#    (어댑터는 순차 실행이라 귀속이 정확하다).
_FETCH_ATT = 0
_FETCH_FAIL = 0


def fetch_stats() -> tuple[int, int]:
    """(시도, 실패) 누적. pipeline 이 어댑터 전후로 델타를 떠 귀속한다."""
    return _FETCH_ATT, _FETCH_FAIL


def allow_over_budget(on: bool = True) -> None:
    """월 천장 강행 — `--force-budget` 전용. 크론은 절대 부르지 않는다."""
    global _FORCE
    _FORCE = bool(on)
    if on:
        print("⚠️  Apify 월 천장 강행(--force-budget) — 무인 실행에서는 쓰지 않는다.")


def _refresh_ceiling(force: bool = False) -> None:
    """월 천장·실사용을 읽어 캐시한다. 실패하면 게이트를 끄되 **조용히 끄지 않는다.**"""
    global _LIVE_USED, _LIVE_CAP, _PROBED_AT_SPENT, _GATE_OFF
    if not force and _LIVE_USED is not None and (_SPENT - _PROBED_AT_SPENT) < _REPROBE_USD:
        return
    try:
        from kv_core import gate as _gate
        v = _gate.check("apify", estimate_usd=0.0, quiet=True)
    except Exception as e:                       # noqa: BLE001
        if not _GATE_OFF:
            print(f"⚠️  Apify 월 게이트 조회 실패({e}) — 이번 실행에 월 가드가 없다. "
                  f"회당 차단선(budget_usd)만 남는다.")
        _GATE_OFF = True
        return
    if v.used_usd is None or not v.ceiling_usd:
        if not _GATE_OFF:
            for w in v.warnings:
                print(f"⚠️  {w}")
            print("⚠️  Apify 월 가드가 이번 실행에 적용되지 않는다 — 회당 차단선만 남는다.")
        _GATE_OFF = True
        return
    _GATE_OFF = False
    _LIVE_USED, _LIVE_CAP = v.used_usd, v.ceiling_usd
    _PROBED_AT_SPENT = _SPENT


def _monthly_block(slug: str, add_usd: float) -> None:
    """이번 호출을 더하면 월 천장을 넘는가 — 넘으면 **쓰기 전에** 막는다."""
    if _FORCE:
        return
    _refresh_ceiling()
    if _GATE_OFF or _LIVE_USED is None or not _LIVE_CAP:
        return
    projected = _LIVE_USED + (_SPENT - _PROBED_AT_SPENT) + add_usd
    if projected <= _LIVE_CAP:
        return
    raise RuntimeError(
        f"🔴 Apify 월 천장 도달 — '{slug}' 를 **시작하지 않는다**(아직 한 푼도 안 썼다).\n"
        f"   실제 청구 ${_LIVE_USED:.2f} + 이번 프로세스 추정 "
        f"${_SPENT - _PROBED_AT_SPENT:.2f} + 이 호출 ${add_usd:.2f} = ${projected:.2f}"
        f" / 천장 ${_LIVE_CAP:.2f}\n"
        f"   천장은 **우리 설정과 플랫폼 한도 중 낮은 쪽**이다(kv_core.gate). 넘겨 돌리면\n"
        f"   Apify 가 중간에 끊어 '돈은 쓰고 결과는 반쪽'이 된다 — 2026-07-10 의 형태다.")

# 액터가 준 **원본** dataset item을 그대로 보관하는 곳.
#   data/raw/*.json은 **파싱이 끝난 Candidate**다 — 어댑터가 필드명을 잘못 읽으면 그 raw엔
#   이미 값이 없다. 원본이 없으면 파싱 버그를 고치려고 **돈을 다시 써서 재수집**해야 한다
#   (07-13 LinkedIn Full모드 필드명 사고가 정확히 이것이었다).
#   여기 원본이 있으면 파싱 오류는 전부 $0에 고치고 재현된다. 저장 비용 0, 위험 0.
_ARCHIVE = Path(__file__).resolve().parents[1] / "data" / "raw" / "_actor"


def configure(cfg: dict) -> None:
    """파이프라인 시작 시 1회 호출 — config.apify 설정을 모듈에 로드.

    proxy: none(기본) | datacenter | residential. residential은 Apify 유료 add-on($8/GB).
    budget_usd: 1회 실행 추정 상한. 초과 시 이후 run_actor가 RuntimeError로 차단된다.
    """
    global _PROXY, _OPTS, _BUDGET, _SPENT, _DEFAULT_MEM
    ap = cfg.get("apify") or {}
    mode = str(ap.get("proxy", "none")).lower()
    if mode == "residential":
        _PROXY = {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]}
        country = ap.get("proxy_country")
        if country:
            _PROXY["apifyProxyCountry"] = str(country).upper()
    elif mode == "datacenter":
        _PROXY = {"useApifyProxy": True}
    else:
        _PROXY = None

    _OPTS = dict(ap.get("actors") or {})
    _BUDGET = float(ap.get("budget_usd") or 0)
    _DEFAULT_MEM = int(ap.get("default_memory_mb") or 1024)
    _SPENT = 0.0


def spent_usd() -> float:
    """이번 실행의 **추정** 누적 비용."""
    return _SPENT


def budget_tripped() -> bool:
    """예산이 터져 이후 액터 실행이 차단된 상태인가.

    이걸 알아야 하는 이유: 예산이 터지면 뒤쪽 층/채널이 통째로 0건이 되는데, 그걸 모르면
    '오늘 수집 성공'으로 기록해 백필 창을 닫아버린다(= 못 가져온 구간이 영영 유실).
    """
    return bool(_BUDGET) and _SPENT >= _BUDGET


def usage_usd() -> float | None:
    """계정의 이번 사이클 **실제** 누적 과금(무과금 조회). 실패 시 None.

    ★ 이게 유일한 진실이다. run_actor의 memory/maxTotalChargeUsd 파라미터 이름이 틀리면
      Apify는 200을 주고 조용히 무시한다 — 가드가 걸린 줄 알았는데 청구서는 그대로다.
      collect() 전후로 이 값을 찍어 delta가 추정과 맞는지 반드시 확인하라.
    """
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        return None
    try:
        r = requests.get(LIMITS, params={"token": token}, timeout=15)
        return float(r.json()["data"]["current"]["monthlyUsageUsd"])
    except Exception:
        return None


def preflight() -> str | None:
    """무과금 사용량 체크(GET /users/me/limits). 한도 초과/임박이면 경고 문자열, 정상이면 None.

    2026-07-10 실사례: 무료 $5 초과 상태로 run → 스레드 400·링크드인 0건이 원인불명으로 보임.
    사전 체크로 '쿼터 문제'를 실행 전에 명시한다. 체크 실패는 조용히 통과(수집을 막지 않음).
    """
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        return None                              # 토큰 부재는 run_actor가 자체 에러로 안내
    try:
        r = requests.get(LIMITS, params={"token": token}, timeout=15)
        d = r.json()["data"]
        used = float(d["current"]["monthlyUsageUsd"])
        mx = float(d["limits"]["maxMonthlyUsageUsd"])
    except Exception:
        return None
    if used >= mx:
        return f"Apify 월 사용량 초과(${used:.2f}/${mx:.0f}) — Apify 채널(스레드·링크드인)은 실패/0건 예상"
    if used >= mx * 0.9:
        return f"Apify 월 사용량 임박(${used:.2f}/${mx:.0f}) — 이번 실행 중 차단될 수 있음"
    return None


def _archive(slug: str, payload: dict, items: list[dict]) -> None:
    """액터 **원본** 응답을 그대로 떨군다 — 파싱 버그를 $0에 고치기 위한 보험.

    실패해도 절대 수집을 막지 않는다. 원본을 못 남기는 것보다 수집이 죽는 게 나쁘다.
    """
    try:
        _ARCHIVE.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
        safe = re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-")
        (_ARCHIVE / f"{safe}_{stamp}.json").write_text(
            json.dumps({"slug": slug, "payload": payload, "items": items},
                       ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:                       # noqa: BLE001 — 보험이 본체를 죽이면 안 된다
        print(f"⚠️  액터 원본 보관 실패({slug}): {e} — 수집은 계속한다")


def _start_cost(o: dict, mem_mb: int | None = None) -> float:
    """**런이 시작되는 순간** 확정되는 비용 — actor-start 는 메모리 GB당 1회 과금.

    🔴 [2026-08-13 W12 · 리뷰 A-F5] 이 값을 응답 뒤에 더하고 있었다. 그런데 Apify 는
       **런이 시작되면 과금한다** — 클라이언트 타임아웃·429·5xx 로 우리가 응답을 못 받아도
       그 런은 시작됐고 돈은 나갔다. 실측(대역 프로브): 10회 연속 ReadTimeout → 장부 $0.00,
       `budget_tripped()` False, 화면엔 "누적 추정 $0.00". 실제로는 최대 $4.00 이 나갈 수 있다.
       `tools/recheck.py:197` 은 배치 실패를 `except: continue` 로 넘기므로 이게 조용히 반복된다.
       = **"돈만 나가고 결과가 0인" 경우를 회당 차단선이 원리적으로 못 본다.**
       그래서 POST **직전**에 선반영한다.
    """
    mb = int(mem_mb if mem_mb is not None else o.get("memory_mb", _DEFAULT_MEM))
    return float(o.get("start_usd", 0)) * max(1, mb // 1024)


def _item_cost(o: dict, n_items: int) -> float:
    """응답을 받은 뒤에야 알 수 있는 부분 — 아이템 수 × 단가."""
    return n_items * float(o.get("price_per_item", 0))


def _estimate(o: dict, n_items: int) -> float:
    """이 런의 추정 비용 = start(메모리 GB당 1회) + 아이템수 × 단가.

    (예측용. 실제 누적은 `_start_cost`/`_item_cost` 로 나눠 시점별로 반영한다.)
    """
    return _start_cost(o) + _item_cost(o, n_items)


def run_actor(slug: str, payload: dict, timeout: int = 180) -> list[dict]:
    """Apify 액터를 동기 실행하고 dataset item 배열(최상위)을 반환.

    slug 예: "harvestapi~linkedin-post-search". token은 환경변수 APIFY_TOKEN.
    _PROXY가 설정돼 있고 payload에 proxyConfiguration이 없으면 주입한다.

    실행 옵션(memory/maxItems/maxTotalChargeUsd)은 **쿼리 파라미터**로 간다 — payload(=액터 입력)가
    아니다. payload에 넣으면 액터가 모르는 키라 무시하고, 가드가 안 걸린 채로 돈이 나간다.
    """
    global _SPENT, _FETCH_ATT, _FETCH_FAIL
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        raise RuntimeError("APIFY_TOKEN 없음 — .env 확인")

    if _BUDGET and _SPENT >= _BUDGET:
        raise RuntimeError(
            f"🔴 Apify 폭주 차단선 도달 — 추정 ${_SPENT:.2f} / 차단선 ${_BUDGET:.2f}. '{slug}' 실행 차단.\n"
            f"   이건 '예산을 알뜰히 썼다'가 아니라 **사고**다. 실행 전 추정은 이 밑이었는데\n"
            f"   실제로는 넘었다는 뜻이다 — 액터가 예상보다 훨씬 많은 아이템을 뱉었거나 단가가 바뀌었다.\n"
            f"   → 이번 실행의 뒤쪽 채널은 0건이다. data/raw/_actor/ 의 원본으로 원인을 보고,\n"
            f"     config.apify.actors 단가를 실측에 맞춰라. (차단선 = config.apify.budget_usd)")

    if _PROXY and "proxyConfiguration" not in payload:
        payload = {**payload, "proxyConfiguration": _PROXY}

    o = _OPTS.get(slug, {})
    params = {"token": token,
              "memory": int(o.get("memory_mb", _DEFAULT_MEM)),
              "timeout": int(o.get("timeout_secs", timeout))}
    if o.get("max_items"):
        params["maxItems"] = int(o["max_items"])
    if o.get("max_charge_usd"):
        params["maxTotalChargeUsd"] = float(o["max_charge_usd"])

    # ── 월 천장(chokepoint). 여기서 막는 것은 공짜다 — 아직 POST 를 안 보냈다. ──
    _monthly_block(slug, _start_cost(o, params.get("memory")))

    # 🔴 런은 **시작되면 과금된다.** 응답을 못 받아도(타임아웃·429·5xx) 그 돈은 나갔다.
    #    그래서 POST 전에 start 비용을 선반영한다 — `try/finally` 가 아니라 **선반영**인 이유는
    #    예외가 나도 이 줄은 이미 실행됐기 때문이다(장부가 예외 경로에서도 정확해진다).
    _SPENT += _start_cost(o, params.get("memory"))

    # ── 수집 건강 계측(chokepoint) ────────────────────────────────────
    # 시도는 **여기서** 센다. 아래 어느 경로로 나가든(예외·4xx·정상) 이미 세어졌다.
    # 실패는 `finally` 가 마무리한다 — 성공 경로에서만 세면 예외가 계측을 건너뛰고,
    # 그게 정확히 "전멸했는데 0건으로 보이는" 상태를 만든다.
    _FETCH_ATT += 1
    _ok = False
    try:
        r = requests.post(RUN.format(slug=slug), params=params, json=payload,
                          timeout=timeout + 30)
        _r = _run_actor_body(slug, payload, timeout, o, params, r)
        _ok = True
        return _r
    finally:
        if not _ok:
            _FETCH_FAIL += 1


def _run_actor_body(slug, payload, timeout, o, params, r):
    """POST 이후 — 응답 처리. 계측/과금은 `run_actor` 가 이미 했다."""
    global _SPENT

    # 메모리가 액터 최소치보다 낮으면 400. 이걸 그냥 던지면 "액터가 죽었다"로 오진되고
    # 그 채널은 0건이 된다 — 실제 원인은 우리가 넣은 memory 값이다. 한 번 벗고 재시도한다.
    if r.status_code == 400 and "memor" in r.text.lower():
        print(f"⚠️  Apify '{slug}': memory={params['memory']}MB 거부됨 — 액터 기본 메모리로 재시도.\n"
              f"    → config.apify.actors['{slug}'].memory_mb 를 올려라 (GB당 start 과금이라 비용 증가)")
        params.pop("memory", None)
        # 🔴 [W12 · A-F5] 폴백 후에도 **거부당한 값으로 계속 계상**하고 있었다. 액터 기본
        #    메모리는 우리가 넣으려던 값보다 크므로(그래서 거부됐다) 과소계상이다 —
        #    실측 예: 4GB 로 요청 → 거부 → 기본 8GB 로 실행 → start 가 우리 계산의 2배.
        #    정확한 기본값은 응답에 없으므로 **보수적으로 최대치를 가정**한다(모르면 비싼 쪽).
        _fallback_mb = int(o.get("max_memory_mb") or _FALLBACK_MEM)
        _extra = _start_cost(o, _fallback_mb) - _start_cost(o, int(o.get("memory_mb", _DEFAULT_MEM)))
        if _extra > 0:
            _SPENT += _extra
            print(f"    · start 비용 보정 +${_extra:.3f} (기본 메모리를 {_fallback_mb}MB 로 가정)")
        _monthly_block(slug, 0.0)               # 보정 후 천장을 다시 본다
        r = requests.post(RUN.format(slug=slug), params=params, json=payload, timeout=timeout + 30)

    if r.status_code >= 400:                    # 응답 본문 노출 — 쿼터 초과·차단 등 진짜 사유 판별
        raise RuntimeError(f"Apify '{slug}' {r.status_code}: {r.text[:400]}")

    data = r.json()
    items = data if isinstance(data, list) else data.get("items", [])
    _SPENT += _item_cost(o, len(items))         # start 는 위에서 이미 반영했다
    _archive(slug, payload, items)              # 돈을 쓴 결과물의 원본은 무조건 남긴다

    if not items:            # [헬스체크] 0건 = 액터 파손(doc_id 로테이션)·차단 의심 신호
        print(f"⚠️  HEALTH: Apify 액터 '{slug}' 0건 반환 — 파손/차단 의심 "
              f"(payload keys={list(payload)})")
    return items
