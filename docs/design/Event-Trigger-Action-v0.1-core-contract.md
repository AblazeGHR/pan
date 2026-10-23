# Event–Trigger–Action core API and data contract (v0.1)

The additive [system quick fields registry](system-fields-registry-v0.1.md)
defines canonical camelCase Event identity (`system.sessionId`, `workerId`,
`threadId`, and related fields), its trust boundary against payload, limited
input aliases, the read-only discovery endpoint and safe
`system.<field>` Trigger/Action mappings.

Quota host source identity is defined by the
[provider identity revision](quota-provider-identity-v0.3.md): official provider
quota is independently queryable; native/managed metadata is not a prerequisite.
No current global provider value establishes a historical Worker launch binding.
The [new-Worker binding contract](provider-launch-binding-v0.3.md) implements
conditional credentialless fixed process/thread attestation and operation audit;
production official export/credential support remains unavailable, default off.

The next backend batch adds [complete Job Templates, Action-reference Jobs and
ephemeral execution snapshots](Event-Trigger-Action-v0.4-job-templates.md). That
document defines the protected `/api/jobs/action-jobs` and `/api/jobs/job-templates`
routes, revision/idempotency semantics, schema discovery, deletion integrity and
cross-file recovery. Existing `/api/jobs/templates` remains schedule presets.
`execution.kind:"job_template"` delegates only one complete template/revision;
enabling requires user Bearer. Completion means Job creation, not Job execution.
No React changes or new MCP tool family are part of this backend batch.

This is the stable backend handoff contract. The integrated v0.1 candidate now
includes the production Action/Automation/query UI and the local stdio external
plugin adapter. The authoritative scope is Event-Trigger-Action-v0.1.md and its
implementation supplement. See `artifacts/eta-final/DESIGN-MATRIX.md` for the
implemented, verified, deferred and manual-acceptance boundaries; integration
validation does not mean overall human acceptance.

The accepted Event Store phase is extended by the authorized
[retry and scheduling batch](Event-Trigger-Action-v0.3-retry-scheduling.md).
That contract supersedes serial-only/no-retry statements below and defines the
additive Trigger scheduling fields, Action idempotent declaration, retry_wait,
per-attempt checkpoints and separate user uncertainty confirmation. No frontend
change is included in this batch.

## Lifetime and storage

`packages.automation.service.Service` is created/stopped by the web lifespan.
All new files live under `session.SESSION_DIR.parent / automation`:
`registry.json` (Trigger/Run ledger), `events.sqlite3` (durable inbox), and
`user.key` (local user key). There are no
new environment variables and no import-time initialization or migration.
Existing Actions remain under the sibling `actions/registry.json`.

Ledger writes reuse the Action atomic tempfile/fsync/replace primitive in a
worker thread. Writer locks are confined to ledger transactions; no lock spans
Action execution, command/provider waits, or target operations. Matching is one
ordered consumer with a matching-only async lock, independently of executors.
No async event producer performs filesystem I/O. A slow subscriber has its own
queue and task. The bounded queue admission is synchronous and nonblocking.

The next-phase durable Event Store contract is specified in
[Event Store foundation](Event-Trigger-Action-v0.2-event-store.md). It supersedes
v0.1's indefinite retention and volatile acceptance: default retention is seven
days, subject to protected pending/uncertain records, and deduplication is bounded
by retained Events/Runs. Accepted runtime publications commit before delivery.
There is still no exactly-once side-effect guarantee. Pre-commit core ingress and
diagnostics are process-local; the durable guarantee starts at commit.

## HTTP

| Method/path | Request | Result / authorization |
|---|---|---|
| POST `/api/automation/login` | `{key: string}` | `{token, expiresIn:43200}`; invalid key 401 |
| POST `/api/automation/logout` | Bearer header | revoke that in-memory token |
| GET `/api/automation` | none | ledger, definitions, dependencies, diagnostics, limits, `storage:{events,runs}` |
| GET `/api/automation/events?after=N&limit=N` | nonnegative cursor; limit 1..100 (default 20) | `{events,nextAfter}` |
| POST `/api/automation/events/replay` | `{eventIds:[string]}`, Bearer | 1..100 unique retained IDs; `{scheduled,automaticRunRetry:false}`; uncertain -> 409 |
| POST `/api/automation/events/maintenance` | Bearer | fixed retention cleanup counts; no arbitrary deletion |
| POST `/api/automation/triggers` | `{item, expectedRevision, agentId?}` | `{item}`; create revision 0, updates exact Trigger revision |
| DELETE `/api/automation/triggers/{id}?expectedRevision=N` | Bearer header | user token always required; disable before delete; history retained |
| POST `/api/automation/runs/{id}/cancel` | `{expectedRevision:N}`, Bearer | queued/retry_wait only; conflict/start already occurred ->409 |
| POST `/api/automation/runs/{id}/confirm-recovery` | `{expectedRevision:N,confirm:true}`, Bearer | current uncertain only; same Run/new attempt, bounded; conflicts409 |
| GET `/api/automation/schema` | none | strict scheduling schema fragment, Action idempotent property and hard limits |

The Trigger item accepts `id?`, `name`, `description?`, `eventTypes`, `condition?`,
`actionId`, `arguments?`, `executionMode?`, `maxConcurrency?`, `retry?` and `enabled?`. New Trigger defaults to
`enabled:false`; serial is default. Parallel defaults to3, integer1..32; serial
requires1. Retry defaults off with total maxAttempts3 (range1..10). IDs are generated if omitted.
Missing/stale revisions are rejected (409). Authentication failures are 401.
Ordinary unauthenticated callers can only maintain disabled drafts. They cannot
enable, disable an enabled Trigger, edit any enabled Trigger, or delete one.
The server records `createdBy:{type:user}` for authenticated creation, otherwise
`{type:agent,id:agentId}`; agentId is informational, never an auth credential.
The origin survives updates. Token validity is checked for management only.
Enabled automatic runs never read a token or original caller identity.

The key is created locally at service start; read `automation/user.key` locally
for login. Bearers live only in server memory, expire after 12 hours measured by
monotonic time and disappear on restart. Clients must keep Bearers in memory.
This accepts the local-process trust boundary from the design. It does not add
authentication to existing Pan APIs or defend against local key-file readers.
There are at most 256 active Bearers; expired sessions are removed on login.

No public registration/publication route exists. Sending a payload with a
`source` does not grant core or plugin identity. External transport adapters
must obtain identity from trusted runtime/connection admission before they call
the internal contract below; never expose `activate` as an unauthenticated route.

## Event / plugin runtime contract

`service.bus.activate(kind, owner)` is trusted runtime-only and returns an opaque
handle, not a serializable credential. `register(handle,type,schema,version=1)`
requires the owner's `<owner>.` namespace; ownership cannot be overwritten.
Schemas use JSON Schema 2020-12, without references or external resolution.
A declaration carries `type/source/schemaVersion/schema/available`.
`await publish_async(handle,type,payload,event_id?)` validates ownership/schema,
commits the Event with its candidate Trigger revisions, then returns
`{event,accepted:true,persisted:true,duplicate:boolean,rejected:[]}`. This receipt
acknowledges durable inbox admission, never Action completion. Reused IDs with
identical source/type/schemaVersion/payload retain the original envelope; different
facts are rejected. `occurredAt` is not part of identity. Live subscriptions remain
best effort and may receive duplicates; `rejected:[]` is not a delivery receipt.
Each callback receives its own envelope copy. The synchronous `publish` is only
available on a standalone volatile Bus; durable Service instances reject it.
IDs must be reused for deliberate redelivery, maximum 128 characters.

`subscribe(handle,name,async_callback)` subscribes to the live stream (callbacks
filter Event types as needed); `deactivate(handle)` revokes publication,
removes that owner's subscriptions and marks its definitions unavailable.
Internal plugin lifetime uses `plugin_enabled(id)` -> register/subscribe ->
`plugin_ready()`; call `plugin_disabled(handle)` on stop. Ready resumes queued
Runs whose dependencies have become available. Plugin definitions are not a
second plugin installation/enable authority: the actual plugin lifecycle adapter
must call these hooks. An internal executable example is `example_plugin.py`.
External transport and Catalog/Session activation now use the additive
`pan-events/v1` adapter; see [external event protocol](../plugins/EVENTS.md).
The three Service hooks and existing HTTP/data contract are unchanged. The
generic plugin Runtime owns one shared event-role connection per enabled plugin,
independent of tool connections and all three MCP projection modes. Catalog
declarations include descriptions, and the adapter preserves this presentation
metadata on definitions. No public registration/publication endpoint is added.

Limits: 64 runtime owners, 128 definitions, 64 subscriptions, 128 events per
subscriber, 64 KiB JSON payload per Event; excess admission is rejected visibly.
Subscriber exceptions and queue-full reports appear in the last 100 bus
diagnostics. Engine diagnostics also keep the last 100. Management reads expose
them. There are at most 64 Trigger definitions and 1024 pending Runs. Over-limit
matches get failed Run records with `Trigger backlog full`; no Action runs.

## Trigger matching and State

`eventTypes` is OR. Unavailable dependencies disable matching without deleting
configuration; GET reports unavailable types. Condition forms:
`{and:[condition...]}`, `{or:[condition...]}`, `{not:condition}`, or
`{field:"workers.runningCount",op:"eq"|"ne"|"gt"|"gte"|"lt"|"lte",value:...}`.
The core State reader currently exposes only `workers.runningCount`; it derives
from actual `worker.workers[*].status == running`. A field is read at most once
per Trigger/Event evaluation. False conditions persist a terminal `skipped`
record; read/comparison/mapping failures persist `failed`. Arguments map each
parameter to `{from:"literal",value:...}` or `{from:"event",path:"payload.x"}`.
Paths traverse dictionary keys only, without scripting or array expressions.
Resolved arguments and State observations are persisted at match time, with no
re-evaluation at Action start.

Worker status assignment and registration/removal call a nonblocking observer.
Its previous sample is edge detection only: it recomputes from the existing
Worker registry every time, never maintains a second running authority. Positive
-> zero publishes `workers.all_idle` with `{runningCount:0}`; zero -> zero does
nothing. Worker queue/lifecycle rules are unchanged.

## Run and execution

Runs store `id/revision/triggerId/triggerRevision/actionId/event/arguments/state`,
`createdAt/startedAt?/finishedAt?`, `status/error?/result?`, `action/actionDigest?`,
and Session Operation checkpoints `targetIds/targetResults`. The Event Store phase
adds `attempts/attemptCount/activeAttemptId`; its first durable claim creates one
attempt; the scheduling batch defines subsequent authorized attempts. Registry and every
object have independent increasing revisions. Event ID + Trigger ID deduplicates
all statuses while retained. Runs are appended before effects and gated by durable
`admitted:true` after all matching persistence attempts for that Event finish.
Partial persistence failures are diagnostic; successfully persisted matches can
run once admission is durable. Admission failure keeps queued records waiting.
Recovery makes persisted queued records admissible; interrupted running records
become uncertain without replay. Disabled/deleted Trigger queues cancel, while
unavailable plugin queues remain waiting. Serial Triggers execute in admission order including retry backoff; parallel
Triggers use bounded independent slots. Opt-in idempotent failed retries persist
nextAttemptAt and retain distinct attempt history under the scheduling contract.

Action definitions are read by ID at execution start; their full snapshot and
SHA-256 digest are persisted before invocation. Deleted, disabled or newly
incompatible Actions fail visibly. A new edit affects later starts without
Group/build. Existing immutable Group snapshots, pins, release and legacy
provider execution restrictions remain intact. The shared executor reuses the
existing fixed GET API and bounded argv command paths. Additional Pan APIs need
explicit internal business adapters; no all-API auth rewrite is performed.

A normal Action can specify:

```json
{
  "id":"pause_selected", "name":"Pause selected", "enabled":true,
  "effect":"write_local",
  "input_schema":{"type":"object","properties":{},"additionalProperties":false},
  "execution":{"kind":"session_operation","operation":"pause_everything",
    "selector":{"mode":"explicit","ids":["ses_example"]}}
}
```

`event` selector uses `{mode:"event",path:"payload.sessionIds"}`; a string or
array of strings is accepted, with IDs deduplicated in order. Targets are fixed
and checkpointed before the first effect. Each target uses the existing
`api_session_queue_paused(id,{paused:true})` business entry, including its queue
lock, persistence/rollback, snapshot and queue-handoff gates. Errors do not stop
other targets. Aggregate result is `ok/matched/succeeded/failed/targets`;
any failed target fails the Run; empty matches succeed. Checkpoint write failure
stops further effects and leaves uncertain. Started Runs cannot be cancelled
through management; disable cancels only queued Runs. Shutdown cancellation
marks interrupted effects uncertain; it does not claim to roll them back.
Session Operation is rejected by the legacy Group build/provider entry points,
so their permission and execution semantics do not expand accidentally.

`query` is implemented by the authorized Session query follow-up below.
Parallel mode is implemented by the separate bounded scheduling batch. Production UI and the stdio external plugin
transport are integrated and have isolated combination evidence. Alternative
remote transports and durable per-plugin consumer delivery remain deferred.
The Event Store phase adds bounded retention, inbox paging and durable Trigger
matching as specified in its separate contract. Overall human Pause/queue acceptance is pending the user.


## Scheduler concurrency clarification (core acceptance rework)

`kick` reads off-loop, then rechecks the terminal close latch before publishing
any runner. Each Trigger has one task owner and a coalesced wake bit. A kick
against an existing owner records the wake; an owner that has read an empty or
otherwise stopping snapshot consumes any intervening wake with a fresh read.
The wake decision and removal of ownership execute without an await, so a later
kick either wakes the existing owner or installs its successor. No polling,
per-Run task spawning, or Action-duration lock is used. Close blocks new runner
publication, cancels/joins its fixed task snapshot, and clears registrations
including tasks cancelled before their coroutine's first step. In-flight start
recovery cannot reopen a closed instance.

If Event dependencies disappear after a durable claim, the pre-effect deferral
is one ledger transaction, conditional on that exact running Run revision. It
rechecks the current Trigger: enabled becomes queued/waiting; disabled/deleted
becomes cancelled. A changed Run status/revision is retained unchanged. Disable
before or after this transaction therefore cannot resurrect a cancelled Run.
This does not change HTTP shapes, selectors, token policy, or the rule that
already-started business effects continue on Trigger disable.


## Session query selector (authorized follow-up)

Create/update a normal Action through the existing source/draft APIs. The additive
`GET /api/actions/session-operation-template` returns a disabled ordinary Action
`item` (no ID; create assigns one), `queryFields`, `queryPredicateSchema` and
`limits`. It neither enumerates Sessions nor executes an operation. Agents still
create disabled drafts; user management/Trigger token boundaries and legacy
Group/provider gates are unchanged. Query is selection data, never authority.
Automatic execution remains the user-enabled rule's business operation; it does
not impersonate an Agent, inject an auth context, or add a scope override. This
is the existing local-process API trust boundary, not new per-user HTTP security.

```json
{"kind":"session_operation","operation":"pause_everything",
 "selector":{"mode":"query","query":{"and":[
   {"field":"name","op":"contains","value":"review"},
   {"field":"readonlySession","op":"eq","value":false},
   {"not":{"field":"adapter","op":"in","value":["codex"]}}
 ]}}}
```

Grammar is closed: `{and:[Predicate,...]}`, `{or:[Predicate,...]}`,
`{not:Predicate}`, or `{field,op,value}`. Every branch is validated before reading
any Session, including branches that evaluation would short-circuit. Empty
objects/and/or/in, unknown keys/fields/operators and type mismatches fail clearly;
there is no default match-all or fallback to all Sessions. No script, SQL, regex,
property path or user-provided comparator is accepted. Text is literal data.

| Field | Source / type | Operators |
|---|---|---|
| id | Session.id, string | eq / ne / in |
| name | Session.name, string | eq / ne / in / contains |
| adapter | Session.adapter, string | eq / ne / in |
| managedBy | Session.managed_by, string or null | eq / ne / in |
| readonlySession | Session.readonly_session, boolean | eq / ne / in |
| pinned | shared session_pin_state membership, boolean | eq / ne / in |
| workspaceIds | effective_workspace_ids (managed-root membership), array of strings | eq / ne / contains |

`eq/ne` compare exact typed values. Array equality is ordered whole-array equality.
`in` requires a nonempty list of the scalar field's type (nullable managedBy also
allows null); it means the field equals one listed value. `workspaceIds.contains`
means one exact string element, never a substring. `name.contains` requires a
nonempty string and means a case-sensitive literal Unicode substring, without
regex, case folding or normalization. Other string fields do not support contains.
Booleans do not accept 0/1 or string representations. Broken/cyclic management
chains inherit the existing effective-workspace behavior (ungrouped), with no new
relationship mutation or traversal language exposed to query callers.

Limits are fixed: depth **8** (root = 1), total predicate nodes **64**,
individual operand arrays / in lists **128**, literal string length **512** Unicode
code points, candidate Sessions **2048**, unique matched targets **256**. and/or
must be nonempty. Candidate overflow is rejected before cold metadata hydration;
result overflow fails the whole selection before effects. Neither case truncates.
Candidate count is checked before filtering, even for an ID predicate. Limits
apply only to query; explicit/event keep their prior accepted shapes and order.

The resolver uses shallow Session metadata and shared Pin state off the event
loop; no histories are loaded. Existing Session listing now has an optional
internal admission limit; ordinary list API callers retain the default behavior.
A single metadata projection is read at Action execution start. This is not a
transactional global snapshot of simultaneous Session edits. The resulting IDs
are deduplicated and sorted lexicographically, then durably checkpointed once,
before the first target operation, together with:
`selection:{mode:"query",matchedAt,candidateCount,matched}`. Matching time is the
completion time of the one selection. Invalid runtime metadata fails explicitly.

Rename/move/pin changes and new Sessions after this checkpoint do not alter that
Run. Per-target operations still recheck their actual business state and may
fail (e.g. a target was deleted); the existing partial-result aggregation applies.
Zero targets checkpoint an empty target set and successful matched=0 result with
selection diagnostics. Cancellation/write failure/uncertain behavior is unchanged.
Recovered running/uncertain Runs never requery or replay their target set. Queued
Runs that have never started resolve once when they actually start after recovery.
The query follow-up itself introduced no generic retry API; the later scheduling
batch reuses its fixed targets across authorized attempts.

## Unified Job integration

The complete Job config/Template/Run/Attempt and System Action contract is specified
in [v0.4 Job Templates](Event-Trigger-Action-v0.4-job-templates.md#unified-execution-contract-2026-10-23).
Jobs consume durable events through the same Event Store, indexed target list and
dispatch lifecycle; a target is marked `_job:true` with Job/config revision.
Event+Job receipts and bounded queued intents are committed before dispatch.
Unavailable event sources cannot start a queued Job; disabled or changed targets
are cancelled. No second Event Store, scheduler or resource controller is added.
Event query freezes `triggerTargetIds` and `triggerSelection`; event Session Actions
may select `jobSelection.ids`. Automatic/confirmed Attempts reuse persisted IDs.
Action query/explicit selectors retain their own existing semantics and gates.
System Actions require trusted Job host capability and cannot enter legacy Group
provider execution or alter immutable Group releases/pins. Existing Trigger retry
rules stay separate from monitoring Job recovery; ordinary Actions need no signal.
