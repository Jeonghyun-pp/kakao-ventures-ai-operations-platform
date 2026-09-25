"""비플랜 테스트용 가짜 손 — 시트·노션·지메일에 닿지 않고, 장부·보고서는 임시 폴더에."""
from __future__ import annotations

import contextlib
import io
import tempfile
from pathlib import Path
from unittest import mock

from kvlib import Fetched, ledger
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
        self.intake, self.labels_, self.relabeled = list(intake), labels if labels is not None else {"3. Evan": "L1"}, []

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


class Notion:
    NotionError = run.notion.NotionError

    def __init__(self, rows):
        self.rows, self.created = rows, []

    def register_writable(self, key, db):
        pass

    def writable_id(self, key):
        return key

    def allowed_options(self, db, prop):
        return ["에반 Evan", "Newly Added"]

    def query(self, db, props, **kw):
        return Fetched.measured(self.rows)

    def create(self, key, props):
        self.created.append(props)
        return Fetched.measured(f"pg-{len(self.created)}")


class Case:
    def __init__(self, *, sheet=(), notion=(), intake=(), labels=None):
        self.sheets, self.notion, self.gmail = Sheets(list(sheet)), Notion(list(notion)), Gmail(intake, labels)
        self.out = io.StringIO()

    def __enter__(self):
        self._stack = contextlib.ExitStack().__enter__()
        d = Path(self._stack.enter_context(tempfile.TemporaryDirectory()))
        self.dir = d
        self._stack.enter_context(mock.patch.object(ledger.paths, "ledger", lambda task: d / f"{task}.jsonl"))
        for name, fake in (("sheets", self.sheets), ("notion", self.notion), ("gmail", self.gmail)):
            self._stack.enter_context(mock.patch.object(run, name, fake))
        self._stack.enter_context(mock.patch.object(run.secrets, "get", lambda k, **kw: f"<{k}>"))
        self._stack.enter_context(mock.patch.object(run, "_reports_dir", lambda: d))
        self._stack.enter_context(contextlib.redirect_stdout(self.out))
        return self

    def __exit__(self, *a):
        self.ledger = ledger.rows("bplan")
        self.reports = sorted(p.name for p in self.dir.glob("*.md"))
        return self._stack.__exit__(*a)

    def rows(self, ev=None):
        return [r for r in self.ledger if ev is None or r["ev"] == ev]
