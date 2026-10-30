# Claim-to-evidence map

| Engineering claim | Public evidence |
|---|---|
| Retries do not duplicate completed side effects | `kvlib/gate.py`, `tests/test_public_demo.py` |
| Failures are recorded and remain retryable | `kvlib/gate.py`, `demo/run_demo.py` |
| Model behavior can be replayed on fixed historical inputs | `kvlib/judgments.py`, `demo/run_demo.py` |
| Human labels remain separate from model output | `kvlib/judgments.py`, `skills/bplan/blind.py` |
| Consequential actions can require explicit human approval | `kvlib/gate.py`, `demo/run_demo.py`, workflow follow-up modules under `skills/` |
| Crash recovery and audit summaries derive from an append-only ledger | `kvlib/ledger.py` |
| External systems are isolated behind bounded adapters | `kv_transport/` |
| Incomplete runs and exceptions are surfaced operationally | `skills/ops/`, `kvlib/report.py`, `kvlib/gate.py` |

Business outcomes and evaluation counts mentioned in application materials are not recreated from production records because those records are private. This repository supports the architectural claims with synthetic, executable checks; it does not use confidential data as public proof. The idempotency claim is scoped to actions routed through the action ledger, not to distributed exactly-once delivery by third-party APIs.
