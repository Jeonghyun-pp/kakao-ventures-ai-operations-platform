"""앱스토어 판정 — 프롬프트가 붙어 있으므로 도메인이다(통로가 아니다)."""
from __future__ import annotations

from pathlib import Path

from kvlib import Fetched
from kv_transport import claude

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
        raw = claude.run_session(prompt, system=_SYSTEM)
    except claude.SessionError as e:
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
        claude.dlq("appstore", "pick_dlq", bad)
        print(f"  ⚠️  검증 실패 {len(bad)}줄 → DLQ (나머지 {len(good)}줄은 살린다)")
    if not good:
        return Fetched.unknown(f"검증 통과 0줄 (원문 {len(raw)}자)")
    return Fetched.measured(good[:n], requested=n, got=len(good), dlq=len(bad))


