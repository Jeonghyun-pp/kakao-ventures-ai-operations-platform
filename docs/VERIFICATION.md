# Verification scope

Historical baseline checked on 2026-11-01 with a fresh Python 3.12.13 virtual environment on macOS 15.7.7. Dependencies were installed from `requirements.txt`. Demo and final test runs used temporary, initially absent `KV_CONFIG` and `KV_DATA` paths rather than a developer’s live configuration.

| Check | Result | What it establishes |
|---|---|---|
| `python -m unittest discover -s tests -t . -v` | 400 discovered, 395 passed, 5 skipped, no failures | Synthetic unit/regression paths, including the new evaluation and approval checks |
| `CI=true python check_intent.py` | 11 passed, 0 failed, 3 explicitly unsupported | Current AST/structure rules and schedule declarations; not installed jobs |
| `CI=true python tools/mutation_test.py` | 10/10 mutations detected; files restored | Selected guard violations cause the intended architecture check to fail |
| `python -m demo.run_demo` | One simulated timeout, two callback attempts, one delivery; unapproved records withheld | Sequential retries and workflow approval in an in-memory example |
| `python -m demo.evaluate` | Agreement 3/6 to 5/6; one new priority miss; `hold` | Case-level interpretation can reject an attractive aggregate |
| README commands, expected JSON fields and relative documentation links | Checked | The documented offline route matches the implementation |

The new demo test blocks socket connections and `subprocess.run` while executing both walkthroughs. This supports their offline execution path; it is not a network-isolation audit of every workflow. An additional adapter test confirms a zero-exit process with empty output is recorded as a failure.

## Skipped and unsupported checks

Five tests require private mail fixtures and intentionally skip in the public clone: one `RealForms` test in `test_mail_clean.py` and four `Recorded` tests in `test_mail_recorded.py`. They are not counted as passes.

The architecture checker labels A10, A11 and A12 separately. A10’s selected mutations were run with the separate command above. The checker does not automatically certify every test’s network isolation or every write’s separation from production data. Test setup supplies temporary configuration and data roots; provider fakes replace live services.

## Reproduce

After the README installation steps:

```sh
python -m unittest discover -s tests -t . -v
CI=true python check_intent.py
python -m demo.run_demo
python -m demo.evaluate
```

For the mutation check, use a disposable clean clone with its own `.venv`. The script temporarily edits selected source files and restores them in `finally` blocks:

```sh
CI=true python tools/mutation_test.py
```

The [GitHub Actions workflow](../.github/workflows/ci.yml) runs the suite, architecture checks and both demos on Ubuntu with Python 3.12. Its run status is available in the repository’s [Actions tab](https://github.com/Jeonghyun-pp/kakao-ventures-ai-operations-platform/actions). The local checks above are distinct from a hosted run.

No live Gmail, Slack, Notion, Apify or model calls were used to validate this change. Installed macOS schedules, remote API contracts, production model scores, optional document-engine/browser dependencies and business outcomes were not revalidated. For that November 1 baseline, runtime behavior was unchanged outside the public demo; the shared judgment-store and LinkedIn CLI changes corrected explanatory text only. Later runtime corrections are recorded below.

## Initial deployment regression check (2026-11-03, before date correction)

On the working tree based on `06c4c893`, the rollout/configuration and scheduler tests passed: 19 tests in `test_deployment`, `test_doctor` and `test_schedule`. They cover missing/partial deployment files, explicit activation, optional mail category restrictions, local doctor diagnostics, and generated scheduler environments/quoting. The two offline demos still produce their documented results. The AST checker reports 11 passes, no failures and 3 unsupported checks. No real scheduler was installed or provider called.

The full suite was run with socket connections and `subprocess.run` blocked: **415 tests, 408 passed, 5 private-fixture skips, 2 errors**. Both errors also reproduce from the unmodified `06c4c893` archive at the same time: `test_fund_lane.Lane.test_notion_change_makes_v2_and_check_marks_done` and `test_memo_lane.Lane.test_feedback_makes_v2_and_check_marks_done_with_metrics`. Their daily report filters compare the KST calendar date with UTC timestamp strings; during the first nine hours of a KST day, current rows can be omitted and the report file is missing. At this initial checkpoint the existing report-date bug remained unresolved, so that run was not a passing full suite. The subsequent correction and clean run below supersede this result.

The network/process guard also exposed a missing nationality-judge fake in the LinkedIn gate test; the test now supplies it explicitly. Tests use a dedicated synthetic deployment fixture for historical enabled policies. Production `constraints.yaml` values were not changed to make tests pass.


## Submission readiness check (2026-11-03, after date correction)

The final local suite ran with socket connections and `subprocess.run` blocked: **422 tests, 417 passed, 5 private-fixture skips, zero errors or failures**. The two formerly failing memo/fund lane tests now pass without freezing their clocks. Seven new report-date regression tests cover KST midnight inclusion/exclusion, month/year transitions, both action and human-feedback records, and all affected report consumers.

`ledger.day_bounds(day)` converts a KST calendar day to a half-open UTC timestamp interval. Memo, fund, appstore, bplan, LinkedIn and the ops dashboard now use that interval for daily SQL counts. LinkedIn's JSONL event counts use the existing `ledger.rows(day=...)` KST filter. Weekly rolling windows and cumulative human-label totals retain their original meaning. Stored timestamps remain UTC; no stored data was rewritten.

The final AST check remains **11 passed, 0 failed, 3 unsupported**. An isolated, temporary launchd probe also verified generated environment and log paths under the actual scheduler; this was not installation of production workflow jobs. Remote service permissions, model quality and private-fixture paths remain outside this verification scope.
