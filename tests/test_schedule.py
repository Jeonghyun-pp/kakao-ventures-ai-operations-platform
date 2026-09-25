"""schedule.py — 선언 → 설치물 변환. launchctl·crontab 은 부르지 않는다."""
from __future__ import annotations

import unittest

import schedule


def job(when, argv=("observe",)):
    j = {"skill": "mail", "cmd": " ".join(argv), "argv": list(argv), "when": when, "why": "", "label": "com.kv.mail." + argv[0]}
    e, m = schedule._EVERY.match(when), schedule._WHEN.match(when)
    if e:
        j.update(interval=int(e.group(1)) * 60, weekday=None, hour=None, minute=None)
    else:
        day, hh, mm = m.group(1), int(m.group(2)), int(m.group(3))
        j.update(interval=None, weekday=None if day == "매일" else schedule._WEEKDAY[day], hour=hh, minute=mm)
    return j


class Cron(unittest.TestCase):
    def test_three_forms(self):
        self.assertTrue(schedule.cron_line(job("매일 10:00")).startswith("0 10 * * * cd "))
        self.assertTrue(schedule.cron_line(job("월 09:10")).startswith("10 9 * * 1 cd "))
        self.assertTrue(schedule.cron_line(job("10분마다")).startswith("*/10 * * * * cd "))

    def test_label_is_the_trailing_comment_and_flags_are_split(self):
        ln = schedule.cron_line(job("매일 09:30", ("observe", "--days", "3")))
        self.assertTrue(ln.endswith("# com.kv.mail.observe"))
        self.assertIn("skills.mail.run observe --days 3 >>", ln)

    def test_interval_must_divide_the_hour(self):
        with self.assertRaises(ValueError):
            schedule.cron_line(job("7분마다"))

    def test_declared_has_no_duplicate_labels(self):
        labels = [j["label"] for j in schedule.declared()]
        self.assertEqual(len(labels), len(set(labels)))


if __name__ == "__main__":
    unittest.main()
