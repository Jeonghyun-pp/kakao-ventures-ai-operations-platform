from __future__ import annotations

import unittest
from unittest import mock

from kvlib import Fetched
from skills.appstore import run


class Port(unittest.TestCase):
    def test_stops_before_create_when_existing_names_are_unknown(self):
        targets = Fetched.measured([{"서비스명": "중복될 수 있는 앱", "DB로 업데이트": True}])
        with (
            mock.patch.object(run.notion, "query", side_effect=[targets, Fetched.unknown("timeout")]),
            mock.patch.object(run.notion, "create") as create,
        ):
            self.assertEqual(run.port(), 0)

        create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
