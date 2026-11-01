# Verification scope

Checked on 2026-11-01 with a fresh Python 3.12.13 virtual environment on macOS 15.7.7. Dependencies were installed from `requirements.txt`. Demo and final test runs used temporary, initially absent `KV_CONFIG` and `KV_DATA` paths rather than a developer’s live configuration.

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

No live Gmail, Slack, Notion, Apify or model calls were used to validate this change. Installed macOS schedules, remote API contracts, production model scores, optional document-engine/browser dependencies and business outcomes were not revalidated. Runtime behavior is unchanged outside the public demo; the shared judgment-store and LinkedIn CLI changes correct explanatory text only.
