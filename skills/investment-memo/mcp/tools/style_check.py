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

# 실제 투심 피드백에서 반복 삭제 지시를 받은 표현.
# 오탐이 나면 룰을 무시하게 되므로, 다른 뜻으로 쓰일 여지가 없는 것만 넣는다.
# (인물평·표 중복처럼 정규식으로 못 잡는 것은 프롬프트 자기점검표 소관)
BANNED = [
    (
        re.compile(r"검증\s*(됨|완료|필요|예정)|미검증|검증\s*신호"),
        "error",
        "검증 라벨(`검증됨`/`미검증`/`검증 예정`/`검증 신호`) — 투심에서 "
        "\"누가 무엇으로 검증했나\"에 답할 수 없다. 라벨을 빼고 사실과 시점으로 "
        "쓸 것: `민감도 94% 확인 (2026.04 미팅)`",
    ),
    (
        re.compile(r"^\s*[-*]?\s*\**\s*(종합하면|정리하면|요약하면)"),
        "error",
        "요약 문단 — 불릿 구조가 이미 요약이라 같은 말이 두 번 나온다. "
        "삭제하고, 결론이 필요하면 첫 볼드 헤드라인이 결론이 되게 쓸 것",
    ),
    (
        re.compile(r"Phase\s*\d+\s*[-–—]\s"),
        "error",
        "Phase 뒤 하이픈 — 불릿 글머리와 겹쳐 계층이 무너져 보인다. 콜론으로: "
        "`Phase 1: 국내 진입`",
    ),
    (
        re.compile(r"(단정하기 어려우나|속단하기 이르나|판단하기 이르나)"),
        "warning",
        "면피성 유보 — 논지를 스스로 무효화한다. 불확실성은 별도 문단이 아니라 "
        "해당 주장 옆 괄호 한 줄로",
    ),
]


def check_banned(content: str) -> tuple[list[str], list[str]]:
    """금지 표현을 찾는다.

    Returns:
        (errors, warnings) — 각 항목에 줄 번호와 대체 방법을 담는다
    """
    errors: list[str] = []
    warnings: list[str] = []
    for lineno, raw in enumerate(content.split("\n"), start=1):
        for pattern, severity, fix in BANNED:
            match = pattern.search(raw)
            if not match:
                continue
            msg = f"L{lineno}: \"{match.group(0).strip()}\" — {fix}"
            (errors if severity == "error" else warnings).append(msg)
    return errors, warnings


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
    # 금지 표현은 불릿 유무와 무관하게 검사한다 — 요약 문단은 불릿이 아니다
    errors, warnings = check_banned(content)

    if not bullets:
        return {
            "ok": not errors,
            "errors": errors,
            "warnings": warnings,
            "stats": {"불릿수": 0, "금지표현": len(errors) + len(warnings)},
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
            "금지표현": sum(
                1 for line in content.split("\n")
                for pattern, _, _ in BANNED if pattern.search(line)
            ),
        },
    }


def format_result(result: dict, section_name: str = "") -> str:
    """검사 결과를 사람이 읽는 형태로."""
    stats = result["stats"]
    if not stats.get("불릿수") and not result["errors"] and not result["warnings"]:
        return ""

    if stats.get("불릿수"):
        head = (
            f"📏 문체 검사 — 불릿 {stats['불릿수']}개, 중앙값 {stats['중앙값']}자, "
            f"{LONG_CHARS}자 초과 {stats['장문비율']}%"
        )
    else:
        head = "📏 문체 검사"
    if stats.get("금지표현"):
        head += f", 금지 표현 {stats['금지표현']}건"
    lines = [head]
    for msg in result["errors"]:
        lines.append(f"  ❌ {msg}")
    for msg in result["warnings"]:
        lines.append(f"  ⚠️ {msg}")
    if result["ok"] and not result["warnings"]:
        lines.append("  ✔ 하우스 보이스 기준 충족")
    elif result["errors"]:
        lines.append("  → 수정 후 save_section을 다시 호출할 것")
    return "\n".join(lines)
