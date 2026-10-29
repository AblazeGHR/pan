# Canonical automatic recovery revision3 candidate

Implementation source: `15e34ba63fcb92dcf77ff0ff563dcb886fdf9730`, on
`r6/job-automatic-recovery-20261029`, descended from proposal `353bb52a` and accepted
platform/lifecycle `aa76b83e`. This report/evidence commit changes no product behavior
beyond that implementation SHA. MA's accepted CI40844718642 is prior baseline
evidence, not CI for this candidate.

Authority is the revision3 brief and `RECOVERY_REV3_MA_DECISION.md` under
`D:/project/Pan/.workflow/job-unification-review-20261028/`, together with the
revised minimal migration plan and D1-D6. The revision2 proposal remains unchanged
as history; this report supersedes its UNIMPLEMENTED status for the bounded slice
described here. MA acceptance and combination are still pending.

## Result and configuration

The default detached process host can now recover a **proven rejected** process
through the original canonical Job/Run/Step/effect and a new Attempt. No new
provider, scheduler, runner, database, receipt ledger or counter authority was
introduced. No shared ledger/coordinator method or process ownership interface
was changed.

Example explicitly enabled policy:

```json
{"automatic":true,"retryIf":["failed","timeout"],"maxAttempts":2,
 "backoffSeconds":["5","15"],"stableResetSeconds":"0"}
```

`automatic` remains false by default. The additive optional unique-array `retryIf`
accepts the old six reason names in the contract; the product rejects automatic
combinations containing currently unsupported reasons before definition write.
An empty allow-list, nonzero stable reset, non-process/high-risk Action or absent
Action retry contract is unsupported. Invalid types, duplicates and maxAttempts
outside 1..10 remain schema errors. The original unbound-automatic rejection test
still passes unchanged. Existing definitions and frozen Runs without these fields
remain readable and do not gain automatic recovery.

## Old to canonical mapping and limits

| Original retryIf contract | Implemented canonical representation |
|---|---|
| retry_if stable reason list | Recovery.retryIf, frozen at Run admission; automatic currently supports failed and timeout only |
| max_retries N **extra** retries | Explicit configuration mapping M=N+1 into maxAttempts; 0 extra -> 1 total, 1 extra -> 2 total. No clamping or live/in-place migration; N>=10 remains incompatible with the canonical maximum. |
| attempt and remaining | Existing Step.attemptCount / Attempt.generation; future automatic eligibility requires count < maxAttempts. Prior manual Attempts count too. Manual explicit retry remains permitted past the automatic limit. |
| constant backoff_sec B | Explicit mapping to backoffSeconds=[decimal string B]; multi-entry schedule repeats its last entry. Decimal deadlines preserve all configured digits. |
| nextRetryAt | New private `plans[rid].autoRecovery` marker: stepId, afterAttemptId, eligibleAt, reason. eligibleAt is exact Decimal epoch seconds encoded as a string, not a float. No stored remaining/counter/history copy. |
| idempotencyKey/deduplication prose | Does not authorize replay. Frozen Action retry contract, same effect identity, original trusted receipt and full cleanup/supervisor proof authorize the bounded path. |
| stable reset absent in retryIf | Explicit stableResetSeconds="0"; separate legacy monitoring/reset semantics remain unsupported. |
| cancellation wins | Existing Run cancellation path; no policy may clear cancellation or allocate another Attempt after it wins. |
| revoked authority | Frozen original principal/revision plus current grants; no grant refresh. Scheduling is denied, or a waiting Run fails at due claim without changing count/deadline. |

Migration tooling and old executable history were not changed. This is a new
canonical capability, not a claim that all old retryIf records or old tests migrated.
Orphaned/interrupted/dependency-unavailable/cancelled automatic policies, nonzero
stable reset, and high-budget/complex instances remain explicit gaps under D6.

## Changed methods and execution path

- `final_contracts._job_step_defs` and `_normalize_job`: optional retryIf and default
  []; generated only job/job_definition schema content changes. One related existing
  contract-test expectation is extended to assert that new default; no unrelated
  assertion is reduced.
- `FinalService._references`: bounded product validation replaces the blanket
  automatic rejection. `_admit` freezes Recovery into the original plan/digest.
- `_retry_boundary`: extraction of the existing manual failed-boundary/unknown/
  success/idempotence/cleanup checks. `control_run` retains its API/CAS/request-key
  semantics and clears an old automatic marker for a newly explicit manual retry.
- `_drive_run`: the default process adapter's known execution_pending placeholder
  for an automatic Run is retained as `plans[rid].pendingProcessAttempt`, a reference
  to the **existing running admission**. It is not classified as lost/uncertain
  merely because the detached process has not returned yet. No replacement occurs
  until lookup supplies terminal proof. Generic manual/non-automatic behavior is
  unchanged.
- `recover_external_receipts`: queries the existing adapter by original Attempt,
  effect and full lease/epoch/generation fence. `_validate_process_document` also
  checks Job, effectId, frozen executable and digest, and latest Step generation.
  A rejected original pending process waits for `_cleanup_complete` (identity,
  empty owned tree, tree termination and supervisor exit). Only then is its receipt
  applied and `_schedule_automatic` allowed to record a marker in the **same existing
  aggregate transaction**. Missing/unconfirmed proof holds; it is not success.
- A real unknown or partial terminal receipt removes pending eligibility and stays
  uncertain. Later history reconciliation does not schedule automatic retry. An
  ambiguous launch, host loss without the known pending reference, arbitrary late
  callback or unrecognized reason cannot authorize replacement.
- `recover`: retains a matching known detached pending admission for query on cold
  reopen. It continues to mark other lost running effects uncertain. A durable
  retry_wait marker survives reopen with its original deadline and count.
- `dispatch_once`: excludes future/malformed deadlines **before** slicing capacity,
  so a waiting Run cannot starve a ready tail. Existing tick/start pulse is reused;
  no per-Run timer or retry Event is created.
- `_automatic_original` does fenced original lookup outside the Job transaction.
  `_claim_automatic` reloads and compares the marker, latest receipt, policy/count,
  current Job/Action availability, original grants and full proof inside the writer
  transaction. Marker removal, new Attempt/generation, and same-key effect admission
  commit together. Existing ExecutionStore still owns receipt/history and rejects
  duplicate/stale/unknown launches. Pause/resume preserves eligibility time; cancel
  and revoke allocate no Attempt. A terminal denied marker may remain as audit and
  is not scanned as executable history.
- `TypedExecutor._process`, timeout branch only: records actual retained-owner
  verification using the normal-exit branch's established verification rule. It
  combines actual terminate.exited and tree.confirmed facts. `_cleanup_complete`,
  supervisor evidence, platform owner methods and ledger transitions are unchanged.

The immutable plan's Action/arguments/event/outputs determine the executable on
every retry. Later definition edits cannot replace policy or argv. Completed
Steps are skipped, including in multi-Step process Jobs; each failed Step uses its
own existing attemptCount and automatic budget. D3 identities remain distinct
from Run again/Clone and run; Scheduler/Trigger admission is not repeated.

## Verification at implementation SHA

All test commands use `D:/project/Pan-main/.venv/Scripts/python.exe` through
`scripts/run_tests_isolated.py`, on Windows, with owned temporary roots/processes
and an ephemeral loopback port. No test uses the deployed profile or 8767/8768.

| Exact-source group | Result | Evidence |
|---|---|---|
| New automatic-recovery tests + final contracts + manual retry + final HTTP service + typed executor | 284 passed, 0 failed, 0 skipped (includes **57 new recovery cases**) | source-15e34ba6-focused-contract-manual.xml |
| Independent executor + canonical owned cancellation | 65 passed, 0 failed, 0 skipped | source-15e34ba6-independent-cancel.xml |
| Total distinct tests at 15e34ba6 | **349 passed, 0 failed, 0 skipped** | verification.json |

New recovery tests cover allow/deny/malformed/cancelled/unmapped reasons, N+1
budget boundaries, exhaustion and manual cap independence, exact Decimal deadline,
last-backoff reuse, per-Step count, definition-edit isolation, cancel/pause/resume/
revoke, lookup-time cancel race, disabled Action/Job, concurrent pulses/late lookup,
marker/admission rollback, malformed marker, missing receipt/tree/supervisor proof,
true unknown/partial hold, pending-versus-lost-host reopen, and no policy retrofit.

The owned **real HTTP + default detached executor + actual existing pulse** test
observes one Run, two Attempts, one effect identity and exactly two process
invocations. The original failed process has complete tree cleanup and confirmed
supervisor exit before replacement; the restored service preserves the marker and
frozen plan. Executor history contains the old Attempt, and the owned HTTP port is
closed afterward (`default-detached-proof.json`). Other actual default process
tests prove zero/one-extra exhaustion and that a completed earlier Step does not
repeat when the later Step fails then succeeds. These are owned processes, not an
injected synchronous provider standing in for production execution.

Timeout evidence has an explicit limit: a real retained-owner process with a short
test timeout proves the added timeout cleanup facts, and synthetic fenced-lookup
tests prove timeout allow/proof gates. The production detached runner's current
24-hour timeout was not waited out or reconfigured. Its actual end-to-end timeout
is therefore unverified; the shorter real default detached failure/retry path is
verified. No process owner or supervisor predicate was weakened to close that gap.

Earlier in this batch, executor/cancel/log regressions had 85 passed and 1 explicit
POSIX-only skip on Windows (86 collected). These ran before final fence checks and
are retained separately as pre-final-source regression evidence, not added to the
349 exact-source count. Earlier 199/28/41/53/57-case runs also are not added again.

The first new focused run had six test-authoring reds: five expected the wrong
HTTP status for invalid_schema (actual contract is 400), and one test subtracted
its tiny Decimal delta under the default precision and accidentally rounded past
the deadline. Corrected the new test setup/expectations, preserving that JUnit as
`first-focused-red.xml`. No product guard or old assertion was weakened. The old
65 retired retryIf tests, old authorization monitoring test and retirement mapping
are byte-identical to the proposal baseline; they were not rerun or declared green.

Generator `python -m packages.jobs.final_contracts --check`, Python compilation,
`git diff --check`, source/blob binding and safety-scope checks pass. The companion
`summarize.py` binds hashes/symbol lines/results to 15e34ba6, verifies unchanged
ledger/coordinator/ExecutionStore/old-test boundaries, redacts JUnit hostnames and
checks actual detached-proof counts. Regression tests' unrelated owned diagnostic
outputs were archived under ignored `.validation/job-automatic-recovery-rev3-regression`;
their tracked historical evidence was restored, not overwritten in this candidate.

## Remaining acceptance boundary

No full-suite, frontend build/browser, remote CI, Linux/macOS runtime, external
provider, real-data migration, deployment or human acceptance is claimed. Automatic
proof absence holds and can require operator inspection; this change does not add
automatic cleanup or refresh authorization. MA owns independent review, combination
and targeted three-platform CI. No main/practical, push, existing service, real
profile/data or protected-port operation occurred.
