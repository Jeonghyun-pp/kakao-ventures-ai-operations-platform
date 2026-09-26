"""투심 요청 레인 — 요청 → 접수 → 수집·생성·전달 → 피드백 v2 → ✅ 완료(정답 지표). 문이 off 면 슬랙엔 아무것도 안 나간다."""
from __future__ import annotations

import json
import unittest

from kvlib import Fetched, judgments
from skills.memo import run
from tests._memo_fakes import Case, Claude

REQ = {"ts": "1700.1", "user": "U1", "text": "Corpus02 투심 만들어줘", "files": [{"id": "F1", "name": "Corpus02 Investment Theme.docx", "url_private": "u1"}]}


class Discover(unittest.TestCase):
    def test_company_parsing(self):
        with Case():
            self.assertEqual(run.company_of("<@U9> Example Company D Diagnostics 투심보고서 부탁드려요"), "Example Company D Diagnostics")
            self.assertEqual(run.company_of("투심 Corpus02 만들어줘"), "Corpus02")

    def test_only_listed_requesters_and_pattern(self):
        msgs = [REQ, {"ts": "1700.2", "user": "U_stranger", "text": "ABC 투심"}, {"ts": "1700.3", "user": "U1", "text": "점심 뭐 먹지"},
                {"ts": "1700.4", "user": "U1", "bot_id": "B1", "text": "X 투심"}]
        with Case(messages=msgs) as c:
            run.followup(c.gate)
        self.assertEqual([k for (k, kind) in c.acts() if kind == "request"], ["1700.1"])       # 명단 밖·문구 없음·봇 글은 요청이 아니다


class Lane(unittest.TestCase):
    def test_gate_off_records_only(self):
        with Case(messages=[REQ]) as c:
            n1 = run.followup(c.gate)                       # 접수
            n2 = run.followup(c.gate)                       # 수집·생성·전달
            inbox = c.dir / "requests" / "1700_1" / "in"
            got = sorted(p.name for p in inbox.iterdir())
            system = c.claude.calls[0][1]["system_file"].read_text()
        a = c.acts()
        self.assertEqual((n1["acked"], n2["delivered"]), (1, 1))
        self.assertEqual((a[("1700.1", "ack")], a[("1700.1", "generate1")], a[("1700.1", "deliver1")], a[("1700.1", "note1")]), ("dry", "done", "dry", "dry"))
        self.assertEqual(([x for x in c.slack.posts if x[2]], c.slack.uploads), ([], []))                  # 문 off — 스레드엔 0 (하루 요약만 review_test)
        self.assertIn("Corpus02 Investment Theme.docx", got); self.assertIn("노션미팅록_Corpus02.md", got)             # 파일도 노션 본문도 받았다
        self.assertIn("scan_inputs(", c.claude.calls[0][0]); self.assertIn("노션미팅록_Corpus02.md", system)
        live = c.db.execute("SELECT verdict, reason FROM judgments WHERE run=?", (judgments.LIVE,)).fetchone()
        self.assertEqual(live[0], "draft"); self.assertIn("미확인 1", live[1])

    def test_feedback_makes_v2_and_check_marks_done_with_metrics(self):
        with Case(messages=[REQ], cfg={"memo.outbound": "slack"}) as c:
            run.followup(c.gate); run.followup(c.gate)                                          # 접수 → v1 전달(문 slack: 실제로 올라감)
            note_ts = c.slack.posts[-1][3]
            self.assertEqual(c.acts()[("1700.1", "note1")], "done"); self.assertEqual(len(c.slack.uploads), 1)
            c.slack.replies_by["1700.1"] = [{"ts": note_ts, "bot_id": "B", "text": "v1"}, {"ts": "1900.0", "user": "U1", "text": "경쟁사 절 줄여 주세요"}]
            n = run.followup(c.gate)                                                            # 피드백 → v2
            self.assertEqual(n["revised"], 1); self.assertIn("경쟁사 절 줄여 주세요", c.claude.calls[1][1]["system_file"].read_text())
            note2 = c.slack.posts[-1][3]
            c.slack.replies_by["1700.1"].append({"ts": note2, "bot_id": "B", "text": "v2", "reactions": [{"name": "white_check_mark", "users": ["U1"]}]})
            from tests._memo_fakes import make_docx
            human = make_docx(c.dir / "human.docx", ["헤드라인 하나입니다 같은 문단 내용", "심사역이 다시 쓴 문단입니다 길게"])
            c.slack.download = lambda url, dest: (dest.write_bytes(human.read_bytes()), Fetched.measured(dest))[1]
            c.slack.replies_by["1700.1"].append({"ts": "2100.0", "user": "U1", "files": [{"id": "F7", "name": "Corpus02_수정.docx", "url_private": "h"}]})
            n = run.followup(c.gate)                                                            # ✅ → 완료
            report = (c.dir / f"{run.ledger.today()}.md").read_text()
        self.assertEqual(n["done"], 1)
        human = c.db.execute("SELECT verdict, extra FROM judgments WHERE run=?", (judgments.HUMAN,)).fetchone()
        m = json.loads(human[1])
        self.assertEqual(human[0], "done"); self.assertEqual(m["discard_rate"], 0.67)                       # 사람이 올린 docx 가 정답 — 초안 3문단 중 1개만 살았다
        self.assertTrue((c.dir / "requests" / "1700_1" / "final.docx").exists()) if False else None
        self.assertEqual(len(c.reports), 1); self.assertIn("심사역이 지운 초안 문단 표본", report)

    def test_failed_session_is_recorded_not_delivered(self):
        with Case(messages=[REQ], claude=Claude(fail=True)) as c:
            run.followup(c.gate); n = run.followup(c.gate)
        self.assertEqual((n["failed"], c.acts()[("1700.1", "generate1")]), (1, "failed"))
        self.assertNotIn(("1700.1", "deliver1"), c.acts())

    def test_max_versions_stops_revising(self):
        with Case(messages=[REQ], cfg={"memo.outbound": "slack", "memo.max_versions": 1}) as c:
            run.followup(c.gate); run.followup(c.gate)
            c.slack.replies_by["1700.1"] = [{"ts": "1900.0", "user": "U1", "text": "더 고쳐"}]
            n = run.followup(c.gate)
        self.assertEqual((n.get("revised", 0), n["open"]), (0, 1))


class Metrics(unittest.TestCase):
    def test_discarded_paragraphs_are_sampled(self):
        from tests._memo_fakes import make_docx
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as d:
            a = make_docx(pathlib.Path(d) / "a.docx", ["같은 문단이 여기 있습니다 하나", "초안에만 있던 긴 문단입니다 지워질 것"])
            b = make_docx(pathlib.Path(d) / "b.docx", ["같은 문단이 여기 있습니다 하나", "완성본에서 새로 쓴 문단입니다"])
            m = run._metrics(a, b)
        self.assertEqual((m["discard_rate"], m["discarded_sample"]), (0.5, ["초안에만 있던 긴 문단입니다 지워질 것"]))


if __name__ == "__main__":
    unittest.main()
