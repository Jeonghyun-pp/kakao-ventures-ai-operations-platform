"""투심 레인 테스트용 가짜 손 — 슬랙·노션·claude 에 닿지 않고, 요청 폴더·보고서는 임시 폴더에."""
from __future__ import annotations

import contextlib
import io
import re
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

import docx

from kvlib import Fetched, judgments, ledger
from kvlib.gate import Gate
from skills.memo import run


def make_docx(path: Path, paras: list[str]) -> Path:
    d = docx.Document()
    for t in paras:
        d.add_paragraph(t)
    d.save(str(path)); return path


class Slack:
    def __init__(self, messages=()):
        self.messages, self.replies_by, self.files_by, self.posts, self.uploads, self.n = list(messages), {}, {}, [], [], 0

    def history(self, key, **kw):
        return Fetched.measured(self.messages)

    def thread_files(self, key, parent):
        return Fetched.measured(self.files_by.get(parent, []))

    def download(self, url, dest):
        dest.write_bytes(b"%PDF-1.4 fake " + url.encode()); return Fetched.measured(dest)

    def replies(self, key, parent):
        return Fetched.measured(self.replies_by.get(parent, []))

    def post(self, key, text, *, thread_ts=None, **kw):
        self.n += 1; ts = f"{1000 + self.n}.0"; self.posts.append((key, text, thread_ts, ts)); return Fetched.measured(ts)

    def upload(self, key, path, *, thread_ts=None, title=None, comment=""):
        self.uploads.append((key, Path(path).name, thread_ts)); return Fetched.measured(f"F{len(self.uploads)}")


class Notion:
    def __init__(self, pages=None, text="# Corpus02\n▸ 최초미팅 2026-04-15\n  - 대표: 김광민"):
        self.pages, self.text = pages if pages is not None else [{"id": "pg-unx", "title": "Corpus02", "parent": "db-startup"}], text

    def find_page(self, q, **kw):
        return Fetched.measured(self.pages)

    def page_text(self, pid, **kw):
        return Fetched.measured(self.text)


class Claude:
    """run_skill 가짜 — 프롬프트에서 폴더를 읽어 그 안에 docx·md 를 만든다(실제 스킬이 하는 일의 결과만)."""
    def __init__(self, paras=None, fail=False):
        self.calls, self.paras, self.fail = [], paras or ["헤드라인 하나입니다 문단", "불릿 문장 하나 (2026.04 미팅)", "불릿 문장 둘 (미확인 — 텀시트 필요)"], fail

    def run_skill(self, prompt, **kw):
        self.calls.append((prompt, kw))
        if self.fail:
            return Fetched.unknown("rc=1: 망")
        folder = Path(re.search(r'scan_inputs\("([^"]+)"\)', prompt).group(1))
        n = len(self.calls)
        make_docx(folder / f"투심보고서_Corpus02_2026-09-26.docx", self.paras + ([f"v{n} 추가 문단입니다 피드백 반영"] if n > 1 else []))
        (folder / "투심보고서_Corpus02_2026-09-26.md").write_text("\n".join("- " + p for p in self.paras), encoding="utf-8")
        return Fetched.measured("done")


class Case:
    def __init__(self, *, messages=(), cfg=None, people=({"names": ["심사역"], "slack_id": "U1"},), claude=None):
        self.slack, self.notion, self.claude = Slack(messages), Notion(), claude or Claude()
        self.cfg, self.people, self.out = {"memo.collect_wait_min": 0, **(cfg or {})}, list(people), io.StringIO()

    def __enter__(self):
        self._stack = contextlib.ExitStack().__enter__()
        d = Path(self._stack.enter_context(tempfile.TemporaryDirectory())); self.dir = d
        for name, fake in (("slack", self.slack), ("notion", self.notion), ("claude", self.claude)):
            self._stack.enter_context(mock.patch.object(run, name, fake))
        real = run.constraints.get
        self._stack.enter_context(mock.patch.object(run.constraints, "get", lambda k: self.cfg[k] if k in self.cfg else real(k)))
        self.db = sqlite3.connect(":memory:", isolation_level=None); self.db.executescript(judgments.SCHEMA)
        self.gate = Gate(self.db, "memo", lambda k: k.rstrip("0123456789") in run.SLACK_KINDS, force="off" if self.cfg.get("_dry") else None)
        self._stack.enter_context(mock.patch.object(run, "_gate", lambda dry_run=False: self.gate))
        self._stack.enter_context(mock.patch.object(run, "_people", lambda: self.people))
        def req_dir(key):
            p = d / "requests" / key.replace(".", "_"); p.mkdir(parents=True, exist_ok=True); return p
        self._stack.enter_context(mock.patch.object(run, "_req_dir", req_dir))
        self._stack.enter_context(mock.patch.object(run, "_reports_dir", lambda: d))
        self._stack.enter_context(mock.patch.object(run, "db_path", lambda: d / "db" / "t.sqlite"))
        self._stack.enter_context(mock.patch.object(ledger.paths, "ledger", lambda task: d / f"{task}.jsonl"))
        self._stack.enter_context(contextlib.redirect_stdout(self.out))
        return self

    def __exit__(self, *a):
        self.reports = sorted(p.name for p in self.dir.glob("*.md"))
        return self._stack.__exit__(*a)

    def acts(self):
        return {(k, kind): st for k, kind, st in self.db.execute("SELECT key, kind, status FROM actions")}
