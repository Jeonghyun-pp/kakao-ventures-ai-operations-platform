"""claude 통로 — 판단이 필요한 칸에만 부르는 유일한 경로.

## 이 파일이 지키는 [0] 제약

    rc 0 + 빈 출력 = 인증 만료   rc 로 성공을 판정하면 **인증 만료가 ok:true 로 기록되고**
                               auth 경보가 자기 목적 시나리오에서 영원히 미발동한다
    출력은 soft                 스키마를 줘도 강제가 아니다 → 되읽어 검증한다
    전역 설정이 상속된다          --strict-mcp-config + 위험 도구 상시 차단
    권한 프롬프트에 걸리면 멈춘다   180초 timeout
    사용량 API 가 없다            호출 수를 프록시로 잰다 (일 30 경고 / 50 차단)

## 출력 형식이 TSV 인 이유

claude 가 `reason` 에 이스케이프 없는 `"` 를 넣어 **JSON 배열이 통째로 깨지고
배치 16건이 전량 유실**된 적이 있다(2회 = 32건). 줄 단위 형식이면 한 줄이 깨져도
나머지는 산다 — events.jsonl 의 torn-tail 규율과 같은 성질이다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from kvlib import Fetched, constraints, paths


class SessionError(RuntimeError):
    pass


def _bin() -> str:
    """claude 실행 파일의 **절대경로**.

    🔴 맨이름 `"claude"` 로 부르면 launchd 에서 못 찾는다 — launchd 의 기본 PATH 는
       로그인 셸의 PATH 가 아니다. 2026-08-19 08:10 에 자동 업데이트가 실행 파일을
       `~/.local/bin` 으로 옮기자 그날 11:00 링크드인 카드와 메일 plan 이 **동시에** 죽었다.
       그때 나온 말이 "claude CLI 가 없다" 였다 — **환경 문제인데 인증 문제처럼 읽힌다.**
       그래서 부를 때마다 절대경로로 해석한다(업데이트로 경로가 또 바뀌어도 따라간다).
    """
    found = shutil.which("claude")
    if not found:
        cand = Path.home() / ".local" / "bin" / "claude"
        found = str(cand) if cand.exists() else ""
    if not found:
        raise SessionError("claude CLI 를 못 찾았다 (PATH 와 ~/.local/bin 둘 다 없음)")
    return found


def _usage_path() -> Path:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return paths.data("_usage", "claude") / f"{day}.jsonl"


def _calls_today() -> int:
    p = _usage_path()
    return len(p.read_text(encoding="utf-8").splitlines()) if p.exists() else 0


def _record(ok: bool, err: str = "") -> None:
    """spawn 을 **시도했으면** 기록한다. timeout 도 쿼터를 태웠으므로 센다.
    게이트에 막힌 호출은 spawn 자체가 없으므로 안 센다 — 이 비대칭이 의도다."""
    row = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": ok}
    if err:
        row["err"] = err
    with _usage_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def run_session(prompt: str, *, system: str | None = None, tools: str | None = None) -> str:
    """claude -p 한 번. stdout 원문을 **파싱하지 않고** 그대로 돌려준다.

    검증은 호출부 몫이다 — 관심사를 섞으면 파싱 실패가 호출 실패로 오인된다.

    `tools` 를 주면 `--tools` 로 넘긴다. `""` 는 **내장 도구 전부 끔**이다 — 바깥 사람이 쓴 글(메일 본문)을
    읽히는 호출은 이걸 쓴다. 금지 목록은 CLI 에 새 도구가 생기면 뚫리지만 빈 허용 목록은 안 뚫린다.
    안 주면(None) 명령줄이 전과 **한 글자도 다르지 않다** — 다른 스킬은 영향이 없다.
    """
    n = _calls_today()
    if n >= constraints.get("claude.daily_calls_block"):
        raise SessionError(f"오늘 claude 호출 {n}회 — 차단선({constraints.get('claude.daily_calls_block')}) 도달")
    if n >= constraints.get("claude.daily_calls_warn"):
        print(f"  ⚠️  오늘 claude 호출 {n}회 (경고선 {constraints.get('claude.daily_calls_warn')})")

    cmd = [
        _bin(), "-p",
        "--strict-mcp-config",                      # 전역 ~/.claude 의 MCP 상속 차단
        "--disallowedTools", ",".join(constraints.get("claude.forbidden_tools")),
    ]
    if system:
        cmd += ["--append-system-prompt", system]
    if tools is not None:
        cmd += ["--tools", tools]

    try:
        # prompt 는 argv 가 아니라 stdin — 데이터가 "-" 로 시작해도 플래그로 안 읽힌다
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                           timeout=constraints.get("claude.timeout_sec"))
    except FileNotFoundError:
        raise SessionError("claude CLI 가 없다")          # spawn 자체가 없었다 → 미계상
    except subprocess.TimeoutExpired:
        _record(False, "timeout")                        # 쿼터는 태웠다 → 계상
        raise SessionError("timeout")

    out = (r.stdout or "").strip()
    # 🔴 rc 가 아니라 출력으로 판정한다
    if not out:
        _record(False, "empty_output")
        raise SessionError("빈 출력 — 인증 만료 의심 (rc=%d)" % r.returncode)
    if r.returncode != 0:
        _record(False, f"rc{r.returncode}")
        raise SessionError(f"rc={r.returncode}: {(r.stderr or out)[:200]}")   # CLI 는 사용량 초과 등을 stdout 으로 말한다
    _record(True)
    return out




def dlq(skill: str, name: str, rows: list[dict]) -> None:
    """검증에 실패한 줄을 격리한다. **버리지 않는다 — 나중에 왜 깨졌는지 봐야 한다.**"""
    p = paths.data(skill, "dlq") / f"{name}.jsonl"
    with p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **r},
                               ensure_ascii=False) + "\n")
