# Session error samples (`session-errors.jsonl`)

Append-only, one global file for classifying Session failures. It is a
diagnostic sample store, **not** a task ledger, retry journal or audit log.

- Default path: `data/logs/session-errors.jsonl`
  (beside the configured `logging.file`, so a custom Pan log directory moves it
  too).
- Schema version: `1`.
- Encoding: UTF-8, one JSON object per line.
- The file is bounded by whole-file rotation, not truncation: when the active
  file reaches `logging.session_error_max_bytes` (default 20 MiB), the append
  path moves it to `session-errors.jsonl.1` (shifting `.1`…`.N-1`) and appends to
  a fresh file. `logging.session_error_backup_count` (default 3) controls how
  many backups are kept (active + N). The move happens under the same
  cross-process lock as the append, and this process holds no append descriptor
  at that moment, so a line is never cut or truncated.
- Optional `data_retention.pan_logs` also treats the rotated names as
  `pan_logs` candidates (same registered `data/logs` root only, exact basenames,
  no link/junction following). The active file is always skipped.
- Writes are best-effort: a failure is logged as a warning and counted in
  `sessionErrorLog` under `GET /api/diagnostics/persistence`. It never raises
  into worker/task/queue code and never changes execution semantics.
- A failed rotation (for example an operator holding the file open) is counted
  in `sessionErrorLog.rotationFailures` / `lastRotationError`; the sample is
  still appended to the current file, so the size bound stays soft until a
  later attempt succeeds. Nothing is dropped or truncated because of it.

## How records are selected

The sample log records **real error entry points**, never text matches:

| `kind` | Terminal | Where it is produced |
|---|---|---|
| `terminal_error` | yes | Provider `result` with `is_error`, or a Pan preflight/spawn failure that becomes a terminal `error`. |
| `worker_crash` | yes | Stream process EOF/unexpected exit, or an unstoppable runtime after EOF. |
| `worker_start_failure` | yes | `create_worker`/restart could not spawn the provider process. |
| `native_operation_error` | no | `pan.native_error` from the native Codex/CBC wrapper. |
| `transient_provider_error` | no | In-turn `codex.turn_error`, a recovered reconnect notice, a bare CLI `{"type":"error"}` event, or an item error that is not a tool call. |
| `tool_failure` | no | A Codex tool/item completed with `error`/`status=failed`; the task may still succeed. |
| `cancelled` | yes (own outcome) | Provider `result` with `cancelled`/`is_cancelled`. Never labelled `error`. |

`kind` is selected by the call site. It is **not** inferred from the error
message. A task that ends `done` is never sampled, including when a tool or a
reconnect notice failed during it.

## Schema and trustworthiness

Top-level identity fields (all present; `null` when the entry point has no such
fact):

| Field | Source | Notes |
|---|---|---|
| `schemaVersion` | Pan | `1`. |
| `eventId` | Pan (derived) | Deterministic UUIDv5 from session/worker/generation/taskSeq/kind/origin (+ dedupe token). Process-local duplicate suppression. |
| `processSequence` | Pan (derived) | Monotonic process-local preparation order; file order follows it for async submissions. |
| `timestamp` | Pan | UTC ISO-8601. |
| `kind` | Pan (call site) | See taxonomy above. |
| `terminal` | Pan (call site) | Whether this observation is a terminal outcome. |
| `cancelled` | Pan (provider flag) | True only for `cancelled`. |
| `sessionId`, `sessionName`, `name`, `adapter`, `model` | Pan Session | `sessionName` and `name` are the same display name (the latter matches the requested flat schema). Model is the Session's configured/captured model, not necessarily echoed by the failing event. |
| `workerId`, `generation` | Pan Worker | Runtime identity; `generation` distinguishes respawns. |
| `taskSeq`, `taskId`, `executionSeq`, `queueItemId`, `sourceSessionId` | Pan Worker/Session | `null` when the error happened outside a claimed task. |
| `panStatus` | Pan | Actual terminal status (`error`/`cancelled`) when applicable. |
| `errorText` | CLI first, Pan fallback | Complete text, never truncated by this module. |
| `errorTextSource` | Pan (provenance label) | See below. |
| `recovered` | Pan (only when known) | `true` for a transient error followed by a completed turn. |
| `previousMessage` | Pan history | Full deep copy of the last history row at error detection. |
| `previousMessageMissing` | Pan | `true` when history was empty/unloaded. |
| `rawDiagnostics` | CLI/wrapper text | Complete non-JSON stream text for this run, attached to terminal/crash samples only. Never used as `errorText`. |
| `rawDiagnosticLineCount` | Pan | Number of unparsed lines included in `rawDiagnostics`. |
| `rawDiagnosticSource` | Pan | Always `unparsed_stream_text`. |
| `rawDiagnosticComplete` | Pan | `true`; the scratch file is read in full by the writer thread. |
| `cli` | CLI/wrapper | Raw provider facts; `null` if the entry point has no CLI payload. |
| `pan` | Pan | Derived runtime facts (`origin`, `terminalResult`, `returncode`, `workerStatus`, `terminalKey`, item/native details). |

`errorTextSource` values:

- `cli.error` – the provider's own error message (preferred).
- `cli.error_text` – a wrapper-normalized provider error text.
- `codex_error_payload` – Codex app-server `turn.error` object was preserved.
- `codex_turn_status` – failed Codex turn had no error object; the message is
  derived from the turn status, never from assistant text.
- `codex_stderr` – Codex `exec` fallback non-zero exit; full stderr.
- `wrapper_exception` – the wrapper itself raised.
- `pan.terminal_result` – no CLI error object; the text is the terminal result
  Pan published. `pan.terminalResultMayBeAssistantText` is `true` when it is
  structurally identical to this run's last assistant text, so a classifier can
  exclude it if desired.
- `cli.item_status` – a tool item failed without an error message; `errorText`
  is empty and the status is in `cli.raw.item_status`.
- `pan.runtime` – Pan-generated diagnostic (spawn failure, crash, timeout,
  preflight).

`cli.raw` is the complete event as received (sanitized for credentials). Only
fields that actually existed are copied; `code`, `status`, `retryable`, `type`
are **not** synthesized. `cli.error` is the raw error object/string, and
`cli.errorEvents` lists every structured `{"type":"error"}` event for one-shot
runs. `cli.errorEventCount` is a count, not a category.

## Previous message semantics

`previousMessage` is snapshotted **synchronously at the moment the error is
detected**, under the Session's history lock, before report/steer/native
appends can run. It is always the last history row of any role
(`user`, `assistant`, `tool`, `thinking`, `native-tui`, ...), not merely the
last user message. It includes:

`role`, `source`, `messageId`, `ts`, `content`, `historyIndex`,
`historyEpoch`, `historyRevision`, and the full original row under `raw`.

If the Session history is empty or not loaded, `previousMessage` is `null` and
`previousMessageMissing` is `true`; no older user row is substituted.

## Encoding contract

Pan's Worker decodes provider stdout as UTF-8 (`line.decode("utf-8",
errors="replace")`), and the Codex wrappers write UTF-8 bytes explicitly via
`sys.stdout.buffer` (CBC is a Node CLI and emits UTF-8). The deterministic E2E
fixture follows the same contract: `_emit` writes UTF-8 bytes through
`sys.stdout.buffer`, and stream input is read from `sys.stdin.buffer` as UTF-8.
It never relies on the Windows console/pipe default (cp936/gbk) or on an
external `PYTHONUTF8=1`.

The regression `test_fixture_uses_explicit_utf8_under_non_utf8_windows_pipe`
runs the fixture with `PYTHONUTF8=0` and `PYTHONIOENCODING=cp936`, feeds UTF-8
stream input, and strictly decodes stdout as UTF-8. This distinguishes a real
fixture protocol bug from an environment workaround.

## Stream fidelity

`_iter_stdout_lines` keeps the existing 256 KiB parse threshold. When a line
exceeds it, the overflow is written to a temporary file instead of being held
in memory. Recognition is **order-independent** and **top-level-only**: a
streaming scanner tracks string/object/array depth and checks the top-level
`type`/`is_error`/`cancelled` keys, so `{"error": {...}, "type": "result",
"is_error": true}` and a `type` appearing after 256 KiB are handled, while a
nested `"type":"result"` inside assistant/tool text is ignored. Only after that
scan does the worker read the full line back; a `result` is accepted only when
`is_error`/`cancelled` is true. Normal oversized non-error output keeps the
previous cap/drop behavior and is never relabelled as an error.

Non-JSON stream text (stdout and stderr are merged by the worker) is copied to a
per-run scratch file. It is attached to terminal/crash samples as
`rawDiagnostics` with an explicit source and is never written into `errorText`.

## Concurrency, dedupe and ordering

- `record_session_error_async` prepares the record (timestamp,
  `processSequence`, previous-message snapshot) on the caller's event loop,
  then submits file locking and writing to one dedicated executor thread.
  Heartbeats and other tasks continue while a write waits.
- A single writer thread plus a bounded cross-process lock. Windows uses a
  named kernel mutex (`Local\PanSessionErrorLog_<path hash>`), avoiding the
  intermittent file-sharing `ERROR_ACCESS_DENIED` seen when several fresh
  processes open a lock file; POSIX uses a sibling
  `session-errors.jsonl.lock` with `flock`. Each record is written by one
  `os.write` loop under `O_APPEND` while the lock is held, so concurrent
  threads/processes cannot interleave bytes. The same lock serializes
  whole-file rotation, so a rotation can never run beside an append in another
  process.
- The lock is mandatory. If acquisition fails (bounded ~1s in the writer
  thread), no unlocked write is attempted; the sample is dropped and
  `lockFailures`/`failures` increment.
- The file tail is re-checked under the lock before **every** append. There is
  deliberately no process-local "already newline terminated" cache: another
  process may crash after a half line between this process's writes. A partial
  trailing line is repaired with a newline before the next append.
- `eventId` is deterministic. A duplicate terminal observation in the same
  process is skipped; a failed write is not remembered, so a retry can persist
  it. Dedupe does not scan the file across process restarts and is not
  cross-process; one data root served by one Pan process is the supported
  topology.
- Out-of-order or repeated provider frames are recorded as they arrive. Only
  the terminal boundary is deduped; transient occurrences keep their own
  records via a content/identity dedupe token.
- Async submissions are FIFO on the single executor and `processSequence` is
  assigned at preparation time, so file order matches detection order for one
  process.
- An `eventId` is reserved in the in-memory in-flight set only inside the
  writer function and is released in a `finally` block. Cancelling the awaiting
  coroutine can therefore not leave a permanently in-flight id: if the write
  already runs it finishes and clears; if it was cancelled before the writer
  started it never reserved anything. `flush()` drains either outcome.
- `flush()` waits for the writer executor; it is called from Pan's
  `begin_shutdown`. Every async caller already awaits its own write, so no
  unbounded background queue exists.
- No `fsync`: the write reaches the OS page cache before the task continues, so
  it survives a process restart but not necessarily a power loss.

## Persistence failure boundary

If opening/locking/writing fails (permissions, disk full, antivirus lock):

1. a warning is logged to `pan.log` with the exception type;
2. `sessionErrorLog.failures` / `lastErrorType` increment in
   `GET /api/diagnostics/persistence`, and lock failures also increment
   `lockFailures`;
3. the sample is dropped;
4. the task, queue, retry and legality paths continue unchanged. No hand-off
   is repeated and no lock is held across the failure.

A lock failure never falls back to an unlocked append. The Windows mutex wait
is bounded (~1s); POSIX retries the lock-file open (~2s) and then the flock
(~1s). All waiting happens on the writer thread, so it cannot block the event
loop or heartbeat.

## Adapter/path availability

| Path | `cli.raw` | Notes |
|---|---|---|
| CBC stream `result` | yes | Full event including any `error` object. |
| CBC stream `{"type":"error"}` | yes | Recorded as `transient_provider_error`; process/exit state is separate. |
| CBC one-shot (`output_mode=oneshot`) | yes | `result` + all structured `error` events + non-JSON stdout in `cli.rawOutputText`. |
| Codex app-server stream | yes | `turn.error`, in-turn `method=error`, recovered notices, item errors are preserved by the wrapper; assistant text is never used as the error detail when an error object exists. |
| Codex `exec` fallback | yes | `error` object; non-zero exit keeps full stderr in `errorText`. |
| Claude/Kimi/OpenCode | depends | Their result `error`/`error_text` is copied if present; no adapter-specific synthesis. |
| Pan spawn/preflight/crash | no CLI payload | `pan.runtime` text, `returncode` in `pan`; non-JSON stream text appears in `rawDiagnostics` when a run failed. |

## Known boundaries (not verified or not represented)

- The 256 KiB threshold still bounds what the normal parser and history see.
  Only error-shaped oversized envelopes are read back in full; normal oversized
  output (including a huge assistant message) keeps the existing cap/drop
  behavior.
- Non-JSON text is attached only when that run produces a terminal/crash
  sample. A successful run with non-JSON stderr produces no error sample, so
  that text is not persisted.
- Oversized non-object text (including a `[...]` log line) is raw diagnostic.
  Oversized JSON objects that are not error-shaped, or have no top-level
  `type`, are treated as normal output and dropped. Only the error-shaped full
  line is read into memory; one such line at a time, bounded by available
  memory for that single error.
- The streaming scanner reads the whole oversized line to find a top-level
  `type` even when it appears late. This is disk/CPU work in a thread, not a
  reason to read normal oversized output into memory or lower the 256 KiB
  parser threshold.
- Terminal PTY/native-UI errors that are not attached to a Session worker are
  not sampled. Pan startup/exit errors without a Session are also out of scope.
- Credential redaction is best-effort (credential-shaped keys and common token
  formats). This module never collects process argv or environment, but a
  provider payload, raw diagnostic or history message can still contain
  sensitive text.
- `previousMessage` is deliberately not redacted (full-fidelity requirement).
- Record ordering in the file follows append order. Across concurrent Sessions
  use `timestamp`, `processSequence`, `eventId`, `workerId`, `generation` and
  `taskSeq` for ordering; `processSequence` is process-local.
- No real remote provider was used to validate the exact error-object shapes;
  Codex/CBC shapes are covered by wrapper/unit, deterministic local E2E and
  real multi-process tests.

## Reading samples

PowerShell:

```powershell
Get-Content data/logs/session-errors.jsonl | ForEach-Object { $_ | ConvertFrom-Json } |
  Where-Object { $_.terminal -eq $true } |
  Select-Object timestamp, kind, sessionId, taskSeq, errorTextSource, errorText
```

Python:

```python
import json, pathlib
for line in pathlib.Path("data/logs/session-errors.jsonl").read_text("utf-8").splitlines():
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        continue  # crash partial line
    print(rec["timestamp"], rec["kind"], rec["sessionId"],
          rec["errorTextSource"], rec["errorText"][:120])
```

For automatic classification, first match on structural facts (`kind`,
`terminal`, `cancelled`, `panStatus`, `cli.errorSource`, `recovered`,
`previousMessage.role`) and only then inspect `errorText` as data. Do not
re-derive `retryable` from wording.

## Tests

- `tests/test_session_error_log.py` – schema, full Chinese multiline text,
  provenance, previous roles, concurrency, dedupe, restart retention, I/O
  failure, lock failure (no unlocked write), async offload/heartbeat,
  submission order, flush, cancelled-await/queued-cancel in-flight cleanup,
  BaseException cleanup, partial-line repair, redaction.
- `tests/test_session_error_log_worker.py` – Codex raw error vs assistant text,
  tool-failure layering, startup failure, normal `done` not recorded,
  >256 KiB error spill, order-independent key detection (type second, type
  after 256 KiB, nested type rejection), normal oversized result not
  reinterpreted, raw diagnostics attached but not used as `errorText`.
- `tests/test_session_error_log_multiprocess.py` – four real processes writing
  large records without interleaving; a long-lived writer must repair another
  process's crashed half line (tail re-check under lock).
- `tests/test_session_error_log_rotation.py` – whole-file rotation keeps every
  sample in order when capacity allows, a file held by another Windows process
  degrades without dropping or truncating samples, and two real processes rotate
  without losing or duplicating records.
- `tests/test_session_error_log_e2e.py` – real isolated HTTP/uvicorn + local
  deterministic CLI on an ephemeral port (never 8767/8768), stream/one-shot,
  crash, huge text, >256 KiB structured error (type first, error first,
  is_error first), non-JSON diagnostics, concurrent Sessions and process
  restart read-back; plus an explicit UTF-8 fixture regression under
  `PYTHONUTF8=0`/`PYTHONIOENCODING=cp936`.
- `tests/test_codex_adapter.py` – wrapper keeps `turn.error` beside assistant
  text and emits a recovered transient notice.

Run with the repository Python env:

```powershell
python -m pytest tests/test_session_error_log.py tests/test_session_error_log_worker.py `
  tests/test_session_error_log_multiprocess.py tests/test_session_error_log_rotation.py `
  tests/test_session_error_log_e2e.py -q
```

## RuntimeIssue/Event v0.3 projection

Provider/native samples may include a three-layer `runtimeIssue` projection and
`runtime.issue.v1` envelope. Structured fields win; adapter text is strict fallback.
Codex quota exhaustion, Codex capacity and CBC rate limiting are separate; generic
429 and tool failures remain `unknown`. Read-only schema: `/api/diagnostics/runtime-issues/schema`.
This projection does not trigger retry, account switch, restart or recovery.

Actual Codex quota/capacity fixtures and source line hashes are now recorded in
`tests/fixtures/runtime_issues/provenance.json`; the native provider enums are
`usageLimitExceeded` and `serverOverloaded`. The policy observer only admits known
classifications with current host Worker/account binding. Its pre-transaction
queue is bounded and volatile; committed plans/receipts are durable. Diagnostic
JSONL is evidence, not an automatic historical-replay source. Production policy
adapter defaults unavailable and never guesses account/restart API behavior.
