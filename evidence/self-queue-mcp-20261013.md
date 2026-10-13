# Own queue MCP implementation and verification

MA-review follow-up supersedes the incident uncertainty and rejection/idle-route
details below: see `self-queue-mcp-review-20261013.md` and its red/green evidence.

Worktree: `D:/project/pan-worktrees/self-queue-mcp-20261013`
Branch: `feature/self-queue-mcp-20261013`
Baseline: `c981dbe533e96612dc7170cc8d968c8a8c9b8a73`

The assigned TA delivers this branch only. MA acceptance, main/practical integration,
practical build and real-service rollout are separate and have not been performed.

## Problem and call chains

The existing Dashboard queue projection truncates report previews and exposes
queue-wide revision/count information. It is unsuitable for own-agent access
under the required hidden-item rules. Queue reorder and Restart also do not prove
that the selected original item reached the CLI. Existing queue Steer already
binds its original item and receipt to Worker ID, generation and request ID.

`self_queue_read()` -> local stdio MCP identity from `PAN_AGENT_SESSION_ID` ->
loopback-only `/api/self-queue/read` bridge -> `self_queue.read` -> shared queue
eligibility and native edit-lease contract -> complete visible original items.
There is no Session argument on either MCP tool. Non-stdio transports cannot
borrow the bound identity. The loopback header follows the repository's existing
trusted local MCP bridge model; it is not an OS sandbox against an agent executing
arbitrary local code or forging local HTTP requests.

`self_queue_consume(item_id)` -> bound bridge -> server-owned attempt keyed by
Session/original item ID -> latest eligibility check. A running stream adapter
with `supports_native_steer` uses `steer_queue_item`: original body/parts encoding,
durable pending reservation, actual control write, contract-specific confirmation,
and original-item ledger/history settlement. Otherwise, an ordered ticket saves
the original item's priority intent, then the existing restart/create and FIFO
reservation/handoff/receipt chain performs delivery. Generation fencing prevents
a stale request from restarting a replacement Worker again. No queue lock spans
the restart or provider confirmation wait.

Only a sent ledger receipt followed by verified persistence can produce
`ok=true/status=consumed`. Restart completion, priority order, row disappearance
and pending intent do not produce success. Original source/taskId/channel/envelope/
parts/message metadata stays on the original item and existing ledger/history
origin mechanism. Pending retries do not recreate messages or repeat Restart.
Server-owned tasks continue after requester cancellation; persistent intent and
existing sent receipts cover a lost response. Receipt retention uses the existing
bounded ledger policy; an expired receipt yields unavailable rather than replay.

## Gates and persistence

- Manual per-item/all-item UI locks use `queueLockManual`. Stop reports writes
  `queueLockAutoReport` only to the reports actually governed by that action;
  explicit unlock can clear it while the future-report rule remains enabled.
  Channel reminders and notices are not hidden merely because that rule is on.
- `queue_paused` hides the entire queue. Active edit leases and native ownership
  also gate access. Native control's global edit-lease exclusion is reflected in
  the own-queue read projection.
- Hidden/guessed/stale IDs use the same `item_unavailable` response. Reads omit
  total counts, positions and whole-queue revisions. Raw provider errors and
  Dashboard snapshots are never relayed through this interface.
- Consumer `_queue_handoff` now rechecks item locks/leases at the final existing
  queue-lock boundary, including channel reminder units. Reservation selection
  alone could previously miss an item lock applied before actual handoff.
- Priority intent, selected-item reservation, selected handoff attempt marker,
  and native pending reservation apply/save/failed rollback inside their own
  ordered persistence ticket. Snapshots are captured when the ticket executes.
  `DurableOutcome.succeeded` distinguishes cancelled-success from failed writes.
- `selfConsumeRequested` records the existing item's priority intent.
  `selfConsumeAttempted` is persisted before its actual handoff operation.
  An unfinished attempted handoff becomes `selfConsumeUncertain`, which the
  shared lock/recovery rules exclude from automatic replay. The policy is
  conservative: cancellation after the marker but before the write can also
  leave an uncertain item. No new Session-wide registry/schema was introduced.

## Verification

Commands used the installed Python 3.14 environment; no dependency installation
or junction was needed. JUnit artifacts contain the exact test identities.

1. Combined targeted regression: **130 passed, 0 failed, 0 skipped** in
   `self-queue-mcp-tests.xml`: own queue, unified FIFO, queue restart hardening,
   Session queue API, Worker branch/Steer, history ordering, MCP handbook,
   Codex session conversion/delayed restart, and real isolated HTTP queue E2E.
2. Final additional own-queue suite: **30 passed**, recorded separately in
   `self-queue-mcp-unit-tests.xml`. Covers filtering and restoration, Stop reports
   versus channel reminders, all-item lock, guessed/stale IDs, final pause/lock/
   lease gates, accepted/rejected/unknown native confirmation, late receipt,
   full metadata, duplicate requests, generation fencing, concurrent writers,
   requester and ticket-owner cancellation, failure rollback, and no unknown
   stream handoff replay. Native stdin/receipts in these tests are simulated.
   HTTP bridge tests also reject browser Origin/Fetch headers, foreign peers,
   unresolved callers and arbitrary Session body arguments through the reused
   local-identity gate.
3. Real isolated HTTP/MCP and OS process E2E: fake CBC stream provider, dynamically
   allocated loopback ports, temporary Session/workdir data. It checks original
   ID priority over earlier FIFO, replacement PID, actual replacement stdin,
   persisted `sent_to_cli`, metadata, same-ID deduplication and hidden-item
   non-delivery. The final HTTP log/JUnit are retained alongside this report.
   The full HTTP suite passed both tests on dynamic port 58498. After reusing
   the existing local identity gate, the original-item test passed again on
   dynamic port 55857 (`self-queue-mcp-http-bridge-tests.xml` and `.log`).
   That final run observed provider PID 16072 -> 25480 and original ID
   `q_f5637ee6155443cea0b811d9b7cb967c`, with one stdin delivery and the original
   `clientMessageId=own-chosen` preserved in the sent ledger receipt.
   An earlier standalone run used port 59794 and observed PID 11288 -> 13252.
4. `compileall`, manifest JSON parsing and `git diff --check` passed.

One intermediate unit run fell through an unstubbed `create_worker` after the
no-Worker path changed. Its exact pytest process tree was terminated. Both
lifecycle stubs and an autouse no-provider-spawn guard now prevent recurrence.
That aborted run is not provider validation; absence of native-data side effects
from that attempted entry has not been independently established.

The environment reports existing pytest timeout-plugin/config and Pydantic/
Starlette warnings. They were not suppressed or converted into skipped tests.
No 8767/8768 requests or real MA/TA service lifecycle operations were performed.

## Remaining acceptance boundaries

Real Codex/Claude provider acceptance, real native UI/OS interaction, actual
self-interruption of this live TA's MCP transport, browser interaction, full
repository suite, practical build and MA independent acceptance remain unverified.
The process E2E uses a deterministic fake provider, not production credentials.
Unknown fallback handoffs deliberately remain blocked and do not automatically
retry; the user must investigate them through existing user-owned controls.
