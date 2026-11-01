# Independent safe successors; reason matrix remains unresolved

Original sources and partial red identities remain in `../full-first-red/`.
This does not classify entire legacy files as obsolete or claim a full pass.

| Original capability / node | Approved boundary and current proof | State |
| --- | --- | --- |
| Interaction questions: trusted choice scope, large Unicode answers, unknown lookup, deletion without resend | Final model S09 retires legacy Automation execution, not Card functionality. The shared test setup uses existing canonical graph/bind/owner. The test reads the full original frozen Event plus exactly one canonical operation; stale target change edits the actual Job. Unicode, 409/stale/open, source identity, lookup-only and no-effect assertions remain. | interaction-current-final.xml: 57 passed including the separate delete case below |
| Scheduled child terminal writeback cannot recreate deleted parent | S03 removes legacy scheduler reconciliation. The old reconciliation call explicitly refuses, before/after historical child callbacks retain deleted parent absence. The real canonical late-receipt/retired-parent behavior remains `test_job_process_logs::test_late_terminal_log_keeps_retired_parent_and_original_reference`, passed in the 199-node combination. | Targeted legacy-history assertion passed; no legacy scheduler reactivated |
| Retry default does not execute again | S01 removes old writer. `test_default_side_effects_never_retry` now uses canonical original native receipt, automatic=false, two pulses, identical entire result and one Attempt. | retry-safe-three.xml |
| Zero extra budget and spawn outcome does not retry infinitely | Canonical maxAttempts includes first attempt. One-attempt policy never executes another; two-attempt policy admits one extra attempt and an executor exception remains uncertain with no further dispatch. | retry-safe-three.xml |
| Registry I/O failure is not swallowed | Injected canonical aggregate save failure rolls back the transaction, propagates original OSError and invokes no executor. | retry-safe-three.xml |
| Cancel during backoff | Current original Run control/operation receipt completes cancellation with trusted prior cleanup; no new attempt or execution. This does not claim cancelled is an automatic-retry reason. | retry-stale-fence-green.xml |
| Concurrent recovery and late old receipt | Four pulses admit only one new Attempt with same effectKey. A stale original receipt cannot change the complete canonical store (including operations, budgets, effects and outbox). The existing automatic recovery test now checks state equality too. | stale-independent-green.xml plus preserved initial retry-safe-control.xml red |

The stale receipt assertion found a product defect: after uncertain removed the
pending marker, ordinary native process reconciliation lacked the complete
original document/Attempt fence. The existing validator now runs before applying
the receipt. A revision conflict uses the existing external_receipt_unavailable
diagnostic with explicit revision_conflict/runId/attemptId, leaves that Run
unchanged, and continues recovering other independent Runs. Only that named
conflict is handled; unrelated errors still propagate. A new two-Run negative
test proves the stale Run remains unchanged while a matching other receipt
settles normally. Non-process executor contracts are unchanged.

The 36 reason matrix and other untransferred nodes remain red/pending. The schema
lists failed/timeout/orphaned/interrupted/cancelled/dependency-unavailable, while
current automatic admission supports failed/timeout only. Tests asserting a
refusal are not authorization to drop a capability. MA requires tracing original
exit/cleanup proof for orphaned/interrupted, cancelled-vs-RunAgain and dependency
gate-wait behavior individually. No broad capability_unsupported alignment,
skip, xfail, or restored legacy execution engine has been applied.
