# Session error samples (`session-errors.jsonl`)

Append-only, one global file for classifying Session failures. It is a
diagnostic sample store, **not** a task ledger, retry journal or audit log.

- Default path: `data/logs/session-errors.jsonl`
  (beside the configured `logging.file`, so a custom Pan log directory moves it
  too).
- Schema version: `1`.
- Encoding: UTF-8, one JSON object per line.
- Pan never rotates, truncates or evicts this file. `data_retention` only
  deletes `pan.log.*` and leaves this file (and its `.lock` sibling) alone.
  Retention is an operator decision.
- Writes are best-effort: a failure is logged as a warning and counted in
  `sessionErrorLog` under `GET /api/diagnostics/persistence`. It never raises
  into worker/task/queue code and never changes execution semantics.

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

## Concurrency, dedupe and ordering

- One writer lock per process plus a bounded advisory cross-process lock
  (`session-errors.jsonl.lock`). Each record is a single `os.write` loop under
  `O_APPEND`, so concurrent threads/processes cannot interleave bytes.
- A partial trailing line from a crash is repaired with a newline before the
  next append; readers should still skip unparseable lines.
- `eventId` is deterministic. A duplicate terminal observation in the same
  process is skipped; a failed write is not remembered, so a retry can persist
  it. Dedupe does not scan the file across process restarts and is not
  cross-process; one data root served by one Pan process is the supported
  topology.
- Out-of-order or repeated provider frames are recorded as they arrive. Only
  the terminal boundary is deduped; transient occurrences keep their own
  records via a content/identity dedupe token.
- No `fsync`: the write reaches the OS page cache before the task continues, so
  it survives a process restart but not necessarily a power loss.

## Persistence failure boundary

If opening/locking/writing fails (permissions, disk full, antivirus lock):

1. a warning is logged to `pan.log` with the exception type;
2. `sessionErrorLog.failures` / `lastErrorType` increment in
   `GET /api/diagnostics/persistence`;
3. the sample is dropped;
4. the task, queue, retry and legality paths continue unchanged. No hand-off
   is repeated and no lock is held across the failure.

The lock wait is bounded (~2s); after that the append proceeds without the
cross-process lock and increments `lockFailures`.

## Adapter/path availability

| Path | `cli.raw` | Notes |
|---|---|---|
| CBC stream `result` | yes | Full event including any `error` object. |
| CBC stream `{"type":"error"}` | yes | Recorded as `transient_provider_error`; process/exit state is separate. |
| CBC one-shot (`output_mode=oneshot`) | yes | `result` + all structured `error` events + non-JSON stdout in `cli.rawOutputText`. |
| Codex app-server stream | yes | `turn.error`, in-turn `method=error`, recovered notices, item errors are preserved by the wrapper; assistant text is never used as the error detail when an error object exists. |
| Codex `exec` fallback | yes | `error` object; non-zero exit keeps full stderr in `errorText`. |
| Claude/Kimi/OpenCode | depends | Their result `error`/`error_text` is copied if present; no adapter-specific synthesis. |
| Pan spawn/preflight/crash | no CLI payload | `pan.runtime` text, `returncode` in `pan`. |

## Known boundaries (not verified or not represented)

- A single provider stream line above the existing 256 KiB reader limit is
  truncated by `_iter_stdout_lines`, so that event cannot produce a sample.
  Large one-shot output (parsed after collection) is not subject to that cap.
- Non-JSON stream stdout/stderr lines (including some wrapper diagnostics and
  reconnect chatter) are dropped by the existing reader; only structured
  events are sampled. One-shot non-JSON output is retained in
  `cli.rawOutputText`.
- Terminal PTY/native-UI errors that are not attached to a Session worker are
  not sampled. Pan startup/exit errors without a Session are also out of scope.
- Credential redaction is best-effort (credential-shaped keys and common token
  formats). This module never collects process argv or environment, but a
  provider payload or history message can still contain sensitive text.
- `previousMessage` is deliberately not redacted (full-fidelity requirement).
- Record ordering in the file follows append order, which may differ from
  provider event order under concurrent sessions. Use `timestamp`, `eventId`,
  `workerId`, `generation` and `taskSeq` for ordering within a Session.
- No real remote provider was used to validate the exact error-object shapes;
  Codex/CBC shapes are covered by wrapper/unit and deterministic local E2E
  tests.

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
  failure, partial-line repair, redaction.
- `tests/test_session_error_log_worker.py` – Codex raw error vs assistant text,
  tool-failure layering, startup failure, normal `done` not recorded.
- `tests/test_session_error_log_e2e.py` – real isolated HTTP/uvicorn + local
  deterministic CLI on an ephemeral port (never 8767/8768), stream/one-shot,
  crash, huge text, concurrent Sessions and process restart read-back.
- `tests/test_codex_adapter.py` – wrapper keeps `turn.error` beside assistant
  text and emits a recovered transient notice.

Run with the repository Python env:

```powershell
python -m pytest tests/test_session_error_log.py tests/test_session_error_log_worker.py `
  tests/test_session_error_log_e2e.py -q
```
