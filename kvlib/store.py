"""SQLite 저장소 — 스킬마다 하나. 연결 규율·온라인 백업·복원 시험은 같다, 스키마만 다르다.

    connect(path, schema)           WAL · 매 문장 커밋 · 600 (안에 메일 본문·프로필이 들어 있다)
    backup(con, dir, prefix, keep)  🔴 돌고 있는 파일을 복사하지 않는다 — 온라인 백업으로 일관된 사본을 뜨고 무결성을 확인한다
    restore_test(dir, prefix, tables)  가장 최근 사본을 **실제로 열어** 읽는다. 백업이 있다는 것과 되살릴 수 있다는 것은 다르다
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def connect(path: Path | str, schema: str) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), isolation_level=None)      # 자동 커밋 — 문장 하나가 한 단위다
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.executescript(schema)
    if str(path) != ":memory:":
        Path(path).chmod(0o600)
    return con


def backup(con: sqlite3.Connection, dest_dir: Path, prefix: str, keep: int) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{prefix}-{datetime.now(timezone.utc):%Y-%m-%d}.sqlite"
    tmp = dest.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(str(tmp))
    with out:
        con.backup(out)
    ok = out.execute("PRAGMA integrity_check").fetchone()[0]
    out.close()
    if ok != "ok":
        raise RuntimeError(f"백업 무결성 검사 실패: {ok}")
    tmp.replace(dest)
    dest.chmod(0o600)
    for old in sorted(dest_dir.glob(f"{prefix}-*.sqlite"))[:-keep]:
        old.unlink()
    return dest


def restore_test(dest_dir: Path, prefix: str, tables: dict[str, str]) -> str:
    """`tables` = {테이블: 표시 이름}. 예: {"messages": "메일", "judgments": "판정"}."""
    files = sorted(dest_dir.glob(f"{prefix}-*.sqlite"))
    if not files:
        raise RuntimeError("백업이 없다")
    con = sqlite3.connect(f"file:{files[-1]}?mode=ro", uri=True)
    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    counts = {label: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t, label in tables.items()}
    con.close()
    if ok != "ok":
        raise RuntimeError(f"복원 시험 실패: {ok}")
    return f"{files[-1].name} · 무결성 ok · " + " · ".join(f"{k} {v}" for k, v in counts.items())
