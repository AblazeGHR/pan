# Run retries and bounded Trigger scheduling

Authorized backend batch after Event Store acceptance `dc1a0edd`. This document
supersedes the earlier serial-only/no-retry limitations, preserving Event Store,
identity, State, dynamic Actions, Session selectors and business admission. No
React, Job Template, Job snapshot, new Action snapshot mechanism, external resource
ownership checks or selectable queue strategy is added.

## Configuration and migration

Trigger accepts the following additive fields. New writes normalize defaults;
missing fields on legacy records are interpreted without eager migration. An old
client omitting these fields while updating an existing Trigger preserves its
existing policy, rather than silently switching a configured parallel Trigger.

```json
{
  "executionMode": "serial",
  "maxConcurrency": 1,
  "retry": {"enabled": false, "maxAttempts": 3}
}
```

`executionMode` is exactly `serial` or `parallel`. Serial requires concurrency1.
Parallel defaults to3 and accepts integer1..32. Booleans, fractional numbers,
unknown values and unknown fields are rejected. `retry` is an object containing
only boolean `enabled` (default false) and integer `maxAttempts`1..10 (default3,
including the initial attempt). The hard attempt limit10 bounds history and
operator-confirmed recovery too. Backoff is fixed:1,2,4,8,16,32,60,60… seconds;
there are no jitter/resource/queue strategy settings in this batch.

Action accepts boolean `idempotent`, default false. This is an explicit business
declaration, not an inference from an Action type or HTTP method. Ordinary Action
drafts accept it while remaining disabled. Immutable Group releases, pins, existing
build/legacy provider checks and execution behavior are unchanged; this declaration
does not make legacy tool calls automatically retry.

Creating/editing disabled drafts is still available to Agents. Changing an existing
Trigger's effective execution mode/concurrency requires a user Bearer even when
disabled; enabled writes and enable/disable retain their existing Bearer requirement.
The authorized revision-checked write is the user confirmation of configuration.
Lowering the limit below currently running Run count returns409; wait for running
work to finish, then resubmit with the current revision. No running effect is killed
to satisfy a lower limit. Creation of a disabled parallel draft grants no execution.

## Scheduler

One owner task per Trigger coordinates up to its configured number of attempt
tasks. A ledger claim atomically checks the current Trigger, current running count,
Run status, due time and attempt authorization before persisting running. Stale
snapshots cannot overbook slots. After a rejected claim, the owner rechecks current
configuration/head before using a stale snapshot on another Run. Different Triggers
have independent owners and do not await one another's effects.

Serial uses admission order. A head Run waiting for backoff holds ordering, not an
execution slot; later Runs wait. Parallel may execute other admitted Runs while an
earlier Run is in backoff. Every logical Run has at most one live attempt task,
including the interval between its final commit and task retirement. Due timers,
coalesced kicks and completion signals wake scheduling; there is no queue polling
loop, sleeping thread, or global lock spanning execution. Current ledger count is
the slot authority, with no external resource lock/occupancy inspection.

The existing bounded pending-Run limit1024 includes queued, running and retry_wait.
Overflow still produces a failed history record with no attempt and an explicit
capacity diagnostic. The Event/Run storage caps remain authoritative; capacity
or disk failure is not hidden by retry. Manual recovery also checks pending capacity.

Disabling a Trigger atomically cancels queued/retry_wait Runs and clears their
retry admission/timestamps, preserving completed attempt history. Running work may
finish, but a failure after disable cannot schedule another attempt. Reenable does
not replay cancelled or failed history. Queued/retry_wait user cancellation requires
Bearer and expected Run revision. Started Runs cannot be cancelled through that API.

## Attempt transitions and frozen eligibility

The logical Run ID remains fixed. Each new invocation gets a distinct `att_…` ID,
increasing `number` and durable `activeAttemptId/attemptCount/attempts` before effects.
The first claim creates attempt1. A pre-effect dependency deferral reuses its queued
attempt; an automatic retry or user-confirmed uncertain recovery creates a new one.
Later attempts carry `reason:"automatic"` or `"user-confirmed"`.

The Run copies `retryPolicy` from its Trigger when appended. On the first actual
Action preparation, `retryEligible` is frozen to true only if that policy enabled
retry and the first Action explicitly declared idempotent. A later Action or Trigger
edit cannot promote false/missing eligibility on an already-started Run. Legacy
started Runs never acquire automatic retry eligibility through migration.

Each invocation rereads the Action by ID and retains the existing Action validation
and business path. A later invocation additionally requires its full definition
digest to match the first persisted `actionDigest`; a changed/deleted/disabled Action
therefore cannot silently substitute another effect. A digest conflict fails visibly
without further automatic retry. This deliberately conservative rule also rejects
metadata-only edits for an existing Run. New Events still resolve the current Action
normally. It reuses v0.1's existing Run Action/digest data, not a Job snapshot feature.

On known failure, one atomic transaction finishes the current attempt as failed and,
if permitted, changes the logical Run to `retry_wait` with UTC ISO8601
`nextAttemptAt`. Eligibility requires both frozen and current Trigger retry enabled,
and attempt count below both frozen/current budgets. Increasing a later budget cannot
exceed the original one; reducing the current budget can stop waiting work. The Run's
last error/result remains visible during backoff. `finishedAt` there describes the
completed attempt; retry_wait is not a terminal Run. It is never retention-pruned.

Claim at/after due time durably creates the next attempt and clears transient final
error/result/time fields. Prior attempts keep independent status, timestamps, result,
error, selection and target checkpoints. Their terminal history is immutable through
waiting/cancel/recovery transitions. Ordinary success terminates retries. A failed
match, capacity rejection, skipped condition or cancelled Run never starts this loop.

Session Operation retries reuse the original durable targetIds/selection. They do
not requery, resolve Event selectors or follow renamed/moved/new Sessions. Targets
with persisted `ok:true` results are skipped; failed/unknown targets pass through the
same Pause business entry again. Result ordering follows frozen target order, and
each attempt retains its own checkpoints. A write failure after effects leaves
uncertain rather than an automatically retried failure. There is no atomic commit
across effects and the ledger; idempotence remains the Action author's declaration.

## Recovery and explicit uncertainty confirmation

Restart retains retry_wait due time, attempt count and history. A due wait can run
after recovery; it is the next authorized attempt of the same Run, never a new Run.
Running interruption becomes uncertain and is never automatically retried. Queued
recovery checks current Trigger enablement; disabled queues cancel. Plugin readiness
and current trusted generation availability remain required before claims/effects.
Accepted historical Events survive producer generations under the Event Store rule.

`POST /api/automation/runs/{id}/confirm-recovery` is an independent user-only route:

```json
{"expectedRevision": 7, "confirm": true}
```

It requires a valid user Bearer, exactly these fields, confirm literally true, and
the current uncertain Run revision. Unknown fields/false confirmation ->400,
missing/expired token ->401, missing Run ->404, stale/non-uncertain/disabled Trigger/
attempt limit/backlog conflict ->409. A successful response is `{run}` with the same
ID, queued status, incremented revision and `retryAuthorized:"user-confirmed"`.
Attempt history stays uncertain; the next claim creates the new attempt. Concurrent
or repeated confirmations cannot create another queued invocation. Recovery after
confirmation preserves this authorization but still checks current Trigger/budget.

This confirmation explicitly accepts potential repetition of unknown effects. It
may authorize another attempt for an Action without automatic idempotent eligibility
or with retry disabled, within the frozen/current total budget (default3). It does
not promote automatic eligibility. Current Action digest, validation, plugin and
business gates still apply. Confirmed successful Session targets remain skipped.
The confirmation is not available as an Agent/MCP tool or Event payload field.

The management token is checked at confirmation/configuration/cancellation time.
Automatic execution and an already-confirmed queued invocation do not reauthenticate
the caller or require the token to remain alive. They retain business checks.
Event replay remains separate: it never clears retry count/time, never creates a
second retained Event+Trigger Run, and still refuses uncertain associations.

## UI/backend handoff and limits

`GET /api/automation/schema` returns version1, a strict `scheduling` JSON Schema
fragment, `actionProperties.idempotent`, and limits/backoff constants. Apply the
fragment to the three scheduling fields, not to the complete Trigger object.
Defaults are schema annotations; the backend applies them when writing. The existing
Trigger endpoint accepts these fields; no queue/resource parameter is accepted.
Run readers must recognize retry_wait plus retryPolicy/retryEligible/nextAttemptAt
and attempt history. These are read-only Run data; clients cannot write attempt IDs,
history or internal authorization markers. `contracts.py` supplies transport-neutral
types. No frontend or durable per-plugin consumer transport is added here.

Upgrade is additive; back up profiles and avoid mixed-version access. Older versions
do not understand retry_wait or parallel scheduling and must not operate this profile.
JSON rewrite/storage capacity limits, clock changes, real hardware durability and
uncertain reconciliation outside explicit confirmation remain operational boundaries.
Tests use deterministic clocks/barriers and owned real Node/HTTP/CLI processes. They
do not prove third-party idempotence, production throughput, real-provider behavior,
or overall human acceptance.
