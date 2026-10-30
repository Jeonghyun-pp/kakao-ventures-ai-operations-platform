# Investment Workflow Platform

A sanitized, deployable version of the AI operations system I built with the Kakao Ventures investment team. It converts inbound applications and public founder signals into review queues, decision briefs, approval requests, controlled drafts, and operational reports. This is a personal project, not an official Kakao Ventures repository or company-endorsed release; it contains no applicant or production data.

The repository is meant to run, not only illustrate an architecture. A new investment team can clone it, supply its own Gmail, Google, Slack, Notion, and Apify connections, reproduce the documented database schemas, and enable workflows one at a time. New installations default to no outbound actions.

## System shape

```text
Gmail / Sheets / Drive / public sources
                  |
          deterministic adapters
                  |
       normalized records + ledger
                  |
       versioned AI judgments  <---- replay / diff / human labels
                  |
       policy and approval gate
                  |
    Slack / Notion / drafts / reports
                  |
       action ledger + health checks
```

Facts, model judgment, and external action are separate. High-consequence actions can remain behind human approval. Stable action keys, an append-only ledger, replayable judgments, retries, health checks, and alerts make failures visible and prevent a completed action from being silently repeated.

## Start from a fresh clone

```bash
git clone https://github.com/Jeonghyun-pp/kakao-ventures-ai-operations-platform.git
cd kakao-ventures-ai-operations-platform
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./kv setup
```

Edit the four files created under `${KV_CONFIG:-~/kv-config}`:

- `.env`: API tokens and credential-file paths;
- `connections.yaml`: database, channel, form, sheet, and organization identifiers;
- `people.yaml`: reviewers and their aliases;
- `deployment.yaml`: rollout gates, initially all closed.

Create the Notion properties in [docs/SETUP.md](docs/SETUP.md), then verify the local configuration without contacting an external service:

```bash
./kv doctor
./kv test
./kv check
python -m demo.run_demo
```

Run a workflow in dry-run/read-only mode first. Open only its `deployment.yaml` gate after reviewing replay results, proposed changes, and failure behavior. Commands and schedules are documented in each `skills/*/SKILL.md`.

## Workflow coverage

- inbound email collection, triage, escalation, draft creation, and archive controls;
- business-plan ingestion, scoring, review cards, blind human labels, and follow-up;
- LinkedIn and App Store founder sourcing, review queues, feedback, and reminders;
- investment-memo and document-generation lanes;
- shared scheduling, backups, health checks, and status reporting.

The nested memo engine has its own optional dependencies under `skills/memo/engine/`. The main platform and offline test suite do not require live credentials.

## Reliability evidence

- `kvlib/gate.py`: allow-listed rollout, dry runs, failure records, and idempotent actions;
- `kvlib/judgments.py`: versioned input/output, human labels, replay, and run diffs;
- `kvlib/ledger.py`: append-only operational state, crash-tail repair, and summaries;
- `kv_transport/`: bounded Gmail, Drive, Sheets, Slack, Notion, public-source, and model adapters;
- `demo/run_demo.py`: a network-free approval, retry, deduplication, and replay walkthrough;
- `tests/`: synthetic regression coverage for workflow and failure paths.

Business outcomes and historical evaluation counts are not reconstructed from private records. The executable evidence here supports the system-design claims without publishing confidential data. See [docs/CLAIM_COVERAGE.md](docs/CLAIM_COVERAGE.md).

## History

The Git graph retains both the earlier automation system and the later reliability-oriented rebuild. It includes the ADRs, failure notes, experiments, and simplification work that led to the current design. The 128 operational commits and all 174 commits from the earlier system retain their original parent relationships and author/committer timestamps. Files, policies, prompts, and tests are sanitized at their original commits. Recent commits contain the previously uncommitted operational work and public deployment adaptations. See [history and verification notes](docs/HISTORY.md).

```bash
git log --graph --decorate --oneline --all
git tag --list
```

Historical commits are preserved for design context, not as recommended deployment points. Start from `main`; older tags may require their original environment and may contain intentionally superseded designs.

## Documentation

- [Installation and external-service contract](docs/SETUP.md)
- [Architecture and failure controls](docs/ARCHITECTURE.md)
- [Claim-to-evidence map](docs/CLAIM_COVERAGE.md)
- [Operations](docs/OPERATIONS.md)
- [Security policy](SECURITY.md)

MIT licensed. Third-party components retain their own licenses.
