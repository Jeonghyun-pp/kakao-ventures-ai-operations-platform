# Architecture and failure controls

## Boundary 1: deterministic collection

External APIs are accessed through `kv_transport`. Collection normalizes identifiers and preserves the source response separately from model output. This prevents a model from becoming the only place where fetch, interpretation, and action are mixed.

## Boundary 2: versioned judgment

`kvlib.judgments` stores the exact input, criteria hash, raw output, parsed verdict, and error for each record. A changed prompt or decision rule creates a new run rather than overwriting the live result. Replays operate on stored inputs rather than refetching a changing inbox. The judging callback may still call a model API and incur cost. A controlled evaluation must also keep model settings and the input set fixed.

The current store keys replay runs by criteria hash, and `diff` includes only valid overlapping results. Inspect missing outputs and label coverage separately; see the [evaluation walkthrough](EVALUATION.md).

Human decisions are stored as a separate run. `diff` compares two system versions beside the human label, making regressions and high-cost false negatives visible before rollout.

## Boundary 3: controlled action

Consequential workflow actions use `kvlib.gate.Gate`; bounded transport adapters handle lower-level provider calls. The deployment level for gated actions is explicit:

- `off`: record a dry action only;
- `slack`: allow only action kinds on the workflow's Slack allow-list;
- `on`: execute the action.

Each action has a primary key of `(record key, action kind)`. A completed action is returned as already done on retries. Failed actions retain their error and remain retryable. New action types default closed at partial rollout levels because the policy is an allow-list, not a deny-list.

This is sequential completed-action suppression. The external effect and local state update are not atomic; concurrent calls or a crash between them can still duplicate an effect. Workflow code checks human approval before invoking the gate.

## Boundary 4: human approval

Workflows can publish a review card and read a reaction or structured decision before calling a consequential action. Investment decisions and external communication are not inferred from a successful model call. The approval becomes its own observable input.

## Boundary 5: operational truth

The append-only ledger provides deduplication keys, restart state, watermarks, and daily summaries. Writes are flushed to disk. A torn final JSONL record can be discarded, while corruption in the middle of the ledger fails loudly instead of hiding data loss.

Health checks and reports are derived from recorded actions and ledger rows. They expose incomplete runs, missing outputs, and adapter failures without asking a model to remember what happened.

## Workflow coverage

The retained source includes:

- inbound email collection, triage, escalation, draft creation, and archive controls;
- business-plan ingestion, scoring, review cards, blind labels, and follow-up;
- LinkedIn and App Store sourcing, review queues, feedback, and reminders;
- investment-memo and document-generation lanes;
- shared scheduling, backups, health checks, and status reporting.

Organization identifiers and credentials are local configuration. The required external schema contract is documented in `docs/SETUP.md`; public CI validates the reliability core and synthetic workflow paths without pretending to exercise third-party services.
