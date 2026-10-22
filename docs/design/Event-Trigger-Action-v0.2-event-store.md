# Durable Event Store foundation

This is the authorized next phase after accepted v0.1. The v0.1 design,
implementation supplement and core contract still govern unchanged behavior.
No Job Template, new Action snapshot mechanism, retry policy, parallel execution
mode, React change or external transport extension is included.

The later authorized [retry/scheduling contract](Event-Trigger-Action-v0.3-retry-scheduling.md)
supersedes this phase's one-attempt/no-retry restriction. It preserves durable inbox
and Event+Trigger dedup: Event replay never itself retries a Run. Other exclusions
remain unchanged; retry_wait is a protected pending Run status.

## Admission and delivery

Runtime identity and JSON Schema validation precede admission. Production callers
await `bus.publish_async(handle,type,payload,event_id?)`; its successful receipt
is `{event,accepted:true,persisted:true,duplicate:boolean,rejected:[]}`. An opaque
handle is rechecked after waiting for the admission snapshot. SQLite commits
with `synchronous=FULL` before waking delivery. All disk work runs in threads,
including cancellation settlement; one bounded dispatcher never awaits Actions.
There is no public publisher or runtime-registration HTTP API.

Each Event stores the eligible enabled Trigger configurations/revisions observed
at acceptance. Later-created Triggers are not backfilled. Changed, disabled or
deleted revisions become cancelled history; unavailable dependencies defer
delivery until real registration/readiness. State predicates and Event argument
mapping run at matching time. A crash before Run persistence can reevaluate State;
after persistence the Run's frozen data and Session query targets govern execution.
Query targets are selected once at Action start, not at Event admission.

The inbox delivers at least once to **Trigger matching**. Partial Run writes,
failure to release admission, or failure to acknowledge inbox delivery leave the
Event pending. Recovery retries matching with bounded backoff (2..30 seconds),
interrupted by admission/readiness/replay. Paging past failed Events prevents an
unavailable plugin from starving later independent Events. Each retained
`(event.id,triggerId)` creates at most one Run, including skipped, failed,
cancelled and uncertain Runs. No terminal Run is reset by inbox replay.
`delivered` means matching/admission completed, not that Actions succeeded.

Event identity is source/type/schemaVersion/payload; `occurredAt` is excluded so
a producer can resend a fact with the same ID. The original envelope is retained.
Reusing an ID for a different fact is rejected. Concurrent resends share one
durable fact. Dedup is guaranteed while the Event/Run records are retained,
not permanently after both expire. Live plugin subscriptions remain independent,
bounded and best effort; they may see repeated notifications and have no durable
consumer acknowledgement or historical cursor. Receipt `rejected:[]` does not
promise subscriber delivery. This phase does not guarantee exactly-once effects.

Worker running counts still come solely from the real Worker registry. The
positive-to-zero observer stages a validated fact in a bounded 128-entry ingress
queue serviced by one task. This synchronous observer cannot wait for disk;
overflow/write failure is visible in diagnostics. This unacknowledged pre-commit
window is volatile, including abrupt process death. The durable guarantee starts
at Event commit, not at the raw Worker transition. There is no second count or
unbounded task-per-event fallback.

## Attempts and execution safety

New Runs begin with `attempts:[]`, `attemptCount:0`. Claim durably creates one
`{id:"att_…",number:1,status:"running",startedAt}` and `activeAttemptId` before
effects. The attempt mirrors status, timestamps, error, result, selection,
targetIds and targetResults as these become available. A pre-effect unavailable
dependency returns that same attempt to queued; it is not another attempt.
No retry API, retry budget, backoff for effects or automatic second attempt exists.
Queued cancellation updates its attempt atomically. Started interruption becomes
uncertain; restart preserves frozen targets and results and never reruns it.
Replay refuses an Event associated with any uncertain Run pending reconciliation.

Automatic execution still uses business checks, without reauthenticating the
original caller or expired management Bearer. Pause/Illegal, disabled Action,
legacy safe execution and immutable Group release/pin gates remain in force.
Existing Run Action data is unchanged; this is not the deferred Action snapshot
feature. Effect completion and ledger commit cannot be made atomic: a failed
progress/final write can leave the last durable state running, which recovery
marks uncertain. Capacity failure never turns an unrecorded effect into success.

## Storage policy and APIs

All storage stays under the existing profile `automation/` directory. SQLite
`events.sqlite3` schema version 1 holds Events, candidate Triggers and delivery
metadata. `registry.json` remains the atomic fsync/replace Trigger/Run ledger.
No import-time migration, global environment setting or new dependency is needed.

| Boundary | Default and behavior |
|---|---|
| Event retention | 7 days from admission; delete delivered Events only |
| Pending Events | Never silently expire; overdue count warns and consumes capacity |
| Event count / logical bytes | 100,000 / 256 MiB including frozen Trigger JSON; reject new facts at capacity |
| SQLite physical database | 320 MiB page cap, including indexes; transient rollback journal/disk overhead is additional |
| Payload | 64 KiB UTF-8 JSON; nonfinite numbers rejected |
| Run ledger | 10,000 Runs / 64 MiB logical JSON; growth past cap refused, shrink remains allowed |
| Run pruning | Terminal succeeded/failed/cancelled/skipped, known zoned finish older than 7 days, and Event no longer retained |
| Protected Runs | queued/running/uncertain and unknown/naive legacy timestamps never auto-pruned |
| Maintenance | Startup, about every 60 seconds and authenticated manual call; SQLite free pages reused without hot-path VACUUM |
| Warning | 80% logical count/bytes, or overdue pending Events; HTTP storage stats and bounded diagnostics expose failures |

`GET /api/automation` adds `storage:{events,runs}` with usage, caps, warning/full
flags, and Event pending/overduePending/databaseBytes/retentionSeconds. Alarms are
backend-visible status, not external notification delivery or a new UI dashboard.
An individual append can exceed remaining space before a `full` flag is true;
the resulting capacity rejection is still explicit. Disk errors remain errors.

`GET /api/automation/events?after=0&limit=20` returns `{events,nextAfter}` in
ascending sequence order. Limit is 1..100; cursor is nonnegative. Each entry has
`seq,event,targets,receivedAt` (Unix seconds), `status` (pending/delivered),
`deliveryCount,lastError`. Delivery count tracks matching passes, not attempts.
This is a bounded administrative page under the same local trust boundary as
existing Automation GET; it is not a permission-bypassing publication route.

`POST /api/automation/events/replay` requires a user Bearer and exactly
`{eventIds:[1..100 unique IDs]}`. All IDs must exist; expired delivered Events
are refused even before maintenance. Protected pending Events remain repairable.
It marks original inbox records pending and returns
`{scheduled:[IDs],automaticRunRetry:false}`. It creates no new fact, target
configuration, source identity or Action attempt. Uncertain association is 409;
authentication failure is 401. It does not await effects.

`POST /api/automation/events/maintenance` requires a Bearer and applies the fixed
policy, returning `deletedEvents/deletedRuns`. No arbitrary deletion range or
retention override is accepted. Capacity and payload policies are backend constants
in this stage, not user-configurable settings.

## Migration and remaining boundaries

Existing registry JSON is read without eager rewriting of individual records.
Startup recovery adds an inferred `legacy:true` attempt for legacy running Runs
as it marks them uncertain; queued work receives its first attempt when claimed.
No historical volatile Events are synthesized or replayed. Legacy Runs with
unusable timestamps and uncertain Runs can eventually fill the bounded ledger;
admission then stops visibly pending later operator reconciliation tooling.
An already oversized old ledger is preserved; growing recovery/status writes may
fail and prevent startup. Back up the isolated profile before upgrade/downgrade;
older versions ignore the new inbox and cannot provide its durability guarantee.
Do not run mixed versions against one profile.

Run JSON still rewrites at every mutation; bounds control growth but do not make
it suitable for high-throughput deployment. SQLite and JSON are separate commits,
coordinated by pending inbox recovery and retained Run dedup rather than a shared
transaction. Storage corruption, disk exhaustion, filesystem/hardware durability,
long-duration throughput and abrupt OS power loss remain operational boundaries.
Tests cover controlled write failures, cancellation, restart and owned real HTTP
with Node/fakeCLI; these do not constitute human acceptance or real-provider proof.
Manual uncertain reconciliation, configurable retention, durable subscriber
delivery, retry orchestration and the other deferred design phases remain undone.
