"""메일함 대조 — 사람 행동(치움·답장·전달·라벨)을 읽어 모델 판정과 맞으면 정답, 다르면 묻는다. 메일함엔 안 쓴다."""
from __future__ import annotations

import unittest

from kvlib import Fetched
from skills.mail import observe, reconcile, rules


class Gmail:
    def __init__(self):
        self.state = {}                                   # mid → (labels, tail)
        self.names = {"kv/VENDOR_PITCH": "Label_7", "따로": "Label_9"}
        self.writes = 0

    def labels(self):
        return Fetched.measured(dict(self.names))

    def headers_of(self, mid, names):
        if mid.startswith("sent"):
            return Fetched.measured({"Subject": "Fwd: 넘깁니다" if mid == "sentF" else "Re: 답장"}, labels=["SENT"])
        labels, _ = self.state[mid]
        return Fetched.measured({"Subject": mid}, labels=labels, thread_id="t" + mid)

    def thread_tail(self, tid, mid):
        return Fetched.measured(self.state[mid][1])


def con_with(cases):
    con = observe.connect(":memory:")
    for mid, cat in cases:
        con.execute("INSERT INTO messages(id, thread_id, direction, sender, subject, internal_ms) VALUES(?,?,?,?,?,?)", (mid, "t" + mid, "in", "x@y", mid, 1))
        observe._save(con, mid, observe.LIVE, "s", "", "", {"ok": 1, "category": cat, "reason": "", "followup": "no"})
    return con


class Reconcile(unittest.TestCase):
    def test_actions_and_verdicts(self):
        g = Gmail()
        g.state = {"a": (["INBOX"], []),                                           # 그대로
                   "b": ([], []),                                                  # 치움 — 모델 AD_NOISE → 일치
                   "c": ([], []),                                                  # 치움 — 모델 INBOUND_EN → 묻는다
                   "d": (["INBOX"], [{"id": "sentR", "labels": ["SENT"]}]),       # 답장 — 모델 INBOUND_KO → 일치
                   "e": (["INBOX"], [{"id": "sentF", "labels": ["SENT"]}]),       # 전달 — 모델 UNKNOWN → 일치
                   "f": (["INBOX", "Label_7"], []),                                # 라벨 kv/VENDOR_PITCH → 정확
                   "g": (["TRASH"], []),                                           # 휴지통 — 모델 SYSTEM_NOTICE → 일치
                   "h": (["INBOX", "Label_9"], [{"id": "sentR", "labels": ["SENT"]}]),  # 이름 모를 라벨 + 답장 — 모델 AD_NOISE → 묻는다
                   "i": ([], [{"id": "x", "labels": []}])}                        # 치움, 상대가 또 보냄(SENT 아님) — 모델 VENDOR → 일치
        con = con_with([("a", rules.UNKNOWN), ("b", rules.AD_NOISE), ("c", rules.INBOUND_EN), ("d", rules.INBOUND_KO), ("e", rules.UNKNOWN),
                        ("f", rules.AD_NOISE), ("g", rules.SYSTEM_NOTICE), ("h", rules.AD_NOISE), ("i", rules.VENDOR_PITCH), ("z", rules.BPLAN_INTAKE)])
        rows = {r["id"]: r for r in reconcile.scan(con, g)}
        self.assertNotIn("z", rows)
        self.assertEqual({k: (v["human"], v["verdict"]) for k, v in rows.items()},
                         {"a": ("none", "pending"), "b": ("archive", "agree"), "c": ("archive", "ask"), "d": ("reply", "agree"), "e": ("forward", "agree"),
                          "f": ("label:VENDOR_PITCH", "exact"), "g": ("trash", "agree"), "h": ("reply", "ask"), "i": ("archive", "agree")})
        saved = []
        n = reconcile.apply(con, list(rows.values()), lambda mid, cat, why: (saved.append((mid, cat)),
                            observe._save(con, mid, observe.HUMAN, "", "", "", {"ok": 1, "category": cat, "reason": why, "followup": None})))
        self.assertEqual(n, 6)
        self.assertEqual(dict(saved), {"b": "AD_NOISE", "d": "INBOUND_KO", "e": "UNKNOWN", "f": "VENDOR_PITCH", "g": "SYSTEM_NOTICE", "i": "VENDOR_PITCH"})
        self.assertEqual(reconcile.apply(con, list(rows.values()), lambda *a: saved.append(a)), 0)     # 두 번 적지 않는다
        self.assertIn("물을 것", reconcile.report(list(rows.values())))


if __name__ == "__main__":
    unittest.main()
