"""한글(.hwp) 통로 — `skills/fund/engine/` 의 스크립트(rhwp WASM · 바이트 패치 · 검사기)를 서브프로세스로 부른다. 망은 없다.

A1: 리포에서 node·엔진 파이썬을 띄우는 자리는 여기뿐이다. 스킬은 이 함수들만 부른다.
성공은 rc 가 아니라 **스크립트가 찍은 JSON 의 status** 로 본다 — create.js 는 op 하나가 실패해도 rc 0 을 줄 수 있다(스킬 문서 실측).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from kvlib import Fetched, constraints, paths

ENGINE = paths.repo_root() / "skills" / "fund" / "engine"      # 2026-09-26 ~/.claude/skills/hwp/scripts 에서 들여옴(vendor 포함). 개인 스킬 폴더는 서버에 없다
HWP = ENGINE / "hwp"


def _run(cmd: list[str], *, stdin: str | None = None, env: dict | None = None) -> tuple[int, str, str]:
    r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, cwd=str(HWP),
                       env={**os.environ, **(env or {})}, timeout=constraints.get("hwp.timeout_sec"))
    return r.returncode, r.stdout, r.stderr


def _json(cmd: list[str], *, stdin: str | None = None, env: dict | None = None) -> Fetched:
    try:
        rc, out, err = _run(cmd, stdin=stdin, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Fetched.unknown(f"hwp:{cmd[1] if len(cmd) > 1 else cmd[0]}:{e}")
    d = None
    for cand in (out[out.find("{"):] if "{" in out else "", next((l for l in reversed(out.strip().splitlines()) if l.startswith("{")), "")):
        try:
            d = json.loads(cand); break                                   # 한 줄 JSON(create·patch) 도, 여러 줄 pretty JSON(format-learn) 도
        except json.JSONDecodeError:
            continue
    if d is None:
        return Fetched.unknown(f"hwp:{Path(cmd[1]).name}: JSON 없음 rc={rc} {err[-300:] or out[-300:]}")
    if d.get("status") == "error":
        return Fetched.unknown(f"hwp:{Path(cmd[1]).name}: {d.get('message', '')[:300]}")
    return Fetched.measured(d, rc=rc)


# ---------------------------------------------------------------- 만들기 · 읽기

def create(path: Path, ops: list[dict], *, full: bool = True) -> Fetched:
    """create.js 에 op 목록 한 번. `full` = rhwp 전체 직렬화(FORCE_RHWP_EMIT) — 서식 복사본 위에 본문을 붙일 때 KIF·LP 둘 다 이 경로였다.
    부분 패치(raw-patch, set_table_font 같은 것)는 full=False."""
    payload = json.dumps({"path": str(path), "operations": ops}, ensure_ascii=False)
    return _json(["node", str(HWP / "create.js")], stdin=payload, env={"FORCE_RHWP_EMIT": "1"} if full else {})


def inspect(path: Path) -> Fetched:
    """`{paragraphCount, tableCount, tables:[{sec,para,ctrl,rowCount,colCount,cells:[{row,col,rowSpan,colSpan,text}]}], …}` — 표 좌표는 op 의 section/para/control 그대로."""
    return _json(["node", str(HWP / "extract_text.js"), "--inspect", "--with-cell-text", str(path)])


def text(path: Path) -> Fetched:
    """본문 글자 — 문단 하나가 한 줄. 표 셀은 inspect 로 따로 본다."""
    try:
        rc, out, err = _run(["node", str(HWP / "extract_text.js"), str(path)])
    except (OSError, subprocess.TimeoutExpired) as e:
        return Fetched.unknown(f"hwp:text:{e}")
    return Fetched.measured(out, rc=rc) if rc == 0 else Fetched.unknown(f"hwp:text rc={rc} {err[-200:]}")


# ---------------------------------------------------------------- 서식 배우기 · 바이트 손질 (engine/patch.mjs)

def learn_format(path: Path) -> Fetched:
    """format-learn.js — 쪽·본문 글꼴·헤딩 계층·표 머리행·셀 불렛·글머리표를 실측한 프리셋 JSON(audit_format 이 그대로 읽는다)."""
    return _json(["node", str(HWP / "format-learn.js"), str(path), "--json"])


def learn_borders(path: Path) -> Fetched:
    """서식 파일의 표에서 셀 테두리 역할표(머리행·둘째행·본문·마지막행 × 좌중우 → BorderFill id)."""
    return _json(["node", str(ENGINE / "patch.mjs"), "learn", str(path)])


def truncate(path: Path) -> Fetched:
    """서식 복사본의 본문을 비운다(DocInfo 는 그대로) — 서식만 물려받은 빈 문서."""
    return _json(["node", str(ENGINE / "patch.mjs"), "truncate", str(path)])


def patch(path: Path, min_para: int, profile: Path) -> Fetched:
    """새 문단·표(min_para 뒤)의 글꼴 슬롯·셀 테두리·불렛 줄캐시·상속 indent 를 고친다. CFB 는 한 번만 쓴다."""
    return _json(["node", str(ENGINE / "patch.mjs"), "patch", str(path), str(min_para), str(profile)])


# ---------------------------------------------------------------- 검사

def audit_format(path: Path, preset: Path | None = None) -> Fetched:
    """서식 프리셋 준수(쪽 여백·줄간격·자간·글머리표·표 머리행·셀 여백·본문 크기). preset 없으면 스크립트 기본값(KIF 제출본 실측)."""
    cmd = ["node", str(HWP / "audit_format.js"), str(path), "--json"] + (["--preset", str(preset)] if preset else [])
    return _json(cmd)


def audit_structure(path: Path) -> Fetched:
    """HWP 레코드·CFB 정합성 — 한컴이 거부하는 종류의 모순. 서식이 아니라 '깨졌는가'만. rc 0 = 이상 없음."""
    try:
        rc, out, err = _run([sys.executable, str(HWP / "audit_structure.py"), str(path)])
    except (OSError, subprocess.TimeoutExpired) as e:
        return Fetched.unknown(f"hwp:audit_structure:{e}")
    report = (out + err).strip()
    return Fetched.measured({"ok": rc == 0 and "❌" not in report, "report": report[-3000:]}, rc=rc)   # 스크립트는 ❌ 를 찍고도 rc 0 을 줄 때가 있다(꼬리 검사 실측)


# ---------------------------------------------------------------- 렌더 (rhwp 미리보기 → PNG)

def render(path: Path, out_dir: Path, *, max_pages: int = 60, width: int = 1240) -> Fetched:
    """쪽마다 PNG 하나. 엔진의 미리보기 서버(node, 임시 포트) + 헤드리스 크로미움(playwright)으로 rhwp 가 그린 캔버스를 찍는다.
    🔴 이건 rhwp 의 그림이지 한컴의 그림이 아니다 — 불렛·들여쓰기·테두리·글꼴 크기를 눈으로 보는 용도. 한컴이 여는가는 보장하지 않는다(스킬 문서 실측)."""
    import socket
    from playwright.sync_api import sync_playwright                          # 통로 안에서만 import — 서버에 브라우저가 없으면 여기서만 죽는다
    out_dir.mkdir(parents=True, exist_ok=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]
    srv = subprocess.Popen(["node", str(HWP / "preview-server.js")], cwd=str(HWP), env={**os.environ, "CLAW_HWP_PREVIEW_PORT": str(port)},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pngs: list[Path] = []
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True)
            pg = b.new_page(viewport={"width": width, "height": 1600}, device_scale_factor=1)
            pg.goto(f"http://127.0.0.1:{port}/?path={Path(path).resolve()}", wait_until="load", timeout=constraints.get("hwp.timeout_sec") * 1000)
            pg.wait_for_function("document.querySelectorAll('.hwp-page canvas').length > 0", timeout=constraints.get("hwp.timeout_sec") * 1000)
            pg.wait_for_timeout(1500)                                             # 마지막 쪽까지 그릴 시간 — 캔버스는 렌더 루프가 순서대로 채운다
            canvases = pg.query_selector_all(".hwp-page canvas")
            for i, c in enumerate(canvases[:max_pages]):
                c.scroll_into_view_if_needed(); pg.wait_for_timeout(150)
                f = out_dir / f"p{i + 1:02d}.png"
                c.screenshot(path=str(f)); pngs.append(f)
            b.close()
    except Exception as e:                                                        # playwright 의 예외 종류가 많다 — 어느 것이든 '못 찍었다'로 재는 게 맞다
        return Fetched.unknown(f"hwp:render:{type(e).__name__}:{str(e)[:200]}")
    finally:
        srv.terminate()
    return Fetched.measured(pngs, pages=len(pngs))
