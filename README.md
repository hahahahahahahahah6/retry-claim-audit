# retry-claim-audit

Offline audit of documented retry claims against Python source.

A standard-library-only static auditor for Python 3.9+. It executes no audited
code, makes no network calls, and never reads `.env` contents. Symlinked files
and directories are skipped. Files are read only within the audited tree.

## Usage

```sh
python3 -m pip install .
retry-claim-audit /path/to/project
python3 -m retry_claim_audit /path/to/project --json
retry-claim-audit . --ignore 'vendor/*' --ignore 'generated/*' --verbose
retry-claim-audit . --fail-on HIGH
retry-claim-audit --version
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m build
```

`--ignore` is repeatable and matches globs against relative paths. `--verbose`
shows verified families and parse/read warnings. JSON always includes
`findings`, `verified_claims`, and `errors`; findings include rule, severity,
claims, and evidence with relative paths and line numbers. Exit 0 means PASS
at the selected threshold, 2 means FAIL, and 1 means usage error. The default
threshold is MEDIUM; HIGH retains MEDIUM findings in output but exits 0 for
a MEDIUM-only report. Version: 0.1.0.

## Rules

| Rule | Severity | Condition |
| --- | --- | --- |
| documented-retry-claim-false | HIGH | Retry family claimed, zero production evidence |
| documented-retry-claim-false-test-only | MEDIUM | Retry family claimed, evidence only in tests/fixtures |
| documented-single-attempt-claim-false | HIGH | Single-attempt family claimed, production evidence exists |
| documented-single-attempt-claim-false-test-only | MEDIUM | Single-attempt family claimed, evidence only in tests/fixtures |

Each family produces at most one finding, including all relevant evidence.
No production source produces a HIGH finding per claimed family with
`no-source` evidence. No claims means PASS. Retry claims with production
evidence are verified; single-attempt claims with zero evidence anywhere are
verified. Contradictory families are audited independently.

## Honest limitations

Claims come only from root README.md and docs/**/*.md. Fenced code is excluded;
inline code counts. Claims use case-insensitive exact phrases only:

```text
Retry: “retries on failure”, “retries on error”, “retries transient errors”,
  “retries failed requests”, “automatic retries”, “automatically retries”,
  “retries automatically”, “with retries”, “retry logic”.
Single-attempt: “single attempt”, “one attempt”, “fails immediately”.
```

Bare retry/retries words do not count. Unlisted hyphen variants do not count.
A sentence containing no, not, never, without, doesn't, don't, or isn't
suppresses all claims in that sentence. Negation-phrased single-attempt claims
such as “no retries” and “does not retry” are therefore not counted.
Sentence boundaries use periods, question marks, and exclamation marks.

The named evidence table is deliberately limited:

| Evidence | Static recognition |
| --- | --- |
| Imports | tenacity, backoff, retrying, including aliases and submodules |
| Calls/decorators | retry, Retrying, wait_exponential, wait_fixed, wait_random, wait_random_exponential, retry_if_exception, retry_if_exception_type, retry_if_result, on_exception, on_predicate; any receiver |
| Attempt limit | stop_after_attempt with first argument literal greater than 1 |
| Adapters | Retry calls; HTTPAdapter/mount calls with literal max_retries greater than 0 |
| Loops | For/While (including async for) with a Try in the body and either an except-body continue or attempt/retry/tries in a target or iterable/bound identifier |

`max_retries=0` and `stop_after_attempt(1)` are excluded. Nonliteral limits
are unknown and skipped. The loop heuristic can overcount nested handling
and cannot prove the number of attempts. Async loops use the same evidence
kind. Imports count even when unused; dead code counts. This intentional
over-approximation proves presence of primitives, not runtime correctness.
Bare requests/urllib3/httpx imports, time.sleep, standalone try/except, logging,
and catching/re-raising alone are not evidence. Other third-party libraries
are out of scope. Production excludes tests/, fixtures/, test_*.py, *_test.py,
docs/, and examples/; test/fixture evidence is collected separately, while
docs/examples source is excluded entirely. Invalid or unreadable source is
reported in JSON errors and verbose output; it supplies no evidence.

## Differentiation

* error-claim-audit checks exit codes, exceptions, and messages rather than attempt counts.
* concurrency-claim-audit checks simultaneous work rather than repeated attempts.
* config-claim-audit checks documented default values rather than retry behaviour.
* bandit/ruff check code issues rather than documentation promises.
* tenacity/backoff documentation, pytest, and timing benchmarks describe or test runtime behaviour; this tool compares documentation to static evidence.

MIT licensed. Local analysis requires no service or credentials.
