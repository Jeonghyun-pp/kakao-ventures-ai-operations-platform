"""bplan sync 의 현재 동작 — 기준선. 순수 함수 검사(Pure) + sync 흐름(가짜 손)."""
from __future__ import annotations

import unittest

from skills.bplan import run
from tests._bplan_fakes import Case, sheet_row

NEW_TS, OLD_TS = "2026. 9. 20 오후 3:00:00", "2026. 8. 1 오전 10:00:00"
NOTION_OLD = {"_page_id": "p0", "접수키": "old@x.com|2026-08-01T10:00:00", "기업명": "옛회사", "이메일": "old@x.com", "DB 업데이트 시간": "2026-08-01T02:00:00.000Z"}


class Pure(unittest.TestCase):
    def test_korean_timestamp(self):
        self.assertEqual(run.parse_ts("2026. 8. 13 오후 1:02:03").hour, 13)
        self.assertEqual(run.parse_ts("2026. 1. 1 오전 12:00:01").hour, 0)
        self.assertIsNone(run.parse_ts("8/13/2026 13:02"))                          # 못 읽으면 None — 추측 안 함

    def test_key_normalization_and_subject(self):
        ts = run.parse_ts("2026. 8. 13 오후 1:02:03")
        self.assertEqual(run.submission_key(" A@B.COM ", ts), "a@b.com|2026-08-13T13:02:03")
        self.assertEqual(run.norm_company("(주)차엔차"), run.norm_company("주식회사 차엔차 "))
        self.assertEqual(run.norm_company("(주)차엔차"), "차엔차")                      # 🔴 긴 패턴 먼저 — 아니면 "주" 가 남는다
        self.assertEqual(run.company_from_subject("[카카오벤처스]주식회사 플레드 로부터 접수된 사업계획서"), "주식회사 플레드")
        self.assertEqual(run.company_from_subject("Re: [카카오벤처스]예시플랫폼 로부터 접수된 사업계획서"), "")   # 답장은 안 잡는다
        self.assertEqual(run.utc_to_kst_iso("2026-08-30T04:21:00.000Z"), "2026-08-30T13:21:00")

    def test_header_prefixes_fail_loud(self):
        from tests._bplan_fakes import HDR
        self.assertEqual(len(run.resolve_columns(HDR)), len(run._COLS) + 1)
        with self.assertRaises(RuntimeError):
            run.resolve_columns([h for h in HDR if not h.startswith("2. 문제")])

    def test_legacy_dup_by_email_and_time_not_name(self):
        legacy = {"a@b.com": "2026-08-30T13:34:00"}
        old = {"key": "a@b.com|2026-08-29T10:00:00", "ts": "2026-08-29T10:00:00", "email": "a@b.com", "props": {"기업명": "사랑의빛 (EACP)"}}
        new = {**old, "key": "a@b.com|2026-09-01T15:13:00", "ts": "2026-09-01T15:13:00"}
        self.assertTrue(run.is_legacy_dup(old, legacy))                              # 옛 행이 접수보다 뒤에 생김 = 같은 접수
        self.assertFalse(run.is_legacy_dup(new, legacy))                             # 행 생성 뒤 재접수 = 새것
        picked, why = run.pick_new([old, new, {**new, "key": "k", "ts": "2026-08-01T00:00:00"}], {"k"}, legacy, "2026-08-13T11:12:43")
        self.assertEqual(([s["key"] for s in picked], why["바닥선 이전"], why["옛 행(키 없음)과 같은 접수"]), ([new["key"]], 1, 1))

    def test_props_skip_blanks_and_never_touch_drop(self):
        p = run._props_for({"key": "k", "props": {"기업명": "회사", "연락처": "", "이메일": "a@b.com"}})
        self.assertEqual((p.get("연락처"), "드랍메일" in p, p[run.TODO_PROP], p["담당자"]), (None, False, "Newly Added", run.ASSIGNEE))


class Sync(unittest.TestCase):
    def test_dry_run_plans_but_writes_nothing(self):
        with Case(sheet=[sheet_row(OLD_TS, "old@x.com", "옛회사"), sheet_row(NEW_TS, "new@x.com", "새회사")],
                  notion=[NOTION_OLD], intake=[("m1", "[카카오벤처스]새회사 로부터 접수된 사업계획서")]) as c:
            self.assertEqual(run.sync(dry_run=True, limit=0, since=None), 0)
        self.assertEqual((c.notion.created, c.gmail.relabeled, c.rows()), ([], [], []))
        self.assertIn("생성(예정)                 1", c.out.getvalue())
        self.assertEqual(len(c.reports), 1)                                          # 보고서는 dry 에도 남는다

    def test_real_run_creates_row_then_ledger_then_label(self):
        with Case(sheet=[sheet_row(NEW_TS, "new@x.com", "새회사")], notion=[NOTION_OLD],
                  intake=[("m1", "[카카오벤처스]새회사 로부터 접수된 사업계획서"), ("m2", "Re: [카카오벤처스]새회사 로부터 접수된 사업계획서")]) as c:
            self.assertEqual(run.sync(dry_run=False, limit=0, since=None), 0)
        self.assertEqual(c.notion.created[0]["기업명"], "새회사")
        self.assertEqual(c.notion.created[0]["접수키"], "new@x.com|2026-09-20T15:00:00")
        landed = c.rows("landed")[0]
        self.assertEqual((landed["key"], landed["submitted"], landed["page_id"]), ("new@x.com|2026-09-20T15:00:00", "2026-09-20T15:00:00", "pg-1"))
        self.assertTrue(landed["ts"].endswith("Z"))                                    # 🔴 접수시각이 기록 시각을 덮지 않는다
        self.assertEqual(c.gmail.relabeled, [("m1", ["L1"], ["INBOX"])])               # 답장(m2)은 안 건드린다
        self.assertEqual(c.rows("labeled")[0]["key"], "m1")
        self.assertIn("답장(Re:)이라 건드리지 않음  1", c.out.getvalue())

    def test_intake_mail_without_notion_row_is_left_alone_and_degrades(self):
        with Case(sheet=[], notion=[NOTION_OLD], intake=[("m9", "[카카오벤처스]누락회사 로부터 접수된 사업계획서")]) as c:
            with self.assertRaises(run.Degraded):
                run.sync(dry_run=False, limit=0, since=None)
        self.assertEqual(c.gmail.relabeled, [])
        self.assertIn("라벨 보류", c.out.getvalue())

    def test_second_run_is_idempotent(self):
        with Case(sheet=[sheet_row(NEW_TS, "new@x.com", "새회사")], notion=[NOTION_OLD],
                  intake=[("m1", "[카카오벤처스]새회사 로부터 접수된 사업계획서")]) as c:
            run.sync(dry_run=False, limit=0, since=None)
            c.notion.rows.append({"_page_id": "pg-1", "접수키": "new@x.com|2026-09-20T15:00:00", "기업명": "새회사", "이메일": "new@x.com", "DB 업데이트 시간": "2026-09-20T06:00:00.000Z"})
            run.sync(dry_run=False, limit=0, since=None)
        self.assertEqual((len(c.notion.created), len(c.gmail.relabeled)), (1, 1))

    def test_missing_label_stops_before_reading(self):
        with Case(sheet=[sheet_row(NEW_TS, "new@x.com", "새회사")], labels={}) as c:
            self.assertEqual(run.sync(dry_run=False, limit=0, since=None), 1)
        self.assertIn("라벨 '3. Evan' 이 없다", c.out.getvalue())


if __name__ == "__main__":
    unittest.main()
