"""앱스토어 테스트용 가짜 손 — 애플·노션·claude 에 닿지 않고, 장부는 임시 파일에."""
from __future__ import annotations

import contextlib
import io
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

from kvlib import Fetched, judgments, ledger
from kvlib.gate import Gate
from skills.appstore import kr_origin, run


def app(aid, name="앱", seller="주식회사 가나다", url="https://ganada.kr", released="2026-09-01", desc="한국어 설명입니다 " * 20, rank=1):
    return {"app_id": aid, "rank": rank, "name": name, "developer": seller,
            "detail": {"name": name, "developer": seller, "seller_name": seller, "seller_url": url, "released_at": released,
                       "description": desc, "subtitle": "", "url": f"https://apps.apple.com/kr/app/id{aid}", "artwork": None, "rating": 4.5, "review_count": 3}}


class Itunes:
    GENRE_NAMES, CHART_LABEL = run.itunes.GENRE_NAMES, run.itunes.CHART_LABEL

    def __init__(self, apps, fail_charts=()):
        self.apps, self.fail_charts, self.charts = apps, set(fail_charts), 0

    def reset_circuit(self):
        pass

    def chart(self, genre, chart_name):
        self.charts += 1
        if chart_name in self.fail_charts:
            return Fetched.unknown("itunes:429")
        return Fetched.measured([{k: v for k, v in a.items() if k != "detail"} for a in self.apps] if chart_name == "top-free" else [])

    def lookup(self, ids):
        return Fetched.measured({a["app_id"]: a["detail"] for a in self.apps if a["app_id"] in ids})


class Notion:
    def __init__(self, rows=()):
        self.rows, self.created = list(rows), []

    def writable_id(self, k):
        return k

    def allowed_options(self, db, prop):
        return list(run.itunes.GENRE_NAMES.values())

    def query(self, db, props, **kw):
        return Fetched.measured(self.rows)

    def create(self, k, props):
        self.created.append(props); return Fetched.measured(f"pg-{len(self.created)}")


class Case:
    def __init__(self, apps, *, notion_rows=(), fail_charts=(), llm=None, dry=False):
        self.itunes, self.notion = Itunes(apps, fail_charts), Notion(notion_rows)
        self.llm = llm or (lambda unknowns, criteria=None: Fetched.measured([]))
        self.out, self.posts, self.dry = io.StringIO(), [], dry

    def __enter__(self):
        self._stack = contextlib.ExitStack().__enter__()
        d = Path(self._stack.enter_context(tempfile.TemporaryDirectory()))
        self._stack.enter_context(mock.patch.object(ledger.paths, "ledger", lambda task: d / f"{task}.jsonl"))
        self._stack.enter_context(mock.patch.object(run, "itunes", self.itunes))
        self._stack.enter_context(mock.patch.object(run, "notion", self.notion))
        self._stack.enter_context(mock.patch.object(kr_origin, "llm_judge_origin", self.llm))
        self._stack.enter_context(mock.patch.object(run, "_GENRES", [6014]))                    # 장르 하나만 — 차트 4번
        self.db = sqlite3.connect(":memory:", isolation_level=None); self.db.executescript(judgments.SCHEMA)     # 자동 커밋 — store.connect 와 같게(열린 트랜잭션이 있으면 backup 이 멈춘다)
        self.gate = Gate(self.db, "appstore", force="off" if self.dry else None)
        self._stack.enter_context(mock.patch.object(run, "_gate", lambda dry_run=False: self.gate))
        self._stack.enter_context(mock.patch.object(run, "_reports_dir", lambda: d))
        self._stack.enter_context(mock.patch.object(run, "db_path", lambda: d / "db" / "t.sqlite"))       # 백업도 임시 폴더로 — 테스트가 프로덕션을 안 건드린다
        self._stack.enter_context(mock.patch.object(run, "_send", lambda ch, text: self.posts.append((ch, text)) or "ts"))
        self.dir = d
        self._stack.enter_context(contextlib.redirect_stdout(self.out))
        return self

    def __exit__(self, *a):
        self.ledger = ledger.rows("appstore")
        self.reports = sorted(p.name for p in self.dir.glob("*.md"))
        return self._stack.__exit__(*a)
