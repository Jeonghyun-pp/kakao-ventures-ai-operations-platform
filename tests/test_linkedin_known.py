"""이미 본 팀 거르기 [2026-10-21] — 스타트업 DB 회사명 대조(카드에서 뺌) · 노션 본문 검색(⚠️ 표시만)."""
from __future__ import annotations

import unittest
from unittest import mock

from kvlib import Fetched
from kv_transport import claude
from skills.linkedin import history, known, recheck, run
from tests._linkedin_fakes import row
from tests.test_linkedin_card import case


class Match(unittest.TestCase):
    """10/21 실측(카드 197장)에서 나온 쌍 — 맞아야 할 것과 걸리면 안 되는 것."""

    def test_true_pairs(self):
        for aliases, title in ((["ExampleLab", "예시랩"], "예시랩AI"), (["SECA"], "SECA Labs"), (["Step Up", "스텝업"], "스텝업 STEP UPS"),
                               (["Out of Set"], "Out of set"), (["예시랩"], "예시랩(ExampleLab)"), (["ExampleLab"], "예시랩(ExampleLab)"),
                               (["레드브릭하우스"], "(주)레드브릭하우스"), (["a2sys"], "a2sys Inc.")):
            with self.subTest(title=title):
                self.assertTrue(known.matches(aliases, title))

    def test_partial_overlaps_are_not_matches(self):
        for aliases, title in ((["METAL"], "Sun Metalon (USA)"), (["SECA"], "P4H Global (Visecare)"),
                               (["ExampleLab"], "ExampleLabon Healthcare Group")):
            with self.subTest(title=title):
                self.assertFalse(known.matches(aliases, title))

    def test_short_names_are_not_compared(self):
        """`AI`·`랩` 같은 한두 글자로는 수백 행이 걸린다."""
        self.assertFalse(known.matches(["AI"], "AI"))
        self.assertFalse(known.matches(["랩"], "랩"))
        self.assertFalse(known.matches(["예시랩"], "예시랩온 헬스케어그룹"))           # 한글엔 꼬리를 안 준다 — 잘못 빼는 쪽이 비싸다
        self.assertFalse(known.matches(["Sume"], "Sumeria"))
        self.assertTrue(known.matches(["Sume"], "Sume"))


class Check(unittest.TestCase):
    def test_aliases_and_lookup(self):
        seen = {}

        def query(db, props, **kw):
            seen["filter"] = kw["filter"]
            return Fetched.measured([{"_page_id": "s1", "회사명": "예시랩AI", "패밀리": False},
                                     {"_page_id": "s2", "회사명": "예시랩온 헬스케어그룹", "패밀리": True}])
        with mock.patch.object(known.claude, "run_session", lambda *a, **k: "ExampleLab\t예시랩\n"), \
                mock.patch.object(known.notion, "query", query):
            got = known.check([("in/a", "ExampleLab"), ("in/b", "(미상)")]).require()
        self.assertEqual(got["in/a"]["aliases"], ["ExampleLab", "예시랩"])
        self.assertEqual([h["title"] for h in got["in/a"]["hits"]], ["예시랩AI"])         # 넓게 받고 코드가 거른다
        self.assertEqual(got["in/b"], {"aliases": [], "hits": []})                       # 회사 미상은 대조하지 않는다
        self.assertEqual([x["title"]["contains"] for x in seen["filter"]["or"]], ["ExampleLab", "예시랩"])

    def test_alias_failure_still_compares_original_name(self):
        def boom(*a, **k):
            raise claude.SessionError("x")
        with mock.patch.object(known.claude, "run_session", boom), \
                mock.patch.object(known.notion, "query", lambda *a, **k: Fetched.measured([{"_page_id": "s", "회사명": "ExampleLab", "패밀리": False}])):
            self.assertEqual(len(known.check([("in/a", "ExampleLab")]).require()["in/a"]["hits"]), 1)

    def test_notion_failure_is_failure(self):
        with mock.patch.object(known.claude, "run_session", lambda *a, **k: "ExampleLab\n"), \
                mock.patch.object(known.notion, "query", lambda *a, **k: Fetched.unknown("notion:망")):
            self.assertFalse(known.check([("in/a", "ExampleLab")]).is_measured)


def hit_for(company):
    def check(people):
        return Fetched.measured({k: {"aliases": [c], "hits": [{"title": c + "AI", "family": True, "page_id": "s"}] if c == company else []}
                                 for k, c in people})
    return check


class CardKnown(unittest.TestCase):
    def test_known_team_is_dropped_and_next_person_fills_the_slot(self):
        rows = [row("p1", "https://www.linkedin.com/in/a", 회사="ExampleLab"), row("p2", "https://www.linkedin.com/in/b"),
                row("p3", "https://www.linkedin.com/in/c")]
        with case(rows, cfg={"linkedin.card_known": True}, **{"known.check": hit_for("ExampleLab")}) as c:
            self.assertEqual(run.card(n=2), 0)
        replies = [p[1] for p in c.slack.posts if p[0] == "net" and p[2]]
        self.assertEqual(len(replies), 2)
        self.assertFalse(any("이름p1" in r for r in replies))
        self.assertIn("이미 본 팀이라 뺐다 — 이름p1 (ExampleLab) = 스타트업 DB ExampleLabAI(패밀리)", c.out.getvalue())
        self.assertEqual(c.notion.updated, [("p1", {"Status": run.LEGACY})])                 # 다음 날 다시 대조하지 않게
        self.assertEqual(c.rows("known")[0]["hits"], ["ExampleLabAI"])

    def test_unreadable_startup_db_stops_the_card(self):
        with case([row("p1")], cfg={"linkedin.card_known": True},
                  **{"known.check": lambda people: Fetched.unknown("스타트업 DB 를 못 읽었다")}) as c:
            self.assertEqual(run.card(n=5), 1)
        self.assertEqual([p for p in c.slack.posts if p[0] == "net"], [])

    def test_dry_run_checks_but_writes_nothing(self):
        with case([row("p1", 회사="ExampleLab"), row("p2", "https://www.linkedin.com/in/b")], cfg={"linkedin.card_known": True},
                  **{"known.check": hit_for("ExampleLab")}) as c:
            run.card(n=5, dry_run=True)
        self.assertEqual((c.notion.updated, c.rows("known")), ([], []))
        self.assertIn("이미 본 팀이라 뺐다", c.out.getvalue())


class History(unittest.TestCase):
    RAW = ("https://app.notion.com/p/000000000000000000008bfb9dd30d44\tExample Startup 주주동의 관련 검토 사항\t회의록\t김창업 대표 사임 — 이전 경력 일치\n"
           "https://app.notion.com/p/000000000000000000007c6b439fce8f\t창업 김\t그물망 링크드인 소싱 DB(링크드인)\t자기 행\n")

    def test_parse_drops_own_row_and_strips_slack_breakers(self):
        got = history.parse(self.RAW + "https://app.notion.com/p/aaaa\t<!channel> 제목|x\t곳\t단서\n")
        self.assertEqual([h["title"] for h in got], ["Example Startup 주주동의 관련 검토 사항", "제목x"])
        self.assertEqual(history.parse("없음"), [])
        self.assertIsNone(history.parse("현재 세션에는 Notion 검색 도구가 연결되어 있지 않아…"))     # 커넥터 없음 = 실패

    def test_parse_drops_own_page_by_id(self):
        got = history.parse("https://app.notion.com/p/000000000000000000007c6b439fce8f\t창업 김\t다른 곳\t단서\n",
                            own_page="00000000-0000-0000-0000-7c6b439fce8f")
        self.assertEqual(got, [])

    def test_flag_is_shown_and_failure_does_not_block(self):
        rows = [row("p1", "https://www.linkedin.com/in/a"), row("p2", "https://www.linkedin.com/in/b")]
        hits = {"in/a": history.parse(self.RAW), "in/b": None}
        with mock.patch.object(recheck, "raw_by_key", lambda: {}), \
                case(rows, cfg={"linkedin.card_history": True}, **{"history.write": lambda people: {p["key"]: hits[p["key"]] for p in people}}) as c:
            self.assertEqual(run.card(n=2), 0)
        replies = [p[1] for p in c.slack.posts if p[0] == "net" and p[2]]
        a = next(r for r in replies if "이름p1" in r)
        self.assertIn("⚠️ 노션 기존 기록: <https://app.notion.com/p/000000000000000000008bfb9dd30d44|Example Startup 주주동의 관련 검토 사항> (회의록)"
                      " — 김창업 대표 사임 — 이전 경력 일치\n", a)
        self.assertNotIn("⚠️", next(r for r in replies if "이름p2" in r))
        self.assertEqual(c.rows("posted")[0]["history"][0]["title"], "Example Startup 주주동의 관련 검토 사항")

    def test_all_failed_alerts_once_and_cards_still_go(self):
        rows = [row("p1", "https://www.linkedin.com/in/a")]
        with mock.patch.object(recheck, "raw_by_key", lambda: {}), \
                case(rows, cfg={"linkedin.card_history": True}, **{"history.write": lambda people: {p["key"]: None for p in people}}) as c:
            self.assertEqual(run.card(n=1), 0)
        self.assertTrue(any(p[0] == "test" and "노션 기존 기록 검색 꺼짐" in p[1] for p in c.slack.posts))
        self.assertEqual(len([p for p in c.slack.posts if p[0] == "net" and p[2]]), 1)

    def test_exception_does_not_block(self):
        def boom(people):
            raise RuntimeError("x")
        with mock.patch.object(recheck, "raw_by_key", lambda: {}), \
                case([row("p1")], cfg={"linkedin.card_history": True}, **{"history.write": boom}) as c:
            self.assertEqual(run.card(n=1), 0)


class Transport(unittest.TestCase):
    def test_notion_search_allows_only_the_search_tool(self):
        seen = {}

        def spawn(cmd, prompt, timeout, cwd=None):
            seen["cmd"] = cmd
            return mock.Mock(stdout="없음", stderr="", returncode=0)
        with mock.patch.object(claude, "_spawn", spawn), mock.patch.object(claude, "_record", lambda *a: None), \
                mock.patch.object(claude, "_calls_today", lambda: 0):
            claude.notion_search("p", system="s", timeout_sec=10)
        cmd = seen["cmd"]
        self.assertEqual(cmd[cmd.index("--allowedTools") + 1], "mcp__claude_ai_Notion__notion-search")
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "dontAsk")
        self.assertIn("Bash", cmd[cmd.index("--disallowedTools") + 1])


if __name__ == "__main__":
    unittest.main()
