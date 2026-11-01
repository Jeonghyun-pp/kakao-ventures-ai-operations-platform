# Compare criteria and inspect the errors

Run `python -m demo.evaluate` after the README installation steps. It uses the real `kvlib.judgments` store, `label`, `replay` and `diff`, with six invented inputs and a deterministic judge. It needs no credentials and does not estimate model accuracy.

## The decision under review

The baseline routes incomplete proposals to review and prioritizes a working prototype or recurring revenue. A candidate rule declines every sufficiently described case without recurring revenue. That reduces unnecessary review in this small fixture, but also loses a working prototype that the synthetic reviewer considers worth attention.

| Synthetic input | Reference label | Baseline | Candidate |
|---|---|---|---|
| Recurring revenue | priority | priority | priority |
| Working prototype without revenue | priority | priority | decline |
| Idea only, consumer utility | decline | review | decline |
| Idea only, marketplace | decline | review | decline |
| Idea only, productivity app | decline | review | decline |
| Insufficient information | review | review | review |

The fixture and policy text are in [demo/evaluate.py](../demo/evaluate.py). Reference labels are authored for the example. They are independent fields, but are not independent human research or a held-out test set.

## Read the report

The command prints each compared case and these summary fields:

```json
{
  "inputs": 6,
  "compared": 6,
  "labeled": 6,
  "baseline_matches": 3,
  "candidate_matches": 5,
  "missing": [],
  "new_priority_misses": ["prototype"],
  "decision": "hold",
  "reasons": ["new priority-to-decline error"]
}
```

This is an excerpt, not the entire output. A move from 3/6 to 5/6 agreement is insufficient: inspect the prototype case and revise the rule. The example’s review policy requires complete labeled comparisons, improved agreement and no new priority-to-decline errors. This is a demonstration policy, not an investment-team policy or automatic production release gate. Even when those checks pass, the result is `review_for_rollout`, never automatic deployment.

## Follow the data through the real implementation

1. `save(live, criteria_sha, inputs, verdicts)` stores the input and baseline output.
2. `set_page` and `label` attach all six reference decisions, including agreements. Historically, keeping only disagreements broke the denominator; see [the correction](https://github.com/Jeonghyun-pp/kakao-ventures-ai-operations-platform/commit/995b9251115d6b4b2da8b0a1ee7ae39107bf2e04).
3. `replay` gives the saved inputs and candidate criteria to the deterministic judge and saves a separate run.
4. `diff` joins valid results and attaches the reference label. The demo separately counts missing results before interpreting the joined comparison.
5. The report exposes the changed cases and the reason to hold the change. No source fetch or external action occurs.

## Failure cases worth testing

```sh
python -m unittest tests.test_public_evaluation tests.test_linkedin_judgments -v
```

- **Better average, harmful change:** the candidate matches five of six labels but loses the prototype. Hold.
- **Missing output:** omit the prototype result. The joined comparison matches five of five, but one input is missing. Hold.
- **No improvement:** returning the baseline outputs is not a reason to recommend rollout. Hold.
- **Corrected candidate:** preserving the prototype while fixing unnecessary review reaches six of six in the fixture. Recommend human rollout review only.

`test_linkedin_judgments.py` checks retained agreement labels, missing judgment records and replay using the original stored lines. The new public tests exercise the example’s interpretation on top of those functions.

## What this does and does not validate

The example validates bookkeeping and the interpretation of changed cases. The deterministic judge makes it repeatable without a model account; it cannot validate prompt quality, model variance or generalization. A real evaluation needs a representative dataset, label provenance, separation of prompt examples from evaluation cases, and a decision about asymmetric error costs.

The current store identifies replay runs by criteria hash, not a full model/version/parameter manifest. Existing entries in that run, including failures, are not automatically retried. `live` rows are replaceable and `diff` compares valid overlapping rows only. Freeze/export a real evaluation dataset and record model settings before using it as a benchmark. A changed input set or model configuration must not be described as a controlled criteria-only comparison.

The original mail evaluation records separate missed important mail from extra review, identify cases needing outside context, and mark examples already cited in criteria. Those private records informed this walkthrough’s scope; neither their contents nor performance figures are published here. Recorded-behavior regression, agreement with a reference label, and operational success remain different measurements.
