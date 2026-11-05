"""Synthetic criteria comparison using the real judgment store, label, replay and diff.

The judge is a deterministic stub, not an LLM. No production records or accuracy
claims are reconstructed. Run with ``python -m demo.evaluate``.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

from kvlib import Fetched, judgments


CASES = {
    "revenue": ("Product with recurring revenue", "priority"),
    "prototype": ("Working prototype, no revenue yet", "priority"),
    "idea-a": ("Idea only: consumer utility", "decline"),
    "idea-b": ("Idea only: marketplace", "decline"),
    "idea-c": ("Idea only: productivity app", "decline"),
    "unclear": ("Insufficient information", "review"),
}
BASELINE = "Review incomplete cases; prioritize recurring revenue or a working prototype."
CANDIDATE = "Prioritize recurring revenue; decline all other sufficiently described cases."


def judge(lines: list[str], keys: list[str], criteria: str) -> Fetched:
    """Implement two deliberately simple policies so the comparison is reproducible."""
    if criteria not in (BASELINE, CANDIDATE):
        raise ValueError("Unknown demo criteria")
    rows = []
    for key, line in zip(keys, lines, strict=True):
        if "Insufficient" in line:
            verdict = "review"
        elif "recurring revenue" in line or (criteria == BASELINE and "Working prototype" in line):
            verdict = "priority"
        else:
            verdict = "review" if criteria == BASELINE else "decline"
        rows.append({"key": key, "verdict": verdict, "reason": criteria, "raw": verdict})
    return Fetched.measured(rows)


def evaluate(candidate_judge=judge) -> dict:
    with closing(sqlite3.connect(":memory:")) as con:
        judgments.ensure(con)
        inputs = {key: text for key, (text, _) in CASES.items()}
        judgments.save(con, judgments.LIVE, judgments.criteria_sha(BASELINE), inputs,
                       judge(list(inputs.values()), list(inputs), BASELINE).require())
        for key in inputs:
            judgments.set_page(con, key, key)
        judgments.label(con, [{"_page_id": key, "decision": label} for key, (_, label) in CASES.items()],
                        "decision", source="Synthetic reference labels, authored for this demo")
        replay = judgments.replay(con, candidate_judge, CANDIDATE, judgments.criteria_sha(CANDIDATE))
        comparison = judgments.comparison_report(con, judgments.LIVE, replay["run"], keys=list(inputs))
        rows = sorted(comparison["rows"], key=lambda row: row["key"])
        missing = sorted(comparison["b"]["failed"] + comparison["b"]["absent"])
        labeled = comparison["labeled"]
        baseline_matches = comparison["a_matches"]
        candidate_matches = comparison["b_matches"]
        new_misses = [row["key"] for row in rows
                      if row["human"] == "priority" and row["a"] != "decline" and row["b"] == "decline"]
        reasons = []
        if not comparison["complete"]:
            reasons.append("incomplete candidate results")
        if labeled != len(inputs):
            reasons.append("not every input has a comparable reference label")
        if new_misses:
            reasons.append("new priority-to-decline error")
        if candidate_matches <= baseline_matches:
            reasons.append("no agreement improvement on comparable labeled cases")
        return {
            "data": "synthetic", "judge": "deterministic stub, no model call",
            "inputs": len(inputs), "baseline_criteria": BASELINE, "candidate_criteria": CANDIDATE,
            "candidate_run": replay["run"], "compared": len(rows), "labeled": labeled,
            "baseline_matches": baseline_matches, "candidate_matches": candidate_matches,
            "missing": missing, "new_priority_misses": new_misses,
            "decision": "hold" if reasons else "review_for_rollout",
            "reasons": reasons, "cases": rows,
        }


if __name__ == "__main__":
    print(json.dumps(evaluate(), indent=2))
