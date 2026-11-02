"""schedule.py — 선언 → 설치물 변환. launchctl·crontab 은 부르지 않는다."""
from __future__ import annotations

import unittest
import plistlib
import shlex
import tempfile
from pathlib import Path
from unittest import mock

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


class ScheduledEnvironment(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.repo = self.root / 'Review & Demo'
        self.data = self.root / 'data & records'
        self.config = self.root / 'config & settings'
        self.enterContext(mock.patch.object(schedule, 'ROOT', self.repo))
        self.enterContext(mock.patch.object(schedule.paths, 'data_root', return_value=self.data))
        self.enterContext(mock.patch.object(schedule.paths, 'config_root', return_value=self.config))

    def test_plist_preserves_roots_arguments_and_xml_characters(self):
        row = job('매일 10:00', ('observe', '--label', 'one & two'))
        row['why'] = 'review -- then apply'
        result = plistlib.loads(schedule.plist(row).encode())
        self.assertEqual(result['EnvironmentVariables']['KV_CONFIG'], str(self.config.resolve()))
        self.assertEqual(result['EnvironmentVariables']['KV_DATA'], str(self.data.resolve()))
        self.assertEqual(result['WorkingDirectory'], str(self.repo))
        self.assertEqual(result['ProgramArguments'][-1], 'one & two')
        self.assertEqual(Path(result['StandardOutPath']).parent, self.data.resolve() / '_logs')

    def test_cron_preserves_each_shell_argument_and_custom_roots(self):
        tokens = shlex.split(schedule.cron_line(job('매일 10:00', ('observe', '--label', 'one & two'))))
        self.assertEqual(tokens[5:8], ['cd', str(self.repo), '&&'])
        self.assertIn('KV_CONFIG=' + str(self.config.resolve()), tokens)
        self.assertIn('KV_DATA=' + str(self.data.resolve()), tokens)
        self.assertIn('one & two', tokens)
        self.assertEqual(Path(tokens[tokens.index('>>') + 1]).parent, self.data.resolve() / '_logs')

    def test_cron_percent_is_escaped_before_shell_and_newlines_are_rejected(self):
        line = schedule.cron_line(job('10분마다', ('observe', '--label', '100%')))
        self.assertIn(r'100\%', line)
        with self.assertRaises(ValueError):
            schedule.cron_line(job('10분마다', ('observe', 'two\nlines')))

    def test_install_creates_custom_log_directory_without_real_scheduler_calls(self):
        agents = self.root / 'agents'
        with mock.patch.object(schedule, 'MAC', True), mock.patch.object(schedule, 'AGENTS', agents), \
             mock.patch.object(schedule, 'declared', return_value=[job('매일 10:00')]), \
             mock.patch.object(schedule, 'loaded', return_value=set()), \
             mock.patch.object(schedule.subprocess, 'run') as run:
            schedule.cmd_install(True)
        self.assertTrue((self.data / '_logs').is_dir())
        self.assertTrue((agents / 'com.kv.mail.observe.plist').is_file())
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
