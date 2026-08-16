#!/usr/bin/env python3
"""공백기 축 프로브 — **"현 직장이 없는 사람이 링크드인 검색에 잡히기는 하나"** 1런으로 판정. ~$0.20

## 왜 필요한가 (2026-07-27)
회의 피드백: *"재직 1년 미만 필터가 아니라 마지막 직장으로부터 공백기가 얼마나 되는가로."*
계산은 세 줄이다(`today − max(endDate)`). 문제는 **분자가 될 사람이 표본에 없다는 것**이다.

  · 저장된 실액터 응답 271명 재계산 → **공백기 있는 사람 0명**(270명이 Present 포지션 보유)
  · 원인: 현행 5축이 전부 `currentJobTitles` + `yearsAtCurrentCompanyIds:["1"]` 기반이다.
    "현 직장 재직 1년 미만"은 **현 직장이 있어야** 성립한다 → 공백자는 매칭 자체가 안 된다.
    stealth 축 2개도 `searchQuery:"Stealth Startup"`이라 그 문자열이 **현 회사명**에 박힌
    사람만 잡힌다 → 역시 Present 보유가 확정된다.
  · 한편 사람들은 공백을 **실제로 프로필에 남긴다**: 경력 2개 이상 260명 중 **232명(89%)** 이
    과거 경력 사이에 공백이 있고, 63%가 6개월 이상 공백을 가진 적이 있다(월 표기 정밀도 93%).
    → 지표 자체는 성립한다. 못 보고 있는 건 **우리가 그 사람들을 안 사기 때문**이다.

## 이 프로브가 답하는 것 (이게 전부다)
  ① `pastJobTitles`(텍스트 입력)로 검색하면 **Present 없는 사람이 반환되는가** ← 핵심
  ② 반환된다면 공백 개월 분포는 어떤가 (필터 구간 설계 입력)
  ③ 그들이 창업 준비자로 보이는가 (headline·직전 직함을 눈으로)
  ④ `_meta.pagination.totalElements` = **이 축의 모수** (pages·cadence·비용 계획 입력)

## 답하지 **못하는** 것
25명으론 이 축의 **정밀도를 못 잰다.** 기저율 11.7%에서 25명 뽑아 빌더 0명이 나올 확률은
충분히 높다 — 그걸 근거로 축을 죽이면 [[킬룰]] 위반이다. 여기서 얻는 건 **가부와 모수**다.

## 🔴 축을 keywords.yaml에 넣고 `collect`를 돌리지 마라
그러면 기존 5축이 같이 돌아 **$5**가 나간다. 이 스크립트는 액터를 **1런만** 직접 친다.
원본은 `run_actor`가 `data/raw/_actor/`에 자동 보관하므로, 파싱이 틀려도 **$0에 재분석**한다.

사용:
  ./.venv/bin/python tools/probe_gap.py --dry     # 무과금. payload와 예상 비용만
  ./.venv/bin/python tools/probe_gap.py           # 유료 1런 (~$0.20)
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import date
from pathlib import Path

import yaml

PROJ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJ))

LI = "harvestapi~linkedin-profile-search"
MAX_ITEMS = 25                      # 1페이지 = 25명. $0.10(페이지) + 25×$0.004 = $0.20

# 두 변형을 비교한다 — **누구의 과거 직함으로 찾느냐가 인구를 통째로 바꾼다.**
#   founder  : 2026-07-27 실측 → 공백자 4/25(16%)인데 **전원 임원 출신 휴지기**
#              (ex-코인원 CEO·ex-Seegene CMO·ex-배스킨라빈스 CEO). 정의상 "이미 CEO를 해본 사람"이라
#              경력이 길 수밖에 없다.
#   practitioner : 가설 — "개발자·PM이었다가 지금 공백" = 창업 준비 신호에 훨씬 가깝다. 미검증.
VARIANTS = {
    "founder": ["Founder", "Co-Founder", "CEO", "CTO"],
    "practitioner": ["Software Engineer", "Product Manager", "Data Scientist", "Designer"],
}
VARIANT = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--variant=")), "founder")

PAYLOAD = {
    # ★ pastJobTitles는 **텍스트를 받는다**(스키마 실측). pastCompanies는 회사 URL만 받으므로
    #   이번 프로브에선 안 쓴다 — URL 수집이라는 준비 단계를 없애기 위해서다.
    "pastJobTitles": VARIANTS[VARIANT],
    "locations": ["South Korea"],
    # ⚠️ currentJobTitles·yearsAtCurrentCompanyIds를 **일부러 안 넣는다.** 그 둘이 공백자를
    #    구조적으로 배제하는 범인이고, 이 프로브의 목적이 바로 그 배제를 푸는 것이다.
    "profileScraperMode": "Full",   # experience가 있어야 공백 계산이 된다. Short는 안 준다
    "takePages": 1,
    "maxItems": MAX_ITEMS,
}

_MON = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _ym(o) -> int | None:
    if not isinstance(o, dict) or not o.get("year"):
        return None
    return int(o["year"]) * 12 + _MON.get(str(o.get("month", "")).strip()[:3].lower(), 1)


def _load_env() -> None:
    # 🔴 [2026-08-16] 인라인 `.env` 파서 → `kv_core.secrets`.
    #    `main.py:28-36` 이 *"자격증명 정본은 `$KV_CONFIG/.env` 하나뿐. 스킬별 `.env` 를
    #    다시 만들지 마라"* 라고 선언하고 옮겼는데 도구들은 옛 파서를 그대로 들고 있었고,
    #    `project/.env` 는 **존재하지 않아** 실행 즉시 `FileNotFoundError` 로 죽었다.
    from kv_core import secrets
    secrets.load()


def analyze(items: list[dict]) -> None:
    """반환된 프로필을 공백기 관점으로 해부한다. 파싱은 $0이므로 몇 번이든 다시 돌릴 수 있다."""
    today = date.today().year * 12 + date.today().month
    n = len(items)
    print(f"\n{'='*70}\n▶ 수신 {n}명")
    if not n:
        print("🔴 0건 — 액터 파손이거나 이 조합으로는 검색이 안 된다. 원본을 보고 판정하라.")
        return

    meta = (items[0].get("_meta") or {}).get("pagination") or {}
    print(f"▶ ④ 축 모수(totalElements): {meta.get('totalElements')} · "
          f"총 페이지 {meta.get('totalPages')}")

    gapped, present_only, undated = [], 0, 0
    for p in items:
        exp = p.get("experience") or []
        pres = [e for e in exp
                if str(((e.get("endDate") or {}).get("text")) or "").lower() == "present"]
        if pres:
            present_only += 1
            continue
        ends = [m for m in (_ym(e.get("endDate")) for e in exp) if m]
        if not ends:
            undated += 1
            continue
        gapped.append((today - max(ends), p))

    print(f"\n▶ ① **핵심 판정**")
    print(f"   · 현재 진행 중인 포지션 보유 : {present_only}/{n}")
    print(f"   · 🔴 **공백기 있는 사람**      : {len(gapped)}/{n}")
    print(f"   · 경력 날짜 없어 판정 불가    : {undated}/{n}")

    if not gapped:
        print("\n   → 이 축으로도 공백자가 안 잡힌다. 링크드인 people search가 구조적으로")
        print("     현직자만 반환한다는 쪽으로 기운다. **공백기 축은 기각**하고,")
        print("     신선도는 `_tenure_months`(창업직함 개시 시점) 교정으로 간다.")
        return

    ms = sorted(g for g, _ in gapped)
    print(f"\n▶ ② 공백 개월: 중앙 {statistics.median(ms):.0f} · 최소 {min(ms)} · 최대 {max(ms)}")
    for lo, hi in ((1, 3), (3, 6), (6, 12), (12, 10**9)):
        c = sum(1 for m in ms if lo <= m < hi)
        print(f"     {lo}~{hi if hi < 10**9 else '∞'}개월: {c}명")

    print(f"\n▶ ③ 공백자 면면 (창업 준비자인가 / 그냥 쉬는 사람인가 — 사람이 판단할 재료)")
    for gap, p in sorted(gapped, key=lambda x: x[0])[:15]:
        exp = p.get("experience") or []
        last = max(exp, key=lambda e: _ym(e.get("endDate")) or 0, default={})
        name = f"{p.get('firstName','')} {p.get('lastName','')}".strip()
        print(f"   · [{gap:>3}개월 공백] {name[:20]:22s} | {str(p.get('headline') or '')[:52]}")
        print(f"       직전: {str(last.get('position') or '')[:32]} @ "
              f"{str(last.get('companyName') or '')[:32]} "
              f"(~{((last.get('endDate') or {}).get('text')) or '?'})")


def main() -> int:
    dry = "--dry" in sys.argv
    _load_env()
    import adapters.apify as apify
    cfg = yaml.safe_load((PROJ / "config.yaml").read_text(encoding="utf-8"))
    apify.configure(cfg)

    est = 0.10 + MAX_ITEMS * 0.004
    cap = (apify._OPTS.get(LI) or {}).get("max_charge_usd")
    print(f"▶ 변형: {VARIANT}  ({', '.join(VARIANTS[VARIANT])})")
    print(f"▶ 액터 {LI}\n▶ payload:\n{json.dumps(PAYLOAD, ensure_ascii=False, indent=2)}")
    print(f"\n▶ 예상 비용 ${est:.2f} (페이지 $0.10 + {MAX_ITEMS}×$0.004) · 액터 캡 ${cap}")
    if cap and est > cap:
        print(f"🔴 추정이 캡을 넘는다 — 액터가 조용히 자른다. MAX_ITEMS를 줄여라.")
        return 1
    if dry:
        print("\n(--dry: 실행하지 않음)")
        return 0

    # 🔴 이 프로브만의 차단선. 풀 실행 예산($16)을 쓰면 사고가 나도 안 걸린다.
    apify._BUDGET = 1.0
    before = apify.usage_usd()
    items = apify.run_actor(LI, PAYLOAD)
    after = apify.usage_usd()
    print(f"\n💰 실제 과금 ${after - before:.3f} (추정 ${est:.2f})")
    if after - before > est * 1.5:
        print("🔴 실제가 추정의 1.5배를 넘었다 — 단가 가정이 틀렸다. config를 고쳐라.")
    analyze(items)
    print(f"\n· 원본 보관: data/raw/_actor/ (재분석은 $0)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
