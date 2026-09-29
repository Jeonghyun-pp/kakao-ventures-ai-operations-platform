"""card() 의 현재 동작 — 기준선. 노션 행에서 5명을 고르고 헤더+스레드로 올린다."""
from __future__ import annotations

import unittest

from kvlib import Fetched, ledger
from skills.linkedin import run
from tests._linkedin_fakes import Case, Notion, Slack, row, SAT


def brief_ok(cards):
    return Fetched.measured([{"key": c["key"], "verdict": "비창업" if "비창업" in str(c["name"]) else "창업",
                              "brief": f"소개 {c['name']}", "company": c["company"] or "(미상)"} for c in cards])


def case(rows, early=(), **more):
    return Case(notion=Notion({run.DB: rows, run.EARLY: [{"링크": u} for u in early]}),
                **{"brief.write": brief_ok, "axes.card_reject": lambda live: live.get("_reject", ""), **more})


class Card(unittest.TestCase):
    def test_picks_good_tiers_first_and_posts_header_plus_threads(self):
        rows = [row("p1", "https://www.linkedin.com/in/a", "Wait"), row("p2", "https://www.linkedin.com/in/b", "Catch"),
                row("p3", "https://www.linkedin.com/in/c", "Trend")]
        with case(rows) as c:
            rc = run.card(n=2)
            rep = (c.data / "linkedin" / "reports" / f"{ledger.today()}.md").read_text()
        self.assertEqual(rc, 0)
        net = [p for p in c.slack.posts if p[0] == "net"]
        self.assertEqual([p[2] for p in net], [None, net[0] and "ts-2", "ts-2"])          # 헤더 + 스레드 답글 2 (앞의 test 채널 글은 재고 경고)
        self.assertIn("이름p2", net[1][1])                                                 # Catch 먼저
        self.assertIn("이름p3", net[2][1])
        self.assertEqual([r["ev"] for r in c.rows()], ["screened"] * 3 + ["alerted", "carded", "posted", "carded", "posted"])   # 판정이 먼저 장부에
        self.assertEqual(c.rows("posted")[0]["slack_ts"], "ts-3")                           # 슬랙 ts 는 제 칸에 — 장부 ts 는 기록 시각
        self.assertTrue(c.rows("posted")[0]["ts"].endswith("Z"))
        self.assertIn("카드 2명", rep)                                                      # 하루 보고서 파일
        self.assertTrue(any(p[0] == "test" and "*링크드인 하루*" in p[1] for p in c.slack.posts))   # 요약은 test 채널로

    def test_skips_already_posted_target_updated_and_early_db(self):
        rows = [row("p1", "https://www.linkedin.com/in/a"), row("p2", "https://www.linkedin.com/in/b", **{"타깃": True}),
                row("p3", "https://www.linkedin.com/in/c", **{"DB로 업데이트": True}), row("p4", "https://www.linkedin.com/in/d"),
                row("p5", "https://www.linkedin.com/in/e")]
        with case(rows, early=["https://www.linkedin.com/in/d"]) as c:
            ledger.append("linkedin", "k1", run.POSTED, page_id="p1", parent="x", ts="t", ch="net", day="2026-09-20")
            run.card(n=5)
        names = [p[1] for p in c.slack.posts if p[0] == "net" and p[2]]                    # 스레드 답글 = 사람
        self.assertEqual(len(names), 1)
        self.assertIn("이름p5", names[0])
        self.assertIn("이미 본 사람이라 뺐다", c.out.getvalue())

    def test_card_reject_reason_is_counted_not_silent(self):
        rows = [row("p1", "https://www.linkedin.com/in/a", _reject="재직 36개월 초과"), row("p2", "https://www.linkedin.com/in/b")]
        with case(rows) as c:
            run.card(n=5)
        self.assertIn("재직 36개월 초과: 1명", c.out.getvalue())
        self.assertEqual(len([p for p in c.slack.posts if p[0] == "net"]), 2)             # 헤더 + 1명

    def test_empty_pool_alerts_once_and_returns_4(self):
        with case([]) as c:
            self.assertEqual(run.card(n=5), 4)
            self.assertEqual(run.card(n=5), 4)
        alerts = [p for p in c.slack.posts if "재고 소진" in p[1]]
        self.assertEqual([p[0] for p in alerts], ["test"])                                   # 알림은 test 채널, 하루 한 번
        self.assertEqual(sum("*링크드인 하루*" in p[1] for p in c.slack.posts), 1)            # 보고서 요약도 하루 한 번

    def test_low_stock_warns(self):
        rows = [row(f"p{i}", f"https://www.linkedin.com/in/{i}") for i in range(6)]
        with case(rows) as c:
            run.card(n=5)
        warn = [p for p in c.slack.posts if "재고 부족" in p[1]]
        self.assertEqual([p[0] for p in warn], ["test"])

    def test_weekend_sends_nothing(self):
        with case([row("p1")], today=SAT) as c:
            self.assertEqual(run.card(n=5), 0)
        self.assertEqual(c.slack.posts, [])
        self.assertIn("주말", c.out.getvalue())

    def test_dry_run_touches_nothing(self):
        with case([row("p1")]) as c:
            run.card(n=5, dry_run=True)
        self.assertEqual((c.slack.posts, c.rows()), ([], []))

    def test_notion_unreadable_stops(self):
        with Case(notion=Notion({})) as c:
            self.assertEqual(run.card(n=5), 1)
        self.assertEqual(c.slack.posts, [])


if __name__ == "__main__":
    unittest.main()


class Foreigner(unittest.TestCase):
    """[2026-09-28] 카드 필터는 `외국인` 태그만 본다 — 누가 외국인인지는 judge.nationality 가 정한다."""
    STEALTH = {"회사": "Stealth Startup", "Tenure Months": 3}

    def test_foreign_tag_is_rejected(self):
        from skills.linkedin import axes
        self.assertEqual(axes.card_reject({**self.STEALTH, "Korea Signal": ["KR-거주", "외국인"]}), "외국인")
        self.assertEqual(axes.card_reject({**self.STEALTH, "Korea Signal": ["KR-거주"]}), "")
        self.assertEqual(axes.card_reject(self.STEALTH), "")                     # 태그 없음 = 모름 = 감점 아님

    def test_kaist_axes_are_exempt(self):
        from skills.linkedin import axes
        self.assertTrue(all(axes.nationality_exempt(a) for a in axes.AXES if a.startswith("kaist")))
        self.assertFalse(axes.nationality_exempt("s-stealth"))

    def test_parser_reads_value_not_position_and_skips_unknown(self):
        from unittest import mock
        from skills.linkedin import judge
        raw = "a\t외국인\nb\t무언가\t한국인\nzz\t외국인\nc\t모름\n"
        with mock.patch.object(judge.claude, "run_session", lambda *a, **k: raw):
            got = judge.nationality({"a": "a", "b": "b", "c": "c"}).require()
        self.assertEqual(got, {"a": "외국인", "b": "한국인"})                  # c 는 빠진다 → 호출부가 한국인으로 둔다


class Founder(unittest.TestCase):
    """[2026-09-29] 문안 단계 판정이 비창업이면 안 올리고, 다음 사람으로 채운다. 판정은 장부에 남아 재고도 같이 준다."""

    def rows(self):
        return [row(f"p{i}", f"https://www.linkedin.com/in/u{i}", **({"서비스명": f"비창업{i}"} if i < 3 else {}))
                for i in range(6)]

    def test_non_founders_are_skipped_and_backfilled(self):
        with case(self.rows()) as c:
            run.card(n=2)
        names = [p[1] for p in c.slack.posts if p[0] == "net" and p[2]]
        self.assertEqual(len(names), 2)
        self.assertFalse(any("비창업" in x for x in names))
        self.assertEqual({r["verdict"] for r in c.rows("screened")}, {"창업", "비창업"})

    def test_screened_non_founder_is_not_rebriefed(self):
        calls = []
        def spy(cards):
            calls.append([x["key"] for x in cards]); return brief_ok(cards)
        with case(self.rows(), **{"brief.write": spy}) as c:
            ledger.append("linkedin", "in/u0", run.SCREENED, page_id="p0", verdict="비창업", company="x", brief="b")
            run.card(n=1)
        self.assertNotIn("in/u0", [k for ks in calls for k in ks])
        self.assertIn(("p0", {"Status": "Legacy"}), c.notion.updated)                   # 판정난 비창업은 Legacy 로

    def test_new_non_founders_go_to_legacy(self):
        with case(self.rows()) as c:
            run.card(n=2)
        self.assertEqual(sorted(p for p, props in c.notion.updated if props == {"Status": "Legacy"}), ["p0", "p1", "p2"])

    def test_legacy_rows_are_not_stock(self):
        rows = [row("p1", "https://www.linkedin.com/in/a", Status="Legacy"), row("p2", "https://www.linkedin.com/in/b")]
        with case(rows) as c:
            run.card(n=5)
        names = [p[1] for p in c.slack.posts if p[0] == "net" and p[2]]
        self.assertEqual(len(names), 1)
        self.assertIn("이름p2", names[0])

    def test_freshest_first_within_tier(self):
        rows = [row("p1", "https://www.linkedin.com/in/a", **{"Captured At": "2026-08-17"}),
                row("p2", "https://www.linkedin.com/in/b", **{"Captured At": "2026-09-29"})]
        with case(rows) as c:
            run.card(n=1)
        names = [p[1] for p in c.slack.posts if p[0] == "net" and p[2]]
        self.assertIn("이름p2", names[0])

    def test_dry_run_does_not_record(self):
        with case(self.rows()) as c:
            run.card(n=2, dry_run=True)
        self.assertEqual((c.rows("screened"), c.notion.updated), ([], []))

    def test_screen_only_records_and_posts_nothing(self):
        with case(self.rows()) as c:
            self.assertEqual(run.card(n=10_000, screen_only=True), 0)
        self.assertEqual(len(c.rows("screened")), 6)
        self.assertEqual(c.slack.posts, [])

    def test_brief_parser_finds_verdict_by_value(self):
        from unittest import mock
        from skills.linkedin import brief
        raw = "a\t창업\tAco\t두 문장.\nb\tBco\t비창업\t합류했다.\nc\tCco\t문장만\n"
        with mock.patch.object(brief.claude, "run_session", lambda *a, **k: raw), \
             mock.patch.object(brief.claude, "dlq", lambda *a, **k: None):
            got = {x["key"]: x for x in brief.write([{"key": k} for k in "abc"]).require()}
        self.assertEqual((got["a"]["verdict"], got["a"]["company"]), ("창업", "Aco"))
        self.assertEqual((got["b"]["verdict"], got["b"]["company"], got["b"]["brief"]), ("비창업", "Bco", "합류했다."))
        self.assertNotIn("c", got)                                             # 판정 없는 줄은 DLQ — 오늘 안 올라간다



class Age(unittest.TestCase):
    """[2026-09-29] 추정 40세 이상은 안 올린다. 추정 불가는 통과."""

    def test_card_reject_by_estimated_age(self):
        from skills.linkedin import axes
        base = {"회사": "Stealth Startup", "Tenure Months": 3}
        self.assertEqual(axes.card_reject({**base, "추정 나이": 40}), "추정 40세 이상")
        self.assertEqual(axes.card_reject({**base, "추정 나이": 39}), "")
        self.assertEqual(axes.card_reject(base), "")

    def test_estimated_age_prefers_undergrad_then_first_job(self):
        from datetime import date
        from skills.linkedin import profile
        y = date.today().year
        edu = [{"degree": "Bachelor of Science", "startDate": {"year": y - 10}}]
        exp = [{"startDate": {"year": y - 3}}, {"startDate": {"year": y - 5}}]
        self.assertEqual(profile.estimated_age({"education": edu, "experience": exp}), 29)
        self.assertEqual(profile.estimated_age({"experience": exp}), 28)
        self.assertIsNone(profile.estimated_age({"education": [{"degree": "MBA", "startDate": {"year": y - 2}}]}))


class LegacyAll(unittest.TestCase):
    """[2026-09-29] 카드 기준 탈락은 전부 Legacy — 단 사람이 손댄 행은 건드리지 않는다."""

    def test_rejected_goes_legacy_but_human_touched_does_not(self):
        rows = [row("p1", "https://www.linkedin.com/in/a", _reject="재직 36개월 초과"),
                row("p2", "https://www.linkedin.com/in/b", _reject="재직 36개월 초과", **{"타깃": True}),
                row("p3", "https://www.linkedin.com/in/c", _reject="재직 36개월 초과", Status="Reviewing"),
                row("p4", "https://www.linkedin.com/in/d")]
        with case(rows) as c:
            run.card(n=5)
        self.assertEqual([p for p, props in c.notion.updated if props == {"Status": "Legacy"}], ["p1"])
