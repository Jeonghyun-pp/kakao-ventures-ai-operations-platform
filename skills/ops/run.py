"""살림 — kv-data 스냅샷 백업 · 보존 규칙 집행 · 그 둘의 health. 옛 kv-automation 스케줄러(backup·retention)를 2026-09-26 에 이리로 옮겼다.

    backup     매일  ~/kv-data → ~/kv-data-backup/<날짜>/  변하지 않은 파일은 전날 스냅샷에 하드링크(rsync --link-dest 와 같은 효과). 7일 보관
    retention  주 1회  constraints ops.retention 의 glob 만(허용 목록) 보관일수 지나면 지운다. 🔴 오늘 백업이 성공했을 때만
    health     매일  여섯 스킬(오늘 보고서·스케줄 설치·투심 접수 지연) + 백업 나이 — 막힌 것만 review_test 한 줄. 스킬별 health 여섯을 이것 하나로
    digest     매일  하루 한 통(review_test) + 현황판(~/kv-data/_reports/dashboard.html). 스킬별 하루 요약 슬랙 다섯을 이것 하나로
    status     현황 한 화면 (status.py)

🔴 같은 디스크다. 맥·디스크 고장은 못 막는다 — 오프사이트 1벌은 서버 이관의 몫이다.
🔴 스킬별 sqlite(*/db/*.sqlite) 는 여기서 제외한다 — 열려 있는 파일을 복사하면 깨질 수 있고, 각 스킬이 store.backup 으로 db/backup/ 에 일관된 사본을 매일 뜬다. 그 사본은 여기 포함된다.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from kvlib import constraints, ledger, paths, report
from kv_transport import slack

TASK = "ops"


def _cfg(k: str):
    return constraints.get(f"ops.{k}")


def _send(ch: str, text: str) -> str:
    return slack.post(ch, text).require()


def _dst() -> Path:
    return Path(os.environ.get("KV_BACKUP_DIR") or _cfg("backup_dir")).expanduser()


def _snapshots(dst: Path) -> list[Path]:
    return sorted(p for p in dst.iterdir() if p.is_dir() and len(p.name) == 10 and p.name[4] == "-") if dst.exists() else []


def _excluded(rel: Path) -> bool:
    """열린 sqlite(<skill>/db/x.sqlite 와 -wal/-shm)와 잠금 파일. db/backup/ 의 사본은 포함한다."""
    return (rel.parent.name == "db" and ".sqlite" in rel.name) or "_locks" in rel.parts


def backup(src: Path | None = None, dst: Path | None = None, keep: int | None = None, today: str | None = None) -> Path:
    """스냅샷 하나. 🔴 원본이 비어 보이면 중단한다 — 빈 백업으로 멀쩡한 스냅샷을 밀어내지 않는다.
    🔴 파일 수가 원본보다 적으면 실패로 남긴다(.last-success 를 안 쓴다)."""
    src, dst, keep, today = src or paths.data_root(), dst or _dst(), keep or int(_cfg("backup_keep")), today or date.today().isoformat()
    files = [p for p in src.rglob("*") if p.is_file() and not _excluded(p.relative_to(src))]
    if len(files) < int(_cfg("backup_min_files")):
        raise RuntimeError(f"원본 파일이 {len(files)}개뿐이다(기대 수천) — 마운트 실패·경로 오타 의심. 중단")
    prev = next((s for s in reversed(_snapshots(dst)) if s.name != today), None)
    out, tmp = dst / today, dst / f".{today}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    linked = 0
    for p in files:
        rel = p.relative_to(src)
        d = tmp / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        q = prev / rel if prev else None
        st = p.stat()
        if q and q.exists() and q.stat().st_size == st.st_size and q.stat().st_mtime_ns == st.st_mtime_ns:
            os.link(q, d); linked += 1                                  # 안 변한 파일은 전날 것과 같은 inode — 디스크를 안 먹는다
        else:
            shutil.copy2(p, d)
    n = sum(1 for _ in tmp.rglob("*") if _.is_file())
    if n < len(files):
        raise RuntimeError(f"백업 파일 수가 원본보다 적다: {n} < {len(files)}")
    shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    for old in _snapshots(dst)[:-keep]:
        shutil.rmtree(old)
    (dst / ".last-success").write_text(f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} files={n} linked={linked} snapshots={len(_snapshots(dst))}\n", encoding="utf-8")
    print(f"· 백업 {out.name}: 파일 {n} (하드링크 {linked}) · 스냅샷 {len(_snapshots(dst))}개 보관")
    return out


def backup_age_days(dst: Path | None = None) -> float | None:
    f = (dst or _dst()) / ".last-success"
    if not f.exists():
        return None
    ts = f.read_text(encoding="utf-8").split()[0]
    return (datetime.now(timezone.utc) - datetime.fromisoformat(ts.replace("Z", "+00:00"))).total_seconds() / 86400


def retention(root: Path | None = None, dst: Path | None = None, *, dry: bool = False) -> dict:
    """규칙(ops.retention) 의 glob 만 본다 — 허용 목록. glob 이 데이터 루트 밖을 가리키면 즉시 중단. 오늘 백업이 없으면 아무것도 안 지운다."""
    root = root or paths.data_root()
    age = backup_age_days(dst)
    if age is None or age > 1:
        print(f"🔴 오늘 성공한 백업이 없다(마지막 {age and round(age, 1)}일 전) — 지우지 않는다. 백업 없이 지우는 경로는 만들지 않는다")
        return {"skipped": "no_backup"}
    out = {}
    for r in _cfg("retention") or []:
        files = sorted((p for p in root.glob(r["glob"]) if p.is_file()), key=lambda p: p.stat().st_mtime)
        if any(root.resolve() not in p.resolve().parents for p in files):
            raise RuntimeError(f"{r['name']}: glob 이 데이터 루트 밖을 가리킨다 — 중단")
        cutoff = time.time() - int(r["keep_days"]) * 86400
        victims = [p for p in files[:max(0, len(files) - int(r.get("min_keep", 0)))] if p.stat().st_mtime < cutoff]
        for p in victims:
            if not dry:
                p.unlink()
        out[r["name"]] = len(victims)
        print(f"· {r['name']}: {len(files)}본 · {'지울 것' if dry else '지움'} {len(victims)} (보관 {r['keep_days']}일)")
    return out


def health() -> int:
    """여섯 스킬(오늘 보고서·스케줄 설치·투심 접수 지연) + 백업 나이를 한 번에 본다. 막힌 것이 없으면 조용하다."""
    from . import status
    st = status.gather()
    if st["gaps"]:
        print("🔴 " + report.post(TASK, "🔴 *kv health* — " + " / ".join(st["gaps"]), _send))
        return 2
    print(f"✅ {len(st['skills'])}스킬 · 백업 {st['backup']['age_days']}일 전 · 막힌 것 없음")
    return 0


def digest(slack_too: bool = True) -> int:
    """하루 한 통 + 현황판 파일. 스킬별 요약 슬랙은 report.daily 가 여기로 넘긴다."""
    from . import status
    st = status.gather()
    out = status.write_dashboard(st)
    print(status.render_text(st))
    if slack_too:
        print(f"· 현황판 {out} | 슬랙 {report.post(TASK, status.digest_text(st), _send)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ops")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backup"); r = sub.add_parser("retention"); r.add_argument("--dry-run", action="store_true"); sub.add_parser("health")
    sub.add_parser("status", help="여섯 스킬 현황 한 화면"); sub.add_parser("digest", help="하루 한 통(review_test) + 현황판 html"); sub.add_parser("dashboard", help="현황판 html 만 다시 쓴다")
    a = p.parse_args(argv)
    try:
        if a.cmd == "backup":
            backup(); return 0
        if a.cmd == "retention":
            retention(dry=a.dry_run); return 0
        if a.cmd == "status":
            from . import status
            print(status.render_text(status.gather())); return 0
        if a.cmd in ("digest", "dashboard"):
            return digest(slack_too=a.cmd == "digest")
        return health()
    except RuntimeError as e:
        print(f"🔴 {e}")
        if a.cmd == "backup":
            report.post(TASK, f"🔴 *kv-data 백업 실패* — {e}", _send)
        return 2


if __name__ == "__main__":
    sys.exit(main())
