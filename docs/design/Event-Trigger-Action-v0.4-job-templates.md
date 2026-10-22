# Job Action references, complete Job Templates and temporary execution snapshots

Backend batch based on accepted integration36b3a9be. Existing background-process,
message, broadcast, shell, schedule presets, Group releases/pins and high-risk gates
stay intact. No React changes or second Job registry/scheduler/notification loop.

## API/data contract recorded before implementation

An Action-reference Job is an ordinary generic `kind:"scheduled-task"` record with
`action:{api:"action_reference",actionId,arguments}`. New protected configuration
routes are below `/api/jobs/action-jobs`; generic Jobs list/detail/runs show the same
record. Existing `/api/jobs/templates` remains schedule presets. Legacy mutation/
run-now aliases reject the new protected action kind, rather than bypass approval.

Complete config has only `name`, `description`, `actionId`, `arguments`, `schedule`,
`enabled`, `maxRuns`, `misfirePolicy`. Schedule uses the existing strict array of
once/interval/cron specs. No text/extra content, target override, arbitrary API,
argument binding or undeclared field is accepted. Arguments use only Action schema
properties, including declared defaults, and must satisfy required/type constraints.
An Action with zero input properties accepts only `{}`. Optional properties without
defaults are intentionally absent, not unresolved template parameters. There is no
interpolation language. Runtime context is host-derived, never caller-forged.

| Route | Contract |
|---|---|
| GET `/api/jobs/action-jobs` | Action-reference Job configs and derived integrity |
| GET `/api/jobs/action-jobs/schema?actionId=...` | Versioned config/template/instantiate JSON Schemas and limits; selected Action argument schema |
| GET `/api/jobs/action-references/{actionId}` | Referencing Job/template IDs and exact reference positions |
| POST `/api/jobs/action-jobs` | `{config}`; Agent creates disabled draft; user may confirm enabled config |
| PUT `/api/jobs/action-jobs/{id}` | `{config,expectedRevision}`; user required for enabled writes, enable, or repairing/replacing references; replacement stays disabled |
| POST `/api/jobs/action-jobs/{id}/run-now` | Bearer +`{expectedRevision}`; enabled complete config only |
| POST `/api/jobs/action-jobs/{id}/cancel` | Bearer +`{expectedRevision}`; reuse Job cancellation and terminal notification |
| DELETE `/api/jobs/action-jobs/{id}` | Bearer +expectedRevision; no deleting running/cleaning execution |
| GET `/api/jobs/job-templates` | Complete configuration templates, revisions and integrity |
| POST `/api/jobs/job-templates` | `{item,expectedRevision:0}`; Agent disabled drafts only |
| PUT `/api/jobs/job-templates/{id}` | `{item,expectedRevision}`; exact template revision; enabled changes/repairs require user |
| DELETE `/api/jobs/job-templates/{id}` | Bearer +expectedRevision; existing instantiated Jobs unaffected |
| POST `/api/jobs/job-templates/{id}/instantiate` | Bearer; exactly `{expectedRevision,idempotencyKey,overrides?}`; overrides only name/description |

Template item contains `id?`, `name`, `description?`, `enabled` (default false),
`config` (complete canonical config). Disabled is approval state, not permission to
leave parameter/schedule holes. Instantiate copies config and records
`templateId/templateRevision`. Future edits cannot change that Job. Missing/stale
revision conflicts409, user auth401, invalid/unknown fields400, absent404. Successful
new responses use `{item}`, `{job}` or `{templates}`. Bound sizes/counts are exposed
by backend constants; no unbounded request keys, executable histories or templates.

Action execution adds `{kind:"job_template",templateId,templateRevision}`. Its
input schema must have no properties and its effect must be write_local. Only that
fixed complete enabled template/revision may be instantiated; no input override.
An enabled Action delegates this fixed capability, so automatic execution does not
require its earlier user Bearer to stay online. Success reports a Job ID/creation
receipt, not Job completion. Group build/provider legacy entry refuses this kind.
Reference graphs reject cycles/excess depth, never recursively spawn by accident.

## Deletion, completeness and authority

Action definitions, per-Action revisions, deletion generations, complete templates,
idempotent creation intents and temporary execution snapshots live in the existing
Action registry. A single atomic replacement serializes definition deletion vs
snapshot admission: delete first blocks snapshot creation; snapshot first permits
that execution to finish from its private definition. No global gate spans effects,
subprocesses, Session IO or Job registry writes. Registry work runs off-loop.

Jobs store references/arguments and approval generation, not Action definitions.
Integrity is derived from the current authoritative Action registry and reports
`complete`/`incomplete`, exact actionId and reference position (including nested
template reference issues). Deleting an Action immediately makes configurations
incomplete without needing a fallible fanout rewrite of every Job file. The same
Action transaction increments a deletion generation. Restoring that ID may restore
completeness but cannot restore execution approval: old generations require user
repair/reapproval. Changing a Job's reference increments config revision and disables
it; a separate user write enables it. Running status is independent of integrity.
Action deletion with Job/Template references requires Bearer and explicit confirmation;
Group references retain the preexisting remove-references-first restriction.

Templates instantiate under a registry transaction checking current revision,
approval, full config and reference graph, reserving a deterministic Job ID for a
server-scoped idempotency key. The persisted intent contains copied Job config only,
never an Action definition. It precedes Job creation outside the Action lock. Retry
or recovery completes that same intent; final receipt retains only IDs/digest.
Same key/different request conflicts; deleting the created Job does not permit key
reuse to recreate it. Template edit/delete after reservation cannot change that
admitted copy; edit/delete before reservation fails old-revision admission. This is
an explicit two-file recovery protocol, not a cross-file atomic transaction.

## Temporary execution snapshots and recovery

Each actual execution claims its generic Job record, then atomically validates the
Action/reference/approval and records a temporary snapshot before any effects. It
contains Action definition, filled fields, Action revision/digest and derived context
(Job/execution/dispatch identity, fire time). Later Action edits do not change that
execution. Query targets are checkpointed once. Existing business executor/Pause
entry and high-risk checks remain mandatory; no historical snapshot resolves a
missing reference for a future execution.

The Job stores execution identity and phase, never the full executable snapshot.
On success/failure/cancel/unknown/uncertain, full snapshot/checkpoints are removed
before publishing terminal Job state; only non-executable reference/digest/count/
status audit survives. Staged finalization handles failures between cleanup and Job
terminal commit. If cleanup cannot persist, execution remains visibly cleaning/
cleanup-pending and is not published as terminal-with-a-snapshot. No effect is retried
from cleanup/recovery. Restart abandons preexisting in-flight execution as uncertain,
cleans its snapshot, reconciles its Job record and emits the existing run/event fact.
Orphan snapshots are cleaned too. Existing Automation Run terminal Action copies
are also discarded while digest/qualification/attempt audit remain; Group immutable
releases are deliberately excluded from ephemeral cleanup.

The existing scheduler recovery loop invokes this dispatch path and bounded live
tasks, using existing schedule entry evaluation/run records/Job notifications. There
is no independent ticking service. New Jobs can be cancelled through their protected
endpoint; shutdown cancels/joins tasks and settles disk writes before recovery.
Legacy process/message/scheduler paths are left behavior-compatible.

## Migration/limits and acceptance boundaries

All changes are additive under existing profile roots; no historical Jobs are
converted into Action refs, no history definition is used as a missing-reference
fallback, and no import-time data migration runs. Existing schedule preset templates
are not complete Job Templates. Profiles must not be opened by mixed old/new writers;
older versions do not implement these approval/snapshot recovery rules. Back up
before upgrade/downgrade. Persistent storage corruption/failure may prevent cleanup
and will be reported, not treated as a successful terminal transition.

Tests must cover unknown fields/empty-schema injection, user/Agent permissions,
idempotent instantiate, mutation/deletion/start interleavings, live snapshot stability,
missing references and restoration, cancel/exception/restart/cleanup failure,
unrelated Session progress on slow storage, template Action delegation and legacy
Group/process/message compatibility. Real isolated HTTP uses owned random ports,
profiles and deterministic CLI only. UI and overall human acceptance remain pending.

## Implemented recovery details and bounded storage

The Actions profile uses a nonblocking lifetime ownership file lock, reusing the
generic Job service lock implementation. Only one new Pan runtime may recover and
execute that profile; duplicate startup cannot clean another owner's snapshots.
The lock is released on close or OS process death. This is not a business resource
lock or a mutex across Session effects. Existing independent background command
runners are unaffected. Direct registry writers and mixed old/new Pan writers are
outside this ownership protocol and must not run concurrently.

Creation reserves a durable intent/receipt in the Actions registry, publishes the
ordinary Job file, then removes the intent's copied configuration. Interrupted
publication returns HTTP503 with `detail:{message,jobId,pending:true}`; retry
instantiate with the same key. Recovery finishes admitted creation even if its
source template was subsequently edited/deleted. An interrupted delegated Action
is uncertain, never falsely failed while its admitted Job can still publish.
Completed receipts retain fingerprints/IDs, not executable Action definitions.
Deleted Jobs cannot be recreated by reusing their receipt keys.

Reference replacement reserves the union of old and new references before the
Job file CAS. Successful publication narrows the index; failed publication leaves
conservative deletion protection. Initial owned recovery reconciles against actual
Job files. No Action registry lock spans Job file I/O. This is a recovery protocol,
not a cross-file atomic transaction. Configuration revision, integrity and execution
status are separate; changing a running configuration does not replace its private
execution snapshot. Action revision/deletion generation remain authoritative at
snapshot admission.

Snapshots contain definition, filled arguments, source/registry revision,
definition/argument/context digests and host execution context. A template Action
also freezes its admitted complete template configuration. Cleanup persists a Job
`cleaning` intent before erasing the full snapshot and committing terminal audit.
Orphan snapshots produce an uncertain run audit and are removed. Notification
failures retain markers and use the existing scheduler tick to retry notifications,
never effects. Disk failure can visibly retain `cleaning`; repair storage and resume
the same profile to finish cleanup. There is no executable history fallback.

Limits: 1 MiB per submitted document/execution snapshot, 256 live templates,
10,000 distinct template revision tombstones, 10,000 instantiation receipts and
128 live snapshots per Actions profile; schedule1..32/reference depth8. Capacity
rejects visibly, never silently evicts idempotency tombstones. Receipt archival and
operator reclamation are deferred; operators must monitor capacity before reaching
the bound. New concurrent Action dispatches use the existing scheduler's
`max_concurrent_dispatch` setting (default5, clamped1..32); legacy dispatch semantics
are preserved. Scheduler-wide policy changes are outside this batch.

Templates copy absolute once schedules without shifting their dates; an expired
once schedule fails existing schedule validation on future instantiation.
The unified implementation below supersedes the original backend-only scope.
Input interpolation, unfinished parameter templates and automatic profile downgrade
conversion remain excluded. Agent traffic has no privileged instantiate/repair
shortcut. Overall human acceptance remains separate from automated validation.

## Unified Job follow-up

The unified backend projection is additive. New Action-reference Jobs persist
`trigger`, `recovery`, `overlap`, `notification`, and `completion` policies with
conservative defaults (`recovery.enabled=false`, `forbid` overlap, parallel limit
3, ordinary completion, job-scoped terminal notifications). Existing `schedule`
and legacy `kind` remain readable and executable through compatibility projection;
unknown legacy kinds map to a disabled-recovery system Action view and are never
implicitly migrated or enabled.

Run claim/finalization now records a stable Run ID and Attempt ID/count, action and
template references, backoff, receipt and side-effect uncertainty. Snapshot
cleanup remains the gate for every terminal outcome. Monitoring completion requires
a structured completion signal; ordinary Actions complete from their returned
result. A monitoring Action without a signal enters `restarting` and consumes a
bounded declared recovery attempt after persisted backoff; unknown/uncertain
outcomes are not automatically replayed. Terminal notification events are emitted
through the existing durable Job event path and use the Job notification allowlist,
never Session message targets.

The new `unified` field is included in the existing Jobs API projection, while old
`kind`/API routes remain compatible. React's New Job entry edits the shared complete
configuration or a complete Template; it no longer chooses a purpose kind.

## Unified execution contract (2026-10-23)

New Jobs use `actionId`, `arguments`, `trigger`, `recovery`, `overlap`,
`notification`, `completion`, plus name/description/enabled/schedule/maxRuns/
misfirePolicy. Unknown keys/types fail closed. Parameters are validated against
the referenced Action's declared properties as well as its schema; an empty
schema accepts only `{}`. Action definitions declare `monitoring` and `recoverable`
(both false for old data). Ordinary completion needs only a successful business
result. Only monitoring/recoverable definitions can opt into monitoring/recovery.
Action changes affect subsequent Attempts, never an admitted snapshot; reference
deletion makes configuration Incomplete immediately. Restoring/replacing a deleted
reference requires user approval and leaves the repaired Job disabled.

| Compatibility kind | Product-owned Action ID | Admission / effect capability |
|---|---|---|
| background-process | system_background_process | Existing safe owned process runner |
| session-message | system_session_message | Existing ordinary send / queue gates |
| session-broadcast | system_session_broadcast | Existing ordered per-target ordinary send |
| scheduled-task | system_scheduled_task | Existing assign/send_session/resume_legal_running/shell/send_qq business entry |
| main-lifecycle | system_main_lifecycle | Existing protected supervisor only; generic Job/Template creation rejects it |

System definitions live in the existing Action registry, are installed explicitly
on owned runtime startup and cannot be overwritten/deleted by user or Agent.
They cannot enter legacy Group releases or the legacy provider execution path.
Internal executor operations do not grant arbitrary API dispatch. The existing
supervisor authenticates/adopts its own lifecycle intent and admits the same
Run/Attempt/snapshot lifecycle before effects; exposing its definition does not
delegate service restart authority. Lifecycle options/identity have a private
snapshot context and only its digest remains in terminal audit.

Legacy endpoints and records are retained during this compatibility window (no
removal date or destructive migration in this candidate). Their actual effects
now admit a System Action snapshot and Run/Attempt in the existing Job file before
execution; terminal cleanup and audit are one atomic Job write. New reference
Jobs use the existing Action snapshot storage and durable cleaning protocol above.
These are persistence formats within one registry and scheduler, not a second Job
system. Old PID/birth/owner/generation checks, ordinary message receipts and main
lifecycle gates remain authoritative. New process Actions call the original
runner, wait on its existing terminal events and checkpoint child Job identity.
On host interruption the parent becomes uncertain and cleans its snapshot; the
original independently owned child is reconciled through the existing PID/birth
checks. It is never silently spawned again or treated as completed without a
receipt. Explicit recovery of an unknown process requires a trusted terminal child
receipt; unavailable identity blocks recovery.

Trigger kinds are `immediate`, `once`, `interval`, `cron`, `event`; `schedule` is
the existing multi-entry compatibility spelling. Immediate/event use `schedule:[]`;
time triggers require complete matching schedule specs. Event requires1..32
distinct `eventTypes` and the existing seven-field typed Session query. It uses
the existing durable Event Store target list, revision check and per-Job event
receipt before dispatch. Unavailable sources retain queued intent. Query IDs are
resolved once at Attempt admission, persisted before effects, sorted/deduplicated
by the shared resolver, exposed as `jobSelection.ids` to an event Session selector,
and reused within the logical Run. Zero matches succeed with diagnostics.

Overlap modes: `forbid` rejects overlap; `queue` persists up to128 waiting Run
intents; `parallel` requires stored limit1..32 (default3). Claim checks capacity
under the existing per-Job lock. Final configuration CAS rechecks active count
before lowering a limit. Queue capacity and audit capacity reject visibly; there
is no new external resource lock. Disable cancels queued intents; reenable never
replays cancelled history. A queued intent whose config revision changed is
cancelled explicitly. Different Jobs do not share an effect-duration lock.

Monitoring completion is an exact structured result:
`{"panJobCompletion":{"type":"<declared signalType>","completed":true},"processTreeEnded":true}`.
For owned process Actions the runner reads a bounded64KiB log tail for exact JSON
records; it trusts process-tree termination only after the original owned tree
cleanup. Generic command Actions use their shared execution result. A wrong or
missing signal or an unterminated tree cannot complete a monitor. Unknown delivery
always stays uncertain, even with a missing signal, and is never auto-retried.

Recovery defaults off. A declared monitor may enable maxAttempts1..10 (default3,
including initial Attempt), three backoffs0..86400 seconds (default5/15/60), and
stableResetSec0..86400 (default300;0 disables reset). Each restart preserves Run ID
and allocates a new Attempt ID. Backoff/count/nextAttemptAt persist; stable running
resets consecutive failures, not audit history. Pause/business gates block before
claim without consuming budget. A gate changing after claim is rechecked at the
business entry; blocked admission retains intent. Started Run eligibility and
completion/recovery/notification policies are frozen so editing an Action cannot
elevate an existing Run. Exhaustion becomes final failed. Unknown/uncertain require
the separate user-confirmed revision endpoint, never automatic recovery.

Each Attempt has id/runId/count/status/actionId/revision/digest/template revision,
backoff/nextAttemptAt/receipt/sideEffectUncertainty/snapshotCleanup audit. `cleaning`
and `snapshotCleanup:pending` mean no effect replay and no falsely completed state.
Clearing the executable snapshot must succeed before terminal status publication.
History is non-executable. Attempt/event dedup ledgers cap at10,000 per Job, waiting
queue128 and concurrent Action snapshots128; archival is an explicit future
operator operation, not silent eviction of safety receipts.

Notifications are `notification:{target:"job",on:[...],sessionIds?:[...]}` with
completed/failed/cancelled/unknown/uncertain/cleaning allowlist. Recipients are
independent of Action business targets. The durable per-Attempt outbox calls the
existing Job event path and `enqueue_notice` with a stable event ID; normal durable
report/force/unknown/Pause/Illegal/owner gates still decide actual handoff. Only
IDs/status are queued, never Action arguments/message body. Failed notice admission
retains the outbox, not a runnable effect. Existing legacy report routing remains
compatible with old target semantics.

React supports Action/system Action reference, declared JSON fields, all trigger
kinds/typed query, completion/signal/recovery/backoff, overlap limit, separate
notification recipients and complete Template revision editing/instantiation.
401/409 keep drafts, dirty switching/close is confirmed, and Incomplete/reference
approval, cleaning/restarting and per-Attempt state are displayed. User key login
uses the existing memory-only Bearer. Fixed Template instantiation never overlays
editor changes and reuses the idempotency key for an unknown response.

Migration is conservative: missing additive policies default serial/no recovery/
ordinary completion; unknown policy values show Incomplete and cannot execute.
Unknown historical kinds remain readable and never acquire recovery. No automatic
profile downgrade conversion, mixed-version writer support, production credential
export/OAuth integration or real provider/account effects are included. Browser
fixture validation is separate from overall human acceptance.
