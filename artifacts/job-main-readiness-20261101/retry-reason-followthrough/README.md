# Six configured reasons and original receipt proof

MA explicitly approved preserving six-reason configuration acceptance after
reviewing the original terminal/retry_due paths. The old schema already contains
all six values. The old four unsupported-configuration parameter cases remain,
now asserting exact configuration persistence with no Run/Attempt/effect.
Process-only, non-high-risk and Action retry-safety constraints remain.

- cancelled never automatically retries, including when configured. New execution
  after cancellation still uses Run again with a new identity.
- dependency-unavailable is not a new failure emitter or new retry engine.
  Existing original lookup unavailability remains observable, spends no Attempt
  budget, and resumes lookup under the same identity when the dependency returns.
- orphaned/interrupted require a trusted original rejected receipt explicitly
  naming that reason, a known nonzero exitCode, complete cleanup proof and original
  supervisor exit proof. Complete matching native identity is mandatory. No reason
  is inferred from a dead/reused/missing PID, missing owner or missing receipt.
- Unproven abnormal labels are unknown observations, not known failed outcomes.
  After pending status is removed, repeated unproven lookup cannot change the
  original unknown aggregate. Existing diagnostics expose reconciliation_required.
  A later complete matching original receipt can reconcile that same Attempt;
  it does not automatically replay the effect or create a new Attempt.

Identity parsing reuses ProcessIdentity.from_dict (including positive pid), with
strict integer pid, nonempty string birth, the existing schema identifier and
complete proof.identity == supervisorIdentity before interpretation. The existing
original document fence still checks jobId, effectKey, effectId, Attempt lease/
epoch/generation and frozenPlan/planDigest. No second identity authority exists.

## Original semantics and evidence limits

Legacy retry_due used only child pid_exists/processTreeEnded and even permitted
retry when the child PID field was absent. Legacy orphan recovery grouped missing,
unavailable, dead and reused runner identity together. KeyboardInterrupt wrote
interrupted without a durable full child exit receipt. These old test inferences
are preserved as red evidence, not reused as trustworthy cleanup proof.

The orphaned/interrupted positive matrix is a **trusted-port contract fixture**;
it does not claim a native emitter currently emits those reasons or that a real
user profile migrated. Native failed/timeout process positives remain in
test_job_automatic_recovery and the broader combination. Cancelled configuration
and actual cancellation controls are separate tests. No global notification or
automatic retry default changed.

## Checks

All tests run through scripts/run_tests_isolated.py in the owned private fresh
venv and temporary fixture roots. Original red sources/JUnit remain in
full-first-red and stale-process-original-red, including all original 36 reason
matrix nodes. No skip/xfail or assertion deletion was added.

| Evidence | Exact result |
| --- | --- |
| retry-reasons-original-red.xml | Original acceptance/trigger matrix red |
| retry-reasons-green.xml | Initial 56 passed |
| retry-reasons-matrix-green.xml | Expanded 84 passed, including original 36 canonical successors |
| abnormal-proof-state-red.xml / green.xml | 10 red then 10 passed for first unknown observation |
| combination-v4.xml | 346 tests: 345 passed, 0 failed/errors, 1 existing POSIX platform skip; source hash unchanged |
| abnormal-nonpending-original-red.xml | 10 failed negative paths, 2 complete-proof positive paths passed |
| abnormal-nonpending-green.xml | All 26 passed, including identity and independent-Run recovery checks |

The final nonpending proof guard was added after the 346-test combination; its
26-node affected-path check is separate evidence. Do not relabel that original
combination as having tested the later guard. Remaining full-suite failures and
the final new-SHA remote CI are still outstanding.


## Remaining original retry nodes

All original 65 test identities remain collected. The 12 policy validation cases
now use authenticated canonical creation with exact invalid_schema/no-write checks;
the retained orphaned configuration case persists exactly and creates no execution.
Four runner exception probes preserve process_exception as unknown with the original
message, cleanup unconfirmed, and no second spawn even with retry requested.
No Python exception is invented into known process-exit proof.

Cold backoff, accepted cancellation, live unproven process, strict HTTP policy,
and absent old remote bridge assertions now target the canonical ledger. The
final notice probe runs an actual first-exit-7/second-exit-0 process: intermediate
retry_wait creates no outbox; terminal completion creates exactly one Session
and one WS row, both ACK once using the original outbox IDs. Independent Python
cold restart completes only the native failed case with original Run/effect and
exactly two Attempts/invocations. The old orphan fixture supplied no child exit
receipt: its successor preserves original uncertain state across a fresh Python
process, no replay; it does not claim a native orphan producer or migration.

retry-settings-complete-v3.xml contains all 65 retry nodes passing and two separate
UI stale-expectation failures. settings-exact-defaults-green.xml then proves all
16 UI cases pass after explicit default and server-managed epoch assertions.
The UI assertions retain whole-object equality, including exact default-off
notification sources and generated 32-hex authorization epochs. Product defaults
and authorization behavior did not change. Earlier three-node followthrough reds
and the complete original 5a7 JUnits/sources are retained. These focused checks do
not close the remaining full-suite or final-SHA CI gate.
