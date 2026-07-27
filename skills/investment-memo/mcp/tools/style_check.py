"""섹션 본문의 하우스 보이스 준수 여부를 기계적으로 검사한다.

"적절히 짧게" 같은 정성 규칙은 모델마다 해석이 달라 지켜지지 않는다.
완성본 코퍼스 41개 문서에서 뽑은 실측 임계값으로 검사하고, 위반을
save_section 시점에 즉시 돌려줘 그 자리에서 고치게 한다.

기준선 산출: `python scripts/backtest.py corpus` (BASELINE.md 참조)
"""

import re
import statistics as st

# 완성본 코퍼스 41개 문서 실측값
TARGET_MEDIAN = 68           # 불릿 길이 중앙값(자)
TARGET_LONG_RATIO = 0.04     # 150자 초과 불릿 비율
LONG_CHARS = 150

# 경고와 오류를 가르는 선. 목표를 조금 넘는 것은 경고, 크게 넘으면 오류로 본다.
ERROR_MEDIAN = 90
ERROR_LONG_RATIO = 0.10
MAX_BULLET_CHARS = 250       # 이 길이를 넘는 불릿은 개별 지목
MAX_DEPTH = 2                # 하위 불릿 2단계까지 (가이드라인)

BULLET_RE = re.compile(r"^(\s*)[-*]\s+(.*)$")


def parse_bullets(content: str) -> list[dict]:
    """마크다운 본문에서 불릿을 뽑는다.

    Returns:
        [{"text": "...", "chars": 84, "depth": 0, "line": 12}, ...]
    """
    out = []
    for lineno, raw in enumerate(content.split("\n"), start=1):
        match = BULLET_RE.match(raw)
        if not match:
            continue
        indent, text = match.group(1), match.group(2).strip()
        if not text:
            continue
        # 볼드 마커는 길이에서 제외한다. 서식이지 내용이 아니다.
        plain = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
        out.append(
            {
                "text": plain,
                "chars": len(plain),
                "depth": len(indent) // 2,
                "line": lineno,
            }
        )
    return out


def check(content: str, section_name: str = "") -> dict:
    """본문을 검사해 위반 목록을 돌려준다.

    Returns:
        {"ok": bool, "errors": [...], "warnings": [...], "stats": {...}}
    """
    bullets = parse_bullets(content)
    errors: list[str] = []
    warnings: list[str] = []

    if not bullets:
        return {
            "ok": True,
            "errors": [],
            "warnings": [],
            "stats": {"불릿수": 0},
        }

    lengths = [b["chars"] for b in bullets]
    median = st.median(lengths)
    long_ones = [b for b in bullets if b["chars"] > LONG_CHARS]
    long_ratio = len(long_ones) / len(bullets)
    deep = [b for b in bullets if b["depth"] > MAX_DEPTH]
    huge = [b for b in bullets if b["chars"] > MAX_BULLET_CHARS]

    if median > ERROR_MEDIAN:
        errors.append(
            f"불릿 길이 중앙값 {median:.0f}자 — 완성본 코퍼스 실측 {TARGET_MEDIAN}자의 "
            f"{median / TARGET_MEDIAN:.1f}배. 한 불릿에 여러 내용이 섞여 있을 가능성이 큼. "
            f"내용이 달라지는 지점에서 불릿을 나눌 것"
        )
    elif median > TARGET_MEDIAN:
        warnings.append(
            f"불릿 길이 중앙값 {median:.0f}자 (목표 {TARGET_MEDIAN}자)"
        )

    if long_ratio > ERROR_LONG_RATIO:
        errors.append(
            f"{LONG_CHARS}자 초과 불릿이 {long_ratio * 100:.0f}% "
            f"({len(long_ones)}/{len(bullets)}개) — 목표 {TARGET_LONG_RATIO * 100:.0f}%. "
            f"근거를 하위 불릿으로 내리면 상위 불릿이 짧아짐"
        )
    elif long_ratio > TARGET_LONG_RATIO:
        warnings.append(
            f"{LONG_CHARS}자 초과 불릿 {long_ratio * 100:.0f}% "
            f"(목표 {TARGET_LONG_RATIO * 100:.0f}%)"
        )

    for bullet in huge:
        errors.append(
            f"L{bullet['line']}: {bullet['chars']}자 불릿 — "
            f"\"{bullet['text'][:40]}…\" 반드시 분리할 것"
        )

    if deep:
        warnings.append(
            f"{MAX_DEPTH}단계를 넘는 하위 불릿 {len(deep)}개 "
            f"(L{', L'.join(str(b['line']) for b in deep[:5])})"
        )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "불릿수": len(bullets),
            "중앙값": round(median),
            "평균": round(st.mean(lengths)),
            "최장": max(lengths),
            "장문비율": round(long_ratio * 100),
        },
    }


def format_result(result: dict, section_name: str = "") -> str:
    """검사 결과를 사람이 읽는 형태로."""
    stats = result["stats"]
    if not stats.get("불릿수"):
        return ""

    lines = [
        f"📏 문체 검사 — 불릿 {stats['불릿수']}개, 중앙값 {stats['중앙값']}자, "
        f"{LONG_CHARS}자 초과 {stats['장문비율']}%"
    ]
    for msg in result["errors"]:
        lines.append(f"  ❌ {msg}")
    for msg in result["warnings"]:
        lines.append(f"  ⚠️ {msg}")
    if result["ok"] and not result["warnings"]:
        lines.append("  ✔ 하우스 보이스 기준 충족")
    elif result["errors"]:
        lines.append("  → 수정 후 save_section을 다시 호출할 것")
    return "\n".join(lines)
