# TA candidate — finite Run retries and bounded Trigger scheduling

- Worktree: `D:/project/pan-worktrees/event-trigger-core-20261022`
- Branch: `feature/event-trigger-core-20261022`
- Accepted integration baseline merged into this tree: `dc1a0edd56aab3da0f9478d4162213dcafb9c266` (fast-forward, all prior candidate history retained).
- Exact tested source: `1fc9ddc85560d6d60208e956efef0fc2054cbac5`.
- The following evidence-only commit changes no product source, tests or API contract.

## Delivered behavior and UI handoff

Trigger defaults to serial/concurrency1. Parallel defaults3, bounded1..32. Ledger
claim atomically checks current running count, status and due time; each Trigger
has one coordinator and bounded invocation tasks, independent of other Triggers.
There is no new external resource ownership/lock/queue-policy feature. Reducing
below actual running count rejects409; authorization and expected revision apply.
Older clients omitting additive fields preserve the configured existing policy.
Agents can create disabled drafts, but cannot enable or change existing concurrency
without a user Bearer. Enabled configuration writes retain existing protection.

Automatic retries default off. They require frozen original Trigger opt-in and
explicit original Action idempotent=true plus current opt-in; maxAttempts defaults3
including first, backend range1..10. Exponential backoff1s capped60s is persisted
as nextAttemptAt under retry_wait. One logical Run keeps distinct immutable terminal
attempt histories. Disable/cancel covers queued and retry_wait; started work may
finish, and reenable does not replay history. Event replay does not reset attempts.

Uncertain never automatically retries. A separate user Bearer route with exact
revision and confirm:true authorizes one queued recovery, within frozen/current
budget. This can explicitly accept unknown-effect repetition even if automatic
retry is off or idempotence false; it never promotes automatic eligibility.
Existing Action validation, plugin dependencies, Group pin/legacy execution and
Pause/Illegal business paths stay authoritative. Token expiry/logout after admission
does not reauthenticate or cancel automatic/confirmed execution.

Session Operation attempts reuse frozen IDs/selection and skip confirmed successful
targets; failed/unknown targets use the same business entry. Query is not rerun after
Session mutations or restart. Existing Run Action/digest data is reused; a later
Action digest change fails visibly without effects or another automatic retry.

Stable API/schema and migration contract:
`docs/design/Event-Trigger-Action-v0.3-retry-scheduling.md`.
- GET `/api/automation/schema`: strict scheduling fragment, idempotent property, limits.
- Existing Trigger write: executionMode/maxConcurrency/retry.
- POST `/api/automation/runs/{id}/confirm-recovery`: Bearer, exactly `{expectedRevision,confirm:true}`.
- Existing cancel now accepts retry_wait. Run readers must support retry_wait,
  nextAttemptAt, retryPolicy/retryEligible, activeAttemptId/attemptCount/attempts.
- `packages/automation/contracts.py`, core contract, phase supplements, Action docs
  and Pan tool/HTTP docs are synchronized. No React change or new Agent recovery tool.

## Executable matrix

All final rows below were run at the exact source SHA above. Full command is in
`validation.json`; filenames are below `tests/` unless otherwise stated.

| Requirement | Tests/evidence | Result and limit |
|---|---|---|
| Strict defaults/unknown fields/types/bounds | test_automation_retry_parallel.py parser/schema parameter cases, legacy-client update, idempotent validation | Defaults serial/off; parallel1..32 and attempts1..10; no silent unknown acceptance |
| Atomic same-Trigger cap / different Trigger independence | atomic_claim_fences_concurrent_readers_and_trigger_limit; parallel_bounded_independent_and_live_reduction |8 concurrent claims respect2; another Trigger progresses; stale reduction read cannot skip the head |
| Serial retry order / parallel progress | serial_retry_head_order_and_parallel_independent_tail | Serial backoff blocks its tail; parallel can use free slots |
| Runner close/lost wake/claim races | all8 existing automation_concurrency cases; new parallel empty snapshot, close-after-claim and unretired-attempt barriers | No post-close creation; one owner; a logical Run never overlaps its retiring invocation |
| Retry opt-in and frozen qualification | idempotence_or_policy_cannot_be_promoted_after_first_start; action_change_during_backoff_fails_without_new_effect | Later idempotence/Trigger edits cannot promote the Run; changed digest stops later effect |
| Count/backoff/restart/Event dedup | retry_backoff_budget_identities_event_dedup_and_recovery; event_store_replay_during_backoff_never_resets_attempt | Same Run3 distinct attempts; persisted1s/2s due time/count; old Event replay does not create a new Run/attempt |
| Disable/cancel/reenable | wait_cancel_disable_and_reenable_never_replay_history; disable_running_allows_finish_cancels_queue_and_wait | Waiting cancelled, running completes, no failed retry after disable, no replay after enable |
| Uncertain user recovery | uncertain confirmation revision/budget cases, API auth/malformed/stale/duplicate tests; uncertain_query_confirmation_reuses_ids_and_successes | Uncertain never automatic; independent401/400/409 checks; same Run/new attempt and preserved history |
| Persistence/cancellation | retry_schedule_write_failure_is_uncertain_not_reexecution; cancel_after_finish_commit_preserves_committed_outcome | Failed retry schedule commit becomes uncertain; cancellation after final commit preserves durable result |
| Query/target/business safety | query_retry_uses_frozen_targets_and_only_failed_targets +6 selected prior query cases +core suite | Query once, skip confirmed success, preserve failures; legacy/draft/invalid/zero-result gates retained |
| Plugin generation/readiness coexistence | retry_pause_gate_plugin_loss_and_new_generation; selected actual external fanout3 modes, rejection/restart/lifecycle cases | Retry waits when dependency unavailable; revoked handle rejected; real readiness resumes eligible Run |
| Group release/pin/legacy | actual_spawn_path_pins_release_and_fences_unbuild; high_risk_and_disabled_cannot_build; generic_modes_isolate_sessions_and_freeze_rebuild | Existing immutable Group/mode/pin restrictions retained |
| Event Store compatibility | all15 prior store regressions; both actual Event Store HTTP tests | Commit-before-delivery, protected retention/capacity, query Pause and restart still pass |
| Real Node/HTTP/command retry/uncertain | test_automation_retry_http.py; http-evidence.json |3 Events produce3 Runs with failed→succeeded histories;2 real retry commands blocked at configured cap; independent fakeCLI queue consumes; logout does not cancel retries; fourth Run shutdown recovers failed→uncertain, user confirms distinct attempt3 |
| Shared delay entry |2 selected mcp_delayed_restart cases | Actual isolated stdio/HTTP lifecycle and original offline contract retained |
| Cleanup | cleanup.json +HTTP evidence | Owned PIDs/create times gone; own port53220 bind rechecked; no protected ports touched |

## Final validation

**114 passed, 0 failed, 0 errors, 0 skipped, 1 existing pydantic warning**, exit0,
56.56 seconds (`final.xml`, `final.log`). The warning is the preexisting unresolved
lifespan forward annotation; it was not suppressed or changed. Canonical Python:
`D:/project/Pan-main/.venv/Scripts/python.exe`, via `scripts/run_tests_isolated.py`.
Default Windows encoding cp936, utf8_mode=0, PYTHONUTF8/PYTHONIOENCODING unset;
new text IO and subprocess decoding explicitly use UTF-8. `static.json` records
compileall and diff checks, both exit0. No repository-wide full suite was run.

Reproduce the exact saved command in PowerShell from this tree:

```powershell
$validation = Get-Content artifacts/retry-parallel/validation.json -Raw -Encoding UTF8 | ConvertFrom-Json
$testExe = $validation.command[0]
$testArgs = $validation.command[1..($validation.command.Count - 1)]
& $testExe @testArgs
```

## Failure evidence and fixes

- `before.xml/log` and `before-command.json`: baseline3 failed, exit1. Missing
  parallel Trigger, Action idempotent declaration and uncertain confirmation were
  established before implementation; no skip/xfail or assertion weakening was used.
- `second.xml/log`:1failed/102passed. The new test helper could accept the old
  retry_wait before attempt2 started, then read another state. It now waits for
  the expected attempt count too, retaining count/time/identity assertions.
- `http-first.xml/log` and `http-first-failure-state.json`: the example Node's
  single atomic publication mailbox was overwritten by3 immediate writes; only
  the final fact was sent. The owned fixture now waits for each publication
  receipt before replacing the mailbox. Product publication semantics unchanged.
- `fourth.xml/log` and `stale-limit-failure-ledger.json`: deterministic blocked
  owner held an old cap2 snapshot after reducing to1. Rejected claim of head2 could
  be followed by claim of head3 after a slot freed, leaving2 queued. The owner now
  stops that candidate scan and refreshes policy/head before another claim. The
  same barrier test passes in the final source. Atomic claims always retained the
  slot cap; this fix preserves ordering across stale configuration reads.
- Additional regressions cover shielded finish commit followed by cancellation,
  and a retry_wait commit while the previous attempt task has not retired.
  Durable terminal results/history are preserved; task lifetime cannot overlap
  another attempt of the same logical Run.

Interim first/third/fifth/sixth passing evidence is retained but is not added to
final counts and is not presented as validation of the final exact SHA.

## Explicit implementation choices and remaining boundaries

- Backend hard maximum10 total attempts and fixed1s/60s backoff are bounded
  implementation choices under finite retry. They are exposed by schema; no
  configurable jitter, external resource check or queue strategy was added.
- A user confirmation may accept unknown-effect repetition for non-idempotent or
  automatic-retry-disabled Actions; it does not bypass any business gate or
  promote eligibility. This is a distinct uncertainty acknowledgement, not a
  manual failed-retry endpoint. The bounded total budget still applies.
- Later attempt digest equality is deliberately conservative: metadata-only Action
  edits also stop the existing Run. New Events use the new Action normally.
- Effects and ledger commit are not atomic. Unknown effects cannot be made
  exactly-once by this backend; failed status/checkpoint persistence stops automatic
  retry and becomes uncertain where storage permits. Protected states/caps remain
  bounded and observable; persistent disk failure can still prevent recovery writes.
- Existing Event Store pre-commit core ingress window, retention-bounded dedup,
  JSON rewrite throughput and protected legacy-record capacity boundaries remain.
  Retry_wait is protected from pruning. Clock jumps, power loss, sustained load
  and real third-party idempotence/provider behavior were not verified.
- Missing legacy fields migrate semantically to serial/no retry/idempotent false;
  already-started legacy Runs do not gain automatic eligibility. Back up profiles;
  older versions cannot schedule retry_wait/parallel and must not share the profile.
- New frontend presentation/configuration/confirmation flows are pending another
  batch. No React build/Chromium acceptance is claimed here. Overall manual user
  acceptance remains pending. Job Template, Job snapshot, new Action snapshot,
  external plugin transport and later stages remain outside this delivery.

## Isolation / handoff

All real servers, Node peers and fake Action/fake model CLI children used owned
profiles and a random nonprotected port. Evidence records command/PID/create-time
ownership and cleanup; temporary profile paths remain evidence, not shared data.
Historical regression artifacts were copied to `regression-snapshots/` then restored
byte-for-byte; new evidence stays under this batch directory. Read-only existing
Python/Node dependencies were reused; no installation/shared dependency changes.

No integration-tree/main/practical change, push/release, existing service operation,
8767/8768 request, real user data/model/provider use, or subagent dispatch occurred.
The code/test/contract commit is followed only by evidence. Deliver a clean candidate,
then stop for MA independent acceptance; TA completion is not MA/user acceptance.
