# Canonical process monitoring: original Attempt completion proof

Final product source: `1e375d59abc9e163339de6641d51f1a4fee350aa` (implementation
`a5890b32` plus original owned cleanup on output overflow), based on the clean
stable-reset candidate `4cf100f38051be48ab9e3b81155bb4ed0be00950` in
`D:/project/pan-worktrees/job-recovery-monitoring-20261029`, branch
`r6/job-recovery-monitoring-20261029`. The original recovery worktree at 972a934c
was not modified. This report supersedes the process-monitoring gap in the
earlier revision4 report; that report and its test artifacts remain historical.

Authority: recovery-r4-brief, the user's function-parity hard order, and MA's
explicit instruction to implement structured completion on the existing
FinalService/ExecutionStore/runner/Attempt/effect path. Amendment 447424c2 concerns
runtime delivery fields; this change adds none and does not retarget deliveries.
This is a TA candidate, not integration/release or MA acceptance.

## Behavior and old contract

The old `packages/actions/provider.py::execute` separately bounded stdout at
512 KiB, parsed JSON, and exposed its object as structuredContent. The original
`Runtime.execute` required an exact signal `panJobCompletion={type,completed}`,
matching the frozen signalType, completed strictly true, and processTreeEnded=true.
Unknown/uncertain results did not become success because a signal was present.

The canonical detached process path now preserves that contract. A declared
monitoring Action can use this Step completion configuration:

```json
{"contract":"monitoring","parameters":{"signalType":"owned.done"}}
```

Its process stdout is a UTF-8 JSON object containing the old signal, for example:

```json
{"panJobCompletion":{"type":"owned.done","completed":true},"output":"value"}
```

The enclosing output object can contain business fields. The signal object itself
must have exactly type/completed. A bare exit0, stderr text, mismatched type,
completed=false, malformed JSON/UTF-8, multiple JSON documents, or output beyond
the old 512 KiB bound cannot establish completion. Nonzero exit remains rejected
even with a matching signal. Full original process-tree proof is still required.

| Condition | Result |
|---|---|
| exit0, matching structured stdout, original tree cleanup | succeeded with original Attempt/effect-bound completionProof |
| exit0, absent/invalid/oversized stdout signal, known cleanup | rejected, monitoring_completion_invalid; eligible for frozen failed retry only |
| nonzero exit / timeout | Original rejected/unknown process result retained; signal cannot override it |
| stdout drain failure / unconfirmed cleanup | unknown; original identity queried, never automatic resend |
| unknown / partial result with valid signal | Remains unknown / partial |
| previously succeeded host receipt without valid proof | Canonical Attempt remains/becomes uncertain; original host record is not relabeled rejected to enable replay |
| automatic=false | Monitoring still completes from proof; known failure settles failed without automatic retry |
| supported automatic policy | Existing reason filter, per-Step budget/window, backoff, grants, cancellation and original proof gates apply |

## Minimal wiring and ownership

Only three product files changed relative to the stable-reset candidate:

- `FinalService._references` checks the required signalType before persistence;
  `_drive_run` freezes completion/signalType in the existing executable plan and
  digest. Ordinary process executable digests remain compatible. Completion is
  immutable; it is not a runtime delivery field.
- `independent_execution_runner.execute_claimed` uses an execution-local stdout
  pipe for monitoring only. A bounded drain retains at most 512 KiB while
  continuously draining, and appends stdout to the same controlled original log.
  stderr goes directly to that log and never enters the proof buffer. Ordinary
  processes retain their direct merged sink. There are no temporary proof files,
  custom process owners, alternate command runners, or persistent reader jobs.
  An overflowing stream or drain error exits the existing wait poll into the
  retained owner's normal termination/tree-cleanup path, just as old bounded
  provider output failure required cleanup; it does not wait for the 24h deadline.
- Before `store.finish`, the runner validates the old signal and attaches a
  receipt completionProof containing the trusted original six-field Attempt
  fence, effectKey, signal, source=stdout, UTF-8 encoding and observed byte count.
  It retains structuredContent for downstream output use, plus existing logRef,
  process identity, cleanupProof and executionTiming even on invalid completion.
- `ExecutionStore.admit` rejects malformed monitoring contracts before launch;
  `finish` checks the proof/fence/tree for monitoring success before its existing
  atomic settlement. An unproven success is unknown, with no retry authorization.
  Claim/fence checks, original outbox, history and same-effect retry rules remain
  authoritative. No table, ledger or dispatcher was added.
- FinalService queries that same original execution, revalidates executable
  digest/fence, proof, tree cleanup and supervisor exit, then settles through the
  existing ledger. A known pending reference also works with automatic=false.
  A monitoring-incomplete rejected result maps to the old failed reason, reusing
  the existing frozen recovery window and pulse. Historical unknown Attempts can
  reconcile a later valid proof without allocating a new Attempt. Duplicate
  lookups do not add rows; cancellation retains its settlement owner.

No ledger/coordinator, process platform/ownership API, Worker, MCP, notification,
target/backlog manager, migration script, generic contracts/schema or log API was
changed. Existing completion.parameters and receipt JSON fields express this
contract; no unknown Binding fields or new runtime semantics were invented.

## Verification

Final commands and exact counts are in
`evidence/job-monitoring-completion-20261031/verification.json`, bound by an offline
summarizer to product/test blobs at 1e375d59. It verifies the six changed
source/test files and thirteen unchanged authority boundaries. All tests use
`D:/project/Pan-main/.venv/Scripts/python.exe` via `scripts/run_tests_isolated.py`.
Owned temporary stores/processes and ephemeral HTTP listeners only; no real
profiles, accounts, services or 8767/8768.

The new monitoring file contains 23 cases: actual detached stdout/stderr separation,
bound/encoding/signal errors, Attempt/effect/fence and cleanup checks, nonzero exit,
pre-admission contract rejection, unknown/partial non-promotion, exactly-once
original reconciliation, and owned HTTP/default detached/cold reopen/actual pulse.
An actual child that outputs beyond the limit and then waits 10 seconds is
terminated and proven cleaned up before its wait ends; the result is a known
monitoring failure, not a timeout-based automatic retry classification.
The existing three revision4 monitoring tests now assert the supported behavior;
unsupported is no longer used as their completion criterion. No retired old
retryIf or monitoring test assertion was changed or declared green.

The owned HTTP integration uses maxAttempts=2. It sees one short incomplete
monitoring failure, a second incomplete failure after 2.2 seconds against a
2-second stable-reset threshold, then a third successful original proof. It
asserts one Run, one effect key, generations 1/2/3, stable window baseGeneration=1,
frozen policy preservation across cold reopen, and released HTTP listener. This
combines monitoring failure recovery and stable reset on the default production
detached path. Separate proof-bearing unknown reconciliation asserts one Attempt,
one effect row and one original reconciliation record, with no resend.

Earlier paused/exploratory results are archived separately and not added to
final-source counts. The first current focused failure was a test using the
public Run projection for private frozen plans (KeyError); its JUnit is retained.
The user-interrupted command is not a complete test result. Earlier draft behavior
was revised to match the actual old panJobCompletion envelope and bounded capture.
The existing stale unsupported expectations were replaced by real failure/success
and original-identity assertions, not lowered unknown/cleanup checks.

Final core source groups: **115 + 289 = 404 distinct passed**, zero failures,
errors or skips, both exit 0. An additional **3 passed** selected original
process-log cases check large Unicode stdout/stderr, nonzero exit and cancellation
without replay, exit 0. The final source success matrix therefore contains
**407 distinct passed, 0 failure/error/skip**. The separately recorded baseline
QQ red below is not omitted or classified as passed.

Supplemental full logs/manual-QQ regression encountered one failure at
`test_retirement_rev10_qq_retry.py:91`, bot=222222, uncertain instead of completed,
plus the explicit POSIX-only log test skip on Windows. That supplemental run
straddled the final overflow fix and is not counted as exact-source success.
The exact failing QQ case was then run in fresh interpreters against 1e375d59
and an overlay of all three changed product modules from 4cf100f3 before test
collection. **Both fail at the same original assertion** (exit 1). This is a
baseline-module-equivalent red in this environment, not a claim that the full
baseline checkout or real QQ passed. Both JUnits and the overlay harness are
retained. No QQ test or production QQ method was modified.

## Remaining layers

This closes structured completion for canonical owned process monitoring. It does
not claim all legacy Action transports or all automatic recovery reasons are
implemented. Orphaned/interrupted/dependency-unavailable/cancelled automatic
reasons and budgets beyond the existing maximum remain prior explicit gaps;
this candidate does not reclassify those missing capabilities as acceptable or
use D6 as a parity exemption. Old executable history lacking coherent original
plan/fence/proof still needs explicit original-identity migration/reconciliation;
no data was migrated here.

Unverified: Linux/macOS runtime, remote CI, full suite, browser/UI, external
provider, real-data migration, deployment, human acceptance, and waiting for the
default detached 24-hour timeout. The owned retained-owner timeout regression is
not that 24-hour production proof. No main/practical/frozen-platform ref, push,
CI, existing service or other worktree changed. MA owns review and integration.
