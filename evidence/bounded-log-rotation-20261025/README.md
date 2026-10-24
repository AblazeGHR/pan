# Bounded log rotation for pan-console out/err and session-errors (TA evidence)

Branch: `feature/bounded-log-rotation-20261025`
Baseline: `f5cc2684a6a2391c626722cab10a5abeafe96745` (worktree was clean, HEAD == baseline before the first edit).

## Gap that was implemented (not re-investigated)

- `launcher._start_main` opened `data/logs/pan-console.out.log` / `pan-console.err.log`
  in `"a"` mode and passed the raw handles as the child's stdout/stderr. The child
  therefore owned an append descriptor for its whole life: on Windows that file can
  never be renamed, so nothing could ever rotate it.
- `session_error_log` had no rotation at all (docstring: "is never rotated or evicted").
- `data_retention.pan_logs` only knew one active file (`pan.log`).

## Mechanism chosen (the load-bearing decision)

A rotate-at-startup or rename-without-reopen would be fake, so the mechanism is a
real forward-and-reopen:

1. `launcher._start_main` keeps opening both files **only as bootstrap crash
   evidence** and passes their exact paths in `PAN_CONSOLE_OUT_LOG` /
   `PAN_CONSOLE_ERR_LOG`.
2. `main.py` calls `console_logs.install()` as its first statement. That creates two
   in-process pipes, re-points the process's own stdout/stderr (`dup2` + Win32
   `SetStdHandle`) at them, and starts one relay thread per stream.
3. The relay appends each chunk with a short-lived `O_APPEND` handle
   (`RotatingByteLog`) after checking the size; at `max_bytes` it renames the active
   file to `.1` (shifting `.1`..`.N-1`) and the next append creates a fresh file.
4. Append and rotation share one cross-process lock (`data_retention.cross_process_file_lock`),
   because a writer holding a handle while another process uses that file as a rename
   destination would lose bytes when Windows unlinks it.
5. `session_error_log` rotates inside its existing named-mutex/FIFO append path,
   before `_ensure_newline`, using the same `logging_setup.rotate_whole_file()`.
6. `data_retention.scan_pan_logs` now accepts all active log families and cleans only
   exact `<active>.N` / `<active>.YYYYMMDD` siblings inside the registered
   `data/logs` root (no link/junction following).

The launcher still exits after readiness; only the main process owns the relay
thread, and panic/exit ordering (`atexit` → drain → `shutdown`) is unchanged.
Console display (`host_console.Output`), exit policy, provider/child handling and
launcher lifecycle are untouched.

## Defaults and configuration

`packages/core/config.py` → `DEFAULT_CONFIG["logging"]` (overridable in `config.json`):

| key | default | meaning |
|---|---|---|
| `console_max_bytes` | 20971520 (20 MiB) | limit for each of `pan-console.out.log` / `pan-console.err.log` |
| `console_backup_count` | 3 | backups kept (active + 3) |
| `session_error_max_bytes` | 20971520 (20 MiB) | limit for `session-errors.jsonl` |
| `session_error_backup_count` | 3 | backups kept (active + 3) |

Invalid values (bool, wrong type, out of range, backups < 1) fall back to the
default; they can never disable logging or produce a truncating configuration.
`pan.log` keeps its existing `_SizeTimeRotatingFileHandler` (10 MiB / daily / 7).

## Bounds and degradation

- Active file + N backups, each at most `max_bytes` + one write/relay chunk
  (relay chunk = 16 KiB; session-error record = one JSONL line).
- A pre-existing oversized file is rotated once on takeover and kept as `.1`
  byte-for-byte; it is never truncated, so old data can exceed the new bound until
  it ages out through the shift chain.
- If another process holds the active file without delete sharing, the rename fails:
  nothing is truncated, the line is still appended, counters increment, and the
  size bound is soft until the handle is released (retry with exponential backoff
  20 ms → 5 s).
- Observable degradation: `GET /api/diagnostics/persistence` →
  `consoleLogs.<log>.{degraded, rotationFailures, overLimit, lastError, rotations, sizeBytes}`
  and `sessionErrorLog.{rotated, rotationFailures, lastRotationError}`.
- A failed rotation never aborts the write path and never raises into worker/task code.

## Commands and results (isolated runner, exit code 0)

```
python scripts/run_tests_isolated.py -- python -m pytest \
  tests/test_console_log_rotation.py tests/test_session_error_log_rotation.py \
  tests/test_data_retention.py -q -o addopts= -p no:cacheprovider \
  --junitxml=evidence/bounded-log-rotation-20261025/junit-rotation.xml
  => 30 passed, 0 failed (exit 0)

python scripts/run_tests_isolated.py -- python -m pytest \
  tests/test_session_error_log.py tests/test_session_error_log_multiprocess.py \
  tests/test_session_error_log_worker.py tests/test_launcher.py \
  tests/test_mcp_port_propagation.py tests/test_console_exit.py \
  tests/test_runtime_diagnostics.py tests/test_pan_cli.py \
  tests/test_persistence_sharding.py tests/test_docs_terminology.py \
  tests/test_login_startup.py tests/test_diagnose_pan.py \
  tests/test_main_service_restart.py -q -o addopts= -p no:cacheprovider \
  --junitxml=evidence/bounded-log-rotation-20261025/junit-regression.xml
  => 203 passed, 1 skipped (pre-existing `test_docs_terminology::test_skill_sync_copy_matches_source`), exit 0
```

`tests/test_console_log_rotation.py` was also run three times in a row (8 passed each
time) to check the real multi-process rotation is not flaky.

## Before / after runtime evidence

- `before-source.txt` – baseline excerpts: raw `"a"` handles passed to `Popen`, no
  rotation code in `session_error_log`, single-active `scan_pan_logs`, no bounds in
  `DEFAULT_CONFIG["logging"]`.
- `before-runtime-handle-block.txt` / `before-runtime-probe.py` – real Windows probe of
  the baseline shape: rename while the inherited descriptor is alive →
  `PermissionError 13`, and the file reaches 248000 B with a 65536 B limit and zero
  backups.
- `after-runtime-relay.txt` – the same 200-line host through the relay with limit
  2048 B / 3 backups: `active + .1 + .2 + .3`, **202/202 lines retained**, every file
  within `max_bytes + 16 KiB`, process exits 0.

## Covered by tests (real Windows, temporary `data/logs`)

- over-limit rotation, backup count honoured, no loss/duplication across rotations,
  line order preserved, active file never truncated.
- file held by another real process: degraded counters, append still lands, rotation
  resumes after release.
- two real writer processes on one profile: every line exactly once, rotation happens.
- relay forwards the process's own stdout/stderr **and** a grandchild's inherited
  output, honours config-provided bounds, and exits cleanly.
- `launcher._start_main` with a real child: env paths assigned, child output rotated in
  the checkout's `data/logs`, process exits, no residue.
- `session-errors`: rotation keeps all samples in order, occupied-file degradation,
  two-process rotation without duplicates.
- retention: console/session rotated families are cleaned only inside the registered
  root, active files and unrelated names untouched, external path refused.

## Known boundaries (not tested / not claimed)

- Real provider/service startup (`pan start`, uvicorn, QQ/WeChat children writing
  through the relay for a long session) was **not** run: no service start/stop, no
  8767/8768, no real profile, and no live `data/logs` inspection.
- POSIX: the same code path is used for `hidden` launches, but all rotation/handle
  tests here ran on Windows only.
- If the main process is killed hard while a child still holds the inherited pipe
  write end, that child's later output has no reader (the launcher tree kill normally
  removes both).
- A rotation blocked by another process keeps the bound soft (documented above); the
  hard guarantee is only "N backups, no truncation, no lost lines".
- No automatic cleanup of historical console copies under other legacy directories
  (`background_jobs/logs` etc.) was added.
