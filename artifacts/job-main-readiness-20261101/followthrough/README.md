# Inbox and native process followthrough

This is an intermediate candidate, not a final full-suite or remote CI pass.
Base: 5a7fda854d8fedae92a0ac21cb105342b7a912ec. main/practical were not changed.

## Product fixes

- An inbox subscription returning canonical `deferred` previously became an
  admitted receipt. It now uses the existing retry/error subscription state;
  this is not an execution failure. The admission port re-reads only the original
  published target ID's current policy. No new targets or Event identities arise.
- Default native process `execution_pending` with the original exact effectKey
  now keeps its original running Attempt for lookup, including when automatic
  recovery is off. Actual unknown/partial receipts remain uncertain.
- If lookup validates the original Attempt/effect/frozen plan and confirms the
  original supervisor exited without a terminal receipt, canonical recovery
  records one unknown observation. It does not fabricate a failed process result,
  modify the native execution result, clean up, or submit execution again.
  The exit proof must contain the complete identical supervisor identity and
  pass the existing ProcessIdentity parser; invalid pid/birth cannot prove exit.

## Preserved business contracts

Pause/disable produce named deferred results, not terminal skipped admissions.
Retire/nonmatch remain skipped. Original stale-revision admission defers until
the same target is re-read. Existing accepted Event/Trigger pairs still query
their original operation. Original source and partial full red JUnit remain in
`../full-first-red/`; no tests were skipped or deleted to resolve these conflicts.

Real HTTP/WS auto-force now logs in through UserAuth and admits a canonical
product_process_logs_v1 Job/Run. Original outbox identity reaches exactly one
Steer delivery, with durable queue receipt and no Worker restart. Hard crash
explicitly subscribes the existing uncertain notification state: absence of a
terminal process receipt is not proof of failure. Its original notice remains
Illegal-locked until explicit Wake and delivers once. Native process/supervisor
births are checked before crashing only fixture-owned processes.

Log tests now obtain a running execution's exact original effect admission;
terminal paths still require a real effect/receipt. The handoff fixture removes
only the pending callback reference, retaining its original admission. Log bytes,
authorization, cold discovery, archive/cleanup and no-replay assertions remain.

## Evidence and limits

All commands use `scripts/run_tests_isolated.py`. Python 3.14, fresh private venv
without system site packages; HTTP fixtures use temporary roots/dynamic ports.

| Evidence | Result |
| --- | --- |
| inbox-deferred-red.xml | Original 2 failures |
| inbox-deferred-cold-v2.xml | 7 passed, cold reconstruction and current-target policy |
| admission-contract-followthrough.xml | 29 passed |
| canonical-auto-force-v3.xml | 1 passed, real HTTP/WS |
| process-pending-red.xml | Original running-vs-uncertain failure |
| process-pending-green-v2.xml | 1 passed, actual process/cold lookup/one effect |
| canonical-crash-notice-v4.xml | 2 passed, crash notice and known pending |
| exit-observation-strict-green.xml | 13 passed, synthetic negative probes, no OS-proof claim |
| ../log-pending-original-red/pytest.xml | First combination: 186 tests, 178 passed, 7 failed, 1 existing POSIX skip |
| ../log-handoff-original-red/pytest.xml | Second combination: 199 tests, 197 passed, 1 failed, 1 existing POSIX skip |
| log-handoff-green.xml | Remaining handoff node passed after fixture correction |

The second combination's sole failure was in the handoff fixture; only that
fixture was subsequently changed. Do not relabel that original combination as
all green or substitute these counts for a full-suite pass. Original partial
full run still has many unmapped retirement/functional failures; see
`../FAILURE_MAP_PARTIAL.md`. Frozen full 5a7 and remote run 41301570121 retain
their original Job/CI identities. Final delivery requires a new exact SHA and
complete CI. No production data migration or deployment is claimed.

Identity negative mappings retained in the combination include
`test_job_automatic_recovery.test_wrong_original_fence_never_admits` (effectKey,
Attempt, frozenPlan, planDigest, jobId),
`test_job_final_independent_execution.test_fe02_stale_fence_never_settles_or_cancels`,
and `test_fe02_success_unknown_partial_cannot_be_replayed`.
