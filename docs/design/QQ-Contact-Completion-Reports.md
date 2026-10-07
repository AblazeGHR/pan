# QQ contact completion reports

## Scope and source chain

This implementation was created in registered worktree
`D:/project/pan-worktrees/qq-contact-report-20261007`, branch
`feature/qq-contact-report-20261007`, from main
`1536400db0102cada92a8ff1561a8bca09dcd836` on 2026-10-07. Practical had the
same SHA. Existing untracked files in both canonical checkouts were preserved.
The implementation branch is synchronized with the subsequent main snapshot
`e27b52b5ee330f4d47abc946b0e8e1e97b04d0ea` before delivery.

Read constraints: repository `docs/skills/pan/SKILL.md`, its HTTP API reference,
the available Pan/workflow skills, `Pan_SMA/AGENTS.md`, and the existing
`Pan-main/docs/plans&overviews/constraints-and-acceptance.md`. Repository root
AGENTS.md and the previously referenced `.workflow` files were absent. The
task's explicit model, low effort, no-suite validation, and fast-forward
instructions override older defaults. Main/practical changes belong to the MA
after strict acceptance, including practical build. No push or service restart.

## Audited paths and changes

- Contact UI: `PostboxModal.tsx` merges contacts from existing bot accounts.
  Subscribe keeps its original independent `qqSubscriptions` semantics;
  Report adds `qqReportTargets`. Exact bot/contact keys deduplicate rows and
  recipients. Saved report targets remain removable if contacts disappear or
  a bot fails to load. Session/open generation guards discard stale async
  completions; edits wait for matching detail metadata and reject readonly.
- Settings API: `PUT /api/sessions/{id}/qq-report` accepts `target`, `enabled`
  and `expectedEnabled`. Targets are `user:<id>[@<bot>]` or
  `group:<id>[@<bot>]`. CAS and readonly checks happen under the existing
  Session queue lock. Failed persistence rolls back memory before propagating
  failure. `session.updated` uses the existing WS/list convergence path.
- Persistence: `qq_report_targets` (set serialized as a sorted array) and
  `qq_report_outbox` are optional Session metadata. Missing old JSON fields
  default off/empty. Full API exposes `qqReportTargets`; summary continues to
  expose the scalar `msgBridgeEnabled`, without per-card detail requests.
- Bell: `_msg_bridge_enabled` is the union of QQ subscribe/report and
  system/browser settings. Existing `/msg-bridge` off clears all these
  connections in one save, with rollback on failure. On enables only system
  notifications. WeChat and TA-to-MA subscriptions retain their existing scope.
- Completion: stream `_read_stdout`, oneshot `_consumer_oneshot`, and
  pre-execution `_finish_task_error` converge on `_persist_terminal_state`.
  The original runtime, last-result and bounded terminal-window duplicate
  checks run before preparation. `_enqueue_report` remains the existing MA
  reporting path. QQ preparation excludes manager report batches (no taskSeq
  and/or `_current_report_items`) and cancelled/zombie paths.
- Body: done takes the last assistant text captured during this task, with
  the current adapter result as fallback. Stream and oneshot reset the capture
  at the task boundary, ignoring thinking/tool blocks. Error takes exactly the
  current terminal result also passed to TA-to-MA, including startup/timeout
  diagnostics when there is no assistant body. This reflects the user's
  explicit clarification. No previous result or full history is searched.
- Sending: terminal preparation commits outbox records with the base terminal
  state. `_publish_terminal_events` schedules `qq_reports.kick`; startup uses
  the already-loaded shallow Session list. A persisted `attempted` claim is
  required before the single send through existing `_send_scheduled_qq` →
  `_qq_plugin_post('/api/qq/send')` → QQ plugin `api_send` → selected
  `QQChannel.send` → `bot.call_api`. Explicit invalid bots do not silently
  reroute; the existing plugin returns an error. QQ plugin code is unchanged.

## Attempt and durability boundary

The user explicitly requested one attempt, with no complex QQ deduplication
or retries. A terminal/recipient key in Session metadata and the existing
terminal guards are sufficient for local duplicate suppression. Pending
records can be recovered after a crash before the attempt claim; attempted,
sent and failed records never resend, even if the response save fails. A new
completion arriving while a send is in flight is drained after that send.
Disabled/removed targets and readonly Sessions cancel pending records. Already
claimed/in-flight network requests cannot be recalled by a later bell click.

A crash after durable claim but before gateway I/O can lose a delivery. Gateway
timeout can mean delivered or undelivered; Pan does not retry either outcome.
This is at-most-one local invocation, not proof of exactly-once recipient
delivery. Completed receipt retention follows the existing bounded terminal
replay window; no infinite guarantee is made for arbitrary provider replay
outside that window. No full-history scan or new polling loop is added.

First terminal save failure propagates before publication/sending and restores
duplicate/routing/unread metadata so retry is not falsely treated as success.
The existing history persistence implementation retains its own partial-write
and cancellation semantics; this feature does not claim a new cross-file
transaction.

## Validation evidence and limits

Offline probe: `data/workdirs/qq-contact-report-20261007/evidence/offline_probe.py`
(outside the feature checkout). It uses a temporary Session store, mocked QQ
sender and broadcaster, and in-process ASGI transport without server lifespan
or listeners. It checks multiple contacts, independent subscriptions, done
assistant body, error result, terminal duplicates, fresh Worker replay, actual
JSON reload, manager/cancelled exclusions, first save failure/retry, readonly,
CAS, settings rollback, bell off/on, legacy JSON, invalid bot's single attempt,
removed pending targets, actual HTTP routing and summary notification state.
Eight sends in this probe are mock invocations only.

Frontend dependencies were installed privately with offline frozen-lockfile
pnpm; no shared node_modules junction was installed. Durable Jobs run TypeScript
build, Vite/precompressed asset verification, and full ESLint. Python compile,
import probe and diff whitespace validation are also performed. No test suites
are created or run. Early verifier/API-name mistakes and early UI type errors
were corrected before final verification.

Actual QQ delivery, running provider completion, Windows/browser notification
behavior, real browser/mobile interactions, live service restart, remote CI,
MA acceptance, main/practical integration and practical build are separate
unverified layers at TA delivery. Protected ports 8767/8768 are not operated;
the baseline 8768 listener identity was PID 38428 (created 2026-10-07 17:39:55),
with no 8767 listener. Durable Job infrastructure alone uses the already
authorized Pan orchestration connection.
