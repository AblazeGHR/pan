# Event–Trigger–Action core API and data contract (v0.1)

This is the backend handoff contract. Production UI and external plugin transport
are a subsequent phase. The authoritative scope is Event-Trigger-Action-v0.1.md
and its implementation supplement.

## Lifetime and storage

`packages.automation.service.Service` is created/stopped by the web lifespan.
All new files live under `session.SESSION_DIR.parent / automation`:
`registry.json` (Trigger/Run ledger) and `user.key` (local user key). There are no
new environment variables and no import-time initialization or migration.
Existing Actions remain under the sibling `actions/registry.json`.

Ledger writes reuse the Action atomic tempfile/fsync/replace primitive in a
worker thread. Writer locks are confined to ledger transactions; no lock spans
Action execution, command/provider waits, or target operations. Matching is one
ordered consumer with a matching-only async lock, independently of executors.
No async event producer performs filesystem I/O. A slow subscriber has its own
queue and task. The bounded queue admission is synchronous and nonblocking.

Run retention is currently indefinite, as is deduplication. The JSON ledger is
rewritten on each transaction; retention/indexing/paging must be designed before
large deployments. There is no durable Event Store, replay, or exactly-once
side-effect guarantee. Diagnostics before persistence are process-local and may
be lost on restart; persisted Runs retain later errors.

## HTTP

| Method/path | Request | Result / authorization |
|---|---|---|
| POST `/api/automation/login` | `{key: string}` | `{token, expiresIn:43200}`; invalid key 401 |
| POST `/api/automation/logout` | Bearer header | revoke that in-memory token |
| GET `/api/automation` | none | ledger, definitions, per-Trigger dependencies, diagnostics, limits |
| POST `/api/automation/triggers` | `{item, expectedRevision, agentId?}` | `{item}`; create revision 0, updates exact Trigger revision |
| DELETE `/api/automation/triggers/{id}?expectedRevision=N` | Bearer header | user token always required; disable before delete; history retained |
| POST `/api/automation/runs/{id}/cancel` | `{expectedRevision:N}`, Bearer | queued only; conflict/start already occurred -> 409 |

The Trigger item accepts `id?`, `name`, `description?`, `eventTypes`, `condition?`,
`actionId`, `arguments?`, `executionMode?` and `enabled?`. New Trigger defaults to
`enabled:false`; serial is the sole accepted mode. IDs are generated if omitted.
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
`publish(handle,type,payload,event_id?)` validates ownership/schema and returns
`{event,accepted,rejected:[subscriber names]}`. Each subscriber receives an
independent copy of the same envelope (`id/type/schemaVersion/source/occurredAt/payload`).
Accepted means admission only, never durable Run or Action completion.
Partial fanout is possible and explicitly reported. Runtime IDs must be stable;
event IDs must be reused for deliberate redelivery, maximum 128 characters.

`subscribe(handle,name,async_callback)` subscribes to the live stream (callbacks
filter Event types as needed); `deactivate(handle)` revokes publication,
removes that owner's subscriptions and marks its definitions unavailable.
Internal plugin lifetime uses `plugin_enabled(id)` -> register/subscribe ->
`plugin_ready()`; call `plugin_disabled(handle)` on stop. Ready resumes queued
Runs whose dependencies have become available. Plugin definitions are not a
second plugin installation/enable authority: the actual plugin lifecycle adapter
must call these hooks. An internal executable example is `example_plugin.py`.
Complete external transport and automatic integration with each catalog plugin's
activation lifecycle are not in this phase.

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
and Session Operation checkpoints `targetIds/targetResults`. Registry and every
object have independent increasing revisions. Event ID + Trigger ID deduplicates
all statuses permanently. Runs are appended before effects and gated by durable
`admitted:true` after all matching persistence attempts for that Event finish.
Partial persistence failures are diagnostic; successfully persisted matches can
run once admission is durable. Admission failure keeps queued records waiting.
Recovery makes persisted queued records admissible; interrupted running records
become uncertain without replay. Disabled/deleted Trigger queues cancel, while
unavailable plugin queues remain waiting. Same Trigger executes in ledger order;
different Triggers have separate tasks. No ordinary failure retries occur.

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

`query` is a **required follow-up**, reserved in `SessionSelector` types but
rejected at runtime pending a model-backed field whitelist and product decisions.
Parallel mode is likewise a reserved type, not an implemented mode. UI, complete
external plugin transport, large-ledger retention/paging, reliable Event delivery,
and human Pause/queue acceptance remain later work and are not claimed here.


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
