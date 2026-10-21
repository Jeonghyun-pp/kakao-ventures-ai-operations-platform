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

from kvlib import Fetched, constraints, ledger, paths


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
    row = {"ts": ledger.now(), "ok": ok}
    if err:
        row["err"] = err
    with _usage_path().open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _spawn(cmd: list[str], prompt: str, timeout: int, cwd=None):
    """claude 프로세스 한 번. prompt 는 argv 가 아니라 stdin — 데이터가 "-" 로 시작해도 플래그로 안 읽힌다."""
    try:
        return subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=str(cwd) if cwd else None)
    except FileNotFoundError:
        raise SessionError("claude CLI 가 없다")          # spawn 자체가 없었다 → 미계상
    except subprocess.TimeoutExpired:
        _record(False, "timeout")                        # 쿼터는 태웠다 → 계상
        raise SessionError("timeout")


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

    r = _spawn(cmd, prompt, constraints.get("claude.timeout_sec"))
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


def web_research(prompt: str, *, system: str, timeout_sec: int) -> str:
    """claude -p 한 번 — **웹만** 본다 (2026-10-15, 링크드인 카드 회사 개괄).

    run_session 은 WebSearch·WebFetch 를 상시 금지한다(`claude.forbidden_tools`). 이 호출만 그 둘을 연다 —
    도구는 둘뿐(`--tools`)이고 파일·셸은 목록에 없다. `dontAsk` 라 목록 밖은 묻지 않고 거부한다(권한 프롬프트에 멈추지 않게).
    웹 페이지 글은 바깥 사람이 쓴 것이다 — 그 안의 지시가 할 수 있는 일은 검색·읽기뿐이다."""
    n = _calls_today()
    if n >= constraints.get("claude.daily_calls_block"):
        raise SessionError(f"오늘 claude 호출 {n}회 — 차단선 도달")
    cmd = [_bin(), "-p", "--strict-mcp-config", "--tools", "WebSearch,WebFetch",
           "--permission-mode", "dontAsk", "--allowedTools", "WebSearch,WebFetch",
           "--append-system-prompt", system]
    r = _spawn(cmd, prompt, timeout_sec)
    out = (r.stdout or "").strip()
    if not out or r.returncode != 0:
        _record(False, "empty_output" if not out else f"rc{r.returncode}")
        raise SessionError(f"rc={r.returncode}: {(r.stderr or out)[:200]}")
    _record(True)
    return out


def notion_search(prompt: str, *, system: str, timeout_sec: int) -> str:
    """claude -p 한 번 — **노션 본문 검색 하나만** 연다 (2026-10-21, 링크드인 카드 "노션 기존 기록").

    노션 공식 API 는 본문을 못 찾는다(제목·칸 필터뿐) — 회의록 본문에만 있는 이름(Example Startup 전 대표)은
    claude.ai 노션 커넥터 검색으로만 찾힌다. 그래서 이 호출만 `--strict-mcp-config` 를 빼서 커넥터를 살린다.
    🔴 대신 허용은 `notion-search` 하나다 — `dontAsk` 라 목록 밖(노션 쓰기·지메일·파일·셸)은 묻지 않고 거부한다.
       금지 목록(forbidden_tools)도 그대로 건다. 커넥터는 이 맥 사용자 계정 것이라, 인증이 풀리면 도구가 안 보인다 —
       호출부가 그 출력을 실패로 다룬다."""
    n = _calls_today()
    if n >= constraints.get("claude.daily_calls_block"):
        raise SessionError(f"오늘 claude 호출 {n}회 — 차단선 도달")
    cmd = [_bin(), "-p", "--permission-mode", "dontAsk", "--allowedTools", NOTION_SEARCH_TOOL,
           "--disallowedTools", ",".join(constraints.get("claude.forbidden_tools")),
           "--append-system-prompt", system]
    r = _spawn(cmd, prompt, timeout_sec)
    out = (r.stdout or "").strip()
    if not out or r.returncode != 0:
        _record(False, "empty_output" if not out else f"rc{r.returncode}")
        raise SessionError(f"rc={r.returncode}: {(r.stderr or out)[:200]}")
    _record(True)
    return out


NOTION_SEARCH_TOOL = "mcp__claude_ai_Notion__notion-search"


def read_file(path: Path, prompt: str, *, system: str, timeout_sec: int) -> str:
    """claude -p 한 번 — **파일 하나만** 읽힌다 (2026-10-14, 비플랜 이미지 덱).

    바깥 사람이 만든 파일(덱)을 읽히므로 도구는 Read 하나, 권한은 그 파일 한 개(`Read(./<이름>)`)뿐이다.
    `--permission-mode dontAsk` 라 목록 밖은 묻지 않고 거부한다 — 덱 안의 지시가 다른 파일(~/kv-config)을 읽으라 해도 못 읽는다
    (실측: 같은 호출에서 /etc/hosts 는 거부됐다). 판정은 run_session 과 같이 rc 가 아니라 출력으로."""
    n = _calls_today()
    if n >= constraints.get("claude.daily_calls_block"):
        raise SessionError(f"오늘 claude 호출 {n}회 — 차단선 도달")
    cmd = [_bin(), "-p", "--strict-mcp-config", "--tools", "Read",
           "--permission-mode", "dontAsk", "--allowedTools", f"Read(./{path.name})",
           "--append-system-prompt", system]
    r = _spawn(cmd, prompt, timeout_sec, cwd=path.parent)
    out = (r.stdout or "").strip()
    if not out or r.returncode != 0:
        _record(False, "empty_output" if not out else f"rc{r.returncode}")
        raise SessionError(f"rc={r.returncode}: {(r.stderr or out)[:200]}")
    _record(True)
    return out


def run_skill(prompt: str, *, mcp_config, system_file, allowed_tools: list[str], timeout_sec: int, cwd=None) -> Fetched:
    """긴 세션 한 번 — 스킬(MCP 서버 + 도구)을 붙여 끝까지 돌린다 (2026-09-26, 투심 레인).

    `run_session` 과 다른 점: MCP 를 붙이고(`--mcp-config`, 전역 상속은 여전히 차단), 허용 도구를 **명시 목록**으로 주고
    (`--permission-mode dontAsk` — 목록 밖 도구는 묻지 않고 거부), 상한이 분 단위다.
    🔴 성공 판정은 호출부가 **산출물 존재**로 한다. 0단계 실측: docx 는 9분에 다 나왔는데 마지막 메시지의 API 호출이
       망 오류로 죽어 rc=1 이었다. 그래서 여기서는 unknown 을 돌려주되 stdout·rc 를 실어 준다 — 호출부가 파일을 본다."""
    n = _calls_today()
    if n >= constraints.get("claude.daily_calls_block"):
        return Fetched.unknown(f"오늘 claude 호출 {n}회 — 차단선 도달")
    cmd = [_bin(), "-p", "--mcp-config", str(mcp_config), "--strict-mcp-config",
           "--append-system-prompt-file", str(system_file),
           "--permission-mode", "dontAsk",
           "--allowedTools", ",".join(allowed_tools),
           "--disallowedTools", ",".join(constraints.get("claude.skill_forbidden_tools")),
           "--output-format", "text"]
    try:
        r = _spawn(cmd, prompt, timeout_sec, cwd)
    except SessionError as e:
        return Fetched.unknown(str(e))
    out = (r.stdout or "").strip()
    if r.returncode != 0 or not out:
        _record(False, f"rc{r.returncode}")
        return Fetched.unknown(f"rc={r.returncode}: {(r.stderr or out)[:200]}", stdout=out[-2000:], rc=r.returncode)
    _record(True)
    return Fetched.measured(out, rc=0)


def dlq(skill: str, name: str, rows: list[dict]) -> None:
    """검증에 실패한 줄을 격리한다. **버리지 않는다 — 나중에 왜 깨졌는지 봐야 한다.**"""
    p = paths.data(skill, "dlq") / f"{name}.jsonl"
    with p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"ts": ledger.now(), **r},
                               ensure_ascii=False) + "\n")
