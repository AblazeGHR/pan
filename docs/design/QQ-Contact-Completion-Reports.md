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
  connections in one save, with rollback on failure. On activates the configured
  completion defaults (legacy configurations remain system-only). WeChat and TA-to-MA subscriptions retain their existing scope.
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


## Additional Notification defaults (same feature branch)

AppSettings → Notification has a completion-defaults panel supporting System,
Browser, QQ Report and QQ Subscribe together. QQ modes share the chosen default
bot/contact list. The panel loads existing bot/contact APIs only when a QQ mode
is selected, retains saved unavailable contacts for removal, and displays an
inline validation message while disabling Save if no QQ contact is selected.
The save is awaited: failed persistence is shown inline and does not update the
saved store value. No new localStorage source is introduced.

`config.json.ui.notifications.completionBridge` stores `system`, `browser`,
`qqReport`, `qqSubscribe`, and deduplicated `qqTargets`. GET/PUT `/api/settings/ui`
remains the configuration API. Notification fields now merge independently so
warning preference edits do not erase completion defaults. Backend validation
rejects empty QQ contact lists and malformed identities with HTTP 422. Missing
legacy defaults preserve system-only bell activation; explicit false and empty
lists are preserved.

Only off-to-on `/msg-bridge` applies the defaults. Before mutating a Session it
validates selected bots and contacts via existing QQ read APIs. An unavailable
bot, removed contact, empty mode selection or failed persistence rejects the
activation with no partial system/browser/QQ enablement. Contact availability
inherits the plugin's existing recent-contact caching and is a point-in-time
check, not a delivery guarantee. Default edits never enumerate or rewrite live
Session connections. Off retains the clear-all semantics and cancels pending
reports. Attempts to reapply defaults to an already lit bell are rejected.

Additional offline evidence: `evidence/defaults_probe.py` uses a temporary config
file and Session store, mocked bot/contact reads, and actual ASGI routing; it
checks validation, persistence/reload, notification-field merging, simultaneous
modes, active-connection preservation, subsequent off-to-on defaults, unavailable
bots/contacts, config/Session save failures and empty modes. The standalone
`evidence/defaults_panel_probe.cjs` renders the actual panel in jsdom with mocked
store/API and checks inline validation, Save disabled without contacts, multi-mode
save and visible save failure. Neither probe invokes a test runner or sends QQ.
Durable Job `job_fcec2b334e585129b7588271` completed TypeScript/build/assets/full
lint with exit 0; full lint had 0 errors and the same 18 unrelated warnings.

## MA rework: locked claim and actual persistence outcome

Incremental repair starts from `7c4882bec64ee1f02ae23c5820b743869570a5da`.
After acquiring the Session queue lock, the drain checks both the current outbox
record identity and its pending state again. Bell off/on while it waits cannot
turn a cancelled or replaced snapshot into an attempted report.

Claims use the existing `DurableOutcome` and per-Session persistence ticket.
The attempted mutation and write-failure rollback execute in that ticket, before
the next writer can run. Caller cancellation still propagates, but a successful
write keeps attempted in memory and on disk; only an unsuccessful write restores
pending. Cancellation after a committed claim can therefore lose a delivery,
consistent with the single-attempt policy, and cannot trigger a resend.

Worker terminal first persistence uses an observable ticket through
`_flush_history_now`. Actual failure rolls back terminal fields, runtime task
identity and outbox before retiring the ticket. Successful persistence followed
by cancellation retains the terminal dedup facts and runtime latch. Its pending
outbox can be drained after reload, while a fresh Worker rejects the same terminal.
Done/error payload selection and Notification defaults are unchanged.

Standalone `evidence/rework_probe.py` and `evidence/rework_probe.log` are outside
the checkout. The probe uses actual `task.cancel()` (including repeated cancel),
a thread-gated real Session writer, temporary disk JSON, cache eviction/reload,
and only a mocked QQ sender. All six deterministic cases passed:

| Interleaving | Durable result | Mock sends after reload |
| --- | --- | --- |
| Bell off/on while drain awaits lock | cancelled | 0 |
| Outbox record replaced while awaiting lock | replacement retained | 0 |
| Claim cancellation, writer succeeds | attempted | 0 |
| Claim cancellation, writer fails | pending; later claim succeeds | 1 |
| Terminal cancellation, writer succeeds | cursor 1; fresh replay suppressed | 1 |
| Terminal cancellation, writer fails | cursor 0; later terminal commit allowed | 1 |

The two intentionally injected writer failures emit asyncio shield diagnostics;
the probe exits 0 and confirms their real failed outcome and disk rollback.
Original offline report/API, defaults/API and rendered-panel probes were rerun
and passed, as did Python compilation and diff whitespace validation. No test
suite, real QQ send, service action or main/practical integration was performed.
Frontend build/lint evidence above belongs to the unchanged frontend at the
parent commit; this Python-only repair does not claim a new frontend build.
