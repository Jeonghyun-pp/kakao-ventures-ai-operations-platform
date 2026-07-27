"""Investment Analysis의 회수 수익성을 계산한다.

IRR·Multiple 계산은 결정론적 사칙연산이라 LLM이 아니라 코드가 해야 한다.
IRR계산_투자수익성_분석_*.xlsx 서식의 수식을 그대로 옮겼다.

역할 분담:
- 피어 시총·매출, 회수방안 선택, 서술은 프롬프트(판단 영역)
- PSR·시가총액·회수금액·Multiple·IRR 계산은 이 모듈(결정론)

엑셀 수식 (IRR 시트):
  시가총액 = Exit매출 × PSR         (보수 ×0.7 / 평균 / 공격 ×1.3)
  회수금액 = 시가총액 × 지분율
  Multiple = 회수금액 / 투자금액
  IRR      = IRR([-투자금, 0, ..., 회수금액])

의존성을 늘리지 않으려고 numpy_financial 대신 순수 파이썬으로 IRR을 푼다.
엑셀 IRR()과 값이 일치함을 Corpus02 실물로 검증했다.
"""


def _npv(rate: float, cashflows: list[float]) -> float:
    """순현재가치. cashflows[0]이 t=0."""
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(cashflows))


def irr(cashflows: list[float], guess: float = 0.1) -> float | None:
    """내부수익률. 엑셀 IRR()과 동일한 정의.

    이분법으로 NPV=0인 rate를 찾는다. 뉴턴법은 초기값에 따라 발산할 수 있어,
    부호가 바뀌는 구간을 이분 탐색하는 쪽이 안정적이다.

    Returns:
        연 IRR (0.5 = 50%). 부호 변화가 없어 해가 없으면 None.
    """
    # 현금흐름에 양수·음수가 모두 있어야 IRR이 존재한다
    if not any(cf > 0 for cf in cashflows) or not any(cf < 0 for cf in cashflows):
        return None

    lo, hi = -0.9999, 10.0
    f_lo = _npv(lo, cashflows)
    f_hi = _npv(hi, cashflows)
    if f_lo * f_hi > 0:
        return None  # 구간 내 부호 변화 없음

    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = _npv(mid, cashflows)
        if abs(f_mid) < 1e-9:
            return mid
        if f_lo * f_mid < 0:
            hi = mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def peer_psr(peers: list[dict], discount: float = 0.7) -> dict:
    """피어그룹에서 적정 PSR을 계산한다 (Target Sales Ratio 시트).

    Args:
        peers: [{"name": "COVER", "market_cap": 10491, "revenue": 4080}, ...]
               시가총액·매출은 억원 단위
        discount: 적정 PSR 할인율 (엑셀 기본 0.7)

    Returns:
        {"individual": [{"name","psr"}...], "avg": 3.26, "applied": 2.28}
    """
    individual = []
    for p in peers:
        rev = p.get("revenue")
        if not rev:
            continue
        individual.append({
            "name": p["name"],
            "psr": p["market_cap"] / rev,
        })
    if not individual:
        return {"individual": [], "avg": None, "applied": None}

    avg = sum(x["psr"] for x in individual) / len(individual)
    return {
        "individual": individual,
        "avg": avg,
        "applied": avg * discount,
    }


def exit_analysis(
    exit_revenue: float,
    applied_psr: float,
    stake: float,
    investment: float,
    invest_year: int,
    exit_year: int,
    scenario_multipliers: tuple[float, float, float] = (0.7, 1.0, 1.3),
) -> dict:
    """회수 시나리오별 Multiple·IRR을 계산한다 (IRR 시트).

    Args:
        exit_revenue: Exit 연도 매출 (억원)
        applied_psr: 적용 PSR (peer_psr의 applied)
        stake: 예상 지분율 (0.0408 = 4.08%)
        investment: 투자금액 (억원)
        invest_year: 투자 연도 (예: 2026)
        exit_year: 회수 연도 (예: 2030 — 현금 유입 시점)
        scenario_multipliers: (보수, 평균, 공격) PSR 배수

    Returns:
        {"보수적": {...}, "평균": {...}, "공격적": {...}} 각각
        psr·시가총액·회수금액·multiple·irr 포함
    """
    labels = ("보수적", "평균", "공격적")
    n_years = exit_year - invest_year
    out = {}

    for label, mult in zip(labels, scenario_multipliers):
        psr = applied_psr * mult
        market_cap = exit_revenue * psr
        proceeds = market_cap * stake
        multiple = proceeds / investment if investment else None

        # 현금흐름: t0에 -투자금, 중간 0, exit_year에 회수금액
        flows = [-investment] + [0.0] * (n_years - 1) + [proceeds]
        rate = irr(flows)

        out[label] = {
            "psr": round(psr, 2),
            "market_cap": round(market_cap, 1),
            "proceeds": round(proceeds, 2),
            "multiple": round(multiple, 2) if multiple else None,
            "irr": round(rate, 4) if rate is not None else None,
        }
    return out


def format_analysis(peer: dict, exit_result: dict, meta: dict) -> str:
    """계산 결과를 Investment Analysis 서술 초안(markdown)으로.

    프롬프트(13_investment_analysis)가 이 초안에 판단·맥락을 붙여 완성한다.
    """
    lines = []

    if peer.get("applied"):
        names = ", ".join(f"{x['name']}(PSR {x['psr']:.1f})" for x in peer["individual"])
        lines.append(f"- 피어그룹 PSR로 적정 밸류 배수를 산정함")
        lines.append(f"  - {names}")
        lines.append(
            f"  - 평균 PSR {peer['avg']:.1f}배에 30% 할인한 {peer['applied']:.2f}배 적용"
        )

    lines.append(f"- {meta.get('exit_method', 'M&A')} 회수 기준 3개 시나리오 수익성")
    for label, s in exit_result.items():
        if s["irr"] is None:
            continue
        lines.append(
            f"  - {label}(PSR {s['psr']}배): 시총 {s['market_cap']:.0f}억, "
            f"회수 {s['proceeds']:.1f}억, {s['multiple']}배, IRR {s['irr'] * 100:.0f}%"
        )

    return "\n".join(lines)
