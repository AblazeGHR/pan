# P23 shared control patch request (not applied)

Baseline: `bdd4c778d55daff58529635b2adc464c03a1393c`. P21 candidate, not MA acceptance.

## Observed blocker

The real authenticated `POST /api/job-runs/{runId}/operations` `cancel` returns
202/accepted and moves the Run to cancelling, but does not call the independent
executor. The real parent/child tree remains running until the test explicitly
uses the independent cleanup HTTP endpoint. Both exact HTTP successors remain
red on this missing connection, after completing real cleanup and verifying
the original identities and one effect/one tree termination.

`DELETE /api/jobs/{id}` is definition retirement. It is not a physical deletion
endpoint and must not be changed to silently kill resources. The early
`canonical-first` experiment incorrectly demanded a 409 from logical retire;
that experimental assertion was corrected, and its original log/XML retained.
Current red tests require explicit Run cancel to reach the original owned
executor. No claim is made that 200/retired proves physical resource cleanup.

## Interface supplied by P23

`IndependentExecutor.cleanup_execution(*, attempt, effect_key) -> executionDoc`
is async and validates Job/effect/Attempt/lease/epoch/generation on the returned
record. It calls `IndependentJobClient.cleanup_execution(effect_id, attempt)`:

```http
POST /executions/{originalEffectId}/cleanup
Content-Type: application/json

{"attempt": "the complete original Attempt object, not a new Attempt"}
```

The actual body must contain exactly `attempt` as an object. No caller-supplied
proof, PID, birth, result, or replacement effect is accepted. The existing local
Host/Origin/content-type restrictions remain in force. Missing effect -> 404;
wrong fence/non-process/unavailable original supervisor -> 409; unavailable
`processOwner` capability -> explicit 501. No lifecycle operation is admitted.

The document's `cleanupRequest` contains monotonic `revision` and
`status=requested|unknown|confirmed`. HTTP 200 only acknowledges/query returns
this document; it is not a successful cancellation or physical purge receipt.
The same outstanding/confirmed request is idempotent. A new explicit call after
unknown increments its revision, retaining `cleanupHistory`. GET and cold
recovery do not retry unknown cleanup. Lost supervisor/handle returns unknown;
root-only recovery cannot prove or replace owned-tree cleanup.

Confirmation comes only from the original canonical supervisor retaining its
original ProcessOwner handle. It checks PID/birth (Windows raw FILETIME), calls
the existing owned Job/group tree termination and waits for the root. It
persists `identityCheck`, `rootExited`, `ownedTree.owned/remaining/confirmed`
before terminal delivery. A confirmed cleanup is reused without a second kill.
The killed business execution produces a rejected effect, not succeeded.

## Shared owner implementation requirements

1. `packages/jobs/final_service.py:control_run`, cancel branch: capture the
   original admitted `(jobId, runId, stepId, attemptId, leaseId, ownerEpoch,
   generation, effectKey)` under the canonical CAS transaction. Resolve the
   executor for this Job. Persist cancellation intent before external IO.
   After releasing the transaction, call the adapter above for already-started
   process effects. Do not await HTTP or OS cleanup while holding SQLite locks.
   Queued/pre-effect cancellation retains its existing no-effect fast path.
2. `FinalService._drive_run`, both cancelling fast paths: they currently move
   directly to cancelled. If an original effect admission exists and its owned
   resources are not proved clean, do not report cancelled/completed or discard
   its identity. Keep cancelling/uncertain plus a queryable pending operation.
3. `FinalService.recover_external_receipts` currently selects only Attempts in
   state running; default `IndependentExecutor.execute` can already have
   returned execution_pending and made that Attempt uncertain. Include the
   unresolved original admissions in a fenced lookup/reconciliation path.
   Never create a new Attempt/Run or resubmit the effect to resolve unknown.
   Use an explicit reconciliation API in the ledger if terminal transitions
   need one; do not bypass finish_attempt's epoch/generation/active fence.
4. Cancellation completion must consume the trusted original execution doc:
   require cleanupRequest.status=confirmed, a final receipt with confirmed
   owned-tree/root proof, and original supervisor exit confirmation before
   claiming *all* execution resources reaped. A requested/unknown doc, a dead
   root, a current PID lookup, or business terminal status alone is insufficient.
   An unknown result must remain queryable by the original operation/effect.
   A new explicit cancel/recheck after inspection may request a new cleanup
   revision; recovery/GET must never silently perform that destructive retry.
5. `FinalService._settle` / operation receipts: keep run cancellation status
   independent from definition retirement and from the killed effect's rejected
   business result. Preserve original history/outbox identities and a single
   receipt. Return unknown/error when proof is missing; do not auto-resend.
6. `FinalService._install_product_actions`: the process Action currently declares
   cancel.supported=false. Change its advertised capability only when the
   canonical control path above works. React belongs to P21; coordinate any
   control/capability UI changes through MA, not this TA.
7. `packages/jobs/final_api.py` Run operations handler: retain exact grant/CAS/
   requestKey validation; provide an awaitable post-commit control path if
   required by (1). No new legacy alias, no implicit destructive DELETE, no
   weakening expectedStateRevision or actor checks. Unknown/recheck must remain
   visible in the canonical operation query contract.

The P23 tests use a small ExecutorPort adapter that waits for the actual
independent final receipt and can delay delivery. It synthesizes no success or
cleanup proof. It validates the real in-flight/late callback path, but does not
claim default execution_pending recovery in (3) is already complete.

## MA rerun gates

Run the isolated wrapper with
`tests/test_job_retained_safety_canonical.py` (3 nodes),
`tests/test_job_retained_safety_owned.py` (3 nodes), and
`tests/test_job_retained_safety_protocol.py` (8 nodes). Both HTTP successors must
pass with `canonicalCleanupRequested=true`, without the diagnostic independent
fallback being needed. Add default pending-adapter reopen/reconciliation
coverage for (3), and explicit public unknown/409 -> inspect -> confirm
coverage. P23 currently proves unknown refusal at the independent cleanup
protocol, not a completed public physical-delete flow. Do not remove this
remaining acceptance boundary or claim all three original gaps are closed.
