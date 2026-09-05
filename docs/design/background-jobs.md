# Background Job Runner MVP

Background Jobs are durable process records, not Workers. An Agent Worker may
start and disappear while the Runner process continues. The Runner never uses
Pan stdout, worker pipes, or a live WebSocket for job facts; command output is
appended to `data/background_jobs/logs/<job_id>.log`.

## Lifecycle

`POST /api/background-jobs` writes a job record before starting an independent
`python -m packages.core.background_runner` process. The record contains the
stable `jobId`, target Session, argv, a short command summary, resolved cwd,
log path, PID, and process creation time. The Runner starts the requested argv
without a shell, streams stdout/stderr to the log, and writes `completed` or
`failed` to the same job record. Cancellation validates PID creation time and
kills the complete descendant tree on Windows (psutil is required for a safe
kill); when identity cannot be verified, cancellation is rejected rather than
killing an unrelated reused PID.

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

- `POST /api/background-jobs` with `{targetSessionId, argv, cwd, label?}`
- `GET /api/background-jobs` (optional `targetSessionId`), and `GET /api/background-jobs/{jobId}`
- `POST /api/background-jobs/{jobId}/cancel` and `/retry`

MCP exposes `agent_background_start/get/list/cancel/retry`. `start` defaults
to the current MCP Agent Session. Ordinary Agents do not need to construct
callback payloads or retry notices; `agent_notify` remains available for
low-level compatibility.

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
keeps the original terminal event identity intact.
