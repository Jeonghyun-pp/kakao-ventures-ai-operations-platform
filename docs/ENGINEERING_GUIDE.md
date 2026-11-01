# Engineering decisions and developer workflow

The product serves an investment team. The reusable engineering work is the runtime beneath those tasks: a common CLI, provider adapters, action state and comparable model judgments. This guide follows three changes a developer actually makes, with links to the implementation and the historical changes that introduced it.

## 1. Add a workflow without copying its runtime

The first workflows carried their own provider code. When a second workflow needed the same integrations, [72aa673](https://github.com/Jeonghyun-pp/kakao-ventures-ai-operations-platform/commit/72aa673e301f984195a832111d8f170dbf872a06) moved the adapters into `kv_transport/`, kept domain prompts in the skill, and replaced a central writable-database list with registration by each caller. The commit documents why extraction followed actual reuse rather than an anticipated framework.

Later, [eaf22a2](https://github.com/Jeonghyun-pp/kakao-ventures-ai-operations-platform/commit/eaf22a2a663b7a291ceeafae3562a01785667c14) extracted mail’s database, action gate and reporting into shared modules. [133f427](https://github.com/Jeonghyun-pp/kakao-ventures-ai-operations-platform/commit/133f4274c4272a90b23a71b50527d11503206b3f) moved LinkedIn’s judgment store into `kvlib`, generalized `tier` to `verdict`, and added adapters for label vocabulary and comparison buckets. These diffs show actual relocation and caller changes, not a reconstructed before/after benchmark.

Today a developer can follow [App Store](../skills/appstore/run.py), [LinkedIn](../skills/linkedin/run.py) and [business-plan screening](../skills/bplan/run.py): each supplies task-specific collection and judging while sharing `Gate`, `judgments.save`, `replay` and `diff`.

| Where the change belongs | What the developer supplies | What is reused |
|---|---|---|
| `skills/<task>/` | Input normalization, decision vocabulary, criteria, action/approval policy | Shared adapters and runtime modules |
| `kvlib/judgments.py` caller | Stable record key, exact input, criteria hash, parsed/raw output | Schema, missing-output records, replay and comparison |
| `kvlib/gate.py` caller | Action key/kind, rollout task, allowed Slack kinds, action callback | `dry`/`failed`/`done` state and completed-action check |
| Skill documentation and tests | Commands, expected behavior, fake provider inputs | Common CLI dispatch and CI |

The CLI [kv](../kv) dispatches to an existing `skills/<name>/run.py`. That removes a second list of per-skill entrypoints. Adding a new external adapter still requires an explicit architecture-check allow-list change; new credentials, schemas and scheduler entries are not created automatically. There is no claim that adding a skill is configuration-only.

**Developer improvement:** changes to shared action bookkeeping or comparison behavior have one implementation, while domain decisions stay in their own skill. Tests such as [test_linkedin_gate.py](../tests/test_linkedin_gate.py) exercise the shared boundary through a real caller.

## 2. Investigate a failure without rerunning everything

A process can exit successfully with no usable output. A model can return a malformed batch. An external action can fail after a valid judgment. Treating all three as “the workflow failed” makes it difficult to choose what to retry.

The [Claude adapter](../kv_transport/claude.py) documents an actual malformed JSON batch that motivated line-oriented results, and distinguishes process errors from empty output. Output parsing remains with the task. The workflow stores missing judgments as `ok=0` rather than dropping them. The action gate records a failed callback separately from its judgment.

| Symptom | Inspect first | Next action |
|---|---|---|
| No model output | Adapter error/usage record | Resolve invocation or authentication failure before judging again |
| One missing judgment | `judgments.ok`, `error`, saved input and raw output | Inspect the failed case; do not treat it as a negative verdict |
| Valid judgment, failed action | `Gate.row(key, kind)` status/error | Resolve the action failure, then retry under the workflow’s policy |
| Repeated completed action | Existing `(key, kind)` `done` record | Confirm the callback is skipped in sequential retry |

`python -m demo.run_demo` exercises the last two rows with an in-memory database and a fake delivery callback. [test_public_demo.py](../tests/test_public_demo.py) also removes approval and verifies zero attempts. Approval is enforced by the demo workflow before calling `Gate`; putting `approved_by` in action metadata alone grants nothing.

**Developer improvement:** the same input and separate records identify which boundary failed. A developer can investigate an action without asking the model to repeat its judgment or contacting the source again.

**Limit:** `Gate.out` checks a record, invokes an external effect, then writes local state. Concurrent callers or a crash after the external effect and before recording it can duplicate that effect. Provider idempotency keys or transactional coordination would be required for a stronger guarantee. The example proves sequential completed-action suppression only.

## 3. Change criteria without losing the comparison

The store saves input, criteria hash, raw output and verdict. A replay reads those saved inputs into a separate `r-<hash>` run; it does not overwrite `live`. Each skill maps its own vocabulary into comparison buckets. A real replay may call a model and incur cost even though it does not recollect source data.

One historical error matters especially: human rows initially recorded only disagreements. That made the apparent reference set a set of failures rather than all labeled cases. [995b925](https://github.com/Jeonghyun-pp/kakao-ventures-ai-operations-platform/commit/995b9251115d6b4b2da8b0a1ee7ae39107bf2e04) changed `label()` to retain every available decision, including agreement. [test_linkedin_judgments.py](../tests/test_linkedin_judgments.py) covers the label being retained when a reviewer agrees again.

The [evaluation walkthrough](EVALUATION.md) uses these actual functions with six invented records. It keeps the denominator visible, shows individual changed cases, and rejects a candidate whose higher agreement introduces a costly false negative. A second test drops that case entirely: an inner join would appear to give perfect agreement, but coverage inspection still holds the change.

**Developer improvement:** a criteria edit has a reproducible input set and an inspectable diff. The developer can distinguish a changed decision, a missing output and a missing human label before making a rollout decision. This evaluates the judgment component; it is not a complete benchmark of every skill’s tool use, latency or business outcome.

## Ownership and AI-assisted development

I defined the investment workflows with users, translated operating rules into criteria and release boundaries, and owned implementation, testing and iteration. I used Claude Code and OpenAI Codex for coding assistance. The design choices I am responsible for include separating collection from judgment and action, making failures inspectable, extracting common behavior after real reuse, and deciding what required human review before rollout.

The linked commits and tests show how the code changed. They do not establish that every line was written manually or independently verify my individual contribution; this statement describes my role. Team feedback and business results are separate from the executable evidence here.

The offline walkthrough, six-case fixture and its report were added for this public release. They are new explanatory checks using the existing runtime, not recovered production evaluations. Original operating records remain private. The current tests and [verification scope](VERIFICATION.md) identify what a reader can reproduce.
