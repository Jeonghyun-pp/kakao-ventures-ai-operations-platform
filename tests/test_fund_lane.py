"""펀드제안서 레인 — 노션 → 문서 모델 → op → 검수, 그리고 요청 → 접수 → v1 → 노션 변경 → v2 → ✅. 모델 호출 0, 문이 off 면 슬랙엔 아무것도 안 나간다."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from kvlib import Fetched, judgments
from skills.fund import audit, build, convert, run
from tests._fund_fakes import BLOCKS, ROWS, Case, Hwp

REQ = {"ts": "1700.1", "user": "U1", "text": "LP 성장펀드 제안서 부탁해요 <https://app.notion.com/p/kv/000000000000000000009897af4fbc1b?v=8fb6|링크>",
       "files": [{"id": "F1", "name": "(별첨) 출자신청서 양식.hwp", "url_private": "u1"}, {"id": "F2", "name": "09_조직도.png", "url_private": "u2"}]}


class Convert(unittest.TestCase):
    def setUp(self):
        self.items, self.f = convert.convert(ROWS, BLOCKS, None)

    def test_outline_and_kinds(self):
        ks = [(i["k"], i.get("t")) for i in self.items if i["k"] in ("SEC", "ITEM", "H1", "H2", "H4", "PB")]
        self.assertEqual(ks[:3], [("SEC", "1. 조합 운용계획"), ("ITEM", "1-1. 개요"), ("H1", "가. 조합 결성 개요")])
        self.assertIn(("ITEM", "1-2. 운용 전략"), ks); self.assertIn(("H4", "(1) 깊은 제목"), ks)
        self.assertEqual(sum(1 for k, _ in ks if k == "PB"), 1)                                   # 대섹션 3 으로 넘어갈 때 한 번
        self.assertEqual([x for x in ks if x[0] == "H1" and x[1] == "가. 결성 배경"], [("H1", "가. 결성 배경")])  # 항목명 뒤 소제목은 페이지 것과 겹치지 않는다
        self.assertEqual(self.f["outside"], ["00 작성 지침"])

    def test_drops_are_reported_not_silent(self):
        texts = [convert.plain(i.get("t", "")) for i in self.items]
        self.assertNotIn("토글 안 작업용 글", texts); self.assertNotIn("(그림 삽입 예정)", texts); self.assertNotIn("✅ 앤 확인 메모", texts)
        self.assertTrue(any("토글" in s for s in self.f["skipped"]) and any("자리표시" in s for s in self.f["skipped"]) and any("메모" in s for s in self.f["skipped"]))
        self.assertEqual(len(self.f["images_missing"]), 2)                                      # 폴더 없음 → 둘 다 미확보, 그 줄은 옮기지 않는다
        self.assertIn("조직도", texts); self.assertNotIn("📁", "".join(texts))                 # 📁 앞 글자만 남고 지시 줄은 사라진다
        self.assertEqual(self.f["slash_cells"], ["01 존속기간 / 투자기간", "01 10년 / 4년"])
        self.assertEqual([i["k"] for i in self.items if i["k"] in ("TT", "NOTE", "TBL")], ["TBL", "TT", "TBL", "NOTE"])

    def test_bullet_levels_and_bold(self):
        b = [i for i in self.items if i["k"] == "BUL"]
        self.assertEqual([x["lv"] for x in b[:2]], [0, 1]); self.assertIn("**업무집행조합원**", b[0]["t"])
        p = {"font": "F", "sizes": {"TXT": 11, "TBL": 10}, "line_spacing": 160, "header_fill": "#eff3fb", "cell_padding": 141,
             "bullets": [{"glyph": "○", "gap": "  ", "margin_left": 3100, "indent": -3100}, {"glyph": "-", "gap": " ", "margin_left": 5100, "indent": -2000}],
             "image_width_mm": 170, "image_max_height_mm": 120}
        ops = build.ops_for(b[:2], p)
        self.assertTrue(ops[0]["text"].startswith("○  ") and ops[1]["text"].startswith("- "))
        self.assertEqual([r.get("bold") for r in ops[0]["runs"]], [None, None, None, True, None])   # 글머리·간격·앞·볼드·뒤


class Audit(unittest.TestCase):
    def test_transfer_catches_missing_sentence_and_cell(self):
        items, _ = convert.convert(ROWS, BLOCKS, None)
        good = "\n".join(convert.plain(i["t"]) for i in items if i.get("t"))
        tables = [{"rowCount": len(i["rows"]), "colCount": 2, "cells": [{"row": r, "col": c, "text": convert.plain(v)} for r, row in enumerate(i["rows"]) for c, v in enumerate(row)]}
                  for i in items if i["k"] == "TBL"]
        self.assertTrue(audit.transfer(items, good, tables)["ok"])
        bad = audit.transfer(items, good.replace("판단함", "판단한다"), tables)
        self.assertEqual([m[1] for m in bad["missing"]], ["당사는 기술의 도입에 있다고 판단함."])
        tables[0]["cells"][3]["text"] = "10년 / 5년"
        self.assertEqual(len(audit.transfer(items, good, tables)["cells_bad"]), 1)


class Lane(unittest.TestCase):
    def test_request_parsing(self):
        with Case():
            self.assertEqual(run.title_of(REQ["text"]), "LP 성장펀드")
            self.assertEqual(run.db_id_of("https://notion.so/kv/00000000-0000-0000-0000-9897af4fbc1b?v=1"), "000000000000000000009897af4fbc1b")
            self.assertEqual(run.db_id_of("링크 없음"), "")

    def test_gate_off_builds_and_records_only(self):
        with Case(messages=[REQ]) as c:
            n1 = run.followup(c.gate); n2 = run.followup(c.gate)
            a = c.acts()
            self.assertEqual((n1["acked"], n2["delivered"]), (1, 1))
            self.assertEqual((a[("1700.1", "ack")], a[("1700.1", "generate1")], a[("1700.1", "deliver1")], a[("1700.1", "note1")]), ("dry", "done", "dry", "dry"))
            self.assertEqual(([x for x in c.slack.posts if x[2]], c.slack.uploads), ([], []))
            inbox = c.dir / "requests" / "1700_1" / "in"
            self.assertTrue((inbox / "(별첨) 출자신청서 양식.hwp").exists() and (inbox / "images" / "09_조직도.png").exists())
            made = json.loads(c.db.execute("SELECT detail FROM actions WHERE kind='generate1'").fetchone()[0])
            self.assertEqual((made["transfer"]["missing"], made["transfer"]["cells_bad"], made["tables"]), (0, 0, 2))
            self.assertEqual(made["findings"]["images_missing"], ["09 images_생성/없는그림.png"])     # 올린 그림은 들어갔고 없는 것만 보고
            self.assertTrue((c.dir / "requests" / "1700_1" / "profile.json").exists())
            live = c.db.execute("SELECT verdict, reason FROM judgments WHERE run=?", (judgments.LIVE,)).fetchone()
            self.assertEqual(live[0], "built"); self.assertIn("누락 0", live[1])

    def test_waits_until_db_and_form_present(self):
        no_form = {**REQ, "files": []}
        with Case(messages=[no_form]) as c:
            run.followup(c.gate); n = run.followup(c.gate)
            self.assertEqual(n["waiting"], 1); self.assertNotIn(("1700.1", "generate1"), c.acts())
            c.slack.replies_by["1700.1"] = [{"ts": "1800.0", "user": "U1", "files": [{"id": "F9", "name": "양식.hwp", "url_private": "u9"}]}]
            n = run.followup(c.gate)
            self.assertEqual(n["delivered"], 1)

    def test_notion_change_makes_v2_and_check_marks_done(self):
        with Case(messages=[REQ], cfg={"fund.outbound": "slack"}) as c:
            run.followup(c.gate); run.followup(c.gate)
            self.assertEqual([u[1] for u in c.slack.uploads], ["v1.hwp", "p01.png", "p02.png"])          # hwp + 미리보기 두 쪽
            note_ts = c.slack.posts[-1][3]
            c.slack.replies_by["1700.1"] = [{"ts": note_ts, "bot_id": "B", "text": "v1"}, {"ts": "1900.0", "user": "U1", "text": "불렛이 서식보다 커요"}]
            n = run.followup(c.gate)
            self.assertEqual((n["open"], n["feedback"]), (1, 1)); self.assertEqual(len(c.slack.uploads), 3)     # 노션이 안 바뀌면 다시 안 만든다
            c.notion.touch("p2", "2026-09-12T00:00:00.000Z")
            n = run.followup(c.gate)
            self.assertEqual((n["revised"], len(c.slack.uploads)), (1, 6))
            note2 = c.slack.posts[-1][3]
            c.slack.replies_by["1700.1"].append({"ts": note2, "bot_id": "B", "text": "v2", "reactions": [{"name": "white_check_mark", "users": ["U1"]}]})
            final = c.dir / "final_human.hwp"; final.write_bytes(b"HWP fake final")
            Path(str(final) + ".ops.json").write_text(json.dumps([{"type": "append_paragraph", "text": "당사는 기술의 도입에 있다고 판단한다."}]))
            c.slack.download = lambda url, dest: (dest.write_bytes(final.read_bytes()), Path(str(dest) + ".ops.json").write_text(Path(str(final) + ".ops.json").read_text()), Fetched.measured(dest))[2]
            c.slack.replies_by["1700.1"].append({"ts": "2100.0", "user": "U1", "ts_": "", "files": [{"id": "F7", "name": "제출본.hwp", "url_private": "h"}]})
            n = run.followup(c.gate)
            self.assertEqual(n["done"], 1)
            done = json.loads(c.db.execute("SELECT detail FROM actions WHERE kind='done'").fetchone()[0])
            self.assertTrue(done["final"].endswith("final.hwp")); self.assertLess(done["kept"], 0.2)                 # 제출본에 원문 문장이 거의 없다 → 생존율 낮음
            self.assertIn("body_pt", done["format_diff"]) if done["format_diff"] else None
            report = (c.dir / f"{run.ledger.today()}.md").read_text()
            self.assertIn("제출본에서만 고친 문장", report); self.assertIn("불렛이 서식보다 커요", report)

    def test_build_failure_is_recorded_not_delivered(self):
        with Case(messages=[REQ], hwp=Hwp(fail_step="create")) as c:
            run.followup(c.gate); n = run.followup(c.gate)
            self.assertEqual(n["failed"], 1); self.assertEqual(c.acts()[("1700.1", "generate1")], "failed"); self.assertNotIn(("1700.1", "deliver1"), c.acts())


if __name__ == "__main__":
    unittest.main()
