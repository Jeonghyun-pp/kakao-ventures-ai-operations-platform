"""밖으로 나가는 문 — 한 스킬의 모든 부작용이 지나는 자리. 무엇을 했(을)는지가 `actions` 에 남는다.

    constraints.yaml  <task>.outbound
      off     아무것도 안 한다. 무엇을 했을지만 dry 로 적는다
      slack   `is_slack(kind)` 인 종류만 실제로 (슬랙 글). 나머지는 dry            ← 시험은 여기서
      on      전부 실제로

🔴 `is_slack` 은 **허용 목록**이다. 여기 없는 종류(앞으로 생길 무엇이든)는 on 이 아니면 기록만 한다 —
   금지 목록으로 두면 새 종류를 만들며 깜빡했을 때 그게 그대로 나간다.
🔴 한 번 done 인 (key, kind) 는 다시 하지 않는다. 실패는 적고 넘어간다 — 다음 실행이 다시 시도한다.
`force="off"` 는 한 실행만 문을 내린다(= --dry-run).
"""
from __future__ import annotations

import json
from collections.abc import Callable

from . import constraints, ledger

SCHEMA = """
CREATE TABLE IF NOT EXISTS actions(
  key TEXT, kind TEXT, status TEXT, detail TEXT, created TEXT,
  PRIMARY KEY(key, kind));
"""


class Gate:
    def __init__(self, con, task: str, is_slack: Callable[[str], bool] = lambda k: False, *,
                 now: Callable[[], str] = ledger.now, force: str | None = None):
        self.con, self.task, self.is_slack, self.now, self.force = con, task, is_slack, now, force
        con.executescript(SCHEMA)

    def level(self) -> str:
        return self.force or constraints.get(f"{self.task}.outbound")

    def out(self, key: str, kind: str, detail: dict, do: Callable[[], object]) -> str:
        if self.con.execute("SELECT 1 FROM actions WHERE key=? AND kind=? AND status='done'", (key, kind)).fetchone():
            return "done"                                                # 이미 했다
        level = self.level()
        status, extra = "dry", {}
        if level == "on" or (level == "slack" and self.is_slack(kind)):
            try:
                extra, status = {"result": do()}, "done"
            except Exception as e:                                       # noqa: BLE001 — 실패는 적고 넘어간다
                extra, status = {"error": f"{type(e).__name__}: {e}"}, "failed"
        self.put(key, kind, status, {**detail, **extra})
        return status

    def put(self, key: str, kind: str, status: str, detail: dict) -> None:
        self.con.execute("INSERT OR REPLACE INTO actions VALUES(?,?,?,?,?)",
                         (key, kind, status, json.dumps(detail, ensure_ascii=False, default=str), self.now()))

    def row(self, key: str, kind: str) -> dict | None:
        r = self.con.execute("SELECT status, detail, created FROM actions WHERE key=? AND kind=?", (key, kind)).fetchone()
        return {"status": r[0], "created": r[2], **json.loads(r[1])} if r else None
