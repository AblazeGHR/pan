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
