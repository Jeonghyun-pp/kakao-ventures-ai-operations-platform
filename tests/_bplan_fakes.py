"""비플랜 테스트용 가짜 손 — 시트·노션·지메일에 닿지 않고, 장부·보고서는 임시 폴더에."""
from __future__ import annotations

import contextlib
import io
import tempfile
from pathlib import Path
from unittest import mock

import sqlite3

from kvlib import Fetched, judgments, ledger
from kvlib.gate import Gate
from kv_transport import notion as _notion
from skills.bplan import run

HDR = ["타임스탬프", "이메일 주소", "대표님의 성함을 알려주세요.", "대표님의 연락처를 알려주세요.",
       "1. 팀의 Service/Product를", "2. 문제 정의 : 누구의", "3. 솔루션 : 그", "4. 핵심 비즈니스 모델(e.g.",
       "5. 핵심 경쟁사들은", "6. 대표님과 핵심 구성원(c-level)", "7. 우리 팀이 이 서비스 & 기술을",
       "(선택)추가로 공유해", "팀의 사업계획서를 첨부해주세요.", "기업명을 알려주세요."]


def sheet_row(ts, email, company, **kw):
    r = [ts, email, kw.get("ceo", "김대표"), kw.get("phone", ""), "서비스", "문제", "솔루션", "BM", "경쟁사", "팀", "경쟁력",
         kw.get("extra", ""), kw.get("deck", "https://drive.google.com/open?id=AAA"), company]
    return r


class Sheets:
    def __init__(self, rows):
        self.rows = rows

    def auth_check(self):
        return Fetched.measured(True)

    def values(self, url, ws):
        return Fetched.measured([HDR, *self.rows])


class Gmail:
    def __init__(self, intake=(), labels=None):
        self.intake, self.labels_, self.relabeled, self.drafts = list(intake), labels if labels is not None else {"3. Evan": "L1"}, [], []

    def auth_check(self):
        return Fetched.measured(True)

    def labels(self):
        return Fetched.measured(self.labels_)

    def search_ids(self, q, **kw):
        return Fetched.measured([mid for mid, _ in self.intake])

    def headers_of(self, mid, names):
        return Fetched.measured({"Subject": dict(self.intake)[mid]})

    def relabel(self, mid, *, add, remove):
        self.relabeled.append((mid, add, remove))
        return Fetched.measured(True)

    def message(self, mid):
        return Fetched.measured({"id": mid, "thread_id": f"t-{mid}", "subject": dict(self.intake).get(mid, ""), "sender": "deals@example.vc"})

    def draft_reply(self, msg, body, to, subject=None):
        self.drafts.append((msg["id"], to, body[:20]))
        return Fetched.measured(f"draft-{len(self.drafts)}")


class Slack:
    def __init__(self):
        self.posts, self.n, self.replies_by_parent = [], 0, {}

    def post(self, ch, text, *, thread_ts=None):
        self.n += 1; self.posts.append((ch, text, thread_ts)); return Fetched.measured(f"ts-{self.n}")

    def replies(self, ch, parent):
        if parent in self.replies_by_parent:
            return Fetched.measured(self.replies_by_parent[parent])
        return Fetched.unknown("slack:망") if self.replies_by_parent.get("_fail") else Fetched.measured([])


class Notion:
    NotionError = run.notion.NotionError
    clip16 = staticmethod(_notion.clip16)

    def __init__(self, rows):
        self.rows, self.created, self.updated = rows, [], []

    def update(self, page_id, key, props):
        self.updated.append((page_id, props)); return Fetched.measured(True)

    def register_writable(self, key, db):
        pass

    def writable_id(self, key):
        return key

    def allowed_options(self, db, prop):
        return ["칼드랍", "Red", "Orange", "Green", "?"] if prop == "점수" else ["에반 Evan", "Newly Added"]

    def query(self, db, props, **kw):
        return Fetched.measured(self.rows)

    def create(self, key, props):
        self.created.append(props)
        return Fetched.measured(f"pg-{len(self.created)}")


class Case:
    def __init__(self, *, sheet=(), notion=(), intake=(), labels=None, cfg=None):
        self.sheets, self.notion, self.gmail, self.slack = Sheets(list(sheet)), Notion(list(notion)), Gmail(intake, labels), Slack()
        self.cfg, self.out = cfg or {}, io.StringIO()

    def __enter__(self):
        self._stack = contextlib.ExitStack().__enter__()
        d = Path(self._stack.enter_context(tempfile.TemporaryDirectory()))
        self.dir = d
        self._stack.enter_context(mock.patch.object(ledger.paths, "ledger", lambda task: d / f"{task}.jsonl"))
        for name, fake in (("sheets", self.sheets), ("notion", self.notion), ("gmail", self.gmail), ("slack", self.slack)):
            self._stack.enter_context(mock.patch.object(run, name, fake))
        real = run.constraints.get
        self._stack.enter_context(mock.patch.object(run.constraints, "get", lambda k: self.cfg[k] if k in self.cfg else real(k)))
        self.db = sqlite3.connect(":memory:"); self.db.executescript(judgments.SCHEMA)
        self.gate = Gate(self.db, "bplan", lambda k: k in run.SLACK_KINDS, force="off" if self.cfg.get("_dry") else None)
        self._stack.enter_context(mock.patch.object(run, "_gate", lambda dry_run=False: self.gate))
        self._stack.enter_context(mock.patch.object(run.secrets, "get", lambda k, **kw: f"<{k}>"))
        self._stack.enter_context(mock.patch.object(run, "_reports_dir", lambda: d))
        self._stack.enter_context(contextlib.redirect_stdout(self.out))
        return self

    def __exit__(self, *a):
        self.ledger = ledger.rows("bplan")
        self.reports = sorted(p.name for p in self.dir.glob("*.md"))
        return self._stack.__exit__(*a)

    def acts(self):
        return {(k, kind): st for k, kind, st in self.db.execute("SELECT key, kind, status FROM actions")}

    def rows(self, ev=None):
        return [r for r in self.ledger if ev is None or r["ev"] == ev]
