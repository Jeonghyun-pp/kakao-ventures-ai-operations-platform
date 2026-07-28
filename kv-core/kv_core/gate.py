"""공유 자원 어드미션 컨트롤 — 돈 쓰기 전에 반드시 통과하는 단일 관문.

기본구조 2단계. net-sourcing 이 자체 구현하던 3중 가드를 승격했다(새로 설계하지 않았다).

## 왜 필요한가

무인화가 시작되는 순간 **"사람이 실행 버튼을 누른다"는 암묵적 예산 가드가 사라진다.**
지금 5개 파이프라인이 수동인 이유의 절반은 "돌리면 얼마 나가지?"라는 망설임이고,
그 망설임이 브레이크 역할을 하고 있다. **그 브레이크를 코드로 옮겨놓고 나서 무인화해야 한다.**
그래서 W2가 W6보다 앞이다.

실제 사고: 2026-07-10 무승인 6런 $5.94 · 계정 정지. 당시 코드에 비용 상한이 한 줄도 없었다.

## 🔴 불변 규칙 02 — 세 브레이크를 절대 같은 값으로 두지 않는다

    preflight   실행 **전** · 추정치로 판단 · 중단 비용 0      → 보수적이어도 된다
    budget      실행 **중** · 이미 쓴 돈     · 회수 불가       → 추정에 밀착시키면 안 된다
    monthly     실행 **전** · 실제 청구액    · 중단 비용 0      → 유일한 진실

추정 $9.79 / 상한 $10 처럼 붙여두면 정상 변동만으로 채널이 통째로 0건이 된다.
budget 은 정상 변동(+40%)은 통과시키고 **액터가 아이템을 10배 뱉는 사고만** 잡는 높이여야 한다.

## 🔴 [2026-07-28] 승격하면서 고친 것 — 우리 상한과 플랫폼 한도 중 **낮은 쪽**

net-sourcing 은 월 상한을 $45 로 알고 있었는데 **Apify 플랜의 실제 한도는 $29** 였다.
즉 `$29~$45` 구간은 **가드가 있다고 믿지만 실제로는 없는 구간**이었다.
플랫폼이 먼저 끊으므로 우리 가드는 한 번도 발동하지 않는다.
→ **둘 중 낮은 쪽을 유효 천장으로 쓴다.** 설정값이 플랫폼보다 높으면 경고한다.

## 쓰는 법

    from kv_core import gate
    v = gate.check("apify", estimate_usd=10.5)
    if not v.allowed:
        gate.notify_blocked(v)      # 실패 + 알림 (결정 B5)
        raise SystemExit(1)
    ...
    gate.record("apify", actual_usd=9.8)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from . import notify, paths


# ────────────────────────────── 설정 ──────────────────────────────

def _config_path() -> Path:
    return paths.repo_root() / "config" / "resources.yaml"


_cfg: dict | None = None


def config(force: bool = False) -> dict:
    """`config/resources.yaml` — 자원별 상한. 시크릿이 아니라 설정이라 리포에 커밋한다."""
    global _cfg
    if _cfg is None or force:
        import yaml
        p = _config_path()
        _cfg = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return _cfg or {}


# ────────────────────────── 자원별 실사용 조회 ──────────────────────────
# 자원마다 "얼마 썼나"를 아는 방법이 다르다. Apify 는 API 로 실청구액을 주지만,
# 그렇지 않은 자원도 있다(예: 구독 쿼터). 조회 불가면 None 을 돌려주고,
# 그 경우 monthly 가드는 **동작하지 않는다**는 사실을 호출부가 알아야 한다.

def _apify_usage() -> tuple[float, float] | None:
    """(이번 달 실청구액, 플랫폼 한도). 조회 실패 시 None."""
    import requests
    token = os.environ.get("APIFY_TOKEN")
    if not token:
        return None
    try:
        r = requests.get("https://api.apify.com/v2/users/me/limits",
                         params={"token": token}, timeout=15)
        d = r.json()["data"]
        return (float(d["current"]["monthlyUsageUsd"]),
                float(d["limits"]["maxMonthlyUsageUsd"]))
    except Exception:
        return None


_USAGE_PROBES = {"apify": _apify_usage}


# ────────────────────────────── 판정 ──────────────────────────────

@dataclass
class Verdict:
    resource: str
    allowed: bool
    reason: str = ""
    used_usd: float | None = None
    ceiling_usd: float | None = None
    estimate_usd: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def remaining_usd(self) -> float | None:
        if self.used_usd is None or self.ceiling_usd is None:
            return None
        return max(0.0, self.ceiling_usd - self.used_usd)

    def summary(self) -> str:
        if self.used_usd is None:
            return f"[{self.resource}] 사용량 조회 불가 — 월 가드 미작동"
        return (f"[{self.resource}] 이번 달 ${self.used_usd:.2f} / 상한 ${self.ceiling_usd:.0f} "
                f"· 잔여 ${self.remaining_usd:.2f} · 이번 실행 추정 ${self.estimate_usd:.2f}")


def check(resource: str, estimate_usd: float = 0.0, *, quiet: bool = False) -> Verdict:
    """실행 **전** 게이트. 여기서 막는 것은 공짜다 — 한 푼도 안 쓰고 중단한다.

    판정식: `실제누적 + 이번런추정 > 유효천장` → 차단.
    유효천장 = min(우리 설정 상한, 플랫폼 한도). 아래 주석 참조.
    """
    rc = (config().get(resource) or {})
    our_cap = rc.get("monthly_budget_usd")
    probe = _USAGE_PROBES.get(resource)
    live = probe() if probe else None

    v = Verdict(resource=resource, allowed=True, estimate_usd=estimate_usd)

    if live is None:
        # 조회가 안 되면 **막지 않는다.** 조회 실패로 파이프라인을 죽이면
        # 네트워크 딸꾹질에 전체가 멈춘다. 대신 가드가 없다는 사실을 표면화한다.
        v.warnings.append(f"{resource} 사용량 조회 실패 — 월 가드가 이번 실행에 적용되지 않는다")
        if not quiet:
            print(f"  ⚠️  {v.warnings[-1]}")
        return v

    used, platform_cap = live
    # 🔴 둘 중 낮은 쪽이 진짜 천장이다. 우리 설정이 더 높으면 그 구간은
    #    "가드가 있다고 믿지만 실제로는 없는 구간"이 된다(2026-07-28 실측 발견).
    ceiling = min(our_cap, platform_cap) if our_cap else platform_cap
    v.used_usd, v.ceiling_usd = used, ceiling

    if our_cap and our_cap > platform_cap:
        v.warnings.append(
            f"설정 상한 ${our_cap:.0f} 이 플랫폼 한도 ${platform_cap:.0f} 보다 높다 — "
            f"${platform_cap:.0f} 를 유효 천장으로 쓴다. config/resources.yaml 을 맞춰라")

    if used + estimate_usd > ceiling:
        v.allowed = False
        v.reason = (f"월 예산 초과 예상: 실사용 ${used:.2f} + 추정 ${estimate_usd:.2f} "
                    f"= ${used + estimate_usd:.2f} > 천장 ${ceiling:.0f}")
    elif used >= ceiling * float(rc.get("warn_at_pct", 80)) / 100:
        v.warnings.append(f"{resource} 사용량 {used / ceiling * 100:.0f}% 도달 — 곧 막힌다")

    if not quiet:
        print(f"  💰 {v.summary()}")
        for w in v.warnings:
            print(f"  ⚠️  {w}")
        if not v.allowed:
            print(f"  🔴 차단: {v.reason}")
    return v


def notify_blocked(v: Verdict) -> bool:
    """게이트에 막혔을 때 알린다 (결정 B5: 실패 + 알림).

    큐잉해서 나중에 자동 재개하는 것은 W3(잡 러너)이 있어야 성립한다.
    지금은 **실패시키고 사람에게 알린다** — 나중에 큐잉으로 승격하기 쉬운 방향이다.
    """
    return notify.slack(
        f"🔴 *예산 게이트 차단* — {v.resource}\n"
        f"{v.reason}\n"
        f"```{v.summary()}```\n"
        f"이번 실행은 중단됐습니다. 한도를 올리거나 다음 주기를 기다려야 합니다.")


# ───────────────────────── 실행 중 차단선 ─────────────────────────
# preflight 를 통과했어도 실행 중에 추정보다 많이 나올 수 있다. 그때 뒤쪽 작업을 끊는다.
# 여기 걸리면 **이미 쓴 돈은 못 돌려받는다** — 그래서 추정에 밀착시키지 않는다.

_spent: dict[str, float] = {}


def record(resource: str, usd: float) -> None:
    _spent[resource] = _spent.get(resource, 0.0) + max(0.0, usd)


def spent(resource: str) -> float:
    return _spent.get(resource, 0.0)


def tripped(resource: str) -> bool:
    """실행 중 차단선을 넘었는가.

    이걸 호출부가 확인해야 하는 이유: 차단선이 터지면 뒤쪽 층·채널이 통째로 0건이 되는데,
    그걸 모르면 '오늘 수집 성공'으로 기록해 백필 창을 닫아버린다(= 못 가져온 구간이 영구 유실).
    """
    cap = (config().get(resource) or {}).get("run_budget_usd")
    return bool(cap) and spent(resource) >= float(cap)


def reset(resource: str | None = None) -> None:
    """실행 단위 누적을 초기화한다(테스트·재실행용)."""
    if resource:
        _spent.pop(resource, None)
    else:
        _spent.clear()
