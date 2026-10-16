"""followup() 의 현재 동작 — ✅ 를 걷어 연락 문안(outreach.md)을 스레드에 올리고, 7일 무반응은 만기."""
from __future__ import annotations

import unittest

from kvlib import Fetched, ledger
from skills.linkedin import run
from tests._linkedin_fakes import Case, Notion, Slack, row

YES = {"name": "white_check_mark", "users": ["U1"]}


def posted(key="in/a", *, day="2026-09-23", page="p1", old=False):
    ledger.append("linkedin", key, run.POSTED, ch="net", **({"ts": "m1"} if old else {"slack_ts": "m1"}), parent="par", day=day, page_id=page,
                  name="A", company="Aco", brief="b", homepage="https://a.co", url="https://www.linkedin.com/in/a")


def case(replies, **more):
    return Case(notion=Notion({run.DB: [row("p1")]}), slack=Slack(replies),
                **{"_promote": lambda k, r, g=None: "created",
                   "names.to_korean": lambda people: {p["key"]: ("김가람", "가람", True) for p in people},
                   **more})


class Followup(unittest.TestCase):
    def test_yes_posts_draft_in_thread_with_sender_and_resolves(self):
        with case({"par": [{"ts": "m1", "reactions": [YES]}]}) as c:
            posted()
            rc = run.followup()
        self.assertEqual(rc, 0)
        draft = [p for p in c.slack.posts if p[2] == "par"]
        self.assertEqual(len(draft), 1)
        self.assertIn("Reviewer A 심사역 이름", draft[0][1])
        self.assertIn("안녕하세요 가람님, 처음 인사드립니다.", draft[0][1])      # 성 뺀 이름
        self.assertIn("카카오벤처스 Reviewer A 드림", draft[0][1])                     # 누른 분 실명
        self.assertNotIn("{", draft[0][1])                                        # 빈 칸이 남지 않는다
        self.assertEqual([(r["ev"], r.get("by")) for r in c.rows() if r["ev"] != "posted"],
                         [("outreach", "Reviewer A"), ("resolved", "reaction_yes")])
        self.assertEqual(c.rows("resolved")[0]["promoted"], "created")
        self.assertEqual(c.rows("resolved")[0]["uid"], "U1")      # 리마인드가 핑할 사람
        self.assertEqual(c.rows("resolved")[0]["name_ko"], "김가람")  # 리마인드가 같은 이름을 쓴다

    def test_pitch_fills_message_and_evidence_goes_below(self):
        """[2026-10-16] 맞춤 문단은 문안 안에, 근거는 코드 블록 밖(보내기 전 확인용)에. 카드 때 남긴 웹 개괄이 pitch 로 간다."""
        seen = {}

        def fake(it, given, web):
            seen.update(it=it, given=given, web=web)
            return {"text": "링크드인에서 Aco 소식을 접하고 연락드립니다. 회의 통역 제품인데, 이력이 인상 깊었습니다.",
                    "close": "Aco가 만들어가는 이야기가 궁금합니다.", "kept": ["product", "past"],
                    "dropped": {"proof": "비어 있음"}, "evidence": ["Built X"]}
        raw = {"in/a": {"headline": "CEO @ Aco"}}
        with case({"par": [{"ts": "m1", "reactions": [YES]}]}, cfg={"linkedin.outreach_pitch": True},
                  **{"pitch.latest_by_key": lambda: raw, "pitch.write": fake}) as c:
            ledger.append("linkedin", "in/a", run.POSTED, ch="net", slack_ts="m1", parent="par", day="2026-09-23", page_id="p1",
                          name="A", company="Aco", url="https://www.linkedin.com/in/a", overview=["뭘 만드나: 통역 (출처: https://a.co)"])
            run.followup()
        msg = [p for p in c.slack.posts if p[2] == "par"][0][1]
        body, after = msg.split("```")[1], msg.split("```")[2]
        self.assertIn("심사역입니다.\n링크드인에서 Aco 소식을 접하고 연락드립니다. 회의 통역 제품인데", body)
        self.assertIn("벤처캐피탈입니다. Aco가 만들어가는 이야기가 궁금합니다. 괜찮으시면", body)
        self.assertNotIn("Built X", body)
        self.assertIn("> Built X", after)
        self.assertEqual((seen["given"], seen["web"]), ("가람", ["뭘 만드나: 통역 (출처: https://a.co)"]))
        self.assertEqual((c.rows("outreach")[0]["pitch"], c.rows("outreach")[0]["pitch_dropped"]), (["product", "past"], ["proof"]))

    def test_pitch_off_or_no_raw_still_posts_with_first_sentence(self):
        for cfg, raw in (({"linkedin.outreach_pitch": False}, {"in/a": {}}), ({"linkedin.outreach_pitch": True}, {})):
            with self.subTest(cfg=cfg), case({"par": [{"ts": "m1", "reactions": [YES]}]}, cfg=cfg,
                                             **{"pitch.latest_by_key": lambda raw=raw: raw,
                                                "pitch.write": lambda *a: self.fail("claude 를 부르면 안 된다")}) as c:
                posted()
                run.followup()
            msg = [p for p in c.slack.posts if p[2] == "par"][0][1]
            self.assertIn("심사역입니다.\n링크드인에서 가람님 프로필을 보고 연락드립니다.\n카카오벤처스는", msg)
            self.assertNotIn("근거", msg)

    def test_old_rows_with_slack_ts_in_ts_column_still_match(self):
        """09-25 전 장부 195줄은 슬랙 ts 가 `ts` 칸에 있다 — 읽기는 둘 다 본다."""
        with case({"par": [{"ts": "m1", "reactions": [YES]}]}) as c:
            posted(old=True)
            self.assertEqual(run.followup(), 0)
        self.assertEqual(c.rows("outreach")[0]["by"], "Reviewer A")

    def test_unknown_reviewer_leaves_sender_blank(self):
        with case({"par": [{"ts": "m1", "reactions": [{"name": "white_check_mark", "users": ["U9"]}]}]}) as c:
            posted()
            run.followup()
        self.assertIn("발신자 이름을 채워주세요", [p for p in c.slack.posts if p[2] == "par"][0][1])

    def test_no_reaction_for_seven_days_expires(self):
        with case({"par": [{"ts": "m1", "reactions": []}]}) as c:
            posted(day="2026-09-10")
            run.followup()
        self.assertEqual([(r["ev"], r.get("by")) for r in c.rows() if r["ev"] != "posted"], [("resolved", "expiry")])
        self.assertEqual([p for p in c.slack.posts if p[2] == "par"], [])

    def test_recent_post_without_reaction_keeps_waiting(self):
        with case({"par": [{"ts": "m1", "reactions": []}]}) as c:
            posted(day=ledger.today())                      # 날짜를 박으면 followup_days 가 지난 날 시험이 거짓으로 깨진다(10/1 실측)
            self.assertEqual(run.followup(), 0)
        self.assertEqual([r["ev"] for r in c.rows()], ["posted"])

    def test_unreadable_thread_is_not_expired(self):
        """🔴 읽기에 실패한 스레드는 만기시키지 않는다 — 망 오류 한 번에 사람이 사라지지 않게."""
        with case({}) as c:
            posted(day="2026-09-10")
            self.assertEqual(run.followup(), 1)
        self.assertEqual([r["ev"] for r in c.rows()], ["posted"])

    def test_dry_run_writes_nothing(self):
        with case({"par": [{"ts": "m1", "reactions": [YES]}]}) as c:
            posted()
            run.followup(dry_run=True)
        self.assertEqual(([r["ev"] for r in c.rows()], c.slack.posts), (["posted"], []))

    def test_nothing_waiting(self):
        with case({}) as c:
            self.assertEqual(run.followup(), 0)
        self.assertIn("기다리는 건이 없다", c.out.getvalue())


NO = {"name": "x", "users": ["U2"]}


class Reactions(unittest.TestCase):
    """❌ 와 늦은 반응 [사용자 결정 2026-10-14] — ❌ 는 '이번엔 관심 없음', 무반응은 '안 봄'. 정답은 앞의 것만."""

    def test_only_no_keeps_waiting_before_due(self):
        """❌ 만으로 닫지 않는다 — 다른 분이 ✅ 를 누를 수 있다."""
        with case({"par": [{"ts": "m1", "reactions": [NO]}]}) as c:
            posted(day=ledger.today())
            run.followup()
        self.assertEqual([r["ev"] for r in c.rows()], ["posted"])

    def test_only_no_at_due_is_reaction_no_not_expiry(self):
        with case({"par": [{"ts": "m1", "reactions": [NO]}]}) as c:
            posted(day="2026-09-10")
            run.followup()
            labels = run._reaction_labels()
        r = c.rows("resolved")[0]
        self.assertEqual((r["by"], r["no_by"]), ("reaction_no", ["U2"]))
        self.assertEqual(labels["in/a"]["label"], "no")

    def test_yes_and_no_together_yes_wins(self):
        with case({"par": [{"ts": "m1", "reactions": [NO, YES]}]}) as c:
            posted()
            run.followup()
            labels = run._reaction_labels()
        r = c.rows("resolved")[0]
        self.assertEqual((r["by"], r["no_by"]), ("reaction_yes", ["U2"]))
        self.assertEqual(len(c.rows("outreach")), 1)
        self.assertEqual(labels["in/a"]["label"], "yes")

    def test_reposted_card_is_read_again(self):
        """한 번 결론 난 사람도 다시 올리면(resurfaced) 대기줄에 다시 선다 — 키 차집합이면 영영 못 읽는다."""
        with case({"par2": [{"ts": "m2", "reactions": [YES]}]}) as c:
            ledger.append("linkedin", "in/a", run.POSTED, ts="2026-09-01T02:00:00Z", ch="net", slack_ts="m1", parent="par",
                          day="2026-09-01", page_id="p1", name="A", company="Aco", brief="b", url="https://www.linkedin.com/in/a")
            ledger.append("linkedin", "in/a", run.RESOLVED, ts="2026-09-08T02:00:00Z", by="expiry")
            ledger.append("linkedin", "in/a", run.POSTED, ts="2026-10-13T02:00:00Z", ch="net", slack_ts="m2", parent="par2",
                          day=ledger.today(), page_id="p1", name="A", company="Aco", brief="b", gk="in/a@2026-10-12",
                          url="https://www.linkedin.com/in/a")
            run.followup()
        self.assertEqual([r["by"] for r in c.rows("resolved")], ["expiry", "reaction_yes"])
        self.assertIn(("in/a@2026-10-12", "outreach"), c.acts())             # 다시 올린 카드는 문 키가 다르다

    def expired(self, day):
        posted(day=day)
        ledger.append("linkedin", "in/a", run.RESOLVED, by="expiry")

    def test_late_yes_gets_draft_and_label_once_a_day(self):
        """만기 뒤에 누른 ✅ — 문안·승격·리마인드가 자동으로(사람이 챙기지 않는다). 훑기는 하루 한 번."""
        with case({"par": [{"ts": "m1", "reactions": [YES]}]}) as c:
            self.expired(day=ledger.today())
            run.followup()
            run.followup()
        rs = c.rows("resolved")
        self.assertEqual([(r["by"], r.get("late")) for r in rs], [("expiry", None), ("reaction_yes", True)])
        self.assertEqual(len(c.rows("outreach")), 1)
        self.assertEqual(len(c.rows(run.SWEPT)), 1)

    def test_late_no_fixes_label_only(self):
        with case({"par": [{"ts": "m1", "reactions": [NO]}]}) as c:
            self.expired(day=ledger.today())
            run.followup()
            labels = run._reaction_labels()
        self.assertEqual([r["by"] for r in c.rows("resolved")], ["expiry", "reaction_no"])
        self.assertEqual((labels["in/a"]["label"], c.rows("outreach")), ("no", []))

    def test_old_cards_are_not_swept(self):
        with case({"par": [{"ts": "m1", "reactions": [YES]}]}) as c:
            self.expired(day="2026-08-01")
            run.followup()
        self.assertEqual(c.rows("outreach"), [])

    def test_x_marks_the_day_seen_so_neighbors_become_not_picked(self):
        """❌ 는 '이번 주엔 관심 없음' — 같은 날 안 고른 카드와 뜻이 같다. 그날을 '본 날'로 만들어 옆 카드도 정답(안 고름)이 된다."""
        with case({}):
            for k, by in (("in/a", "reaction_no"), ("in/b", "expiry")):
                ledger.append("linkedin", k, run.POSTED, ch="net", slack_ts=k, parent="par", day="2026-09-10", page_id=k)
                ledger.append("linkedin", k, run.RESOLVED, by=by)
            labels = run._reaction_labels()
        self.assertEqual({k: (v["label"], v["pressed_no"]) for k, v in labels.items()},
                         {"in/a": ("no", True), "in/b": ("no", False)})

    def test_no_reaction_is_not_a_label(self):
        with case({"par": [{"ts": "m1", "reactions": []}]}):
            posted(day="2026-09-10")
            run.followup()
            self.assertEqual(run._reaction_labels(), {})


if __name__ == "__main__":
    unittest.main()
