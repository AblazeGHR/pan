# Lifecycle fixture publication read repair

- Base: `fa656211955af5e2bf87ff768617ca88a2b3fa48`.
- Branch: `fix/lifecycle-fixture-publication-20261112`.
- Source failure: main CI `43211390284`, attempt 1, Python 3.14,
  `test_owned_multigeneration_tree_cleanup_on_exit_or_exception[wait-error-legacy]`.
  XML: `D:/project/Pan/.workflow/practical-final-main-ci-43211390284/artifacts/attempt-1/pytest-results-3.14/pytest-results.xml`.

The original failure is a `PermissionError(13)` at the fixture's gate read,
before comparing the runner identity and before releasing the injected wait
fault. The writer already closes a temporary JSON file and publishes it with
`os.replace`. The reader waited for existence, then attempted a single read.
Existence does not prove that a Windows reader can open the file at that moment.
The CI artifact does not identify the handle or external component responsible
for the denial; no product lifecycle failure has been demonstrated by that trace.

`read_gate` now reuses the existing `wait_value` 20-second bound to wait for a
complete successful JSON read, retrying only missing-file and permission errors.
At exhaustion it raises the last access error. Invalid JSON fails immediately.
Both canonical fixture gate consumers use it. Publication remains atomic and
production code is unchanged. All runner PID/birth, unknown outcome, cleanup
proof, owned-tree exit, desktop release, receipt, same-attempt and no-replay
assertions remain in place.

## Evidence

All commands ran through the current Pan background Job tool, using the original
terminal notification and isolated test wrapper, with local Windows Python 3.14.

- Initial red Job `job_2e683c0d1a4ccfb33c81e513`, exit 1:
  `.tmp/practical-main/fixture-publication-red.xml` (missing-file setup race).
- Exact permission red Job `job_aaba451f69ce053e8551fe26`, exit 1:
  `.tmp/practical-main/fixture-publication-red-denial.xml`, 4 cases, 2 failures.
  One deterministic case raises the same `PermissionError(13)` on the first
  observation; the other starts with missing publication. The baseline single
  read fails both. Permanent denial and invalid JSON already fail explicitly.
- Green Job `job_eff762c512ff03d3890c13de`, exit 0:
  `.tmp/practical-main/fixture-publication-green.xml`, **39 passed, 0 skipped**.
  Includes all real owned-process lifecycle prevention and hidden production
  tests, plus transient denial, missing publication, permanent denial and invalid
  JSON cases. Original real-process cleanup assertions passed.

Green command (both `python` entries are the explicit local Python 3.14 executable):

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_lifecycle_fixture_publication.py tests/test_job_lifecycle_prevention.py tests/test_job_hidden_production.py -q --junitxml=.tmp/practical-main/fixture-publication-green.xml
```

Job logs are `D:/project/Pan/data/background_jobs/logs/<jobId>.log`.
Normal Git hook is enabled via `core.hooksPath=scripts`; no bypass is used.
This tests/docs-only change does not trigger the hook's conditional React check.
No frontend build, remote CI, other platform execution, service restart, push or
protected-ref promotion is claimed. MA owns exact-candidate remote verification.
