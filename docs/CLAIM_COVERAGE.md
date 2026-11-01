# Claim-to-evidence map

| Claim | Code and repeatable evidence | Boundary |
|---|---|---|
| Several workflows reuse action bookkeeping and judgment evaluation | [Gate](../kvlib/gate.py), [judgments](../kvlib/judgments.py), callers in [bplan](../skills/bplan/run.py), [LinkedIn](../skills/linkedin/run.py), [App Store](../skills/appstore/run.py); [extraction history](ENGINEERING_GUIDE.md) | Reuse is observable in code; developer-time savings were not measured |
| A sequential retry skips an action already recorded as done | [demo](../demo/run_demo.py), [test](../tests/test_public_demo.py) | Not atomic with external APIs and not a concurrent exactly-once guarantee |
| Failed callbacks retain error state and remain retryable | [Gate.out / Gate.row](../kvlib/gate.py), [demo](../demo/run_demo.py) | The demo simulates failure before delivery, not a timeout after a remote success |
| A workflow can withhold unapproved actions | [demo](../demo/run_demo.py), [approval test](../tests/test_public_demo.py), [workflow follow-up tests](../tests/test_linkedin_followup.py) | Approval is caller policy; Gate enforces rollout/action state, not reviewer identity |
| Changed criteria can be compared on stored inputs | [replay / diff](../kvlib/judgments.py), [evaluation demo](../demo/evaluate.py), [judgment tests](../tests/test_linkedin_judgments.py) | Real replay may call a model; the example uses a deterministic stub |
| Reference labels are retained even when they agree | [label](../kvlib/judgments.py), [judgment tests](../tests/test_linkedin_judgments.py) | Agreement is not independent absolute accuracy |
| Missing outputs and high-cost changes remain visible in the example | [report](../demo/evaluate.py), [tests](../tests/test_public_evaluation.py) | The report checks coverage separately because diff joins only valid outputs |
| Collection failure is distinct from an empty measured result | [Fetched](../kvlib/fetched.py), [collection tests](../tests/test_linkedin_collect.py) | Provider fakes do not certify live integrations |
| External access is concentrated in explicit adapters | [adapters](../kv_transport), [architecture checker](../check_intent.py) | Static checks have explicit allow-lists and unsupported cases; they are not a complete security proof |
| Operational event records support restart and reporting | [ledger](../kvlib/ledger.py), [operations tests](../tests/test_ops.py) | The append-only event ledger is separate from replaceable SQLite action/judgment rows |

Personal contribution is described in the [engineering guide](ENGINEERING_GUIDE.md#ownership-and-ai-assisted-development). Business results and historical model scores are not reconstructed from private records. The [verification report](VERIFICATION.md) distinguishes executed checks, skipped private tests and untested live integrations.
