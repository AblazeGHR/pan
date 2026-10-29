# Automatic recovery: minimal canonical seam proposal, revision 2

**UNIMPLEMENTED. Product source is read-only in this batch.** Source baseline:
`aa76b83e16ac580d8a548752011b7c81636c6519`, fast-forwarded from
`b5b3f81d57e56b735d65d18d4446ae29c4cb39c6` on
`r6/job-automatic-recovery-20261029`. The accepted four-job CI result
40844718642 is supplied by MA, not rerun or independently certified here.

## Recommendation and decisions for MA

Reuse **FinalService**, its retry validation, its existing pulse, and its existing
Job aggregate transaction. No recovery provider, new engine, scheduler, runner,
database, outbox, or receipt ledger is inherently required. My first report's
blanket missing-provider conclusion was too broad. The literal rejection message
at `FinalService._references` is an implementation guard, not a model mandate.

The narrow proposed first implementation is automatic retry of a **known rejected
effect with a trusted stable reason and affirmative original cleanup proof**, at
the first failed Step of the same frozen Run. Completed Steps never repeat.
Unknown/partial outcomes, missing receipts/reasons/proof, cancellation, stale
ownership, and revoked authority cannot authorize an Attempt. Existing manual
retry semantics stay available independently; the automatic path uses stricter
proof and policy gates.

MA needs to approve these exact design choices before implementation:

| Decision | Recommended narrow choice | Alternative / consequence |
|---|---|---|
| Reason representation | Add optional `Recovery.retryIf` enum array, default `[]`; automatic=true with empty list is rejected as unsupported. Only `failed` and proven `timeout` execute initially. | A fixed server-only reason policy avoids a schema addition but cannot preserve caller-selected old reason filters; explicit migration must mark those records incompatible. |
| Count conversion | `maxAttempts = maxRetries + 1`, for a single Step; exact only for old maxRetries 0..9. Never clamp larger values. | Raise the canonical 10-attempt ceiling only by explicit model decision; otherwise preserve larger records unchanged/nonexecutable under D6. |
| Budget domain | Per Step, all actual Attempts (manual and automatic) count toward future automatic eligibility; manual retry remains explicitly permitted past this automatic cap. | Separate automatic-only budget requires another derived selection rule and metadata; Run-wide cap would consume budget on successful earlier Steps. Neither is silently selected. |
| Stable reset | Migrated retryIf uses `stableResetSeconds="0"`; first implementation rejects automatic=true with nonzero stable reset. | Supporting watchdog-style stable reset changes a finite total cap into rolling failures and needs a separate agreed rule. |
| Revocation | Keep frozen original authority/revision; fail the waiting Run without another Attempt, preserving count and eligibility timestamp as audit. Regrant cannot silently refresh frozen authority. | A held retry_wait requiring explicit user intervention is possible but needs a resume/reauthorization contract. |
| Explicit manual retry during automatic wait | Leave current API unchanged: retry requires failed, so retry_wait returns not_retryable; cancel/pause/resume remain usable. | An explicit override of the automatic delay needs separate semantics and tests. |
| Host loss / orphan / interrupted | Hold unknown, query original; no auto retry of anything that became uncertain, even after later reconciliation, in this first slice. | Automatic retry after trusted resolution of a former unknown needs explicit scope approval; it must never be a blind resend. |
| Supported scope | First reject unsupported automatic definitions before write: multi-Step automatic policy deferred unless MA approves per-Step semantics; system/high-risk/lifecycle and nonzero stable reset excluded. Migration never executes old history. | Multi-Step support is a small later wiring extension with explicit per-Step budget tests, not a second engine. |

The first slice is deliberately single-Step for a lossless old background-process
count mapping. The shared retry helper remains Step-oriented and is usable by
multi-Step manual retry. The old complete reason matrix is **not** closed by this
proposal. Publication/activation should only be enabled for action/backend pairs
whose existing receipt path can actually provide the required evidence.

## Authority

- [Revised migration plan v2](D:/project/Pan/.workflow/job-unification-review-20261028/REVISED_MIGRATION_PLAN_V2.md): extract/wrap/migrate; recovery belongs to Job; stop for broad rewrite or model conflict.
- [D1-D6 authority](D:/project/Pan/.workflow/job-unification-review-20261028/JOB_UNIFICATION_DESIGN_AND_DELTA.md): lines 34-39, especially D3 same Run/Step new Attempt and D5 existing profile permissions; recovery row at line 68.
- [Original chain design](D:/project/Pan-main/docs/design/job-unification/JOB_ACTION_CHAIN_SCHEDULER_EVENT_DESIGN_20261024.md): line 62, Step recovery cannot repeat completed Steps; line 107, Scheduler miss/overlap/recovery are distinct and restart cannot replay unknown.
- [Revision2 task brief](D:/project/Pan/.workflow/job-unification-review-20261028/brief-recovery-policy-seam-review-20261029.md).

All source line anchors below refer to the accepted source SHA. The companion
evidence JSON records source hashes, symbol lines, and source-check results.

## Exact old call chain and semantic mapping

The exact pre-retirement source inspected is commit
`7385f807f4d93fabf10c3d40f15148961b53f337` (read with git show, not checked out).
Its `background_jobs.start` validates `_retry.policy(...)` before spawn/remote
dispatch, expands the returned policy into the Job, initializes attempt=1 and a
new attemptId, and rejects retryIf when an independent legacy service is configured.
The evidence JSON records the original function lines and Git blob hashes.

Historical caller `jobs.start(... retry_if, max_retries, backoff_sec,
idempotency_key/deduplication)` is still asserted by
`tests/test_background_job_retry_if.py:32-34`; it is retired, not a candidate
entrypoint to restore. `background_retry.policy` validates the six stable reasons,
nonnegative finite backoff, unbounded nonnegative integer extra retry count, and
caller deduplication statement. `_write_job` calls `background_retry.terminal`
(`background_jobs.py:103`); terminal appends one attempt history entry and computes
nextRetryAt. `runner_update` validates a reason, fences expected_attempt, and lets
cancelRequestedAt win (`background_jobs.py:1764-1810`). `background_runner.main`
enters the legacy owner pass and delegates to `_run_legacy` (:26), which
maps nonzero exit to failed, TimeoutError/TimeoutExpired to timeout, and
KeyboardInterrupt to interrupted (`background_runner.py:50-66`).

`reconcile_running` marks lost/missing/reused runner identity orphaned and invokes
`retry_due` (`background_jobs.py:1813-1871`). `retry_due` checks reason, budget,
cancel, deduplication, due time and child liveness under spawn/task/job locks;
it calls `new_attempt`, persists, then spawns (`background_jobs.py:1875-1925`).
Live or unverifiable child blocks replay. These historical functions are source
evidence only; this proposal never calls them or restores their registry.

| Old field / assertion | Existing canonical field or seam | Required delta / mismatch |
|---|---|---|
| retryIf omitted / [] | Recovery.automatic default false | Preserve opt-in. Do not infer automatic from failure. |
| retryIf reason allow-list | No equivalent in Recovery (`final_contracts.py:1097-1105`) | **New public model field** retryIf needed for lossless filtering, unless approved fixed-policy alternative. |
| maxRetries N extra retries | Recovery.maxAttempts M includes first, range 1..10 | M=N+1; N=0 means M=1 and no replacement; N=2 means Attempts 1,2,3. N>=10 is incompatible without an approved bound change. |
| remaining=maxRetries-(attempt-1) | Step.attemptCount; Attempt.generation | remaining=max(0,M-step.attemptCount). Do not add remaining/count as another authority. |
| backoffSec B, fixed seconds | Recovery.backoffSeconds string durations | `[decimal(B)]`, e.g. 10 -> `["10"]`; reject nonfinite/out-of-range/unrepresentable values. Index min(attemptCount-1,len-1), repeat last entry. Never use float NaN/inf or binary serialization artifacts. |
| No reset in background_retry | stableResetSeconds | Set "0" explicitly. Canonical default "300" is not an equivalent migration default. |
| attempt / retryAttempts / attemptId | steps[rid][sid].attemptCount and attempts keyed by attemptId | Existing ledger increments once in begin_attempt; no legacy retryAttempts projection needed. |
| nextRetryAt / lastReason | No dedicated current canonical fields | Proposed private per-Run plan.autoRecovery record; reason derived from latest trusted receipt, not copied from status text. These are **new keys in an existing aggregate**, not existing fields. |
| idempotencyKey or deduplication string | Frozen Action.retry{idempotent,sameKeySupported,requiresProof}, effectKey + existing executor store | Caller prose is not proof and cannot promote Action safety. Require original receipt and backend same-key/idempotence contract. Preserve prose only as reviewed provenance. |
| same Job/task/argv/cwd | Same Job/Run/Step, frozen actions/input/event/outputs | Same effectKey, new Attempt/lease/generation. Frozen args only; never re-resolve current definition or dynamic target list. |
| accepted cancel beats recovery | Run cancelling/cancelled; aggregate.cancellations; existing _cancel_boundary | Check before scheduling, due claim, and effect admission. Cancellation is not an automatic retry reason. |
| original retryIf did not refresh grants | frozen.authority + _permit(execution=True), _guard_effect | Original background_retry has no grant-revision budget rule. The separate monitoring-recovery test requires no new effects/count/deadline change on revoke. D5 supplies current canonical auth checks. |

### Reason representation: no invented success or generic failure fallback

Canonical Effect outcome is succeeded/rejected/unknown/partial. Attempt/Step state
failed does **not** encode timeout/orphaned/interrupted/dependency-unavailable.
The Effect receipt is extensible JSON, so a server-derived reason can use existing
receipt evidence without adding a new execution record type. Do not put legacy
reason codes in canonical ErrorCode enums or equate all rejection with failed.

| Old reason | Existing trusted evidence | Proposed automatic disposition |
|---|---|---|
| failed | TypedExecutor._process: known nonzero exitCode plus confirmed cleanup (`typed_executor.py:465-482`), outcome rejected | Normalize to failed only for this recognized process receipt; generic rejected/invalid-schema/authorization-denied is not a failed command. |
| timeout | _process returns rejected with receipt.code=timeout only when retained-owner terminate/tree cleanup confirmed (`:450-464`) | Normalize to timeout when proof is adequate. Provider timeout from exception_receipt (`:277`) is unknown and stays held. |
| orphaned | recover records host_restarted as unknown (`final_service.py:1236-1265`) | Missing/dead runner is not cleanup proof. No automatic equivalence; original lost-runner tests remain a gap. |
| interrupted | _process exceptions yield unknown/process_exception (`typed_executor.py:485-486`) | No trusted rejected+cleaned interrupted receipt today; hold. No catch/translation to failed to force eligibility. |
| dependency-unavailable | Some providers return rejected/provider_unavailable, often without cleanup proof (`typed_executor.py:507-532,637-640`) | No blanket mapping. Would need exact existing backend pre-effect proof and a reviewed mapping; excluded first slice. No new provider protocol is required merely to express it. |
| cancelled | ExecutionStore.cancel returns cancelled_before_effect with confirmed effectStarted=false (`independent_executor.py:204-219`) | Always deny even if selected. Existing executor already forbids retry of cancelled_before_effect. |

Suggested additive `retryIf` enum may retain all six values for migration analysis,
but the initial publication validator rejects automatic policies containing reasons
without implemented trusted mappings (or require a narrower enum initially by MA
choice). Unknown is never a member. Defaults must not auto-enable old Runs.

## Existing storage, manual retry, and pulse reuse

`FinalService.transaction` (`:119-126`) enters OwnerFence.writer(canonical=True)
and CreatorStore.transaction (`creators.py:244-266`, BEGIN IMMEDIATE). AggregateStore
mutate (`final_service.py:57-62`) and FinalRuntime.compare_and_swap
(`coordinator.py:251-257`) use that same transaction. No call to control_run from
inside another transaction; extract its pure mutation/validation body instead.

`_admit` (`final_service.py:531-582`) freezes actions, steps, input, event,
authority and configRevision, **not Recovery**. Proposed delta: freeze
`recovery=deepcopy(cfg['recovery'])` at admission, included in planDigest. Existing
Runs lacking this field stay automatic-disabled; no startup retrofit and no
definition update can promote their policy. This is a new frozen-plan key, not a
new public Run field. It preserves original authority including Agent revision.

`control_run` retry branch (`:729-753`) validates failed boundary, rejects
uncertain/unknown/partial/success, checks frozen Action retry contract and optional
cleanup proof, then transitions failed -> retry_wait. It does not limit manual
attempts or impose Job Recovery backoff. Extract validation only into
`_retry_boundary(job,run_id,step_id=None)`; return frozen Step, Action, latest
matching Effect and Step attemptCount. Manual callers retain today's acceptance,
request-key replay/CAS and permissions behavior. Automatic callers add stricter
reason/proof/count/auth gates, without fabricating a user HTTP operation or grants.

Use `state['plans'][rid]['autoRecovery']` for only a scheduling marker:

```json
{"stepId":"main","afterAttemptId":"attempt_original","eligibleAt":"2026-10-29T12:00:10Z","reason":"failed"}
```

All four keys are **proposed new private metadata**. Count comes solely from
Step.attemptCount, retry origin from afterAttemptId, frozen policy from
plan.frozen.recovery. Do not maintain another reservation/count/history list.
`job.runtimeMetadata` is also persisted (coordinator metadata/pin APIs at :258-285),
but is Job-wide and exposed through metadata(); per-Run plan storage already owns
pauseRequested, outputs and resumeSelections. Prefer that existing plan container
to avoid mixing recovery writes with pin revision. Neither container currently
defines retry deadlines. Validate metadata explicitly; malformed deadline/marker
holds, never defaults to due.

`dispatch_once` (`:1075-1094`) already scans retry_wait; `tick` (`:1560-1590`) calls
it; `start.loop` (`:1668-1679`) pulses every 0.25 seconds. Add due eligibility
filter before capacity slicing and repeat the check in `_drive_run` under the
writer transaction, so delayed Runs do not starve ready tail Runs. No timer per
Run and no Event Creator/Event for retry. Pause delays eligibility without resetting
the deadline. On resume a still-pending marker uses the original deadline.

## Proposed method-level change (pseudocode, NOT applied)

1. `final_contracts._job_step_defs`, `_normalize_job`: optional retryIf, strict
   enum/unique-list validation, default []; regenerate published schemas/manifest
   and relevant contract fixtures only after approval. This is the only proposed
   public model addition. Do not add nextEligibility/count to public Run schema.
2. `FinalService._references`: replace unconditional rejection with bounded
   validation: automatic opt-in, explicit supported retryIf, stableResetSeconds=0,
   supported single-Step trusted receipt operation, frozen Action retry contract.
   Leave unsupported combinations rejected before definition/operation write.
   Missing proof at runtime still holds, even for accepted definitions.
3. `_admit`: copy normalized Recovery into frozen plan. Never bind from live
   config during retry. `Ledger.admit` already digests/persists that whole plan.
4. `_retry_boundary`: extract the existing manual retry checks without relaxing
   them. `control_run` calls this helper and retains operation/CAS machinery.
5. Add local `_automatic_retry_marker(job,rid,receipt)` using trusted receipt
   normalization plus the helper. No external call or await inside transaction.

```python
# _drive_run receipt transaction, after fenced finish_attempt + record_effect:
if cancellation_exists: save_and_return()  # existing priority
if mapped != 'completed':
    transition_run(mapped)  # known rejected -> failed; unknown -> uncertain
    # Only original known-rejected completion enters automatic scheduling.
    # No failed-Run scan, no uncertain-reconcile callback enters this helper.
    if mapped == 'failed' and receipt.outcome == 'rejected':
        boundary = _retry_boundary(job, rid)
        policy = frozen.get('recovery')
        if supported(policy) and count < policy.maxAttempts:
            reason = trusted_reason(receipt, boundary.action)
            if reason in policy.retryIf and original_cleanup_confirmed(receipt):
                _guard_effect(db, job, rid, frozen, boundary.action)
                # gate failure => settle failed, no marker/Attempt allocation
                plan.autoRecovery = marker(boundary, receipt, eligible_time)
                transition_run('retry_wait', operation='retry')
                save(job)  # same commit as failed receipt + marker + transition
                return    # do not _settle a terminal Run or create final outbox
    _settle(...)           # exhausted/denied/unproven/unknown stays terminal/held
```

Eligibility time is `Attempt.endedAt + delay`; parse canonical UTC/decimal duration
without precision loss. Single-entry backoff repeats; for a schedule choose
`backoffSeconds[min(step.attemptCount-1,len-1)]`. Scheduling does not consume budget.
Exceptions from gating are handled deliberately, not swallowed: preserve the
recorded receipt and fail closed with a bounded diagnostic. A transaction failure
rolls back receipt/marker together; no effect is resent to repair it. Query original
receipt using existing recovery. If that becomes uncertain, automatic remains held.

6. `_drive_run` before start, and again immediately before begin_attempt:

```python
if cancellation_or_pause: existing_boundary_handling(); return
if marker_present:
    validate_marker_matches_latest_failed_attempt_and_step()
    if clock_utc < eligibleAt: return
    _retry_boundary(...)             # recheck success/unknown/partial/proof
    _guard_effect(...frozen...)       # original authority, current grants/action
    if revoked_or_action_unavailable:
        transition_wait_to_failed_via_legal_transitions()
        _settle(failure='authorization_denied')
        # no begin_attempt, no count/deadline change; marker retained for audit
        return
    if count >= frozen.recovery.maxAttempts: settle_exhausted_without_attempt(); return
# existing: resolve frozen bindings/selection; begin_attempt(owner_epoch=current)
# existing: admit_effect(same rid,sid,target,ordinal), save before execute
# clear marker in SAME commit as new Attempt+effect admission (not at start())
```

Audit preservation means the marker may remain on a terminal failed Run; dispatcher
must only honor it on retry_wait or paused_at_boundary. Legal Run transitions must
be checked: retry_wait -> running -> failed is already used by legacy exhaustion;
prefer adding no new state transition. Never start an Attempt just to mark failure.
At initial guard failure or due failure, do not change the original frozen authority.

7. `_drive_run` already passes `retry=attempt.generation>1`. Keep Ledger.begin_attempt
   count/lease/owner epoch and Ledger.admit_effect unchanged. `ExecutionStore.admit`
   (`:87-134`) reuses same effectId/frozen executable digest and archives prior
   receipts, demanding rejected+cleanup, new attemptId, increasing generation and
   nondecreasing ownerEpoch. Keep its cancellation rejection. No second ledger.
8. `recover`, `recover_external_receipts`, `recover_session_receipts`: preserve
   query-only behavior. Persisted retry_wait with no live Attempt remains eligible
   at its original deadline on cold reopen. Running/lost effect becomes uncertain;
   do not create a replacement. Known later reconciliation may settle failed, but
   does not automatically call the scheduling helper in this first slice.

### Process proof and actual product limitation

Do not confuse `cleanupProof.confirmed` with a complete detached-host exit proof.
`_cleanup_complete` (`final_service.py:1267-1280`) requires owned tree empty,
identityVerified/treeTerminated and supervisorExitProof; it is currently used by
the cancellation path, not the ordinary external-receipt recovery branch.
`ExecutionStore.admit` requires confirmed cleanup, but does not require supervisor
exit proof. The first implementation should use the existing fenced
`lookup_execution` to obtain that original document before scheduling/claiming a
process replacement, then revalidate original attempt/effect/plan under the Job
transaction. Do not perform a blocking lookup inside the aggregate transaction.
Persist a reference to the original receipt, not a second effect fact; re-query
if proof absent. No automatic cleanup request or kill is authorized.

The timeout branch of `_process` lacks identityVerified in its proof, so the stricter
existing `_cleanup_complete` predicate will deny it today even with confirmed=true.
That is a concrete timeout proof gap, not permission to weaken the predicate.
MA can approve a narrow existing-owner proof completion later, or first support
only known nonzero exits with complete proof. `_automatic_retry_marker` must defer
without changing unknown into failed if the original proof cannot be obtained.

Production `IndependentExecutor.execute` (`independent_executor.py:465-484`) usually
returns execution_pending as unknown before detached completion. This proposal
does **not** pretend that an injected synchronous executor proves a useful fully
deployed process recovery path. A strict no-auto-after-uncertain first slice may
therefore have few or zero production process opportunities. MA must choose:
(A) retain that explicit safety boundary and validate the seam only; or
(B) separately authorize treating trusted same-identity terminal reconciliation
as a fresh known rejection eligible for policy (after full proof), while still
never replaying an unresolved unknown. B reuses existing lookup, not a new provider;
it must be stated explicitly and tested as a larger slice. No provider-binding
mandate follows from this limitation.

## Cold reopen, races, notifications, and boundary checks

- Crash before marker commit: transaction rollback leaves no replacement claim;
  original receipt lookup is authoritative. Crash after marker commit: reopen reads
  same deadline/count/afterAttemptId, waits if future, claims once if due and safe.
- Crash after Run.start but before begin_attempt: current recover marks running
  Run uncertain even with no active Attempt. Preserve this conservative hold in
  the first slice; do not infer safety from absence of a row. Same-transaction
  start+Attempt would reduce this gap later, but is not necessary to enable replay.
- Crash after Attempt/effect admission: unknown hold, original lookup only. Executor
  receipts/history and owner epoch still fence old callbacks and new owners.
- Same-process dispatcher lock plus single canonical OwnerFence and database
  transaction prevent double scheduling/claims. Recheck marker/latest Attempt in
  the receipt commit and claim commit; stale callbacks cannot reset a newer deadline.
- Cancel during lookup/backoff: reload aggregate and cancellation intent before
  touching marker/Attempt. Existing cancellation controls/cleanup proof own settle;
  policy never clears cancellation. Paused Job/Run consumes no new budget.
- Keep final `_settle`/outbox only when completed, exhausted, denied or held uncertain.
  Do not emit a terminal failed Run notification for every intermediate retry.
  Do not re-use a settled Run's final outbox as a retry event. Manual operation
  result semantics remain existing; automatic policy creates no fake user request.
- D1/D2/D4: no time event/admission per retry. D3: same Run/Step/effect, new Attempt.
  D5: no authority renewal/ACL addition. D6: explicit reviewed config migration only,
  complex/high-budget/missing-proof histories unchanged, no execution at migration.

## Existing tests and proposed acceptance (not results)

Existing source contracts: background retry reason matrix (:39), zero-budget/spawn
count (:67), durable backoff/reopen (:107), cancel precedence (:135/:144), concurrent
and stale runner fence (:154), live child hold (:169), fresh interpreter (:242).
Authorization monitoring regression (:132 in test_authorization_runtime.py) checks
effects/count/deadline unchanged on revoke. It is a different old policy family;
its maxAttempts/recoveryFailures/stableReset fields must not replace retryIf's
extra-count semantics.

For that separate old monitoring family, `action_runtime.Runtime.launch` (:808)
checks `_check_authorization` before `claim`, tick (:858, recovery branch :899)
checks `nextAttemptAt`, and finalize (:443, policy branch :512-524) stores
recoveryFailures/nextAttemptAt with stableResetSec. Its mapping is
enabled -> automatic, backoffSec array -> backoffSeconds string array,
stableResetSec -> stableResetSeconds, with maxAttempts' actual failure-counter
semantics requiring review rather than applying the extra-retry +1 conversion.
It is not the selected finite retryIf migration; nonzero reset/monitoring stays
unsupported in the first proposed slice. This is why the old revoke test is a
required future canonical behavioral successor, not a field-conversion oracle.

Current source references, **not rerun as new automatic proof**:
test_automation_retry_parallel.py:78,105,127,145,171,191 (manual identity/unknown/
cleanup/CAS/controls); test_job_final_independent_execution.py:70,85,97,117,152,219,
241 (fences/unknown/history/cleanup/atomic receipt/original lookup);
test_job_final_http_service.py:245,301,542,550 (revocation/reopen/unsupported/cancel).

After approval, add owned tests against actual FinalService pulse and aggregate:

1. Normalized known rejected reason allow/deny; cancelled always deny; malformed/
   absent reason/proof and arbitrary provider_unavailable never qualify.
2. N=0/1/2 conversion; N>=10 incompatibility; manual Attempts count toward future
   automatic cap; stableReset=0 and unsupported nonzero rejection.
3. Fake UTC clock, repeated last backoff, due-before/at boundary, ready-tail fairness;
   no sleep-based test engine and no new scheduler.
4. Cancel/pause/revoke before scheduling and at due/lookup races; original authority,
   no new Attempt/count/deadline change on revoke; no grant refresh on reenable.
5. Fresh service/reopened database before/after marker commit and after admission;
   preserve deadlines/counts, unknown/partial never replay, no implicit policy retrofit.
6. Same Job/Run/Step/effect key and frozen args; new Attempt/lease/generation; concurrent
   pulse and late receipt; completed Step/target never repeats; original executor
   history shows one replacement and rejects duplicate launch.
7. Write failure rollback, proof unavailable, supervisor not exited, timeout lacking
   identity proof, and execution_pending followed by terminal lookup (both A/B policy
   decisions); no unsupported -> successful test translation.
8. Preserve existing unbound-automatic rejection fixture; add separate explicitly
   supported-policy tests. Do not edit old 65 reds or retirement mappings to claim closure.

## Actual checks and scope

This batch performs source review, AST symbol/source-hash verification, field/
container checks, and docs-only diff validation through the companion script.
**No pytest matrix was rerun**; the previous 100 explicit-retry passes and 65 retired
failures are historical evidence only. No product files or schemas were edited.
The evidence script is an offline source checker; it cannot establish runtime
automatic behavior. It binds source references to the reviewed Git version.

Actual command: `python evidence/job-automatic-recovery-rev2/check_source.py`:
15 baseline-equivalent source/test files, 57 method/function anchors,
17 source characterization checks passed, 0 failed. Additionally, two original
Git source files at 7385f807 are hashed and seven historical symbols are located;
the original start policy/count initialization assertions pass. `git diff --check`
passes. These are offline source checks, not tests of the proposed runtime.

This is not necessarily a broad rewrite: the same service can reuse its trusted
retry logic, pulse, writer transaction and ledger. The additive reason field,
budget domain, nonzero reset rule and unknown-resolution policy are precise model/
scope decisions. Restoring the legacy writer, new recovery provider protocol,
separate counter ledger, replacement scheduler, or classifying lost ownership as
failed would cross the stop threshold. The full old reason closure is blocked on
those explicit decisions and concrete trusted evidence, not a generic missing
policy engine. All proposed changes remain UNIMPLEMENTED until MA authorizes scope.

Unverified: runtime automatic scheduling/revocation/reopen, real provider/process
effects, migration on real data, three-platform runtime/CI, full suite, browser,
production/deployment and human acceptance. No main/practical, real profile/data,
service, port 8767/8768, push or CI operation was performed.
