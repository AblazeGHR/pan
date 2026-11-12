# Isolated HTTP observation repair

Base `67a788a64b5dba71666bd62832ac749392898643`; branch
`fix/isolated-http-observation-20261112`.

## Original evidence and limits

Exact-candidate CI `43232305271` attempt 1 has two Python 3.12 failures.
Original XML:
`D:/project/Pan/.workflow/fixture-publication-ci-43232305271/artifacts/attempt-1/pytest-results-3.12/pytest-results.xml`.
The complete run log was read through `gh run view --log` and preserved locally
at `.tmp/practical-main/ci43232305271-full.log` (read-only CI operation).

`P6_CONFIG_01` failed on its first synchronous run-now POST with httpx's implicit
5-second read timeout. The artifact contains no server log or durable receipt
for that request, so it cannot establish the precise slow stage or whether that
original Run eventually completed. No unknown original request was resent.
The unchanged local original test passed (Job `job_2cc874e7d547dfe5f23c6cf0`,
exit 0, `.tmp/practical-main/http-config-baseline.xml`; optional profiler output
`.tmp/practical-main/http-config-baseline.prof`).

The route's real chain is `control` admission, `_dispatch_lock`, `_drive_run`,
actual provider dispatch and durable receipt settlement, then `project` and
`manual_result`. There is no five-second completion promise in this synchronous
route. The product's `packages/jobs/final_client.py` HTTPTransport already uses
a 30-second budget. To demonstrate the test-budget mismatch, the regression
holds only the provider entry for 5.25 seconds and then calls the original
executor, retaining the real queue and receipt path. This reproduces the
transport timeout deterministically; it does not claim the CI delay was caused
by that injected wait or prove a product performance root cause.

Only that first synchronous POST now explicitly uses the product's 30-second
budget. Other CRUD and replay queries retain the client's default five seconds.
Both normal and slow cases retain the original dispatch/queue, single item,
same Run replay, runCount, cold completed Run and no legacy write assertions,
and assert exactly one original executor call. Success still requires the
actual dispatched result and durable queue receipt; a timeout is never success.

The second CI failure is direct Session metadata `read_text` raising
PermissionError(13) after a durable terminal had already been observed. The
auto-report test now reuses `_read_session_metadata` from the existing delayed
restart HTTP fixture. That helper retries only Windows sharing/access errors,
at most six reads with a total 150ms sleep; other errors, invalid JSON and
exhausted permission failures still raise. The test's durable terminal,
autoReportAdmission snapshot, upstream report and zero ancestor echo assertions
are unchanged. Added checks exercise this actual auto-report read entry with
transient and persistent denial.

## Red and green

All test commands used explicit local Windows Python 3.14 through
`scripts/run_tests_isolated.py`, current Pan background Jobs and original
terminal notifications.

- Deterministic red Job `job_1ca80fb3d7fa180abf5eb3f4`, exit 1:
  `.tmp/practical-main/http-observation-red.xml`, 6 cases, 2 failures: real HTTP
  slow-provider ReadTimeout and auto-report metadata EACCES. Four error-boundary
  checks passed.
- Combined green Job `job_2fd0e0cd21f89859b7f42430`, exit 0:
  `.tmp/practical-main/http-observation-green.xml`, **136 passed, 0 skipped**.
  Normal config case 2.375s, held-provider case 7.765s, real HTTP/WS/Chromium
  source-policy scenario 10.264s (JUnit case durations, not POST latency).

Green test files:

```text
tests/test_action_unification_p6_task_config.py
tests/test_action_unification_p6_json_scheduler.py
tests/test_action_unification_p6_json_receipts.py
tests/test_source_aware_auto_report_real.py
tests/test_source_aware_auto_report.py
tests/test_delayed_restart_metadata_read.py
tests/test_session_evidence_read.py
```

Job logs: `D:/project/Pan/data/background_jobs/logs/<jobId>.log`.
No product source changed. Chromium used the existing production build (frontend
sources unchanged); no fresh build or frontend compile is claimed for this
tests/docs patch. Owned test services/processes were stopped by the passing
fixtures. No real profile, protected ports, service operations, push or CI writes.
Normal Git hook is enabled (`core.hooksPath=scripts`) without bypass; its React
check is conditional and does not run for this patch. MA owns exact-candidate
remote validation and target-ref promotion.
