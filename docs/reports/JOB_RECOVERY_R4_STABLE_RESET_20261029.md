# Recovery revision4: stable execution windows and explicit monitoring gap

Designated tree: `D:/project/pan-worktrees/job-recovery-monitoring-20261029`, branch
`r6/job-recovery-monitoring-20261029`. Exact input baseline:
`4b577633d2d9ffcdc5bf8a44b12836724c18804e`. Implementation commits:
`9c3030f41f7b26869d7abdc45bbbd539702cbb69` and
`e48926a5e7d6d2bbe1bdcfb405b8a481e94b5abf`; the latter is the final product source
for the evidence. A following documentation/evidence commit makes no product edits.
The original recovery tree and its 972a934c evidence were not modified.

Authority: the complete `recovery-r4-brief.md` in
`D:/project/Pan/.workflow/job-unification-review-20261028/`, repository AGENTS/CODEBUDDY,
Pan TA instructions, and `docs/design/job-unification/JOB_FINAL_MODEL_20261025.md`
(especially section 2.6, original execution-bound completion and frozen recovery).
Baseline acceptance is not overall migration/release acceptance. This remains an
unaccepted TA candidate for MA review/combination.

## What is supported

Positive `stableResetSeconds` now works for the existing canonical automatic
**known process failed/timeout** path, with original cleanup and supervisor-exit
proof. It reuses the same FinalService, original executor, aggregate transaction,
canonical Run/Step/Attempt/effect, and existing pulse. There is no second engine,
provider, ledger, reservation counter, receipt stream, timer or scheduler.

| Policy / condition | Candidate behavior |
|---|---|
| automatic=false | No automatic retry or new policy inference |
| failed/timeout, reset="0" | Existing lifetime per-Step budget retained; long execution never resets it |
| failed/timeout, positive Decimal reset, trusted original execution interval < threshold | Consecutive budget unchanged; normal exhaustion/backoff |
| Same, elapsed == or > threshold | Stable rejected Attempt becomes first failure of a new window; backoff restarts from first configured entry |
| Positive reset, missing/malformed timing | Known original result remains failed; no reset or automatic replacement is authorized |
| Missing cleanup / live or unproved supervisor | Original lookup holds; no reset, new Attempt or replay |
| True unknown/partial, lost/ambiguous ownership | Original-identity query only; elapsed prose/timing never overrides uncertainty |
| Revoked grants / cancellation | No new Attempt or recovery-window mutation; an already waiting deadline/base is preserved |
| Pause | Existing boundary/pulse behavior; waiting alone never earns a stable window or consumes an Attempt |
| orphaned/interrupted/dependency-unavailable/cancelled automatic reasons | Still explicitly unsupported before write |
| monitoring completion | **Not implemented**; explicit capability_unsupported, including when the Action declares monitoring. Old frozen monitoring execution is blocked before effect; historical original receipt cannot be completed from exit0 alone. |

No migration script or real-data migration was performed. Ordinary retryIf still
maps extra retries exactly `maxAttempts=maxRetries+1` for representable budgets,
never clamps. Stable-reset monitoring is a different legacy policy family: its
existing maxAttempts already bounds consecutive failures including the current
one. Do not apply the retryIf +1 conversion to it. Budgets over the existing bound,
complex records and unsupported completion contracts retain explicit D6 gaps.

## Original semantics, checked from exact source

At baseline `packages/jobs/action_runtime.py::Runtime.finalize`, lines 512-524,
only `status='restarting'`, enabled recovery and a non-safe-boundary result enter
the old reset calculation:

```python
stable = reset > 0 and time.time() - execution_audit['startedAt'] >= reset
count = (0 if stable else previous_failures) + (0 if gate_blocked else 1)
```

The source uses `get` defaults, but the actual admitted execution has startedAt
and frozen policy. It does **not** reset historical Attempts or mean “time spent
waiting since last failure.” The just-ended stable failure counts as 1. Tick
checks that count against maxAttempts before starting a successor, and finalize
selects the corresponding backoff entry. RecoveryBlocked/business gate wait does
not consume another effect Attempt. Old source is retained, not called or revived.

`Runtime.execute:636-645` additionally requires monitoring success to carry a
matching exact `{type,completed:true}` signal and processTreeEnded=true; unknown
remains uncertain regardless of signal. `action_templates.py:133-154` declares
monitoring/recoverable policy and prohibits parallel monitoring. The source
characterization test executes **baseline FinalService._references** against an
owned definition and proves positive reset was rejected before this change. It
also binds the old reset/signal expressions to baseline source. It does not claim
the retired Runtime was successfully executed.

## Implementation and identity safety

`TypedExecutor._process` now adds `receipt.executionTiming` from that original
executor invocation: RFC3339 startedAt/endedAt plus startedMonotonicNs and
endedMonotonicNs. The interval covers spawn/wait/owned cleanup/handle close, not
queue/backoff/host absence/late lookup or supervisor-exit polling. Nanosecond ticks
are compared only within one invocation; they are not compared across workers or
reboots. Decimal subtraction/division is exact for that resolution, UTC clock
adjustment does not mint stability, and UTC fields remain validated provenance.
This is conservative relative to the old host startedAt-to-finalize wall interval:
detached bootstrap delays and offline observation latency do not count as stable
process execution. No platform or independent runner method changed.

`FinalService._stable_execution_seconds` validates these original receipt facts;
caller arguments/log text cannot assert them. `_schedule_automatic` reaches this
logic only after existing frozen reason, latest Attempt, original full proof,
Job availability and original-authority/current-grant gates. Positive policy with
missing or invalid interval fails closed. The existing original lookup revalidates
effectId/effectKey, Attempt lease/epoch/generation and frozen executable digest.

When the original interval reaches the frozen positive threshold, the same
per-Run mutable plan stores one per-Step boundary:

```json
{"recoveryWindows":{"s0":{"afterAttemptId":"original_failed_attempt","baseGeneration":1}}}
```

It stores no independent count or retry history. Usage is derived from the
existing monotonic `Step.attemptCount - baseGeneration`. For a stable failure at
generation 2, baseGeneration=1, so usage=1 and a maxAttempts=2 window permits
generation 3. A following short failure uses 2 and exhausts. Generations never
decrease; old Attempts/effects are never deleted, rebound or rewritten.

`_automatic_budget` validates the boundary against the exact original failed
Attempt/generation, rejected canonical effect and its frozen-policy stable timing.
Malformed boundaries cannot buy budget. `_claim_automatic` uses that derived
budget while retaining the original proof/current grant checks. Receipt + stable
window + deadline + transition are in the existing owner-fenced aggregate commit;
a failed write rolls them all back. Consumed original pending references prevent
late/duplicate lookups from applying the stable decision twice. Waiting markers
survive cold reopen unchanged, and the existing pulse claims at most one Attempt.

`_references` accepts the positive duration with the prior process/reason/retry
safety constraints. Monitoring is specifically rejected in Job/Template admission.
`_drive_run` blocks old frozen monitoring before effect admission, and the original
receipt-recovery branch holds historical monitoring completion query-only. Cancel
cleanup still owns cancellation settlement; this is not a substitute completion.
No Recovery/completion schema change was necessary; the existing Decimal field
already expresses this supported slice. The schema generator/check has no diff.

The only existing test change replaces the revision3 parameter that asserted
positive reset was unsupported with a negative-duration invalid_schema case.
The original red result is preserved in `first-regression.xml` (183 passed, 1 failed).
No assertion body was weakened. Positive reset has new before/exact/after and actual
default-host successors. Original retired retryIf/monitoring tests and retirement
mapping are unchanged and are not declared green.

## Exact monitoring blocker and coordinated alternatives

The model already has a monitoring completion contract; it is not necessary to
invent another engine/provider. The missing part is a trustworthy existing-host
**structured completion result path**, before executor terminal settlement:

- Old `packages/actions/provider.py::execute:103-113` parsed bounded JSON stdout
  separately from stderr when output_format=json. Runtime then checked
  structuredContent.panJobCompletion + processTreeEnded.
- Current `_process` chooses success from exit outcome/cleanup. Original
  `independent_execution_runner.execute_claimed` uses RecordingOwner.spawn to merge
  stdout/stderr into the canonical log and stores that typed result directly with
  `store.finish`. It does not parse or attest a declared completion signal.
- A Step declaring monitoring cannot safely be completed by a plain exit0 receipt.
  Nor can FinalService relabel a stored succeeded effect as failed in order to
  replay: `ExecutionStore.admit` intentionally forbids same-key repetition after
  succeeded. Weakening that fence would be a model/safety conflict.

The exact smallest alternative for MA scope coordination is: select/freeze a
declared process monitoring contract and signalType into the executable plan;
bind it in `_drive_run` and `_validate_process_document`; at the original
`independent_execution_runner.execute_claimed` result boundary, preserve a bounded
structured stdout result separately from log/stderr, validate the signal against
that original Attempt, then store a matching completion proof or a known rejected
monitoring-incomplete receipt **before** `store.finish`. This requires an explicit
output encoding/size/source contract and coordinated runner method scope. It
must not silently parse arbitrary merged log lines or add an externally asserted
signal endpoint. This candidate does not implement that alternative.

MA was sent this exact symbol-level choice through Pan with stable message ID
`recovery-r4-monitoring-symbol-coordination-20261029`, queue item
`q_c8f68e445067418998a5cbc56e9d80c2`; receipt accepted, consumption unconfirmed.
That receipt is not scope approval. The independent stable-reset slice continued
to completion while the affected monitoring implementation remained held. This
report explicitly leaves old monitoring success closure unaccepted/unimplemented.

## Verification and limits

The evidence summary binds final source `e48926a5...`, commands, JUnit counts and
changed function lines. Commands use `D:/project/Pan-main/.venv/Scripts/python.exe`
through `scripts/run_tests_isolated.py`; Windows only, owned temporary data and
processes, ephemeral loopback HTTP port, no 8767/8768 or deployed profile.

Final-source groups:

1. `tests/test_job_recovery_stable_reset.py`, `test_job_automatic_recovery.py`,
   `test_job_final_executors.py`, `test_automation_retry_parallel.py`.
2. `tests/test_job_final_independent_execution.py`, `test_job_final_http_service.py`,
   `test_job_final_contracts.py`.

Counts/exits are recorded in `evidence/job-recovery-r4/verification.json` and the
`source-e48926a5-*.xml` files. Earlier exploratory/source-9c3030f4 results are
preserved separately and are not added again to final-source totals. The initial
positive-reset rejection assertion is the only expected stale-policy red; no old
65-test rerun is counted as progress.

Exact final-source results: group 1 **157 passed**, group 2 **224 passed**;
**381 distinct passed, 0 failed/errors, 0 skipped**, both commands exit 0.
This includes **35 new revision4 characterization/stable/reset/monitoring-boundary
cases**. Generator check and diff check also exit 0. The offline evidence summarizer
verifies exactly two product files/four source-and-test files changed, and thirteen
ledger/runner/platform/MCP/migration/schema/old-red boundaries unchanged.

The owned HTTP/default detached test sets total maxAttempts=2, has one short
failure and then a genuinely stable 2.2-second failure against a 2-second threshold,
cold reopens the service with its window intact, and completes the third process
via the existing actual `FinalService.start` pulse. It verifies invocation count 3,
one Run/effect, unique generations 1/2/3, two original executor history rows,
frozen policy preservation and released owned HTTP listener. Long thresholds use
fake original execution clock records for before/exact/after checks; no 300-second
wait or process-owner substitution is used in the actual detached proof.

New tests cover consecutive exhaustion/reset, exact Decimal threshold/backoff,
cold reopen/lost lookup ack/concurrent pulse, stale receipts, cleanup/supervisor
fences, true unknown/partial, immutable policy, revocation/cancel/pause, rollback,
corrupt windows, zero reset, and explicitly unsupported monitoring/old exit0
history. Existing failed/timeout/manual QQ behavior remains regression scope;
no QQ/provider/MCP/Worker/notification/migration code was changed.

Not verified: monitoring success/proof transport, real external provider, real-data
migration, Linux/macOS runtime, remote CI, full suite, frontend/browser, production
deployment or human acceptance. No main/practical/frozen-platform ref, push/CI,
existing service, account or original worktree was changed. MA owns acceptance,
combination and any further monitoring contract decision.
