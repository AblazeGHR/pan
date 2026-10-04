# Background-test isolation incident and recovery

## Invalid run

`job_e98f4895dafb6ddc426e548f` was launched on frozen candidate `17b33e35`.
The command wrapper copied the outer Runner's environment and failed to remove
`PAN_BACKGROUND_JOBS_DIR`. Some tests patched only DEFAULT_ROOT, so real Job
registry paths won over fixture paths. The run is invalid and has no accepted
final pytest result. Its log remains in the deployed Job log directory, not
represented as a successful suite or recreated historical evidence here.

The real registry was also visible to mocked reconciliation. The outer Job was
marked failed/orphaned while its process was still alive; cancel therefore
returned the terminal record without stopping it. Runner PID 40500 was checked
against exact recorded psutil creation time 1791128280.5850768 before stopping
its own process tree. No process-name or command-line-wide termination was used.

## Owned records quarantined, not deleted

18 fixture records were identified from source-defined synthetic Session IDs,
literal Job IDs, fixture temporary cwd and incident observations. The initial
`job_retry-test-artifact.json` and 17 records under `test-artifacts/` preserve
their bytes and can be moved back if required. No blanket registry cleanup,
repair of user records or invented restoration of unknown historical state was
performed. Real Session IDs, legitimate scheduled/lifecycle Jobs and the actual
probe/final-run Job records were left in place.

`job_retry` lacked createdAt. Old list_jobs used an empty-string fallback alongside
numeric timestamps, causing TypeError and HTTP 500 for both GET /api/jobs and
/api/jobs/next. Quarantine restored both endpoints to HTTP 200 without restart.
After all 18 moves, both endpoints were rechecked at 200 and no synthetic
ses_target/ses_creator/ses_caller fixture targets remained in the live records.
Because no complete pre-run snapshot exists, absence of every possible mutation
to existing Job metadata cannot be proven. The report deliberately does not
claim restoration of unseen historical values. The retention marker records an
earlier normal run (2026-10-04 04:12 UTC), not evidence for deleting user data in
this incident; it was not altered as part of recovery.

## Prevention

`scripts/run_tests_isolated.py` filters all inherited PAN_* configuration only
for the test child, replaces inherited Python imports and selects a test port.
The outer Runner retains the real registry needed for result delivery.
`tests/conftest.py` provides a second bootstrap/fixture barrier for direct pytest
use: inherited application settings removed before imports and default Session,
Job, scheduler, Terminal and config stores are per-test tmp_path values.
See docs/TESTING.md and CODEBUDDY.md. Tests that need application settings must
explicitly set their own temporary values after bootstrap.

Focused validation: job-isolation-regression-uv.xml = 132 passed; the real poisoned
parent pytest child leaves a decoy production directory byte-for-byte unchanged.
job-isolation-entry-regression-uv.xml = 100 passed / 1 skipped. New mixed-createdAt
list regression keeps every record and checks no file bytes were rewritten.
These are focused results, not final repository acceptance or deployment.
