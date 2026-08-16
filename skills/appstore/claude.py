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
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from kvlib import Fetched, constraints, paths


class SessionError(RuntimeError):
    pass


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


def run_session(prompt: str, *, system: str | None = None) -> str:
    """claude -p 한 번. stdout 원문을 **파싱하지 않고** 그대로 돌려준다.

    검증은 호출부 몫이다 — 관심사를 섞으면 파싱 실패가 호출 실패로 오인된다.
    """
    n = _calls_today()
    if n >= constraints.get("claude.daily_calls_block"):
        raise SessionError(f"오늘 claude 호출 {n}회 — 차단선({constraints.get('claude.daily_calls_block')}) 도달")
    if n >= constraints.get("claude.daily_calls_warn"):
        print(f"  ⚠️  오늘 claude 호출 {n}회 (경고선 {constraints.get('claude.daily_calls_warn')})")

    cmd = [
        "claude", "-p",
        "--strict-mcp-config",                      # 전역 ~/.claude 의 MCP 상속 차단
        "--disallowedTools", ",".join(constraints.get("claude.forbidden_tools")),
    ]
    if system:
        cmd += ["--append-system-prompt", system]

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
        raise SessionError(f"rc={r.returncode}: {(r.stderr or '')[:200]}")
    _record(True)
    return out


# ---------------------------------------------------------------- 선정

_SYSTEM = (
    "너는 벤처캐피털 소싱 담당자를 돕는다. 주어진 앱 목록에서 사람이 직접 볼 값어치가 있는 것만 고른다. "
    "설명이나 인사말을 쓰지 말고 요구된 형식만 출력한다."
)


def pick_apps(rows: list[dict], *, n: int = 2) -> Fetched:
    """오늘 후보 중 n 개를 고른다. 반환 [{key, reason}].

    출력을 **한 줄씩 되읽어 검증**한다. 어긋난 줄만 DLQ 로 가고 나머지는 산다.
    """
    criteria = (Path(__file__).parent / "criteria.md").read_text(encoding="utf-8")
    lines = [f"{r['key']}\t{r.get('name', '')}\t{r.get('released', '')}" for r in rows]
    prompt = (
        f"{criteria}\n\n"
        f"# 오늘 후보 {len(rows)}개 (app_id · 이름 · 출시일)\n"
        + "\n".join(lines)
        + f"\n\n# 출력 형식\n정확히 {n}줄. 각 줄은 `app_id<TAB>고른 이유(한 문장)`.\n"
          "머리말·꼬리말·코드펜스 금지. 위 목록에 있는 app_id 만 쓴다.\n"
    )
    try:
        raw = run_session(prompt, system=_SYSTEM)
    except SessionError as e:
        return Fetched.unknown(f"claude:{e}")

    valid_keys = {r["key"] for r in rows}
    good, bad = [], []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            continue
        parts = line.split("\t") if "\t" in line else line.split(None, 1)
        if len(parts) < 2:
            bad.append({"line": line, "why": "필드 부족"})
            continue
        key, reason = parts[0].strip(), parts[1].strip()
        if key not in valid_keys:
            bad.append({"line": line, "why": "목록에 없는 app_id"})
            continue
        good.append({"key": key, "reason": reason})

    if bad:
        _dlq(bad)
        print(f"  ⚠️  검증 실패 {len(bad)}줄 → DLQ (나머지 {len(good)}줄은 살린다)")
    if not good:
        return Fetched.unknown(f"검증 통과 0줄 (원문 {len(raw)}자)")
    return Fetched.measured(good[:n], requested=n, got=len(good), dlq=len(bad))


def _dlq(rows: list[dict]) -> None:
    p = paths.data("appstore", "dlq") / "pick_dlq.jsonl"
    with p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **r}, ensure_ascii=False) + "\n")
