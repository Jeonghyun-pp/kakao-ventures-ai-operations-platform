"""Synthetic proof of retry safety, approval control, and judgment replay."""
from __future__ import annotations

import json
import sqlite3

from kvlib import Fetched, judgments
from kvlib.gate import Gate


RECORDS = {
    "company-001": "Enterprise security platform with recurring revenue",
    "company-002": "Consumer idea without a working product",
    "company-003": "Ambiguous infrastructure product; request human review",
}


def classify(text: str, version: str) -> str:
    if "Ambiguous" in text:
        return "review"
    if "recurring revenue" in text:
        return "priority"
    return "decline" if version == "v2" else "review"


def verdicts(lines: dict[str, str], version: str) -> list[dict]:
    return [
        {"key": key, "verdict": classify(text, version), "reason": f"criteria={version}", "raw": classify(text, version)}
        for key, text in lines.items()
    ]


def run(*, approved: frozenset[str] = frozenset({"company-001"})) -> dict:
    con = sqlite3.connect(":memory:")
    judgments.ensure(con)

    v1_sha = judgments.criteria_sha("v1")
    judgments.save(con, judgments.LIVE, v1_sha, RECORDS, verdicts(RECORDS, "v1"))
    judgments.save(
        con,
        judgments.HUMAN,
        "human",
        RECORDS,
        [
            {"key": "company-001", "verdict": "priority", "reason": "approved", "raw": "priority"},
            {"key": "company-002", "verdict": "decline", "reason": "reviewed", "raw": "decline"},
            {"key": "company-003", "verdict": "review", "reason": "needs context", "raw": "review"},
        ],
    )

    def replay_judge(lines: list[str], keys: list[str], _criteria: str) -> Fetched:
        return Fetched.measured(verdicts(dict(zip(keys, lines)), "v2"))

    v2_sha = judgments.criteria_sha("v2")
    replay = judgments.replay(con, replay_judge, "v2", v2_sha)
    changes = judgments.diff(con, judgments.LIVE, replay["run"])

    gate = Gate(con, "demo", force="on")
    attempts = 0
    delivered: list[str] = []

    def flaky_delivery(key: str) -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("synthetic timeout")
        delivered.append(key)
        return f"draft-{key}"

    # Approval belongs to the workflow; Gate only enforces rollout and action state.
    consequential = [key for key in RECORDS if key in approved]
    statuses = []
    for key in consequential:
        for _ in range(3):
            statuses.append(gate.out(key, "create_draft",
                                     {"approved_by": "reviewer"}, lambda: flaky_delivery(key)))

    result = {
        "replay": replay,
        "changed_judgments": sum(row["a"] != row["b"] for row in changes),
        "action_statuses": statuses,
        "delivery_attempts": attempts,
        "delivered": delivered,
        "approved_for_action": consequential,
        "withheld": [key for key in RECORDS if key not in approved],
    }
    con.close()
    assert result["delivery_attempts"] == (len(consequential) + 1 if consequential else 0)
    assert result["delivered"] == consequential
    assert result["changed_judgments"] == 1
    return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, ensure_ascii=False))
