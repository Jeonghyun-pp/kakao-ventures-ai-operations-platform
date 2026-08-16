"""파이프라인 오케스트레이션: collect → normalize → dedup → judge → stage.

어댑터(수집) 결과를 코어(판단·적재)로 흘린다. 채널 무관 — Candidate만 다룬다.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
RAW = PROJ / "data" / "raw"
CAND = PROJ / "data"

# 그물망 소싱 DB에 Channel 태그로 착지 가능한 채널(=DB Channel select 옵션 보유).
# 나머지(naver·disquiet 등)는 이 DB에 대응 옵션이 없어 land 대상에서 제외.
# [C9] 링크드인은 2개 독립 어댑터(harvestapi·listly). 착지는 source="링크드인"으로 동일.
LANDABLE = ["appstore", "threads", "linkedin_harvestapi", "linkedin_listly"]

# 어댑터 등록명 → (모듈 경로, 클래스명, config_key). 지연 import — 한 어댑터의 import
# 실패가 다른 채널 수집을 막지 않는다. [C9] 링크드인 2어댑터는 서로 다른 등록명 →
# raw 파일이 분리 저장(linkedin_harvestapi_*.json / linkedin_listly_*.json)되고
# config_key로 독립 on/off(드롭세이프). config_key 생략 시 등록명==config_key.
_REGISTRY = {
    "appstore":           ("adapters.appstore", "AppStoreAdapter", None),
    "threads":            ("adapters.threads", "ThreadsAdapter", None),
    "linkedin_harvestapi":("adapters.linkedin", "LinkedInAdapter", "linkedin"),
    "linkedin_listly":    ("adapters.linkedin_listly", "ListlyAdapter", "linkedin_listly"),
    "naver":              ("adapters.naver", "NaverAdapter", None),
    "disquiet":           ("adapters.disquiet", "DisquietAdapter", None),
    "velog":              ("adapters.velog", "VelogAdapter", None),
    "x":                  ("adapters.x", "XAdapter", None),
    "hackernews":         ("adapters.hackernews", "HackerNewsAdapter", None),
    "producthunt":        ("adapters.producthunt", "ProductHuntAdapter", None),
}

# 편의 별칭 — CLI --channel 값 하나가 여러 어댑터로 확장. "linkedin" = 2어댑터 전부.
_ALIASES = {"linkedin": ["linkedin_harvestapi", "linkedin_listly"]}


def _expand(channel: str) -> list[str]:
    if channel == "all":
        return list(_REGISTRY.keys())
    if channel in _ALIASES:
        return _ALIASES[channel]
    return [channel]


def _adapters(cfg: dict, keywords: dict, channel: str):
    import importlib
    for name in _expand(channel):
        mod_path, cls_name, config_key = _REGISTRY[name]
        if not cfg["channels"].get(config_key or name, {}).get("enabled", False):
            continue
        cls = getattr(importlib.import_module(mod_path), cls_name)
        yield name, cls(cfg, keywords)


class CollectDegraded(RuntimeError):
    """수집이 '돌긴 했는데 정상이 아니다'를 나타낸다.

    🔴 왜 별도 예외인가: 이건 **'0건'과 구분해야 하는 상태**다. 앱스토어 크론이 DNS 실패로
       76개 요청이 전멸했는데 `✅ 0건` + 종료코드 0으로 성공 보고한 게 이 클래스를 만든 이유다
       (2026-07-19. 전날은 10,112건이었다). 크론은 **종료코드로만** 건강을 판단하므로,
       사람이 로그를 안 봐도 실패가 드러나려면 반드시 비-0으로 끝나야 한다.
       ⚠️ 이미 수집된 건은 파일로 **저장된 뒤**에 이 예외가 난다 — 데이터를 버리지 않는다.
    """


def collect(cfg: dict, keywords: dict, channel: str) -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    from adapters import apify
    apify.configure(cfg)                          # 프록시 · 비용 가드 · 액터 옵션 로드
    warn = apify.preflight()                      # 쿼터 사전 체크(무과금)
    if warn:
        print(f"⚠️  HEALTH: {warn}")

    # ★ **돈을 쓰기 전에** 총액을 뽑는다. budget_usd 가드는 '이미 쓴 돈'을 보고 막으므로,
    #   config가 예산보다 크면 스레드가 예산을 다 태우고 **링크드인이 0건**이 된다.
    #   그건 가드가 아니라 사고다. 그래서 실행 전에 멈추고 config를 고칠 기회를 준다.
    est = _preflight_cost(cfg, channel)

    before = apify.usage_usd()                    # 실제 과금 기준선(무과금 조회)

    # ★ [2026-07-20 결정 12] **월 예산 가드.** 회당 가드는 12번 돌면 12배가 나가는 걸 못 막는다.
    #   결정 #6(수동 실행)이 지금까지 사실상 월 예산 가드였고, 크론이 그 사람을 없앤다.
    _force = bool(cfg.get("_force_budget"))
    # [W12] 가드를 `run_actor`(chokepoint)로 내리면서 **사람 우회로도 같이 내려보낸다.**
    # 안 그러면 --force-budget 를 승인한 사람이 여기는 통과하고 런 중간에 막힌다 —
    # 돈은 쓰고 결과는 반쪽. 정확히 이 가드가 막으려던 상태다.
    if _force:
        apify.allow_over_budget(True)
    _monthly_gate(cfg, before, est, force=_force)

    degraded: list[str] = []
    counts: dict = {}
    for name, adapter in _adapters(cfg, keywords, channel):
        # [W12 · A-F6] Apify 계측을 chokepoint(`run_actor`)로 내렸다. 어댑터별 귀속은
        # 실행 전후 델타로 한다 — 어댑터는 순차 실행이라 이 방식이 정확하다.
        _fa0, _ff0 = apify.fetch_stats()
        try:
            cands = adapter.collect()
        except Exception as e:                    # 한 채널 실패가 나머지를 막지 않음(자격 누락 등)
            print(f"⚠️  collect {name} 실패: {e}")
            degraded.append(f"{name}: 채널 전체 예외 ({type(e).__name__})")
            continue
        finally:
            _fa1, _ff1 = apify.fetch_stats()
            adapter.fetch_attempts += _fa1 - _fa0
            adapter.fetch_failures += _ff1 - _ff0
        # ★ [2026-07-20] **'0건'과 '전멸'을 구분한다.** 어댑터는 개별 실패를 격리하므로
        #   전부 실패해도 빈 리스트가 돌아온다 — 그게 2026-07-19에 앱스토어 크론이
        #   `✅ 0건` + 종료코드 0으로 성공 보고한 이유다(전날 10,112건).
        att = getattr(adapter, "fetch_attempts", 0)
        fail = getattr(adapter, "fetch_failures", 0)
        # 🔴 [W12 · A-F6] `if att and fail:` 은 **계측이 아예 없는 어댑터를 원리적으로 못 봤다.**
        #    threads·linkedin 이 `_note_fetch` 를 한 번도 안 불렀으므로 att=0 → 이 블록을
        #    통째로 건너뛰고 `✅ collect threads: 0건` + exit 0. 전멸과 진짜 0건이 같아진다.
        #    계측 부재 자체를 **결손으로 센다**(정본 R5: 0 이 두 뜻이면 둘을 갈라 본다).
        #    ⚠️ 현재 `naver`·`producthunt`·`disquiet`·`hackernews`·`velog` 는 apify 를 안 거치고
        #       직접 `requests` 를 쓰므로 chokepoint 계측 밖이다(전부 config 에서 꺼져 있다).
        #       켜는 순간 이 줄이 뜬다 — **오탐이 아니라 사실이다.** 그 채널의 0건은 지금도
        #       전멸과 구분되지 않는다. 켤 때 둘 중 하나를 하라고 메시지가 지시한다.
        if att == 0 and not getattr(adapter, "no_fetch", False):
            degraded.append(
                f"{name}: 외부 요청 계측이 0건 (후보 {len(cands)}건) — 계측 없는 0건은 "
                f"전멸과 구분되지 않는다. 요청을 apify.run_actor 로 보내 계측에 태우거나, "
                f"외부 요청이 없는 어댑터면 클래스에 `no_fetch = True` 를 선언하라")
        if att and fail:
            rate = fail / att
            if fail == att:
                degraded.append(f"{name}: 요청 {att}건 **전부 실패**")
            elif rate > 0.5:
                degraded.append(f"{name}: 요청 {att}건 중 {fail}건 실패 ({rate:.0%})")
            else:
                print(f"· {name}: 요청 {att}건 중 {fail}건 실패 ({rate:.0%}) — 부분 실패, 계속한다")
        for c in cands:                           # [C9] provenance 보증 — 미설정 시 등록명으로
            if not c.adapter:
                c.adapter = name
        path = RAW / f"{name}_{date.today().isoformat()}.json"
        path.write_text(json.dumps([c.to_dict() for c in cands], ensure_ascii=False, indent=2),
                        encoding="utf-8")
        counts[name] = len(cands)
        print(f"✅ collect {name}: {len(cands)}건 → {path.name}")

    _spend_report(apify, before)

    _write_health(channel, degraded, counts)

    # 🔴 **여기가 조용한 실패를 소리나게 만드는 자리다.** 종료코드로 올려보내지 않으면
    #   launchctl 상태는 `- 0`(정상)으로 남고, 크론이 몇 주째 0건을 내도 아무도 모른다.
    if degraded:
        raise CollectDegraded("수집이 정상적으로 끝나지 않았다:\n"
                              + "\n".join(f"   · {d}" for d in degraded))


def _write_health(channel: str, degraded: list[str], counts: dict) -> None:
    """마지막 실행의 건강 상태를 파일로 남긴다 — `install-cron-*.sh status`가 이걸 보여준다.

    왜 필요한가: 종료코드는 **그 순간에만** 존재한다. 크론이 새벽에 실패하고 지나가면
    `launchctl list`는 마지막 상태만 보여주고, 사람은 수백 줄 로그를 뒤져야 원인을 안다.
    한 줄로 "언제 · 무엇이 · 왜 실패했나"를 남겨두면 status 한 번에 드러난다.

    ⚠️ 이건 **알림이 아니다.** 사람이 봐야 보인다. 진짜 푸시(슬랙 웹훅 등)는 별건 —
       웹훅 URL이 필요해서 아직 안 만들었다(2026-07-21 아침 결정 대기).
    """
    # 🔴 [2026-08-13 W12 · 리뷰 A-F8] 예전엔 **파일 하나**에 썼다(`last_run_health.json`).
    #    그런데 `health.compute` 는 `launchd-direct` task(=appstore-sourcing)의 건강을
    #    그 파일로 판정하면서 **`channel` 필드를 안 봤다.** 그래서 사람이
    #    `main.py collect --channel linkedin` 을 한 번만 수동 실행하면, 그날 앱스토어
    #    크론이 무슨 짓을 했든 그 결과가 덮여 **✅ 로 보고된다.**
    #    실측(스크래치 재현): 앱스토어 76요청 전멸 기록 → 링크드인 수동 수집 →
    #    `appstore-sourcing: ok · 0일 전 정상`.
    #
    #    **파일 1개가 채널 N개의 건강을 공유**한 것이 결함이다(계열 ②, 단위 불일치).
    #    채널별로 쪼갠다. 판정 정본(health.compute)은 하나지만 **그 입력이 오염돼 있었다.**
    for _ch, _n in (counts or {}).items():
        _deg = [d for d in degraded if d.startswith(f"{_ch}:")]
        _write_health_one(_ch, _deg, {_ch: _n})
    # 어느 어댑터도 안 돌았으면(전 채널 예외 등) 요청 채널 이름으로라도 사실을 남긴다.
    if not counts:
        _write_health_one(channel, degraded, {})


def _write_health_one(channel: str, degraded: list[str], counts: dict) -> None:
    """채널 하나의 건강을 `data/last_run_health/<채널>.json` 에 남긴다."""
    try:
        d = PROJ / "data" / "last_run_health"
        d.mkdir(parents=True, exist_ok=True)
        hp = d / f"{channel}.json"
        # [W9 재설계] **마지막 성공 시각을 보존한다.**
        # 예전엔 `ts` 만 남겼는데 그 값은 **실패해도 매 실행 갱신**된다. 그래서 읽는 쪽이
        # "며칠째 성공이 없나"를 셀 방법이 없었고, 매일 돌며 매일 실패해도 관측은 늘
        # "1일 전"이라 영원히 경보가 안 떴다(적대 리뷰 H-F1). 판정 쪽에서 우회할 수도
        # 있지만, 근본은 **writer 가 필요한 사실을 안 남긴 것**이다.
        prev = {}
        try:
            prev = json.loads(hp.read_text(encoding="utf-8"))
        except Exception:                         # noqa: BLE001  구 스키마·파손은 빈 값
            prev = {}
        now_ts = datetime.now().isoformat(timespec="seconds")
        ok = not degraded
        hp.write_text(json.dumps({
            "ts": now_ts,
            "channel": channel,
            "ok": ok,
            "degraded": degraded,
            "counts": counts,
            # 성공했을 때만 전진 — 실패는 이 값을 건드리지 않는다(워터마크와 같은 규율)
            "last_ok_ts": now_ts if ok else prev.get("last_ok_ts"),
            # 최초 기록 시각 — "한 번도 성공한 적 없다"의 경과일 기준선
            "first_ts": prev.get("first_ts") or now_ts,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:                        # 상태 기록 실패가 수집을 죽이면 안 된다
        print(f"⚠️  건강 상태 기록 실패(무해): {e}")


def _preflight_cost(cfg: dict, channel: str = "all") -> float | None:
    """실행 전 비용 추정 — 예산 초과면 RuntimeError로 **수집 자체를 막는다.**

    🔴 `channel`을 반드시 넘겨라. 안 넘기면 `--channel appstore`(= $0)로 돌려도 스레드+링크드인
       전체를 추정한다. 지금은 추정($8.14)이 게이트($12)보다 낮아 통과하지만, 키워드를 늘려
       추정이 게이트를 넘는 순간 **한 푼도 안 쓰는 앱스토어 크론이 조용히 죽는다.**

    반환: 이번 런 추정 총액(월 예산 가드가 쓴다). 못 읽으면 None.
    """
    import subprocess
    script = PROJ / "tools" / "estimate_cost.py"
    if not script.exists():
        return None
    r = subprocess.run([sys.executable, str(script), "--channel", channel],
                       capture_output=True, text=True)
    # 기계용 마커는 사람에게 보여줄 필요가 없다(estimate_cost.py의 #EST_TOTAL와 한 몸).
    est: float | None = None
    lines = []
    for ln in r.stdout.rstrip().splitlines():
        if ln.startswith("#EST_TOTAL "):
            try:
                est = float(ln.split(None, 1)[1])
            except (ValueError, IndexError):
                pass                              # 형식이 깨졌으면 월 가드가 fail-open으로 간다
            continue
        lines.append(ln)
    print("\n".join(lines))
    if r.returncode != 0:
        raise RuntimeError(
            "예산 초과 — 수집을 시작하지 않는다. **아직 한 푼도 안 썼다.**\n"
            "   (그냥 돌리면 앞쪽 채널이 예산을 다 쓰고 뒤쪽 채널이 0건이 된다.\n"
            "    config.apify.preflight_max_usd 를 올리거나, 위 안내대로 config를 좁혀라.)")
    return est


def _monthly_gate(cfg: dict, used: float | None, est: float | None,
                  force: bool = False) -> None:
    """월 예산 가드 — **이번 달 실제 청구액 + 이번 런 추정**이 상한을 넘으면 시작 전에 막는다.

    왜 회당 가드로 안 되나: `preflight_max_usd`(런당 $12)는 **한 번**의 지출만 본다.
    12번 돌면 12배가 나가는 걸 못 막는다. 지금까지는 결정 #6(수동 실행)이 그 역할을 했다 —
    사람이 매번 "돌릴까"를 판단하는 게 월 총액을 누르는 유일한 장치였다. **크론은 그걸 없앤다.**

    기준이 **추정이 아니라 실제 청구액**인 이유: 액터 오버슛(+15%)·재시도 등으로 추정은 늘 어긋난다.
    Apify가 청구한 값을 보면 추정이 틀려도 이 가드는 안 속는다.

    🔴 사용량을 못 읽으면 **크게 경고하고 통과**한다(fail-open). 막으면 Apify API 딸꾹질 한 번이
       수집을 통째로 죽인다. 통과시켜도 회당 가드가 한 번의 손해를 budget_usd로 묶는다 —
       **조용히 죽는 것보다 시끄럽게 도는 게 낫다.**
    """
    # 🔴 [2026-07-28 W2] 천장 판정을 kv_core.gate 로 옮겼다. **우리 설정과 플랫폼 한도 중
    #    낮은 쪽**을 쓴다. 그 전엔 config 의 $45 만 봤는데 Apify 플랜 실제 한도는 $29 였다 —
    #    $29~$45 는 "가드가 있다고 믿지만 실제로는 없는 구간"이었고, 통과시킨 뒤 Apify 가
    #    중간에 끊어 **돈은 쓰고 결과는 반쪽**이 되는 경로였다.
    from kv_core import gate as _gate
    _v = _gate.check("apify", estimate_usd=est or 0.0, quiet=True)
    # 🔴 [2026-08-13 W12 · 리뷰 A-F4] 여기엔 `or float(cfg.apify.monthly_budget_usd)` 폴백이
    #    있었다. **그게 천장의 두 번째 진실이었다** — resources.yaml $29 vs config.yaml $45.
    #    gate 조회가 한 번 실패하면(독립 HTTP 요청이라 흔하다) 유효 천장이 조용히 $45 로
    #    올라간다. W2 에서 닫았다고 기록한 바로 그 $29~$45 구간이 다시 열리는 경로다.
    #    실측(대역 프로브): gate 만 실패시키면 `$37.00 / $45.00` 로 **통과**하고, 같은 출력에
    #    "사용량 조회 실패" 경고가 나란히 찍힌다 — 사람은 낙관적인 쪽을 믿는다(R2).
    #    이제 천장의 출처는 `kv_core.gate` 하나다. 못 읽으면 **가드 미작동이라는 사실 하나만**
    #    남기고 통과한다(두 fail-open 분기를 하나로 합친다).
    cap = _v.ceiling_usd
    for _w in _v.warnings:
        print(f"⚠️  {_w}")
    if not cap or used is None:
        print("🔴 월 예산 가드: 천장 또는 실사용을 못 읽었다 — **가드가 이번 실행에 없다.**\n"
              "   회당 차단선(budget_usd)과 액터측 maxTotalChargeUsd 만 남는다.\n"
              "   (천장의 정본은 config/resources.yaml 의 apify.monthly_budget_usd 다.)")
        return

    projected = used + (est or 0.0)
    if projected <= cap:
        room = cap - projected
        if room < cap * 0.15:
            print(f"⚠️  월 예산 임박: 실제 ${used:.2f} + 이번 런 ${est or 0:.2f} = ${projected:.2f} "
                  f"/ 상한 ${cap:.2f} (여유 ${room:.2f})")
        else:
            print(f"· 월 예산: 실제 ${used:.2f} + 이번 런 ${est or 0:.2f} = ${projected:.2f} "
                  f"/ ${cap:.2f}")
        return

    msg = (f"월 예산 초과 — 수집을 시작하지 않는다. **아직 한 푼도 안 썼다.**\n"
           f"   이번 달 실제 청구 ${used:.2f} + 이번 런 추정 ${est or 0:.2f} = ${projected:.2f} "
           f"> 상한 ${cap:.2f}\n"
           f"   (기준은 Apify 실제 누적이다 — 우리 추정이 아니라 진짜 청구액.)")
    if force:
        print(f"⚠️  {msg}\n   → --force-budget 지정됨: **그대로 진행한다.**")
        return
    # 결정 B5(2026-07-28): 게이트에 막히면 **실패시키고 알린다.** 큐잉 후 자동 재개는
    # W3(잡 러너)이 있어야 성립한다. 지금 알리지 않으면 크론이 조용히 안 도는 상태가 된다.
    _gate.notify_blocked(_v)
    raise RuntimeError(
        msg + "\n   · 사람이 판단해 강행하려면: --force-budget\n"
              "   · 다음 달까지 기다리거나, config.apify.monthly_budget_usd 를 올려라.\n"
              "   🔴 크론은 --force-budget 를 쓰지 않는다 — 무인 실행이 상한을 뚫으면 안 된다.")


def _spend_report(apify, before: float | None) -> None:
    """실행 후 **실제** 과금과 **추정** 비용을 나란히 찍는다.

    ★ 이 한 줄이 비용 가드의 유일한 검증 수단이다. run_actor가 넘기는 memory/maxTotalChargeUsd
      파라미터 이름이 틀리면 Apify는 200을 주고 조용히 무시한다 — 가드가 걸린 줄 알았는데
      청구서는 그대로다. 실제 delta가 추정과 크게 어긋나면 파라미터가 안 먹은 것이다.
    """
    est = apify.spent_usd()
    if est <= 0 and before is None:
        return

    # ★ 차단선에 걸린 실행은 **싸게 끝난 실행처럼 보인다.** 비용만 찍으면 아무도 눈치 못 챈다.
    #   이 경고가 없으면 반쪽짜리 수집을 정상으로 착각하고 착지시킨다.
    if apify.budget_tripped():
        print("\n🔴 폭주 차단선에 걸려 **수집이 중간에 끊겼다.** 이 실행 결과는 불완전하다.\n"
              "   · 뒤쪽 채널/층은 0건이다 — 아래 '✅ collect' 줄에 빠진 채널을 확인하라.\n"
              "   · 커서·seen-store는 커밋되지 않았다 → 다음 실행이 같은 구간을 다시 산다(돈은 안 날렸다).\n"
              "   · 원인은 data/raw/_actor/ 의 원본을 보고 실제 아이템 수 vs config 단가를 대조하라.\n"
              "   · 이 상태로 land 하지 마라. config를 고치고 다시 collect 하라.")

    after = apify.usage_usd()
    if before is None or after is None:
        print(f"💰 이번 실행 추정 비용 ${est:.2f} (실제 과금 조회 실패 — 대조 불가)")
        return
    real = after - before
    print(f"💰 이번 실행: 실제 과금 **${real:.2f}** / 추정 ${est:.2f} · 계정 누적 ${after:.2f}")
    if est > 0 and (real > est * 1.5 + 0.10):
        print(f"🔴 실제가 추정의 1.5배를 넘는다 — run_actor의 실행옵션(memory/maxTotalChargeUsd)이 "
              f"안 먹었을 수 있다. 가드가 장식인지 확인하라.")


def screen(cfg: dict) -> None:
    from core import dedup, judge
    raws = _load_latest_raw()
    print(f"· 수집 {len(raws)}건 로드")

    fresh = dedup.filter_new(cfg, raws)          # 중복 제거 (극초기/스타트업 DB 대조) [C1/C2]
    print(f"· 신규 {len(fresh)}건 (중복 {len(raws) - len(fresh)}건 제거)")

    judged = judge.run(cfg, fresh)               # L0 하드컷 + L1/L2 AI 판단 [A그룹]
    surfaced = [j for j in judged if j["verdict"] == "surface"]
    border = [j for j in judged if j["verdict"] == "borderline"]
    cut = [j for j in judged if j["verdict"] == "cut"]
    print(f"· 판단: surface {len(surfaced)} / borderline {len(border)} / cut {len(cut)}")

    out = CAND / f"candidates_{date.today().isoformat()}.json"
    out.write_text(json.dumps(judged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"🎉 screen 완료 → {out.name}")


def sync(cfg: dict) -> None:
    from core import notion
    judged = _load_latest_candidates()
    include_border = cfg["judge"].get("include_borderline_in_sync", False)   # [D2]
    to_load = [j for j in judged
               if j["verdict"] == "surface" or (include_border and j["verdict"] == "borderline")]
    n = notion.stage(cfg, to_load)               # 극초기 DB에 미검토로 적재 [D1/D3]
    print(f"🎉 sync 완료: {n}건 적재")


def _landable(cfg: dict, channels: list[str]) -> list[str]:
    """착지 대상 = LANDABLE ∩ **config에서 켜진 채널**.

    꺼둔 채널(linkedin_listly)이 매 실행 '🔴 collect 실패 의심' 오탐을 찍고 있었다.
    진짜 실패(오늘의 Apify 403)와 구분이 안 돼 경고의 신호가치를 죽인다.
    """
    out = []
    for name in channels:
        if name not in LANDABLE:
            continue
        _, _, config_key = _REGISTRY[name]
        if not cfg["channels"].get(config_key or name, {}).get("enabled", False):
            continue
        out.append(name)
    return out


def _dry_run_report(cfg: dict, cands: list[dict]) -> None:
    """쓰지 않고 **무엇이 일어날지만** 보여준다. 착지 로직을 유료/파괴 없이 검증하는 유일한 방법."""
    from core import notion
    seen = notion.existing_landing_keys(cfg)
    create = update = 0
    blocks = 0
    for c in cands:
        key = notion._landing_key(c)
        posts = ((c.get("signals") or {}).get("posts") or []) if notion._person_key(c) else []
        if key and key in seen:
            update += 1
            blocks += len(posts)          # 실제로는 '이미 붙은 글'을 빼지만, 여기선 상한만 본다
        else:
            create += 1
            blocks += len(posts)
    print(f"\n🧪 [dry-run] 신규 생성 {create}행 · 기존 갱신 {update}행 · 본문에 붙을 글 최대 {blocks}편")
    print("   (실제 갱신 시엔 이미 붙어 있는 글을 빼므로 블록 수는 이보다 적다)")
    ppl = [c for c in cands if c.get("source") == "스레드"]
    if ppl:
        multi = [c for c in ppl if (c["signals"].get("post_count") or 0) > 1]
        print(f"   스레드 인물 {len(ppl)}명 (글 2편 이상 {len(multi)}명)")


def land(cfg: dict, channel: str, dry_run: bool = False) -> None:
    """수집된 raw를 그물망 소싱 DB에 Channel 태그로 착지.

    수집은 넓게, 착지 전에 **L0 하드컷(홍보/뉴스/2년초과/대기업)만** 적용한다.
    AI 판단은 하지 않는다 — 정예 선별은 사람이 노션에서. (사용자 결정 2026-07-11)
    컷된 건은 data/cut_{date}.json에 사유와 함께 남긴다(조용한 삭제 없음).
    """
    from core import notion, dedup, judge, fold
    cands = _load_raw_for(_landable(cfg, _expand(channel)))
    if not cands:
        print("· 착지할 raw 없음 (먼저 collect 실행)")
        return
    before = len(cands)
    cands = dedup.filter_new(cfg, cands)      # [2단 dedup] 주간 DB URL 정확 + 레거시 이름 퍼지
    print(f"· dedup: {before} → {len(cands)} 신규 (기존 {before - len(cands)}건 제거)")

    cands, cut = judge.hardcut(cfg, cands)    # [L0] 홍보/뉴스·2년초과·대기업 제거 (AI 없음, $0)
    if cut:
        _dump_cut(cut)
        print(f"· L0 컷: {len(cut)}건 제거 → {len(cands)}건 착지 대상 "
              f"(사유 breakdown은 data/cut_{date.today().isoformat()}.json)")

    # ★ [인물 1행] 스레드 글-후보를 작성자별로 접는다. **judge 뒤**여야 한다 —
    #   judge의 홍보 정규식은 글 하나를 보고 만들어졌다. 먼저 접으면 최신 글이 홍보인 사람의
    #   이틀 전 런칭 글까지 통째로 죽는다.
    cands = fold.by_author(cands)

    if not cands:
        print("· 신규 없음 — 착지 생략")
        return
    if dry_run:
        _dry_run_report(cfg, cands)
        return
    run_id = datetime.now().isoformat(timespec="seconds")
    # ★ [2026-07-14] **채널별 DB 착지.** notion.land 는 DB 하나를 가정하므로(스키마·기존키를
    #   그 DB에서 읽는다) 여기서 **소스별로 나눠** 각자의 DB로 보낸다. cfg는 얕은 복사 +
    #   notion.db_id 만 갈아끼운다 — notion.py 내부를 건드리지 않는 게 이 방식의 요점이다.
    #   채널 매핑이 없으면 db_for()가 기본 db_id를 주므로 **기존 동작과 동일**하다.
    groups: dict[str, list[dict]] = {}
    for c in cands:
        groups.setdefault(c.get("source", ""), []).append(c)
    n = failed = 0
    # 🔴 [2026-08-13 W12 · 리뷰 B-F9] `failed` 를 **채널 넘어 합산**하고 있었다.
    #    `_COMMIT_HOOKS` 는 채널별로 **분리 정의**돼 있는데(linkedin/appstore/threads)
    #    게이트만 하나라, 링크드인 1건 실패가 앱스토어 200건 성공의 커밋까지 막았다.
    #    앱스토어 차트 스냅샷 기준선이 동결되면 "신규 진입" 판정이 무의미해진다.
    #    커밋 단위가 채널이면 **실패 단위도 채널**이어야 한다(계열 ②).
    failed_by: dict[str, int] = {}
    try:
        for src, group in groups.items():
            db = notion.db_for(cfg, src)
            # `_landing_source` = "지금 어느 채널을 착지 중인가". notion.py의 스레드 인물키
            # 경보가 **앱스토어 DB에서 거짓으로 울리는 것**을 막는 데 쓴다(거짓 경보 금지).
            cfg2 = {**cfg, "notion": {**cfg["notion"], "db_id": db, "_landing_source": src}}
            if len(groups) > 1 or db != notion.db_for(cfg, ""):
                print(f"· 착지 대상 [{src or '기타'}] {len(group)}건 → DB {db[:8]}")
            gn, gf = notion.land(cfg2, group, run_id)
            n += gn
            failed += gf
            failed_by[src] = failed_by.get(src, 0) + gf
    except RuntimeError as e:            # 토큰 없음/DB 404 등 전제 미충족 — 깔끔히 안내
        print(f"⚠️  착지 중단: {e}")
        print("   → HARD-GATE: 통합을 착지 DB에 공유했는지, 토큰이 있는지 확인.")
        return                            # 실패 시 seen-store 커밋 안 함 → 후보 유실 없음
    print(f"🎉 land 완료: {n}건 착지 (run {run_id})")
    # ★ **행 하나라도 착지에 실패했으면 상태를 확정하지 않는다.**
    #   확정하면 그 사람은 seen-store에 '봤음'으로 박혀 다음 실행에 안 잡힌다 = 조용한 영구 유실.
    #   재수집(돈 조금 더)이 유실(영영 못 만남)보다 항상 낫다 — 이 스킬의 기본 방향이다.
    # 실패한 **채널만** 커밋을 보류한다. 성공한 채널은 확정한다 — 남의 실패로 내 기준선이
    # 동결되면, 그 손실(앱스토어 Δ 는 어제와 비교하는 값이라 하루 놓치면 못 되돌린다)이
    # 유실 방지 이득보다 크다.
    _bad = _failed_adapters(failed_by)
    if _bad:
        print(f"· seen-store/스냅샷 미확정 [{', '.join(sorted(_bad))}] — "
              f"실패 {failed}건이 다음 실행에 다시 후보로 올라온다")
    _commit_hooks(cfg, channel, skip=_bad)   # [2단계 커밋] 성공한 채널만 확정
    _enrich_bio(cfg, channel)             # bio가 없으면 judge가 판단할 근거가 없다


def _enrich_bio(cfg: dict, channel: str) -> None:
    """착지 직후 **bio(Author Headline)를 채운다.** 멱등 — 이미 찬 행은 건너뛴다.

    🔴 왜 파이프라인에 넣나: 사람이 DB를 열고 한 말이 *"뭘 하는 곳인지 알 수가 없네"*였다.
      글 한 편은 중앙값 **75자**라 정체를 못 알려주는데, bio는 한 줄로 알려준다:
        「불당 22대로92 1층 더덕삼겹살 전문점!」  vs  「🚀 Founder & CEO of ONE」
      특히 **팔로잉그래프 인물은 글이 아예 없다**(120명 중 100명) — bio가 없으면 judge가
      **판정 자체를 못 한다.** 이 층이 정밀도 1위(19.1%)인데 근거가 없어 못 쓰는 상태가 된다.
      $0.005/명. 키워드 액터도 리플라이 액터도 bio를 주지 않으므로 별도로 사야 한다.
    """
    if "threads" not in _expand(channel):
        return
    import subprocess
    script = PROJ / "tools" / "backfill_bio.py"
    if not script.exists():
        return
    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True)
    print(r.stdout.rstrip() or "· bio 백필: 출력 없음")
    if r.returncode != 0:
        # 실패해도 착지는 이미 끝났다. 다음 실행이 멱등하게 다시 시도한다.
        print(f"⚠️  bio 백필 실패(착지는 정상) — 다음 실행이 재시도한다\n{r.stderr[-300:]}")


# land 성공 후 확정할 어댑터 상태(seen-store · 스냅샷 기준선 · 페이지 커서).
# 실패 시 미호출 → 다음 실행에 재수집. **유실 대신 재수집**이 안전한 방향이다.
_COMMIT_HOOKS = {
    "linkedin_harvestapi": ("adapters.linkedin", "commit_seen"),   # seen-store + 페이지 커서
    "appstore": ("adapters.appstore", "commit_seen"),              # 차트 스냅샷 기준선
    "threads": ("adapters.threads", "commit_seen"),                # 팔로잉·워치리스트 로테이션 커서
}


def _failed_adapters(failed_by: dict[str, int]) -> set[str]:
    """착지 실패 `source`(한국어) → 그 소스를 내는 **어댑터 등록명** 집합.

    🔴 [W12 · B-F9] 이 변환이 이 수정의 핵심이다. 처음엔 `failed_by` 의 키를 그대로
       `skip` 으로 넘겼는데, **어휘가 다르다**:

           _COMMIT_HOOKS 키 : appstore · threads · linkedin_harvestapi   (어댑터 등록명)
           착지 그룹 키      : 앱스토어 · 스레드 · 링크드인              (Candidate.source)

       그래서 스킵이 **한 번도 안 걸리는 no-op** 이었다. 내가 바로 위 독스트링에
       "어휘가 같아야 한다"고 적어놓고 그대로 걸렸다 — 그래서 손으로 적지 않고
       **어댑터 클래스의 `source` 에서 유도**한다. 매핑을 손으로 적으면 어댑터가 하나
       늘 때마다 조용히 썩는다.

       ⚠️ 링크드인은 어댑터가 둘인데(harvestapi·listly) `source` 가 같다. 그 채널의
          착지가 실패하면 **둘 다** 보류한다 — 어느 어댑터가 만든 행이 실패했는지
          `source` 만으로는 모르고, 보수적인 쪽이 유실을 막는다.
    """
    import importlib
    bad_srcs = {s for s, f in failed_by.items() if f}
    out: set[str] = set()
    for name, (mod, cls, _) in _REGISTRY.items():
        if name not in _COMMIT_HOOKS:
            continue
        try:
            src = getattr(getattr(importlib.import_module(mod), cls), "source", None)
        except Exception:                         # noqa: BLE001 — 매핑 실패가 커밋을 막으면 안 된다
            continue
        if src and src in bad_srcs:
            out.add(name)
    return out


def _commit_hooks(cfg: dict, channel: str, skip: set[str] | None = None) -> None:
    """성공한 채널의 어댑터 상태를 확정한다. `skip`(어댑터 등록명)은 보류한다(B-F9)."""
    import importlib
    skip = skip or set()
    for name in _expand(channel):
        hook = _COMMIT_HOOKS.get(name)
        if not hook:
            continue
        if name in skip:
            print(f"· commit 보류 [{name}] — 이 채널에 착지 실패가 있었다")
            continue
        try:
            getattr(importlib.import_module(hook[0]), hook[1])(cfg)
        except Exception as e:
            print(f"⚠️  commit hook {name} 실패: {e}")


# --- io helpers ---
def _dump_cut(cut: list[dict], channels: list[str] | None = None) -> None:
    """L0 컷 기록 — 사유별 집계 출력 + 원본 보존(복원 가능).

    🔴 [2026-08-13 W12 · 리뷰 A-F2] 예전엔 `Counter(...).most_common()` 만 찍었다.
       그러면 **0건이 된 사유는 줄 자체가 사라진다** — "대기업컷 0건"과 "대기업 룰이
       죽었다"가 출력상 같아진다. 실제로 그 상태가 8일간 유지됐고 20본짜리 검증
       스위트도 못 봤다(그 축을 원리적으로 보지 않았다).

       이제 `judge.CUT_REASONS` 에 선언된 기대 사유를 들고 와서 **0건인 사유를 명시**한다.
       정본 R5 의 원칙(*"0 이 두 뜻이면 둘을 갈라 본다"*)을 레코드 수가 아니라
       **사유 분포** 축에 적용한 것이다.
    """
    from collections import Counter
    from core import judge

    tally = Counter(c["_cut_reason"].split("(")[0] for c in cut)
    for reason, n in tally.most_common():
        print(f"    - {reason}: {n}건")

    # 이번 실행이 다룬 채널의 **룰 계열** 중 한 번도 발화하지 않은 것 — 사실로 말한다.
    # 단위가 계열인 이유는 judge.CUT_RULES 주석 참조(사유 단위면 매 실행 경고 = 경보 피로).
    seen_src = {c.get("source") for c in cut} or set(channels or [])
    silent: list[str] = []
    for src, rules in judge.CUT_RULES.items():
        if src not in seen_src:
            continue                              # 이번에 안 돈 채널은 볼 것 없다
        for rule, reasons in rules.items():
            fired = any(any(k.startswith(r.split("{")[0]) for k in tally) for r in reasons)
            if not fired:
                silent.append(f"{src} / {rule}")
    if silent:
        print(f"    ⚠️  한 번도 발화하지 않은 컷 룰 {len(silent)}개 — "
              f"진짜 0 인지 룰이 죽었는지 확인하라:")
        for s in silent:
            print(f"       · {s}")

    _write_cut_compact(cut)


# 🔴 [2026-08-16] 예전엔 `cut_<날짜>.json` 에 **컷된 후보 전량을 그대로** 썼다.
#    21일치가 275MB 가 됐고, 실측하니:
#
#        cut 레코드 14필드 = raw/appstore_*.json 13필드 + `_cut_reason` 1개
#        컷 키 8,697/8,697(100%) 이 같은 날 raw 에 존재
#        공통 13필드 값 불일치 **0** (앞 2,000행)
#        연속 이틀 65% 중복 · 165,719행 → 고유 키 15,750개
#
#    즉 이 파일이 만들어낸 정보는 **`_cut_reason` 하나**였고 나머지는 raw 의 글자 그대로
#    사본이었다. 과거분은 `scripts/compact_cuts.py` 로 접었고(275MB → 3.1MB), **여기서
#    처음부터 접힌 형태로 쓰지 않으면 매일 다시 쌓인다.**
def _write_cut_compact(cut: list[dict]) -> None:
    """컷 결과를 누적 압축본(`cut_compact.json`)에 병합한다.

    남기는 것 — 이 파일에만 있는 정보(`reason`) + 압축본을 혼자 읽게 하는 색인
    (`k`·`name`·`source`·`adapter`) + 접으면서 생기는 정보(`first`·`last`·`n`).
    버리는 것 — `raw_text`·`signals`·`one_liner`·`founder`·`homepage`·`linkedin_url`·
    `released_at`. 전부 `raw/` 에 정본이 있다.

    ⚠️ 색인을 남기는 이유: appstore raw 는 14일 뒤 지워진다(`config/retention.yaml`).
       그 뒤엔 조인할 상대가 없어 키만 있으면 사람이 무엇인지 알 수 없다.
    """
    import collections as _c
    day = date.today().isoformat()
    path = CAND / "cut_compact.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError:
        # 파손이면 **덮어쓰지 않고** 보존한다 — 조용한 유실을 만들지 않는다(장부 규율).
        bad = path.with_suffix(f".corrupt-{int(date.today().strftime('%Y%m%d'))}")
        path.rename(bad)
        print(f"    🔴 cut_compact.json 파손 — {bad.name} 로 보존하고 새로 시작한다")
        doc = {}

    recs = {r["k"]: r for r in doc.get("records", [])}
    for r in cut:
        k = str(r.get("ext_id") or r.get("app_id") or r.get("url") or r.get("name") or "")
        if not k:
            continue
        e = recs.get(k)
        if e is None:
            recs[k] = {"k": k, "name": r.get("name", ""), "source": r.get("source", ""),
                       "adapter": r.get("adapter", ""), "reason": str(r.get("_cut_reason", "")),
                       "first": day, "last": day, "n": 1}
        else:
            e["n"] = int(e.get("n", 0)) + 1
            if day >= e.get("last", ""):
                e["last"] = day
                if r.get("_cut_reason"):
                    e["reason"] = str(r["_cut_reason"])
            if day < e.get("first", day):
                e["first"] = day

    tally = dict(doc.get("daily_reason_tally", {}))
    tally[day] = dict(_c.Counter(
        str(r.get("_cut_reason", "")).split("(")[0].strip() for r in cut))
    dsrc = dict(doc.get("daily_sources", {}))
    dsrc[day] = sorted({str(r.get("source", "")) for r in cut if r.get("source")})

    out = {"records": sorted(recs.values(), key=lambda r: r["k"]),
           "daily_reason_tally": dict(sorted(tally.items())),
           "daily_sources": dict(sorted(dsrc.items())),
           "source_files": doc.get("source_files", []),
           "compacted_rows": int(doc.get("compacted_rows", 0)) + len(cut)}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)                                   # 원자 교체
    print(f"    · 컷 {len(cut):,}건 → cut_compact.json "
          f"(고유 {len(out['records']):,} · {path.stat().st_size/2**20:.1f}MB)")


def _load_raw_for(channels: list[str]) -> list[dict]:
    """지정 채널별로 **오늘자** raw 파일을 읽어 합친다. LANDABLE만 착지 대상.

    ⚠️ 날짜 가드가 핵심이다. 예전엔 `files[-1]`(가장 최근 파일)을 그냥 읽어서, 오늘 collect가
    실패한 채널이 **어제 raw를 다시 착지**했다. 실제로 07-11 착지분의 스레드는 07-10 파일에서
    나왔다 — "오늘 수집됐다"고 믿은 데이터가 하루 묵은 것이었다.
    수집이 실패했으면 0건이 맞다. 조용히 어제 것으로 때우면 실패를 영영 모른다.
    """
    today = date.today().isoformat()
    out: list[dict] = []
    for name in channels:
        path = RAW / f"{name}_{today}.json"
        if not path.exists():
            stale = sorted(RAW.glob(f"{name}_*.json"))
            hint = f"(최근 파일: {stale[-1].name})" if stale else "(raw 파일 없음)"
            print(f"🔴 land skip: '{name}' 오늘({today}) 수집분 없음 {hint} — collect 실패 의심")
            continue
        out.extend(json.loads(path.read_text(encoding="utf-8")))
    return out


def _load_latest_raw() -> list[dict]:
    out: list[dict] = []
    for f in sorted(RAW.glob("*.json")):
        out.extend(json.loads(f.read_text(encoding="utf-8")))
    return out


def _load_latest_candidates() -> list[dict]:
    files = sorted(CAND.glob("candidates_*.json"))
    if not files:
        return []
    return json.loads(files[-1].read_text(encoding="utf-8"))
