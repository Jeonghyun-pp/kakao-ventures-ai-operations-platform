"""claude -p 세션 — 코드가 판단·생성을 claude 에게 위임하는 **단일 통로**. [ADR 0003]

## 왜 이 한 곳인가 (Anti-Corruption Layer)
AI 호출을 이 모듈로 격리한다. 파이프라인의 나머지 코드는 claude 의 존재를 모르고,
claude 를 어떻게 부르는지(플래그·모델·권한)도 여기 한 곳만 안다. 나중에 부르는 방식이
바뀌어도(모델 교체·tmux 전환) 고칠 곳은 여기뿐이다.

## 세 가지 구조적 안전장치
- **API 키 안 씀.** `claude -p` 구독 세션만 부른다. `ANTHROPIC_API_KEY` 를 읽지 않는다.
- **최소권한(least privilege).** 기본은 **도구 0개** — 판단/초안은 프롬프트를 읽고 stdout 으로
  답하면 되지, 파일·셸·네트워크가 필요 없다. 도구를 굳이 줄 때도 `_assert_safe_tools` 가
  발송·셸·네트워크 계열을 **구조적으로 거부**한다(선언이 아니라 코드로).
- **hang 방지.** subprocess timeout 을 건다. 세션이 권한 프롬프트에 걸려 멈추면 timeout 이
  죽이고 **시끄럽게 실패**한다(조용히 매달리지 않는다).

## 출력은 파싱하지 않는다 (관심사 분리)
claude 출력은 **soft** 다 — 스키마를 프롬프트에 줘도 강제가 아니라서 프로즈가 섞이거나
코드펜스로 감싸일 수 있다. 그 검증은 이 모듈이 아니라 `validate.py`(WAP 의 Audit) 몫이다.
여기서는 stdout 원문(str)을 그대로 돌려준다.

## 쿼터 [W6]
구독엔 사용량 API 가 없어 **호출 수를 프록시**로 잰다. 이 모듈이 유일한 통로라 여기가 계측
지점(chokepoint metering): spawn 전 `gate.check_calls("claude")` 로 어드미션, spawn 시도 후
(성공/실패/timeout 공통) `gate.record_call` 로 1콜 기록. 게이트에 막힌 호출은 spawn 자체가
없으므로 카운트되지 않는다. 임계는 config/resources.yaml `claude:` 선언(하드코딩 0).
자원 직렬화는 파이프라인 레벨 `run_pipeline(resources=["claude"])` (이 모듈이 아니라).
"""
from __future__ import annotations

import subprocess

from . import gate

# 부작용이 가역·로컬이라 허용 가능한 도구(굳이 줄 때만).
_SAFE_TOOLS = {"Read", "Write", "Edit", "Grep", "Glob", "LS"}
# 언제나 명시적으로 차단 — 네트워크·셸·임의 실행(발송 경로 포함).
_FORBIDDEN_TOOLS = ["Bash", "WebFetch", "WebSearch"]

_DEFAULT_TIMEOUT = 180          # 초. 배치 판단 한 번이 이보다 오래 걸리면 뭔가 잘못된 것.


class SessionError(RuntimeError):
    """claude -p 호출이 실패했다(비정상 종료·timeout·빈 출력). fail-loud."""


def _assert_safe_tools(tools: "tuple[str, ...]") -> None:
    """허용 도구가 안전 집합 안인지 **구조로** 확인한다. 발송·셸·네트워크는 못 준다."""
    bad = [t for t in tools if t not in _SAFE_TOOLS]
    if bad:
        raise SessionError(
            f"session.py 는 위험 도구를 세션에 못 준다: {bad} "
            f"(허용: {sorted(_SAFE_TOOLS)}). 발송·셸·네트워크 금지 — ADR 0003."
        )


def run_session(
    prompt: str,
    *,
    system: str | None = None,
    allowed_tools: "tuple[str, ...]" = (),
    model: str | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
    cwd: str | None = None,
) -> str:
    """claude -p 세션 1회. 반환 = stdout 원문(검증 전). 실패는 SessionError.

    prompt        : 무엇을 판단/생성할지(판단 대상 데이터를 프롬프트에 담는다).
    system        : 있으면 --append-system-prompt 로 붙인다("JSON 만 출력" 같은 형식 규율).
    allowed_tools : 기본 () = 도구 없음(가장 안전). 주더라도 _SAFE_TOOLS 만.
    model         : 없으면 세션 기본 모델.
    """
    _assert_safe_tools(allowed_tools)

    # [W6] 어드미션 — 일일 호출 게이트. 막히면 spawn 없이 시끄럽게 실패(카운트 안 됨).
    v = gate.check_calls("claude")
    if not v.allowed:
        gate.notify_blocked(v)
        raise SessionError(f"claude 일일 호출 게이트 차단: {v.reason}")

    # prompt 는 argv 가 아니라 **stdin** 으로 넘긴다 — 판단 대상 데이터가 "-" 로 시작해도
    # CLI 플래그로 해석될 수 없다(리뷰 W5-med, 인자 주입 차단).
    cmd = ["claude", "-p"]
    if system:
        cmd += ["--append-system-prompt", system]
    if allowed_tools:
        cmd += ["--allowedTools", *allowed_tools]
    cmd += ["--disallowedTools", *_FORBIDDEN_TOOLS]   # 항상 위험 도구 차단(belt-and-suspenders)
    # 전역 설정(~/.claude)의 MCP 서버·allowlist 를 세션에 상속하지 않는다(리뷰 H5).
    # --strict-mcp-config + --mcp-config 미지정 = MCP 0개 — "도구 0개"가 비로소 구조가 된다.
    cmd += ["--strict-mcp-config"]
    if model:
        cmd += ["--model", model]

    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
    except subprocess.TimeoutExpired as e:
        gate.record_call("claude", ok=False, err="timeout")   # timeout 도 쿼터를 태웠다 — 카운트
        raise SessionError(f"claude -p timeout({timeout}s) — 권한 프롬프트에 걸렸거나 과부하") from e
    except FileNotFoundError as e:
        # spawn 자체가 없었음 — 쿼터 소모 0, 카운트하지 않는다
        raise SessionError("claude 실행 파일을 못 찾음 — CLI 설치·PATH 확인") from e
    except Exception as e:                                # noqa: BLE001  [중간점검 B7]
        # 그 외 예외(OSError 등)도 spawn 은 시도됐을 수 있다 — 카운트 + SessionError 변환
        # ("실패는 SessionError" 계약 준수 — 원시 예외를 그대로 흘리지 않는다).
        gate.record_call("claude", ok=False, err=type(e).__name__)
        raise SessionError(f"claude -p 예기치 못한 실패: {e!r}") from e

    gate.record_call("claude", ok=(proc.returncode == 0))
    if proc.returncode != 0:
        raise SessionError(
            f"claude -p 비정상 종료 rc={proc.returncode}: {(proc.stderr or '').strip()[:300]}"
        )
    out = (proc.stdout or "").strip()
    if not out:
        raise SessionError("claude -p 빈 출력 — 인증 만료 또는 프롬프트 문제")
    return out
