# P23 process logging migration

Date: 2026-10-28. Worktree `D:/project/pan-worktrees/job-p23-retained-safety-20261027`,
branch `r6/p23-retained-safety-20261027`. Started clean at `ebbf54e2`, verified it
was an ancestor, then fast-forwarded only this task branch to authorized baseline
`67d125de794132f72011012d8c6814110aafe8ef`. No prior work was discarded. Code SHA:
`fc58de1f869d9a28c2a3734305c4a34e6b5610e5`; final clean evidence/report commit is identified in delivery.

Authority: MA's `REVISED_MIGRATION_PLAN_V2.md` (2026-10-28) and the canonical
`JOB_FINAL_MODEL_20261025.md`: reuse original behavior under D1-D6; logs belong
to the original canonical Run/Step/Attempt/effect, with no second lifecycle.

## Confirmed loss and original behavior

Old `background_runner` opened controlled `job.logPath` as unbuffered append and
passed `stdout=file, stderr=STDOUT`. It retained output after the supplier exited,
with a Job/history pointer and conservative log cleanup policy.

The default canonical path is `FinalService._drive_run` -> job-bound
`IndependentExecutor` -> `ExecutionHost/ExecutionStore` durable admission ->
platform detached bootstrap -> production independent runner -> existing
`RecordingOwner` -> `TypedExecutor._process` -> native `ProcessOwner.spawn`.
Business output was lost because `_process` supplied DEVNULL to stdout/stderr.
The DEVNULL in `execution_host._spawn` is instead bootstrap/supervisor diagnostic
output. These two sinks have different responsibilities.

With MA approval the repair overrides only stdout/stderr in the existing
production `RecordingOwner.spawn` adapter. TypedExecutor and bootstrap diagnostic
code stay unchanged. This is the original merged-file business behavior, using
the existing native ownership/cleanup algorithm. No PIPE, reader thread, log
daemon, second outbox, new receipt ledger, or new execution manager was added.

## Minimal connection and public contract

- Process admission persists `logRef` in the existing execution document before
  launch. Its opaque `log_` ID is derived from the ORIGINAL effect and complete
  Attempt fence. It records job/effect/effectKey, original attemptId/runId/stepId/
  leaseId/ownerEpoch/generation, merged stream names and byte cursor unit. It
  contains no filesystem path or business output.
- The runner appends raw stdout+stderr bytes to `<executor-root>/logs/<logId>.log`.
  The default pending projection and actual runner terminal/unknown receipt carry
  only that reference. Generic/custom provider results remain byte-for-byte
  unchanged, including original unknown/partial results.
- File access reuses `_controlled_logs_dir`, `_path_has_reparse_component`,
  `_is_plain_file_entry` and `pinned_directories`. Only generated direct children
  of the trusted controlled root are eligible. Symlink/junction/reparse/hardlink,
  non-regular files, inconsistent references and changed file identity are refused.
  Native directory pinning / POSIX no-follow descriptors protect the open window.
- `read_execution_log` selects the original current OR archived execution document
  by the recorded six-field Attempt fence. A later Attempt, lease or epoch does
  not rewrite the old reference or block legitimate historical reads. No lookup,
  cold recovery or missing log can authorize a launch or retry.
- MA authorized only new `FinalService.read_process_log` and discovery additions,
  their protocol entries and GET routes. The service locates trusted live/archive
  original aggregates, reads outside the SQLite lock and rechecks the existing job
  read grant after external I/O. Caller Job/effect/logRef/path/delete input is not
  accepted. The internal executor HTTP port uses the original Attempt identity and
  a bounded byte window, behind existing local Host/Origin/content-type defenses.
- `GET /api/job-runs/{run_id}/attempts/{attempt_id}/log` accepts only `offset` and
  `limit`, one decimal integer each. offset is 0..2^63-1; limit is 4..65536 bytes
  (four allows the largest UTF-8 code point). `nextOffset` advances by the exact
  bytes returned. UTF-8 pages end at a complete character; an incomplete suffix
  stays at the original byte cursor and is exposed as bounded pendingBase64.
  Non-UTF-8 data is explicitly binary/text=null with exact bytesBase64, never
  silently replaced or skipped. Appending output is read as a bounded size snapshot.

`GET /api/job-runs/{run_id}/logs` is part of this same candidate, as authorized
after confirming ordinary create operations do not expose an Attempt ID. It
lists original logRef/attemptId/read href with pending/available/unavailable
status and no file content. It reuses the existing principal/profile/Run scoped
cursor and limit<=100. A pending admission can query the original trusted
execution's persisted reference; a legacy effect without one remains explicitly
unavailable. Both new methods share one live/archive locator. Cold reopen,
archive, cursor replay and cross-principal/profile rejection are tested.

## Retention, pin, cleanup and publication boundaries

No log deletion/rotation/GC was added. Logs follow retained history. Canonical
definition DELETE remains logical retire and never implicitly kills or erases
logs. Existing pin, unknown ownership, unsettled effect/outbox and native tree /
supervisor-exit proof blockers remain in force. Even a successful log read says
nothing about whether resources have been cleaned.

The real archive test first observes pin and unacknowledged-outbox rejection.
Its own isolated dashboard notification is explicitly suppressed through the
original notification settlement method; no real delivery is claimed. Only then
does the existing coordinator archive the settled definition. The same original
public log remains readable from runtime_archive after that boundary.

Publication belongs to Session TA `ses_37b685e9873419e6`. MA approved its additive
`product_process_logs_v1` copied from `product_process_cleanup_v1`, new provider
digest `digest("pan.typed.process-logs.v1")`/generation=3, optional output logRef,
all old inputs/capabilities/completion/cancel/reconcile unchanged. P23 does not
edit `_install_product_actions` or merge that candidate. MA reported the Session
publication integrated at `00593314f3ac093eab43544ce5f4964258486bcd`; P23 remains
based on 67d125de and requires method-level combination with that tree and quota.
See [method/publication agreement](../../evidence/p23-process-logs/SHARED_ENTRY_AND_PUBLICATION.md).

## Tests, evidence and limits

Final actual interpreter argv, cwd, exit, count, per-node result, skip reasons,
protected method/file checks and owned resource proofs are recorded in
[validation.json](../../evidence/p23-process-logs/validation.json). Commands are
reproducible through [run_validation.py](../../evidence/p23-process-logs/run_validation.py).
All pytest commands use `scripts/run_tests_isolated.py`; all roots/processes and
loopback ports are owned temporary test resources, excluding 8767/8768.

| Validation | Exit | Passed | Failed | Skipped | JUnit |
|---|---:|---:|---:|---:|---|
| Windows system full matrix, first run | 1 | 544 | 1 | 19 | system-final.xml |
| Same full system matrix, unchanged product code retry | 0 | 545 | 0 | 19 | system-final-retry.xml |
| Pan-main venv logs + original four P23 safety suites | 0 | 46 | 0 | 0 | venv-final.xml |
| System and venv dedicated logging suite, each | 0 | 20 | 0 | 0 | discovery-full-system.xml / discovery-full-venv.xml |
| Final route ownership/snapshot checks | 0 | 2 | 0 | 0 | route-fixtures-final.xml |
| Import packages.web.server | 0 | n/a | n/a | n/a | import-final.log |

The first full system run failed in the unchanged historical independent-service
`test_cancel_with_wrong_runner_identity_does_not_touch_job`: Windows aborted the
HTTP connection (10053) before the expected 409. The unchanged single node passed
on retry, then the unchanged full command passed. This suggests a transient
transport failure; its cause was not established and its original failure XML/log
is retained. No retry was added to product code or the test.

Canonical route count is 37 and both new GET registrations are explicitly unique.
The regenerated snapshot adds the two log routes plus deprecated aliases; it also
repairs four baseline-registered rows missing from the old snapshot: time-input
resolve and template instantiate, each canonical and deprecated. No existing row
was removed or changed. Alias identity and noShadow checks remain unchanged.
The last commit only adds the two explicit registration assertions; those checks
were rerun after the full matrix. Full matrix product code is unchanged.

Evidence records contain 54 owned-resource cleanup proofs. Old P23/R6-I evidence
was restored byte-for-byte after tests wrote their normal output locations; new
run resource proofs are copied under this candidate's evidence root. The final
system retry command's seconds value is JUnit suite duration, not shell elapsed.

Real default canonical tests exercise stdout+stderr, Unicode and output above
pipe capacity, original child ownership/cleanup, pending reads, cold interpreter
reopen, failed exit, unknown/cancel, late terminal after retire, archive read,
anonymous/revoked authorization and path/boundary errors. Separate protocol tests
exercise partial multi-byte file writes, binary byte preservation, historical
old-epoch Attempt references, stale fence rejection, forged remote page/reference,
hardlink and actual Windows junction refusal. These negative protocol checks do
not substitute for the real default process or P23 tree tests.

Exploratory red evidence is retained: compat-first (8 failures) exposed an initial
generic-store receipt rewrite, which was removed; first (2 failures) exposed
incorrect child-script string escaping and a platform-newline assumption in the
test; second (1 failure) correctly observed the existing outbox archive blocker;
third (15 pass) followed those repairs. Later full matrices and any remaining
failures are listed separately; no assertion, error or skip is suppressed.

`raw-output.zip` archives the available JUnit/log snapshots with hashes. Some
review artifacts were whitespace-normalized during evidence finalization; binary
console output is retained byte-for-byte. This is not a claim that every archived
review artifact preserves the first console capture byte-for-byte. Old P23/R6-I evidence remains unchanged.
No conftest/support, pin/coordinator resolver, TypedExecutor, bootstrap, scheduler
host, React or existing shared service method was modified. Existing source ASTs
are checked against 67d125de; only explicitly authorized new log methods/routes
are added.

[Capability matrix](../../evidence/p23-process-logs/CAPABILITY_MATRIX.md) distinguishes
Windows system/MA venv execution from Linux/macOS and remote CI not executed here.
Whole-repository suite, browser, real data migration, deployment, and MA combined
integration/acceptance are unverified. No protected ref, push, existing service,
real profile/data/51/60 collection, account or monitoring Job operation occurred.
No new TA was created; this continues the original process-safety Session.
