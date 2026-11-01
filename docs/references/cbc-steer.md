# CBC stream Steer

CBC's ordinary `stream` transport accepts an out-of-band `control_request`
with subtype `steer`. Pan does not send a second normal user prompt and does
not restart/resume the CLI to implement this operation. The protocol was
introduced in CBC 2.140; the investigation exercised 2.164.0 and inspected
the same control fields in the subsequently installed 2.168.0. The 2.168.0
production browser trial also exercised direct/queue UI, self-queue HTTP and
force HTTP on one PID/FILETIME/run. All four returned admission, then were
persisted as native same-run inputs before the same-run assistant final.
The model kept the original baseline answer rather than complying with the
marker changes. The probe's marker assertion failed; zero-model verification
of its retained records separately passed identity, admission, consumption,
original queue IDs/history, one terminal and cleanup checks. See the sanitized
`research/cbc_steer/production-evidence.json`; do not call that failed Job an
unqualified end-to-end success or equate model compliance with admission.

The CLI's `steered: true` response confirms **admission to the current run's
buffer**, not model consumption. Pan exposes `outcome: accepted` and
`consumption: unconfirmed`. A model safe point can subsequently consume that
buffer; cancellation or run termination can occur first. Only the native
transcript/final result establishes consumption in the real validation.

Pan binds every CBC control to the CLI session and the observed native
request ID (`expected_request_id`). The handoff checks the Worker object,
generation, process, stdin, active run and Pan input owner. Direct and queued
history preserve the original run/task/execution snapshot even when the
admission receipt arrives after the task ends. A replacement pipe cannot
acknowledge the original pipe's request. Steer never changes the original
task/result identity.

The direct transaction reserves a durable Session-scoped receipt before
writing. Repeating its message ID is idempotent; an unresolved request blocks
another direct submission even after Worker replacement. Queue Steer retains
the original item and its reservation on unknown outcomes. Unknown results
must not automatically replay, become a new queue item or restore a composer
draft for resubmission. Rejection can release the transaction; a late accepted
receipt settles only its original transaction. The existing queue HTTP and
provider receipt boundaries remain 10 and 30 seconds.

Direct controls propagate a structured outcome from write/admission through
late settlement and HTTP to the composer. Error text is diagnostic only:
neither wording nor provider detail can release an unknown transaction or
turn a confirmed rejection into unknown. Force-send also rejects reuse of a
client message ID with different text, including a reservation race under
the queue lock; it cannot report the original message as the new body's
acceptance.

Queue pause, manual locks and edit leases remain authoritative. `self_queue`
uses the same queue Steer path for an active stream Worker; its legacy
`status: consumed` means the **Pan queue item** was consumed, while the nested
`steer.consumption` remains `unconfirmed` for the model.

An accepted delayed restart/start outranks idle queue/report/self-consume
wakeups. Preparation rechecks retirement immediately before the actual
handoff, including after self-consume attempt persistence. Self-consume never
replaces an existing delayed intent with an immediate restart, even when the
adapter lacks Steer. A task already handed off remains active until its
terminal persistence/publication and queue-unit cleanup complete. The
existing per-Session spawn lock coalesces delayed and automatic starts.

MCP `agent_send` and the `worker_send_force` alias delegate to the
backend's same capability router. An active capable stream immediately
attempts native Steer; idle capable runtimes use ordinary queue handoff;
only incapable runtimes use the existing restart/send fallback. Force input
has an original durable queue item held against ordinary FIFO while native
admission is attempted. Rejection and unknown never fall back to restart;
unknown retains the item and explicit retry after rejection reuses its ID.
The optional `client_message_id` reconciles duplicate force calls. Neither
force nor self-consume bypasses manual/report locks, pause or edit leases.

Generic malformed/error control responses are conservatively unknown, with
sanitized diagnostics. Unsupported/older CLIs cannot silently turn Steer into
a normal turn: an unconfirmed run is rejected before write, and an absent or
unrecognized response becomes unknown without retry. Check the installed
version/protocol when diagnosing such a result.

CBC Terminal TUI is a separate transport. Its running input can be consumed
within the current native run, but its PTY write receipt and existing hook
bridge do not provide this stream control admission contract. Pan therefore
keeps native TUI Steer disabled and retains its ownership/hook gates.
Oneshot remains unchanged for CBC and Claude.

The installed-method fixture also exercises ACP and server admission: ACP
returns an owner request ID; server admission additionally returns message
IDs. These are method-level evidence, not live authenticated ACP/server
trials. Neither transport is needed for the current stream integration.
Keeping ordinary stream avoids a new Session migration, context resume,
MCP/approval/permission, cancellation/recovery and large-output buffering
contract. A future server/ACP migration would need separate validation of
all of those costs; stronger-looking receipt fields alone do not justify it.

Oneshot audit: CBC and Claude explicitly expose the mode; Kimi/OpenCode
expose only stream and use their own short-process wrappers internally.
The Worker consumer, report injection, lifecycle/usage accounting and
persisted Session `output_mode` still reference oneshot. Existing mode,
argument, usage and restart tests depend on those paths. It is currently an
explicit compatibility/per-task isolation option, not an automatic MCP or
long-prompt fallback. Recommend deprecating CBC oneshot first, with an
explicit migration of saved CBC Sessions to stream at a safe idle boundary,
preserving native session/context and MCP configuration. Only after that
migration and release compatibility review should CBC's declaration/argv
and selection UI be removed. Shared Worker/Claude mode removal would need
a separate decision; no mode is removed by this change.

Validation entry points:

- `pytest tests/test_cbc_steer.py` exercises real Pan control/queue/self-queue
  code with deterministic native responses, including duplicate, unknown,
  late response, replacement and cancellation cases.
- `python tools/probe_cbc_stream_steer.py` is an opt-in single-task `hy3` proof
  through built Chromium UI, real HTTP/WS, production Worker and private CLI
  profile. Run through an owned hidden Job; it binds an ephemeral loopback
  port and must never target 8767/8768. It preserves native and Pan histories,
  CLI version/hash, PID/FILETIME, controls and terminal evidence under
  `research/cbc_steer/production-*`. It neither reads/copies credentials nor
  changes global settings, HOME or persistent trust.
