"""판단이 필요한 메일의 흐름 — 슬랙에 묻고, **기다리고**, 결정이 오면 다음 행동을 한다. (LangGraph)

    post ──▶ wait ──▶ act ──▶ 끝
    묻는다    멈춘다    결정대로 (⭕ 접수 안내 초안 · ❌ 아카이브)
             └ 며칠이든 멈춰 있다. 상태는 SQLite 체크포인트에 있고, 다음 실행(다른 프로세스)이 잇는다

## 🔴 밖으로 나가는 문은 `_out` 하나이고, 기본은 닫혀 있다

`constraints.yaml  mail_observe.outbound: off` 인 동안 슬랙 게시도 초안 생성도 아카이브도 **하지 않는다.**
무엇을 했을지만 `actions` 에 `dry` 로 적는다. 여는 건 사람이 그 값을 `on` 으로 바꿀 때뿐이다.
열려도 게시 채널은 `route.POST_CH`(지금 test)이고, 메일은 **초안까지**다 — 발송 경로는 이 리포에 없다(A8).

## 왜 부작용이 wait 앞에 있나

LangGraph 는 재개할 때 **멈춘 노드를 처음부터 다시 돈다.** 게시를 wait 안에 두면 재개할 때마다 또 올린다.
그래서 post 와 wait 를 가르고, `actions` 의 기본키(메일+종류)로 한 번 더 막는다.

## 정책은 현행 그대로다 [사용자 결정 2026-09-21]

대상 갈래·질문 문구·템플릿은 `route.decide()` 에서 가져온다. 다른 건 하나 — **무응답을 거절로 바꾸지 않는다.**
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import TypedDict

os.environ.setdefault("LANGSMITH_TRACING", "false")             # 메일 내용이 추적 서비스로 나가지 않게

from langgraph.checkpoint.sqlite import SqliteSaver             # noqa: E402
from langgraph.graph import END, START, StateGraph              # noqa: E402
from langgraph.types import Command, interrupt                  # noqa: E402

from kvlib import constraints                                   # noqa: E402

from . import route, rules                                      # noqa: E402

ASK = (rules.EVENT_SPEAKER, rules.TRUSTED_REF)                  # route.decide 가 'slack' 으로 보내는 갈래
YES_DRAFT = {"trusted_ref": "trusted_direct_ko"}                # ⭕ 에 나가는 초안. event 의 ⭕ 는 사람이 잇는다(일정 조율은 범위 밖)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS actions(
  message_id TEXT, kind TEXT, status TEXT, detail TEXT, created TEXT,
  PRIMARY KEY(message_id, kind));
"""


class State(TypedDict, total=False):
    mid: str
    kind: str          # event | trusted_ref
    decision: str      # yes | no
    by: str


class Flow:
    """`io` 는 밖에 닿는 손이다: slack_post(ch, text)->ts · draft_reply(mid, to, body)->id · archive(mid). 테스트는 가짜를 넣는다."""

    def __init__(self, con, db_file: str, io, *, now):
        self.con, self.io, self.now = con, io, now
        con.executescript(_SCHEMA)
        g = StateGraph(State)
        g.add_node("post", self._post)
        g.add_node("wait", lambda s: interrupt({"mid": s["mid"]}))      # 결정 dict 가 상태에 합쳐진다
        g.add_node("act", self._act)
        g.add_edge(START, "post")
        g.add_edge("post", "wait")
        g.add_edge("wait", "act")
        g.add_edge("act", END)
        self._cp = sqlite3.connect(db_file, check_same_thread=False)    # 체크포인트는 같은 DB 파일에 — 백업이 한 덩어리다
        self.app = g.compile(checkpointer=SqliteSaver(self._cp))

    def close(self) -> None:
        self._cp.close()

    # ---- 밖으로 나가는 유일한 문 ------------------------------------------------
    def _out(self, mid: str, kind: str, detail: dict, do) -> None:
        if self.con.execute("SELECT 1 FROM actions WHERE message_id=? AND kind=? AND status='done'", (mid, kind)).fetchone():
            return                                                       # 이미 했다
        status, extra = "dry", {}
        if constraints.get("mail_observe.outbound") == "on":
            try:
                extra, status = {"result": do()}, "done"
            except Exception as e:                                       # 실패는 적고 넘어간다 — 다음 실행이 다시 시도한다
                extra, status = {"error": f"{type(e).__name__}: {e}"}, "failed"
        self.con.execute("INSERT OR REPLACE INTO actions VALUES(?,?,?,?,?)",
                         (mid, kind, status, json.dumps({**detail, **extra}, ensure_ascii=False), self.now()))

    def _msg(self, mid: str) -> tuple[dict, sqlite3.Row]:
        row = self.con.execute(
            "SELECT m.*, j.category, j.reason FROM messages m JOIN judgments j ON j.message_id=m.id AND j.run='live'"
            " WHERE m.id=?", (mid,)).fetchone()
        m = json.loads(row["raw_json"])
        return {**m, "sender": row["sender"], "subject": row["subject"]}, row

    def _post(self, s: State) -> dict:
        m, row = self._msg(s["mid"])
        act = route.decide(m, {"category": row["category"], "why": row["reason"]})
        text = route._post_text(m, act["lead"], act["ask"], summary=row["reason"])   # 요약은 분류가 이미 쓴 한 문장
        self._out(s["mid"], "post", {"ch": act["ch"], "text": text}, lambda: self.io.slack_post(act["ch"], text))
        return {"kind": act["kind"]}

    def _act(self, s: State) -> dict:
        m, _ = self._msg(s["mid"])
        tpl = YES_DRAFT.get(s["kind"]) if s["decision"] == "yes" else None
        if tpl:
            body = route.tpl(tpl, google_form=route.GOOGLE_FORM)
            self._out(s["mid"], "draft", {"to": m["sender"], "template": tpl},
                      lambda: self.io.draft_reply(s["mid"], m["sender"], body))
        elif s["decision"] == "no":
            self._out(s["mid"], "archive", {}, lambda: self.io.archive(s["mid"]))     # 현행과 같다: ❌ 는 회신 없이 아카이브
        else:
            self._out(s["mid"], "handoff", {"note": "⭕ — 사람이 잇는다"}, lambda: None)
        return {}

    # ---- 실행마다 부른다 --------------------------------------------------------
    def _cfg(self, mid: str) -> dict:
        return {"configurable": {"thread_id": mid}}

    def waiting(self) -> list[str]:
        ids = [r[0] for r in self.con.execute("SELECT message_id FROM actions WHERE kind='post'")]
        return [i for i in ids if self.app.get_state(self._cfg(i)).next == ("wait",)]

    def advance(self) -> dict:
        """새로 물을 메일을 시작하고, 게시된 글의 반응을 읽어 결정이 난 것을 잇는다."""
        new = [r[0] for r in self.con.execute(
            "SELECT j.message_id FROM judgments j WHERE j.run='live' AND j.ok=1 AND j.category IN (?,?)"
            " AND NOT EXISTS(SELECT 1 FROM actions a WHERE a.message_id=j.message_id AND a.kind='post')", ASK)]
        for mid in new:
            self.app.invoke({"mid": mid}, self._cfg(mid))
        decided = 0
        for mid in self.waiting():
            # 문이 닫혀 있을 때 'dry' 로만 적힌 질문은, 문이 열리면 그때 올린다 (실패한 게시의 재시도도 같은 길)
            self._post({"mid": mid})
            d = self._read_decision(mid)
            if d:
                self.decide(mid, d, by="slack")
                decided += 1
        return {"asked": len(new), "decided": decided, "waiting": len(self.waiting())}

    def _read_decision(self, mid: str) -> str | None:
        row = self.con.execute("SELECT status, detail FROM actions WHERE message_id=? AND kind='post'", (mid,)).fetchone()
        if row["status"] != "done":                                      # 실제로 안 올렸으면 읽을 반응도 없다
            return None
        d = json.loads(row["detail"])
        names = set(self.io.slack_reactions(d["ch"], d["result"]))
        if names & set(constraints.get("slack.reaction_no")):
            return "no"
        if names & set(constraints.get("slack.reaction_yes")):
            return "yes"
        return None

    def decide(self, mid: str, decision: str, *, by: str) -> None:
        """결정을 넣어 멈춘 흐름을 잇는다. 슬랙 반응이 부르고, 사람이 손으로 부를 수도 있다."""
        if self.app.get_state(self._cfg(mid)).next != ("wait",):
            raise ValueError(f"기다리는 흐름이 아니다: {mid}")
        self.app.invoke(Command(resume={"decision": decision, "by": by}), self._cfg(mid))


def post_report(text: str, io=None) -> str:
    """하루 보고 요약을 슬랙에. 판단 질문의 문(`outbound`)과 **별개의 스위치**다 — 보고서만 먼저 연다."""
    if constraints.get("mail_observe.report_slack") != "on":
        return "꺼짐"
    try:
        (io or RealIO()).slack_post(constraints.get("mail_observe.report_channel"), text[:route._CHUNK])
        return "게시함"
    except Exception as e:                                       # 보고 실패가 관찰 실행을 죽이지 않는다. 파일은 남아 있다
        return f"실패({type(e).__name__}: {e})"


class RealIO:
    """진짜 통로. `_out` 이 문을 열었을 때만 불린다(반응 읽기는 예외 — 읽기다)."""

    def __init__(self):
        from kv_transport import gmail, slack
        from . import run                                        # noqa: F401  채널 등록이 거기 있다
        self.gmail, self.slack = gmail, slack

    def slack_post(self, ch: str, text: str) -> str:
        return self.slack.post(ch, text).require()

    def slack_reactions(self, ch: str, ts: str) -> list[str]:
        return [x.get("name") for x in self.slack.reactions(ch, ts).require()]

    def draft_reply(self, mid: str, to: str, body: str) -> str:
        return self.gmail.draft_reply(self.gmail.message(mid).require(), body, to).require()

    def archive(self, mid: str) -> bool:
        return self.gmail.archive(mid).require()
