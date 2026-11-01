"""claude 통로의 명령줄 — 공유 파일이라 **기본 동작이 안 바뀌었음**을 고정한다. 프로세스를 띄우지 않는다."""
from __future__ import annotations

import types
import unittest
from unittest import mock

from kv_transport import claude


def _argv(**kw) -> list[str]:
    seen = {}

    def fake_run(cmd, **_):
        seen["cmd"] = cmd
        return types.SimpleNamespace(stdout="ok", stderr="", returncode=0)

    with mock.patch("kv_transport.claude.subprocess.run", fake_run), \
         mock.patch("kv_transport.claude._bin", return_value="claude"), \
         mock.patch("kv_transport.claude._calls_today", return_value=0), \
         mock.patch("kv_transport.claude._record"):
        claude.run_session("prompt", **kw)
    return seen["cmd"]


class Argv(unittest.TestCase):
    def test_zero_exit_with_empty_output_is_a_recorded_failure(self):
        with mock.patch.object(claude, "_bin", return_value="claude"), \
             mock.patch.object(claude, "_calls_today", return_value=0), \
             mock.patch.object(claude, "_spawn", return_value=types.SimpleNamespace(
                 stdout=" \n", stderr="", returncode=0)), \
             mock.patch.object(claude, "_record") as record:
            with self.assertRaisesRegex(claude.SessionError, "빈 출력"):
                claude.run_session("synthetic prompt")
            record.assert_called_once_with(False, "empty_output")

    def test_default_is_unchanged(self):
        self.assertEqual(_argv(system="s"),
                         ["claude", "-p", "--strict-mcp-config", "--disallowedTools",
                          "Bash,WebFetch,WebSearch", "--append-system-prompt", "s"])

    def test_empty_tools_disables_all(self):
        self.assertEqual(_argv(tools="")[-2:], ["--tools", ""])

    def test_none_adds_nothing(self):
        self.assertNotIn("--tools", _argv())


if __name__ == "__main__":
    unittest.main()
