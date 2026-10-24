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

## Mechanism chosen (the load-bearing decision, revised after MA review)

A rotate-at-startup or rename-without-reopen would be fake, so the mechanism is a
real forward-and-reopen.  The first attempt handed the log files to the managed
child as stdout/stderr; that silently failed in the production shape, because a
Windows venv `python.exe` is a **redirector stub** that keeps its own copy of
every inherited handle for the child's whole lifetime — the active file can never
be renamed while Pan runs.  The accepted design therefore keeps the relay but
removes the inherited handle:

1. `launcher._start_main` only creates/verifies `data/logs/pan-console.out.log` /
   `.err.log` and passes their exact paths in `PAN_CONSOLE_OUT_LOG` /
   `PAN_CONSOLE_ERR_LOG`.  It starts `python -c <shim> main.py <markers>` with
   `stdout`/`stderr` = `DEVNULL`: **no log file handle crosses the process
   boundary**, so neither the redirector nor the launcher can pin the file.
2. The shim (`packages/core/console_bootstrap.SHIM`, stdlib only) resolves the
   checkout from the `main.py` argument, and — if Pan cannot even be imported —
   still writes the destinations with a plain append handle before re-raising, so
   import-time failures keep their evidence.
3. `console_bootstrap.open_console_logs()` redirects fd 1/2 to the destination
   files (crash/compile evidence), then `console_logs.install()` replaces them
   with in-process pipes; `dup2` releases the file handles in this process.
4. `runpy.run_path(main.py, "__main__")` runs the real entry, so every import and
   every later write is already relayed.
5. The relay appends each chunk with a short-lived `O_APPEND` handle after
   checking the size; at `max_bytes` it renames the active file to `.1`
   (shifting `.1`..`.N-1`) and the next append creates a fresh file.
6. Append and rotation share one cross-process lock
   (`data_retention.cross_process_file_lock`), because a writer holding a handle
   while another process uses that file as a rename destination would lose bytes
   when Windows unlinks it.
7. `session_error_log` rotates inside its existing named-mutex/FIFO append path,
   before `_ensure_newline`, using the same `logging_setup.rotate_whole_file()`.
8. `data_retention.scan_pan_logs` now accepts all active log families and cleans
   only exact `<active>.N` / `<active>.YYYYMMDD` siblings inside the registered
   `data/logs` root (no link/junction following).

The launcher still exits after readiness; only the main process owns the relay
thread, and exit ordering (`atexit` → drain → `shutdown`) is unchanged.  Console
display (`host_console.Output`), exit policy, provider/child handling and
launcher lifecycle are untouched.  QQ/WeChat/providers inherit fd 1/2, which are
the relay's pipes, so their output still lands in the same file.

**POSIX impact:** `_start_main(hidden=False)` never redirected before, and still
does not: it keeps the old argv (`python main.py ...`), no shim, inherited
terminal stdio.  `_start_main(hidden=True)` on POSIX uses the same shim+DEVNULL
path as Windows, so the child opens the files and relays identically; POSIX has
no rename-while-open limitation, so the change only moves where the file is
opened.  POSIX behaviour was not executed in this round (all rotation tests ran on
Windows).

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
  => 31 passed, 0 failed (exit 0)

# Same console suite under the production-shape interpreter (venv redirector):
D:/project/Pan-main/.venv/Scripts/python.exe -m pytest \
  tests/test_console_log_rotation.py tests/test_launcher.py \
  tests/test_mcp_port_propagation.py -q -o addopts= -p no:cacheprovider
  => 79 passed (exit 0)

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

`tests/test_console_log_rotation.py` runs the launcher integration twice —
`direct` (`sys.executable`) and `venv-redirector` (a venv created with
`python -m venv --without-pip`, i.e. the same 255 KB redirector stub that broke
the first attempt) — and requires `rotations >= 1`, `degraded == false`, a
`.1` backup that still holds the pre-existing oversized bytes, and a bounded
active file.  The suite was also run three times back to back to check the real
multi-process rotation is not flaky.

## Before / after runtime evidence

- `before-source.txt` – baseline excerpts: raw `"a"` handles passed to `Popen`, no
  rotation code in `session_error_log`, single-active `scan_pan_logs`, no bounds in
  `DEFAULT_CONFIG["logging"]`.
- `before-runtime-handle-block.txt` / `before-runtime-probe.py` – real Windows probe of
  the baseline shape: rename while the inherited descriptor is alive →
  `PermissionError 13`, and the file reaches 248000 B with a 65536 B limit and zero
  backups.
- `before-venv-stub-handle.txt` / `before-venv-stub-probe.py` – isolates the MA
  finding: a child that does exactly what the relay does (`dup2` a pipe over fd 1/2)
  releases the file for a base interpreter (`renamed`) but not for the venv
  redirector (`PermissionError 13`), because the stub keeps its own copy.
- `after-runtime-relay.txt` – the real launch shape after the fix
  (`launcher._start_main` → `python -c <shim> main.py`, venv redirector child, a
  20 MiB + 4 KiB pre-existing file): `rotations=1`, `degraded=false`, `.1` keeps all
  20975616 old bytes, active file 10817 B, 200/200 host lines plus the grandchild
  line in the active file, process gone.

## Covered by tests (real Windows, temporary `data/logs`)

- over-limit rotation, backup count honoured, no loss/duplication across rotations,
  line order preserved, active file never truncated.
- file held by another real process: degraded counters, append still lands, rotation
  resumes after release.
- two real writer processes on one profile: every line exactly once, rotation happens.
- relay forwards the process's own stdout/stderr **and** a grandchild's inherited
  output, honours config-provided bounds, and exits cleanly.
- `launcher._start_main` with a real child, **both** with the plain interpreter and
  with a venv redirector stub: env paths assigned, child output rotated in the
  checkout's `data/logs`, `.1` present, active file bounded, process exits, no
  residue.
- `session-errors`: rotation keeps all samples in order, occupied-file degradation,
  two-process rotation without duplicates.
- retention: console/session rotated families are cleaned only inside the registered
  root, active files and unrelated names untouched, external path refused.

## Known boundaries (not tested / not claimed)

- Real provider/service startup (`pan start`, uvicorn readiness/`wait_ready`, QQ/WeChat
  children writing through the relay for a long session) was **not** run: no service
  start/stop, no 8767/8768, no real profile, and no live `data/logs` inspection.  The
  launcher integration stops at a real spawned child + identity + clean exit.
- POSIX behaviour was not executed in this round (see the POSIX note above).
- If the main process is killed hard while a child still holds the inherited pipe
  write end, that child's later output has no reader (the launcher tree kill normally
  removes both).
- A rotation blocked by another process keeps the bound soft (documented above); the
  hard guarantee is only "N backups, no truncation, no lost lines".
- Output produced before the shim installs (interpreter/import phase) goes to
  `DEVNULL`; bootstrap evidence starts when the shim's first statement opens the
  destinations.
- No automatic cleanup of historical console copies under other legacy directories
  (`background_jobs/logs` etc.) was added.
