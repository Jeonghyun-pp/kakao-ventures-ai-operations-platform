"""펀드제안서 레인 테스트용 가짜 손 — 슬랙·노션·한글 통로에 닿지 않는다. 한글 가짜는 op 목록을 기억해 text/inspect 를 그 목록에서 만든다."""
from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import mock

from kvlib import Fetched, judgments
from kvlib.gate import Gate
from skills.fund import build, run
from tests._memo_fakes import Slack

ROWS = [{"_page_id": "p0", "_edited": "2026-09-10T00:00:00.000Z", "항목번호": "00", "항목명": "작성 지침", "대섹션": "PT·기타", "상태": "1차완료"},
        {"_page_id": "p1", "_edited": "2026-09-10T00:00:00.000Z", "항목번호": "01", "항목명": "1-1. 개요", "대섹션": "1. 조합 운용계획", "상태": "1차완료"},
        {"_page_id": "p2", "_edited": "2026-09-11T05:00:00.000Z", "항목번호": "02", "항목명": "1-2. 운용 전략 / 가. 결성 배경", "대섹션": "1. 조합 운용계획", "상태": "1차완료"},
        {"_page_id": "p9", "_edited": "2026-09-10T00:00:00.000Z", "항목번호": "09", "항목명": "3-1. 운용사 일반현황", "대섹션": "3. 운용사", "상태": "컨펌필요"}]
BLOCKS = {
    "p1": [{"type": "heading_1", "level": 0, "text": "가. 조합 결성 개요"},
           {"type": "table", "level": 0, "text": "", "has_header": True, "cols": 2},
           {"type": "table_row", "level": 0, "text": "", "cells": ["항목", "내용"]},
           {"type": "table_row", "level": 0, "text": "", "cells": ["조합명", "2026 KIF-카카오 코파일럿 제3호 펀드"]},
           {"type": "table_row", "level": 0, "text": "", "cells": ["존속기간 / 투자기간", "10년 / 4년"]},
           {"type": "bulleted_list_item", "level": 0, "text": "당사는 **업무집행조합원**으로서 약정총액의 12.5%를 출자함."},
           {"type": "bulleted_list_item", "level": 1, "text": "하위 불렛 하나."}],
    "p2": [{"type": "toggle", "level": 0, "text": "🎯 확정 목차"}, {"type": "paragraph", "level": 1, "text": "토글 안 작업용 글"},
           {"type": "heading_1", "level": 0, "text": "가. 결성 배경"},
           {"type": "heading_2", "level": 0, "text": "1) 산업 현장의 AI 전환기"},
           {"type": "paragraph", "level": 0, "text": "당사는 기술의 도입에 있다고 판단함."},
           {"type": "heading_4", "level": 0, "text": "(1) 깊은 제목"},
           {"type": "paragraph", "level": 0, "text": "(그림 삽입 예정)"},
           {"type": "callout", "level": 0, "text": "✅ 앤 확인 메모"},
           {"type": "paragraph", "level": 0, "text": "[표 제목 하나]"},
           {"type": "table", "level": 0, "text": "", "has_header": True, "cols": 2},
           {"type": "table_row", "level": 0, "text": "", "cells": ["구분", "값"]},
           {"type": "table_row", "level": 0, "text": "", "cells": ["A", "1"]},
           {"type": "paragraph", "level": 0, "text": "* 주석 한 줄"}],
    "p9": [{"type": "heading_1", "level": 0, "text": "나. 조직구성"},
           {"type": "bulleted_list_item", "level": 0, "text": "조직도: 📁 HWP 삽입 파일: images_생성/09_조직도.png"},
           {"type": "bulleted_list_item", "level": 0, "text": "📁 HWP 삽입 파일: images_생성/없는그림.png"}],
}


class Notion:
    def __init__(self, rows=None, blocks=None):
        self.rows, self.blocks, self.queries = [dict(r) for r in (rows or ROWS)], blocks or BLOCKS, 0

    def query(self, db, props, **kw):
        self.queries += 1; return Fetched.measured(self.rows)

    def page_blocks(self, pid, **kw):
        return Fetched.measured(self.blocks.get(pid, []))

    def touch(self, page_id, when):
        for r in self.rows:
            if r["_page_id"] == page_id:
                r["_edited"] = when


class Hwp:
    """create 로 받은 op 를 파일 옆 JSON 에 쌓고, text/inspect 는 그 op 에서 만든다. 검사기는 전부 통과."""
    def __init__(self, fail_step=None):
        self.fail_step, self.calls = fail_step, []

    def _ops(self, path):
        side = Path(str(path) + ".ops.json")
        return json.loads(side.read_text()) if side.exists() else []

    def learn_format(self, path):
        return Fetched.measured({"body": {"fontKo": "휴먼명조", "lineSpacing": 160, "fontPtRaw": 1100}, "table": {"headerFillColor": "#EFF3FB", "cellFontPt": 10}})

    def learn_borders(self, path):
        return Fetched.measured({"tables_used": 2, "bf": {"hdr_mid": 73}})

    def truncate(self, path):
        self.calls.append("truncate"); Path(str(path) + ".ops.json").write_text("[]"); return Fetched.measured({"trimmed": 1})

    def create(self, path, ops, *, full=True):
        self.calls.append(("create", len(ops), full))
        if self.fail_step == "create" and full:
            return Fetched.unknown("create.js: op 3 failed")
        if full:
            Path(str(path) + ".ops.json").write_text(json.dumps(self._ops(path) + ops, ensure_ascii=False))
            Path(path).write_bytes(b"HWP fake " + str(len(ops)).encode())
        return Fetched.measured({"ops_applied": len(ops), "edits": [{"paragraphs_styled": 1} for _ in ops]})

    def inspect(self, path):
        ops = self._ops(path); tables = []
        for i, o in enumerate(ops):
            if o["type"] == "append_table":
                rows = ([o["headers"]] if "headers" in o else []) + o["rows"]
                tables.append({"sec": 0, "para": 1 + i, "ctrl": 0, "rowCount": len(rows), "colCount": len(rows[0]),
                               "cells": [{"row": r, "col": c, "rowSpan": 1, "colSpan": 1, "text": v} for r, row in enumerate(rows) for c, v in enumerate(row)]})
        return Fetched.measured({"paragraphCount": 1 + len(ops), "tableCount": len(tables), "tables": tables})

    def text(self, path):
        return Fetched.measured("\n".join(o.get("text", "") for o in self._ops(path) if o["type"] == "append_paragraph"))

    def patch(self, path, min_para, profile):
        n = sum(1 for o in self._ops(path) if o["type"] == "append_paragraph" and o.get("text", "").startswith(("○ ", "- ")))
        return Fetched.measured({"fonts": {"total": 1, "patched": 1}, "cells": {"tables": 0, "patched": 0}, "lineseg_trimmed": n, "indent_patched": 0})

    def audit_structure(self, path):
        return Fetched.measured({"ok": True, "report": "✅ 이상 없음"})

    def audit_format(self, path, preset=None):
        return Fetched.measured({"rows": [{"n": 1, "name": "쪽 여백", "got": "ok", "ok": True, "sev": None}]})

    def render(self, path, out_dir, **kw):
        out_dir.mkdir(parents=True, exist_ok=True); pngs = []
        for i in (1, 2, 3):
            f = out_dir / f"p0{i}.png"; f.write_bytes(b"PNG fake"); pngs.append(f)
        return Fetched.measured(pngs)


class Case:
    def __init__(self, *, messages=(), cfg=None, people=({"names": ["심사역"], "slack_id": "U1"},), hwp=None, notion=None):
        self.slack, self.notion, self.hwp = Slack(messages), notion or Notion(), hwp or Hwp()
        self.cfg, self.people, self.out = dict(cfg or {}), list(people), io.StringIO()

    def __enter__(self):
        self._stack = contextlib.ExitStack().__enter__()
        d = Path(self._stack.enter_context(tempfile.TemporaryDirectory())); self.dir = d
        for mod in (run, build):
            self._stack.enter_context(mock.patch.object(mod, "hwp", self.hwp))
        self._stack.enter_context(mock.patch.object(run, "slack", self.slack))
        self._stack.enter_context(mock.patch.object(run, "notion", self.notion))
        real = run.constraints.get
        self._stack.enter_context(mock.patch.object(run.constraints, "get", lambda k: self.cfg[k] if k in self.cfg else real(k)))
        self.db = sqlite3.connect(":memory:", isolation_level=None); self.db.executescript(judgments.SCHEMA)
        self.gate = Gate(self.db, "fund", lambda k: k.rstrip("0123456789") in run.SLACK_KINDS, force="off" if self.cfg.get("_dry") else None)
        self._stack.enter_context(mock.patch.object(run, "_gate", lambda dry_run=False: self.gate))
        self._stack.enter_context(mock.patch.object(run.people, "load", lambda: self.people))
        def req_dir(key):
            p = d / "requests" / key.replace(".", "_"); p.mkdir(parents=True, exist_ok=True); return p
        self._stack.enter_context(mock.patch.object(run, "_req_dir", req_dir))
        self._stack.enter_context(mock.patch.object(run, "_reports_dir", lambda: d))
        self._stack.enter_context(mock.patch.object(run, "db_path", lambda: d / "db" / "t.sqlite"))
        self._stack.enter_context(contextlib.redirect_stdout(self.out))
        return self

    def __exit__(self, *a):
        return self._stack.__exit__(*a)

    def acts(self):
        return {(k, kind): st for k, kind, st in self.db.execute("SELECT key, kind, status FROM actions")}
