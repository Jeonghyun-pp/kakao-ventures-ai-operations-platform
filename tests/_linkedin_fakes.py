"""링크드인 테스트용 가짜 손. 노션·슬랙·Apify·claude 에 닿지 않고, 장부는 임시 파일에 쓴다."""
from __future__ import annotations

import contextlib
import io
import tempfile
from datetime import date
from pathlib import Path
from unittest import mock

import sqlite3

from kvlib import Fetched, ledger
from kvlib.gate import Gate
from kv_transport import notion as _notion
from skills.linkedin import judgments, run

THU = date(2026, 9, 24)      # 목요일
SAT = date(2026, 9, 26)


class FakeDate(date):
    fixed = THU

    @classmethod
    def today(cls):
        return cls.fixed


def row(pid, url="https://www.linkedin.com/in/aaa", tier="Catch", **kw):
    base = {"_page_id": pid, "LinkedIn URL": url, "Tier": tier, "서비스명": f"이름{pid}", "One Liner": "Aco · CEO",
            "Author Headline": "CEO at Aco", "회사": "Aco", "창업 개월": 3, "Raw Text": "r", "AI Reason": "why",
            "Homepage": f"https://{pid}.co", "Axis": ["posted-early"], "Match Reason": "", "타깃": False,
            "DB로 업데이트": False, "Tenure Months": 3, "공백 개월": 0, "본업 공백": 0}
    base.update(kw)
    return base


class Slack:
    def __init__(self, replies=None):
        self.posts, self.replies_by_parent, self.n = [], replies or {}, 0

    def post(self, ch, text, *, thread_ts=None):
        self.n += 1
        self.posts.append((ch, text, thread_ts))
        return Fetched.measured(f"ts-{self.n}")

    def replies(self, ch, parent):
        if parent in self.replies_by_parent:
            return Fetched.measured(self.replies_by_parent[parent])
        return Fetched.unknown("slack:망")


class Notion:
    """query 는 db_id 별 행 목록을 돌려준다. writable_id(key) == key."""

    def __init__(self, tables):
        self.tables, self.created, self.updated = tables, [], []

    clip16 = staticmethod(_notion.clip16)                          # 순수 함수 — 진짜를 쓴다

    def writable_id(self, key):
        return key

    def query(self, db_id, props, **kw):
        if db_id not in self.tables:
            return Fetched.unknown(f"notion:{db_id} 없음")
        return Fetched.measured(self.tables[db_id])

    def allowed_options(self, db_id, prop):
        return ["posted-early", "Reviewing", "New"]

    def create(self, db_key, props):
        self.created.append((db_key, props))
        return Fetched.measured(f"pg-{len(self.created)}")

    def update(self, page_id, db_key, props):
        self.updated.append((page_id, props))
        return Fetched.measured(True)


class Case:
    """with Case(...) as c: 안에서 run.* 이 전부 가짜를 본다."""

    def __init__(self, *, notion=None, slack=None, today=THU, cfg=None, **more):
        self.notion, self.slack, self.today, self.more, self.cfg = notion or Notion({}), slack or Slack(), today, more, cfg or {}
        self.out = io.StringIO()

    def __enter__(self):
        self._stack = contextlib.ExitStack().__enter__()
        d = Path(self._stack.enter_context(tempfile.TemporaryDirectory()))
        FakeDate.fixed = self.today
        self._stack.enter_context(mock.patch.object(ledger.paths, "ledger", lambda task: d / f"{task}.jsonl"))
        self._stack.enter_context(mock.patch.object(run, "notion", self.notion))
        self._stack.enter_context(mock.patch.object(run, "slack", self.slack))
        self._stack.enter_context(mock.patch.object(run, "date", FakeDate))
        real = run.constraints.get                                # cfg={"linkedin.promote": False} 처럼 설정값을 바꿔 끼운다
        self._stack.enter_context(mock.patch.object(run.constraints, "get", lambda k: self.cfg[k] if k in self.cfg else real(k)))
        self._stack.enter_context(mock.patch.object(run, "_reviewers", lambda: {"U1": "Reviewer A"}))
        self.db = sqlite3.connect(":memory:")
        self.db.executescript(judgments.SCHEMA)
        self._stack.enter_context(mock.patch.object(run, "_gate", lambda dry_run=False: Gate(
            self.db, "linkedin", lambda k: k in run.SLACK_KINDS, force="off" if dry_run else None)))
        for k, v in self.more.items():                            # "brief.write" → run.brief.write · "PROMOTE_ENABLED" → run.PROMOTE_ENABLED
            owner, _, attr = k.rpartition(".")
            self._stack.enter_context(mock.patch.object(getattr(run, owner) if owner else run, attr, v))
        self._stack.enter_context(contextlib.redirect_stdout(self.out))
        return self

    def __exit__(self, *a):
        self.ledger = ledger.rows("linkedin")                      # 🔴 나가기 전에 읽어 둔다 — 밖에서 읽으면 실 장부다
        return self._stack.__exit__(*a)

    def acts(self):
        return {(k, kind): st for k, kind, st in self.db.execute("SELECT key, kind, status FROM actions")}

    def rows(self, ev=None):
        rows = self.ledger if hasattr(self, "ledger") else ledger.rows("linkedin")
        return [r for r in rows if ev is None or r["ev"] == ev]
