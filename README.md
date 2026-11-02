# Investment Workflow Platform

A Python CLI and shared runtime for AI-assisted investment workflows. It separates source collection, model judgments, human review and external actions so developers can change one part without rebuilding every workflow’s execution and evaluation controls.

I built the underlying system with the Kakao Ventures investment team. This is a sanitized personal release, not an official or company-endorsed repository. It contains no applicant or production data. [My contribution and AI-assisted development](docs/ENGINEERING_GUIDE.md#ownership-and-ai-assisted-development).

## What changed for a developer

| Task | Earlier implementation | Current implementation and evidence |
|---|---|---|
| Add a workflow | Provider adapters and action bookkeeping lived inside individual skills | Domain logic stays in `skills/`; shared adapters, `Gate` and records are reused. [Actual extraction commits](docs/ENGINEERING_GUIDE.md#1-add-a-workflow-without-copying-its-runtime) |
| Investigate a failure | A completed process or missing result could be mistaken for a successful task | Inspect the model response, judgment error and action state separately. [Failure boundaries](docs/ENGINEERING_GUIDE.md#2-investigate-a-failure-without-rerunning-everything) |
| Change a decision rule | Evaluation was tied to one workflow’s vocabulary; agreement records initially kept only disagreements | Reuse stored inputs, keep all available human labels, compare changed cases and missing results. [Evaluation walkthrough](docs/EVALUATION.md) |

These are code-backed changes in where work is implemented and inspected, not measured claims about developer-hours saved. The [engineering guide](docs/ENGINEERING_GUIDE.md) links each change to its code, history and tests.

## Try it without credentials

Requires Python 3.12 and Git on macOS or Linux. Installation downloads Python packages; the two demos below make no API or model calls and use in-memory SQLite. No Gmail, Slack, Notion account, Claude CLI, browser install or `kv setup` is needed.

```sh
git clone https://github.com/Jeonghyun-pp/kakao-ventures-ai-operations-platform.git
cd kakao-ventures-ai-operations-platform
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m demo.run_demo
python -m demo.evaluate
```

Both commands print JSON. The fixtures and the judge are synthetic; the storage, replay and gate are the actual shared implementation.

| Command | Expected result | How to read it |
|---|---|---|
| `python -m demo.run_demo` | `action_statuses: ["failed", "done", "done"]`, `delivery_attempts: 2`, `delivered: ["company-001"]` | The first fake delivery fails. A retry succeeds. The third call returns the completed record without calling delivery again. Two unapproved records are withheld. |
| `python -m demo.evaluate` | `baseline_matches: 3`, `candidate_matches: 5`, `labeled: 6`, `decision: "hold"` | Better aggregate agreement still introduces a priority-to-decline error. Inspect the changed case before considering rollout. |

The fake delivery appends an ID to a local list; it sends nothing. The second example demonstrates evaluation mechanics, not LLM quality. [Expected cases, error handling and limits](docs/EVALUATION.md).

## Verify the implementation

```sh
python -m unittest discover -s tests -t .
CI=true python check_intent.py
```

`CI=true` checks schedule declarations without requiring this machine to have installed macOS jobs. The checker reports unsupported checks separately. Five private-fixture tests are skipped in a public clone; a skipped check is not evidence of a passing production test. [Verification scope](docs/VERIFICATION.md).

The [CI workflow](.github/workflows/ci.yml) runs the tests, architecture checks and both demos on Python 3.12. Dependencies are pinned in [requirements.txt](requirements.txt). The project uses a custom AST/structure checker; it does not claim a separate linter or Python type-checking pipeline.

## System shape

```text
Gmail / Sheets / Drive / public sources
                  |
       deterministic adapters         kv_transport/
                  |
       normalized records + ledger    kvlib/ledger.py
                  |
       versioned judgments            kvlib/judgments.py
          |       ^
          |       +-- stored-input replay / diff / human labels
          |
       workflow approval + rollout gate
                  |
       drafts / review cards / reports
                  |
       action state + health checks
```

A workflow owns the domain decision and approval check. `Gate` owns rollout levels and completed-action checks. It does not infer approval from a model result or make an external API call atomic with the local database write. [Architecture and failure controls](docs/ARCHITECTURE.md).

## Deploy with your own connections

After the offline walkthrough, follow [SETUP.md](docs/SETUP.md) for credentials, external schemas and rollout controls. `./kv setup` creates local configuration; `./kv doctor` checks it. Workflows cover email triage, application screening, founder sourcing, memo/document generation and operations. Each `skills/*/SKILL.md` describes its commands and operating contract.

New installations default to no outbound actions, including when local rollout settings are missing. Set rollout switches in `$KV_CONFIG/deployment.yaml`; `./kv doctor` reports local checks and explicitly enabled switches without certifying remote access. Begin with dry runs, inspect outputs and explicitly enable only the workflow you intend to operate. Optional document engines have their own dependencies. macOS scheduling is separate from the portable Python test/demo path.

## History

The Git graph retains both the earlier automation system and the later reliability-oriented rebuild. It includes the ADRs, failure notes, experiments, and simplification work that led to the current design. The 128 operational commits and all 174 commits from the earlier system retain their original parent relationships and author/committer timestamps. Files, policies, prompts, and tests are sanitized at their original commits. Recent commits contain the previously uncommitted operational work and public deployment adaptations. See [history and verification notes](docs/HISTORY.md).

```bash
git log --graph --decorate --oneline --all
git tag --list
```

Historical commits are preserved for design context, not as recommended deployment points. Start from `main`; older tags may require their original environment and may contain intentionally superseded designs.

## Documentation

- [Engineering decisions, developer tasks and ownership](docs/ENGINEERING_GUIDE.md)
- [Reproducible evaluation example](docs/EVALUATION.md)
- [Claim-to-evidence map](docs/CLAIM_COVERAGE.md)
- [Verification results and limits](docs/VERIFICATION.md)
- [Installation and external-service contract](docs/SETUP.md)
- [Architecture](docs/ARCHITECTURE.md) · [Operations](docs/OPERATIONS.md) · [Security](SECURITY.md)

MIT licensed. Third-party components retain their own licenses.
