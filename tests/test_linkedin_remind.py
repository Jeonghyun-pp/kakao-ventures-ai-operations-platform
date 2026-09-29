"""remind() — ✅ 2일 뒤(주말·공휴일이면 다음 평일) 누른 분을 채널 본문에서 핑한다. 한 사람 한 번."""
from __future__ import annotations

import unittest
from datetime import date

from kvlib import ledger
from skills.linkedin import names, run
from tests._linkedin_fakes import Case, Slack

TUE, THU = date.fromisoformat("2026-09-29"), date.fromisoformat("2026-10-01")


def pressed(key="in/a", *, ts="2026-09-29T02:11:15Z", name="가람 김", who="Reviewer A", uid="U1", url="https://www.linkedin.com/in/a"):
    ledger.append("linkedin", key, run.POSTED, ch="net", slack_ts="m1", parent="par", day="2026-09-28",
                  page_id="p1", name=name, company="Aco", brief="b", url=url)
    extra = {"uid": uid} if uid else {}
    ledger.append("linkedin", key, run.RESOLVED, ts=ts, by="reaction_yes", who=who, name=name, **extra)


def case(today, ko=None):
    return Case(slack=Slack(), today=today,
                **{"names.to_korean": lambda people: {p["key"]: (ko or {}).get(p["key"], ("김가람", True)) for p in people}})


class Due(unittest.TestCase):
    def test_two_days_later_weekend_and_holiday_roll_to_next_weekday(self):
        with case(TUE):
            got = {d: run._remind_day(date.fromisoformat(d)).isoformat() for d in
                   ("2026-09-28", "2026-09-29", "2026-10-01", "2026-10-02", "2026-10-07")}
        self.assertEqual(got, {"2026-09-28": "2026-09-30",     # 월 → 수
                               "2026-09-29": "2026-10-01",     # 화 → 목
                               "2026-10-01": "2026-10-06",     # 목 → 토 → 월 10/5 는 개천절 대체 → 화
                               "2026-10-02": "2026-10-06",     # 금 → 일 → 〃
                               "2026-10-07": "2026-10-12"})    # 수 → 금 10/9 한글날 → 월


class Remind(unittest.TestCase):
    def test_pings_presser_in_channel_body_once(self):
        with case(THU) as c:
            pressed()
            self.assertEqual(run.remind(), 0)
            self.assertEqual(run.remind(), 0)                  # 두 번 돌아도 한 번
        self.assertEqual(c.slack.posts, [("net", "<@U1> <https://www.linkedin.com/in/a|김가람>(Aco) 님께 연락 보내셔야 합니다 :excited:", None)])
        self.assertEqual(c.rows("reminded")[0]["name_ko"], "김가람")

    def test_not_yet_due(self):
        with case(date(2026, 9, 30)) as c:
            pressed()
            run.remind()
        self.assertEqual(c.slack.posts, [])

    def test_overdue_still_sent(self):
        """맥이 자서 목요일을 건너뛰어도 다음 평일에 나간다."""
        with case(date(2026, 10, 2)) as c:
            pressed()
            run.remind()
        self.assertEqual(len(c.slack.posts), 1)

    def test_presses_before_cutover_are_not_backfilled(self):
        with case(THU) as c:
            pressed(ts="2026-09-28T07:30:24Z")
            run.remind()
        self.assertEqual(c.slack.posts, [])

    def test_quiet_on_weekend_and_holiday(self):
        for today in (date(2026, 10, 3), date(2026, 10, 5)):
            with case(today) as c:
                pressed()
                self.assertEqual(run.remind(), 0)
            self.assertEqual(c.slack.posts, [], today)

    def test_old_rows_without_uid_find_presser_by_name(self):
        with case(THU) as c:
            pressed(uid=None)
            run.remind()
        self.assertTrue(c.slack.posts[0][1].startswith("<@U1> "))

    def test_unsure_name_shows_original(self):
        with case(THU, ko={"in/a": ("한상훈", False)}) as c:
            pressed(name="Minsoo Kim")
            run.remind()
        self.assertIn("한상훈", c.slack.posts[0][1])
        self.assertIn("(원문 Minsoo Kim — 이름 확인)", c.slack.posts[0][1])

    def test_expired_without_press_is_not_reminded(self):
        with case(THU) as c:
            ledger.append("linkedin", "in/a", run.RESOLVED, ts="2026-09-29T02:11:15Z", by="expiry", name="A")
            run.remind()
        self.assertEqual(c.slack.posts, [])

    def test_dry_run_writes_nothing(self):
        with case(THU) as c:
            pressed()
            run.remind(dry_run=True)
        self.assertEqual((c.slack.posts, c.rows("reminded")), ([], []))
        self.assertIn("님께 연락 보내셔야 합니다", c.out.getvalue())


class Names(unittest.TestCase):
    def test_rule_handles_hangul_without_model(self):
        self.assertEqual(names._rule("가람 김"), ("김가람", True))
        self.assertEqual(names._rule("최 가람"), ("최가람", True))
        self.assertEqual(names._rule("김가람"), ("김가람", True))
        self.assertEqual(names._rule("민수 남궁")[1], False)
        self.assertIsNone(names._rule("Minsoo Kim"))

    def test_model_failure_falls_back_to_original(self):
        from unittest import mock
        with mock.patch.object(names.claude, "run_session", side_effect=names.claude.SessionError("x")):
            got = names.to_korean([{"key": "a", "name": "Minsoo Kim"}, {"key": "b", "name": "가람 김"}])
        self.assertEqual(got, {"a": ("Minsoo Kim", False), "b": ("김가람", True)})


if __name__ == "__main__":
    unittest.main()
