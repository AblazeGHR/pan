# Independent Job Service interfaces

Date: 2026-10-09. This document describes the bounded user-level host implemented
in `packages/jobs/independent_service.py`. It is not a deployment or production
installation claim. The service must be started by an external user-level host;
starting it as a Pan child does not prove lifecycle independence.

## Scope and start

The first implementation accepts argv commands only. It has no scheduler, legacy
Job migration, production installation, or Pan imports for Session, Worker,
provider, or Web behavior. The process uses only stdlib HTTP/SQLite at its API
boundary and binds strictly to `127.0.0.1`.

```powershell
python -m packages.jobs.independent_service --root D:\jobs\isolated-root --port 8765
```

`--root` is required and must be absolute. The service writes `jobs.sqlite3`, WAL
files, and `logs/job_<24 hex>.log` under that root. `--port` is required and must
be 1 through 65535. There is no bearer token in this local trusted loopback
version; never bind it to a remote interface or expose it through a tunnel.

## HTTP contract

All request and response bodies are JSON. Requests are capped at 64 KiB.
Malformed requests use static error strings and do not echo argv, environment,
or exception details.

| Method and path | Result |
| --- | --- |
| `GET /health` | `{ "ok": true, "instanceId": "..." }` |
| `POST /jobs` | Create or idempotently return a job. Required: `argv` (nonempty string array), existing `cwd`, `targetSessionId`; optional: `creatorSessionId`, `label`, `name`, `description`, `idempotencyKey`. |
| `GET /jobs` | `{ "jobs": [...] }` |
| `GET /jobs/{jobId}` | Job object, or 404. |
| `POST /jobs/{jobId}/cancel` | Requests cancellation only after matching the stored live runner identity. Unknown/mismatched ownership is refused. |
| `POST /jobs/{jobId}/retry` | Create a new job from a failed or cancelled job; UNKNOWN jobs are refused because their ownership is unresolved. |
| `GET /events` | `{ "events": [...] }` for unacknowledged durable terminal results. |
| `POST /events/{eventId}/ack` | Idempotently acknowledge a known event. Pan must call this only after durable enqueue into its target Session. |

Job ids are `job_` plus 24 lowercase hex digits. Public state includes `kind:
background-process`, `executionOwner: independent-job-service`, `status`, argv,
cwd, target and creator Session ids, runner identity, `exitCode`, `logPath`, and
`notificationState`. Status is `starting`, `running`, `completed`, `failed`,
`cancelled`, or explicit `UNKNOWN`. Terminal events use stable id
`<jobId>:terminal` and carry the same routing and result fields.

An idempotency key reused with the same normalized command payload returns the
existing job; reuse with different content returns HTTP 409. Registration is
committed before runner launch. If launch outcome cannot be established, the
service leaves an UNKNOWN fact and does not launch a replacement automatically.
The runner commits the terminal job state and outbox event in one SQLite
transaction. An unacknowledged event is retained across service restarts. UNKNOWN
jobs cannot be retried because the service has not proved the original execution
stopped.

## Process ownership limits

The service holds a nonblocking single-instance lock under the data root.
Registration is committed before runner launch, and each runner must atomically
claim a registration before it can spawn. A duplicate runner exits without
launching a task. Service shutdown closes only its HTTP listener; it does not
signal runner processes. Restart checks the runner PID and raw creation identity; a mismatch,
unavailable probe, or otherwise unproven outcome becomes UNKNOWN rather than
being called dead. No automatic re-execution occurs. Cancellation is handled by
the runner through a verified Windows Job Object tree created suspended, assigned,
membership checked, then resumed. Cancellation reports `cancelled` only after
the guard proves zero active members and an empty member list; otherwise it
leaves UNKNOWN. Natural completion also requires no remaining guarded
descendants. The task's command environment is sanitized of Pan application
variables and `PYTHONPATH`. These checks do not prove behavior under machine
power loss or arbitrary external side effects.

## Test boundary

Tests use a fresh temporary root and loopback ephemeral listener. This establishes
source-level API and process behavior for the tested platform; it does not
establish an installed external host, real Pan shutdown/Taskkill survival,
production security, schedule migration, or durable delivery into real Session
queues. Those remain separate integration and acceptance steps.
