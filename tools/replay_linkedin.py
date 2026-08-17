"""리플레이 — 저장해둔 액터 원본으로 파서를 **돈 0 · 망 0** 으로 검증한다.

## 왜 가능한가

옛 시스템이 유료로 받은 원본을 `{slug, payload, items}` 로 무조건 저장해뒀다.
그 습관 하나 덕에 [0] 계약 전체가 네트워크 0회로 만들어졌고, 지금 파서 검증도 공짜다.

    ~/kv-data/net-sourcing/raw/_actor/*.json   136본 · 161MB

## 무엇을 확인하나

[0] 실측 수치를 그대로 다시 잰다. 어긋나면 파서가 틀렸거나 데이터가 바뀐 것이다.

    id(URN)          39자 고정 · 100%
    publicIdentifier 99.5% — 🔴 100% 가 아니다
    한글 vanity       6.5% · 퍼센트 인코딩 0건
    endDate          dict 100% · 문자열 0건
    현재직장 누락      currentPosition 만 보면 33.9% 를 놓친다

    ~/kv/.venv/bin/python tools/replay_linkedin.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kvlib import paths  # noqa: E402
from skills.linkedin import profile  # noqa: E402

ARCHIVE = paths.data_root() / "net-sourcing" / "raw" / "_actor"
LINKEDIN_SLUGS = ("harvestapi-linkedin-profile-search", "harvestapi-linkedin-profile-scraper")


def load() -> list[dict]:
    items = []
    for p in sorted(ARCHIVE.glob("*.json")):
        if not any(s in p.name for s in LINKEDIN_SLUGS):
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️  {p.name} 파싱 실패: {type(e).__name__}")
            continue
        items += data.get("items", []) if isinstance(data, dict) else data
    return items


def main() -> int:
    if not ARCHIVE.exists():
        print(f"🔴 액터 원본이 없다: {ARCHIVE}")
        return 1
    items = load()
    n = len(items)
    print(f"· 링크드인 프로필 원본 {n}행 (망 0 · 돈 0)\n")
    if not n:
        return 1

    ok = True

    def expect(name: str, got: float, want: float, tol: float, unit: str = "%") -> None:
        nonlocal ok
        good = abs(got - want) <= tol
        ok &= good
        print(f"  {'✅' if good else '🔴'} {name:34} {got:7.1f}{unit}  (실측 {want}{unit} ±{tol})")

    # 1) URN — 39자 고정, 100%
    urns = [str(i.get("id") or "") for i in items]
    urn39 = sum(1 for u in urns if len(u) == 39 and profile.canonical_urn(u))
    expect("id(URN) 39자 · 정규화 성공", 100 * urn39 / n, 100.0, 0.5)

    # 2) publicIdentifier — 🔴 100% 가 아니다
    pub = sum(1 for i in items if str(i.get("publicIdentifier") or "").strip())
    expect("publicIdentifier 보유", 100 * pub / n, 99.5, 1.0)

    # 3) 한글 vanity raw — 인코딩하면 같은 사람이 두 키가 된다
    han = sum(1 for i in items if re.search(r"[가-힣]", str(i.get("publicIdentifier") or "")))
    pct_enc = sum(1 for i in items if "%" in str(i.get("publicIdentifier") or ""))
    expect("한글 vanity(raw)", 100 * han / n, 6.5, 2.0)
    print(f"  {'✅' if pct_enc == 0 else '🔴'} {'퍼센트 인코딩':34} {pct_enc:7d}건   (실측 0건)")
    ok &= pct_enc == 0

    # 4) endDate 는 dict — 문자열이면 is_present 가 전건 False 가 된다
    dicts = strs = 0
    for i in items:
        for e in i.get("experience") or []:
            end = e.get("endDate")
            if isinstance(end, dict):
                dicts += 1
            elif isinstance(end, str):
                strs += 1
    print(f"  {'✅' if strs == 0 else '🔴'} {'endDate dict / 문자열':34} {dicts:7d} / {strs}   (실측 문자열 0건)")
    ok &= strs == 0

    # 5) 🔴 currentPosition 만 보면 얼마나 놓치나
    miss = 0
    for i in items:
        cur = {(p.get("companyName") or p.get("company") or "").strip().lower()
               for p in (i.get("currentPosition") or []) if isinstance(p, dict)}
        pres = {(e.get("companyName") or e.get("company") or "").strip().lower()
                for e in (i.get("experience") or []) if profile.is_present(e)}
        if pres - cur - {""}:
            miss += 1
    expect("currentPosition 이 놓치는 사람", 100 * miss / n, 33.9, 12.0)
    print(f"      → positions() 가 이걸 흡수한다. 안 하면 이 사람들의 '현재'를 못 본다")

    # 6) 개월 계산 — 노션에 쌓인 기존 값과 분포를 대조한다
    import statistics as stt
    ten = [profile.tenure_months(i) for i in items]
    fnd = [profile.founding_months(i) for i in items]
    gap = [profile.gap_months(i) for i in items]
    tv = [x for x in ten if x is not None]
    fv = [x for x in fnd if x is not None]
    gv = [x for x in gap if x is not None]
    print(f"\n  · 재직 개월   산출 {len(tv)}/{n} ({100*len(tv)/n:.0f}%) · 중앙값 {stt.median(tv):.0f}")
    print(f"  · 창업 개월   산출 {len(fv)}/{n} ({100*len(fv)/n:.0f}%) · 중앙값 {stt.median(fv):.0f}")
    print(f"  · 공백 개월   산출 {len(gv)}/{n} ({100*len(gv)/n:.0f}%) · 0인 사람 {sum(1 for x in gv if x==0)}")
    # 🔴 창업 직함 우선 min 이 실제로 작동하나 — 그냥 min 과 달라지는 사람 수
    plain = []
    for i in items:
        ps = profile._raw_positions(i)
        vs = [m for m in (profile._pos_months(x) for x in ps) if m is not None]
        plain.append(min(vs) if vs else None)
    any_diff = sum(1 for a, b in zip(ten, plain) if a is not None and b is not None and a != b)
    # 🔴 옛 실측 7.7% 는 **좁은 정의**다 — "그냥 min 으로는 12개월 미만인데 창업 직함으로는
    #    24개월 이상"인 사람. 값이 조금이라도 다른 사람을 세면 훨씬 커진다(그건 다른 지표다).
    #    같은 것을 재야 대조가 성립한다.
    rescued = sum(1 for pl, fd in zip(plain, fnd)
                  if pl is not None and pl < 12 and fd is not None and fd >= 24)
    print(f"  · 창업직함 우선 min 이 값을 바꾼 사람: {any_diff}명 ({100*any_diff/n:.1f}%)")
    print(f"  · 그중 **12개월 미만 → 창업 24개월 이상** 으로 구제된 사람: {rescued}명 ({100*rescued/n:.1f}%)")
    ok2 = 3.0 <= 100*rescued/n <= 15.0
    print(f"  {'✅' if ok2 else '🔴'} 구제 비율이 실측(7.7%) 범위 안 (3~15%)")
    print("      → 0 이면 Example Person F 보정이 작동 안 하는 것이다")
    ok &= ok2

    # 7) 정규화가 실제로 접는가
    raw_keys = {u for u in urns if u}
    folded = {profile.canonical_urn(u) or u for u in raw_keys}
    print(f"\n  · URN 정규화: 원본 키 {len(raw_keys)} → 접은 키 {len(folded)} "
          f"(같은 사람 {len(raw_keys) - len(folded)}쌍이 합쳐졌다)")

    # 8) 지문 버전
    fps = [profile.fingerprint(i) for i in items[:200]]
    versioned = all(f.startswith(profile.FP_VERSION + "|") for f in fps)
    print(f"  {'✅' if versioned else '🔴'} 지문에 버전 접두가 붙는다 ({profile.FP_VERSION})")
    ok &= versioned

    print("\n" + ("  ✅ 리플레이 통과 — 파서가 실측과 맞는다" if ok
                  else "  🔴 실측과 어긋난다 — 파서가 틀렸거나 데이터가 바뀌었다"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
