"""분류 라벨 — 문이 off 면 무엇을 붙였을지만 적고, on 이면 kv/<카테고리> 를 붙이고(없으면 만들고), 사람이 옮긴 라벨은 정답이 된다."""
from __future__ import annotations

import sqlite3
import unittest
from unittest import mock

from kvlib import Fetched
from skills.mail import labels, observe, rules


class Gmail:
    def __init__(self, have=None):
        self.have, self.applied, self.created, self.current = dict(have or {}), [], [], {}

    def labels(self):
        return Fetched.measured(dict(self.have))

    def create_label(self, name):
        self.created.append(name); self.have[name] = f"L_{name}"; return Fetched.measured(self.have[name])

    def relabel(self, mid, *, add, remove):
        self.applied.append((mid, tuple(add))); self.current[mid] = list(add); return Fetched.measured(True)

    def headers_of(self, mid, names):
        return Fetched.measured({"Subject": ""}, labels=self.current.get(mid, []))


class Case:
    def __init__(self, level="off"):
        self.con = observe.connect(":memory:")
        self.gmail, self.level = Gmail({"INBOX": "INBOX"}), level

    def __enter__(self):
        real = labels.constraints.get
        self._p = mock.patch.object(labels.constraints, "get", lambda k: self.level if k == "mail_labels.outbound" else real(k)); self._p.start()
        return self

    def __exit__(self, *a):
        self._p.stop()

    def judged(self, mid, cat, ok=1):
        self.con.execute("INSERT INTO messages(id, direction, sender, subject) VALUES(?,?,?,?)", (mid, "in", "x@y", mid))
        observe._save(self.con, mid, observe.LIVE, "s", "", "", {"ok": ok, "category": cat, "reason": "", "followup": "no"})

    def human(self, mid):
        return self.con.execute("SELECT category, reason FROM judgments WHERE message_id=? AND run=?", (mid, observe.HUMAN)).fetchone()


class Labels(unittest.TestCase):
    def test_off_records_dry_only(self):
        with Case("off") as c:
            c.judged("m1", rules.AD_NOISE); c.judged("m2", rules.BPLAN_INTAKE); c.judged("m3", rules.MASS_BLAST)
            n = labels.apply(c.con, c.gmail)
            self.assertEqual((n["dry"], n["labeled"], n["skipped"]), (2, 0, 1))                  # 접수 알림은 대상이 아니다
            self.assertEqual(c.gmail.applied, []); self.assertEqual(c.gmail.created, [])
            rows = {k: (s, d) for k, s, d in c.con.execute("SELECT key, status, json_extract(detail,'$.label') FROM actions WHERE kind='label'")}
            self.assertEqual(rows, {"m1": ("dry", "kv/AD_NOISE"), "m3": ("dry", "kv/AD_NOISE")})       # MASS_BLAST 는 사람이 보는 이름으로

    def test_on_creates_missing_labels_and_applies_once(self):
        with Case("on") as c:
            c.judged("m1", rules.INBOUND_EN); c.judged("m2", rules.UNKNOWN); c.judged("m4", rules.UNKNOWN, ok=0)
            n = labels.apply(c.con, c.gmail)
            self.assertEqual((n["labeled"], n["dry"]), (2, 0))
            self.assertEqual(sorted(c.gmail.created), ["kv/INBOUND_EN", "kv/UNKNOWN"])
            self.assertEqual(c.gmail.applied, [("m1", ("L_kv/INBOUND_EN",)), ("m2", ("L_kv/UNKNOWN",))])
            self.assertEqual(labels.apply(c.con, c.gmail)["labeled"], 0)                              # 한 통 한 번

    def test_sync_turns_moved_label_into_human_answer(self):
        with Case("on") as c:
            c.judged("m1", rules.INBOUND_EN); c.judged("m2", rules.AD_NOISE); c.judged("m3", rules.UNKNOWN)
            labels.apply(c.con, c.gmail)
            saved = []
            save = lambda mid, cat, why: (saved.append((mid, cat)), observe._save(c.con, mid, observe.HUMAN, "", "", "", {"ok": 1, "category": cat, "reason": why, "followup": None}))
            c.gmail.have["kv/VENDOR_PITCH"] = "L_kv/VENDOR_PITCH"
            c.gmail.current["m1"] = ["L_kv/VENDOR_PITCH"]                                                # 사람이 옮김
            c.gmail.current["m2"] = []                                                                   # 사람이 뗌
            n = labels.sync(c.con, c.gmail, save_human=save)
            self.assertEqual((n["checked"], n["changed"], n["removed"]), (3, 1, 1))
            self.assertEqual(saved, [("m1", "VENDOR_PITCH")]); self.assertIsNone(c.human("m2"))
            self.assertEqual(labels.sync(c.con, c.gmail, save_human=save)["changed"], 0)                # 같은 이동은 다시 안 적는다


if __name__ == "__main__":
    unittest.main()
