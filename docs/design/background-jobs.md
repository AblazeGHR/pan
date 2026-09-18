# Background Job Runner MVP

Background Jobs are durable records, not Workers. An Agent Worker may start and
disappear while a process Runner or the Session-message scheduler continues.
Process Jobs never use a shell. Session-message Jobs never interpret their
`text` as a command: they call the same `worker.send_session` path as
`agent_send`.

## Lifecycle

`POST /api/background-jobs` writes a job record before starting an independent
`python -m packages.core.background_runner` process. The record contains the
stable `jobId`, `creatorSessionId` (the creating/owning Agent, when present),
`targetSessionId` (the only Session that receives the terminal notice), argv,
a short command summary, resolved cwd, log path, PID, and process creation
time. The Runner starts the requested argv without a shell, streams
stdout/stderr to the log, and writes `completed` or `failed` to the same job
record. Cancellation validates PID creation time and kills the complete
descendant tree on Windows (psutil is required for a safe kill); when identity
cannot be verified, cancellation is rejected rather than killing an
unrelated reused PID.

Pan's lifespan starts a small recovery loop. It first reconciles `starting` /
`running` records: a live Runner with a matching PID creation time is left
running; missing/unavailable/reused/dead Runner identity is persisted as
`failed` with an orphan error. It then scans completed/failed/cancelled
records whose `notificationState` is pending and projects one
terminal notice into the target Session's `queue_pending`. The event key is
`<jobId>:terminal`; `enqueue_notice` checks both the pending queue and its
delivery ledger, so a Pan crash or repeated scan cannot create a duplicate.
Only after the projection succeeds is the Job record changed to
`notificationState=delivered`. A deleted or missing target Session leaves the
Job fact intact and the notification pending.

Every registry read-modify-write transaction is protected per Job: Windows
uses a named kernel mutex (automatically released if Pan or Runner crashes),
POSIX uses `flock`, and the canonical JSON is replaced atomically with bounded
retry for transient Windows sharing violations. This protects Runner updates,
cancel/retry, and the recovery `delivered` mark from cross-process lost
updates.

## API and MCP

HTTP endpoints are:

- `POST /api/background-jobs` with `{targetSessionId, creatorSessionId?, argv, cwd, label?}`
- `GET /api/background-jobs` (optional `targetSessionId`), and `GET /api/background-jobs/{jobId}`
- `POST /api/background-jobs/{jobId}/cancel` and `/retry`

MCP exposes `agent_background_start/get/list/cancel/retry` for OS-process Jobs
and `agent_message_job_create/get/list/update/cancel` for time-based Session
messages. A message Job has one `targetSessionId`, a
`creatorSessionId`/`sourceSessionId` for the creating Agent when present, a
description, text, and a normalized schedule:

- one-time: `{"type":"once","at":"<ISO-8601>"}` or
  `{"type":"once","delaySeconds":N}`;
- recurring interval: `{"type":"interval","intervalSeconds":N}`;
- recurring weekly: `{"type":"weekly","weekday":0..6,"time":"HH:MM"}`
  (Monday is 0; the server's local timezone is used unless `timezone` is
  supplied as `UTC` or an IANA zone).

`pending`/`scheduled`/`running`/`completed`/`failed`/`cancelled` are persisted
states. The service recovery loop claims due records under the same per-Job
cross-process lock as the process Registry, so a restart sends one missed
occurrence and persists the next occurrence. Missed recurring occurrences are
not replayed in a burst; the next interval/weekly occurrence is calculated
from the recovery send time. A cancelled Job never schedules another send;
an already in-flight send cannot be retracted.

The immediate selected-Session fan-out API is `POST /api/sessions/broadcast`
and MCP `agent_send_many`. It calls the ordinary send path once per unique
Session and reports per-target results. Scheduled fan-out is intentionally not
part of this stage and must be added only after the immediate fan-out contract
is accepted.

## Security and product decisions

This local MVP accepts an argv array, never a shell string, and only permits a
cwd inside the Pan project directory. It does not yet provide a command
allowlist, result-file contract, or remote/tunnel authentication. These are
product decisions before enabling external work directories or remote control.
Only terminal Jobs may be retried; retrying a `starting`/`running` Job is
rejected and the caller must cancel it first. `sourceSessionId` remains
metadata and is not an authentication credential;
future callbacks must add a short-lived token or signed event boundary.

The current API follows Pan's existing loopback/no-auth model. No new remote
binding or tunnel exposure is introduced. A retry creates a new Job ID and
keeps the original terminal event identity intact for the original Job. The
retry inherits `creatorSessionId`, while its terminal notice is routed only to
its `targetSessionId`.

## Terminal notice envelope

Process Job terminal states (`completed`, `failed`, `cancelled`, including an
orphaned Runner reconciled to `failed`) are projected through the existing
`queue_pending`/`enqueue_notice` path. The durable queue item carries these
structured fields; consumers must not parse them back out of `result` text:

```json
{
  "type": "notice",
  "source": "automation",
  "noticeKind": "background_job_terminal",
  "jobId": "job_...",
  "status": "completed",
  "targetSessionId": "ses_target",
  "targetSessionIds": ["ses_target"],
  "creatorSessionId": "ses_creator",
  "eventId": "job_...:terminal"
}
```

`creatorSessionId` is audit/ownership metadata only. It does not add a second
delivery target, and a missing creator remains absent/null. The formatted
Agent message starts with `////by pan system`, including when no creator is
known. The existing `agent_notify` path has no `noticeKind` and continues to
render its Agent source with the existing `@@@@by agent` format. Agent-created
Session-message Jobs still use normal `agent_send` semantics and retain
`sourceSessionId` plus the `////by agent : <creatorSessionId> | <title>`
message prefix.

Legacy Job JSON without `creatorSessionId` remains readable and is exposed as
`null` at read time without rewriting the file. Service lifecycle Jobs remain
system-level Registry records with no target Session and are never projected
into `queue_pending`.

## Service lifecycle Jobs

The same Registry also stores service-level Jobs with `kind="main-lifecycle"`.
These records deliberately have no `targetSessionId`, are never projected to
`queue_pending`, and use checkout `root` plus `port` for duplicate detection.
A main restart is recorded before the detached supervisor is spawned:

`requested -> stopping -> stopped -> starting -> ready`

The terminal failure phases are `failed` and `timed_out`. Records retain the
API `requestId`, operation, checkout root, port, old/new PID and PID creation
times, timestamps, log path, and the last error. The HTTP status endpoint reads
the persisted record after a Pan process restart, so an in-flight request still
blocks a duplicate and a supervisor failure remains visible. Readiness is
accepted only when the target port is owned by a new process whose creation
time, checkout marker, Pan entry marker, and `/api/health` response all verify.

The PowerShell file remains a detached two-hop launcher, but its second hop
delegates stop/start and these checks to `packages.core.main_lifecycle`. The
helper invokes the existing checkout-scoped `stop_pan.bat` and `start_pan.bat`;
it does not recursively kill its own supervisor. Exit integration can attach
to `create_service_job` and `transition_service_job` later without changing
the Session or background-process contracts.
