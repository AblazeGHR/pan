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
No Job retries, input interpolation, unfinished parameter templates, new MCP CRUD
tools, React controls or automatic profile downgrade conversion are added here.
Gateway/API discovery can use protected HTTP routes; Agent traffic has no privileged
instantiate/repair shortcut. Production UI and overall human acceptance remain
separate work.
