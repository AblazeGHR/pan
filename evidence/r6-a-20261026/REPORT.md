# R6-A legacy write/execution leaf retirement

Candidate worktree: `D:/project/pan-worktrees/job-p13-r6-leaves-20261025`

Branch: `r5/p13-r6-leaves-20261025`
Baseline: `43655680f030765de5bce5ea5b675c7a0e2672d5`

R6-A removes 37 function implementations and replaces 13 already-retired
producer bodies with the existing authenticated HTTP 410 boundary. This is a
leaf-only candidate, not overall R6/P7 integration or a production cutover.

## Reverse references and entrypoints

Before deletion, `scripts/audit_r6_leaves.py` inventoried 4,352 tracked text
files, including production code, tests, manifests/catalogs, documentation and
historical audit artifacts. `references-before.json` records symbol spans,
lexical references, imports, dynamic loaders/getattr expressions and route
registration expressions. AST alias resolution against baseline source finds
62 references to the 37 targets. Every reference is classified individually in
`review.json`:

- the removed execution closure itself;
- a producer body already unmounted by R5 and replaced here by the refusal;
- the old system Action rejection port in Action/chain hosts, now imported from
  `retired.reject_execution`, which still rejects before effects.

There are zero unclassified references and zero surviving qualified production
references. Same-named unrelated functions are retained; lexical hits in old
tests/documents are not treated as live callers. `references-after.json` is the
post-change scan. It excludes this audit's generated outputs to avoid recursive
self-inventory. No module is deleted, so module-level dynamic imports remain
valid. Repository manifests contain no surviving symbol-string dispatcher for
these targets. Arbitrary external plugins and deployed processes are outside
this repository proof.

Effective routes are checked by the existing production route snapshot and
retired-route tests. The route snapshot remains unchanged. MCP registration is
unchanged. The Python functions are absent from module dictionaries;
`__getattr__` gives an explicit `RetiredEntryPoint(AttributeError)` containing
`retired`, without opening a registry, owner DB, lease or executor. The old HTTP
addresses retain authenticated 410 behavior. Authentication still fails closed
when unavailable, and anonymous callers do not bypass it.

## Removed implementations

`packages/core/background_jobs.py` — 28:

```text
start, retry, start_scheduled_process,
_message_next_run, _normalize_target_session_ids, _message_job_common,
start_message, start_broadcast, update_message, run_due_message_jobs,
_tail_log_output, _run_job_action, _execute_job_action,
_scheduled_task_requeue_after, _append_task_run,
_reconcile_scheduled_processes, _redeliver_undelivered,
_advance_paused_entries, _repair_missing_next_fire,
_backlog_undelivered_fire, _fire_scheduled_entry, run_due_scheduled_tasks,
_terminal_broadcast_candidate, _mark_terminal_broadcast,
_broadcast_independent_terminals, recover_notifications,
_recovery_loop, start_recovery_loop
```

`packages/jobs/system_actions.py` — 2: `invoke`, `execute`.

`packages/scheduler/engine.py` — 7:
`_emit`, `_emit_strict`, `_consume`, `_ensure_registered`, `start_loop`,
`tick_once`, `run_now`.

The 13 retired producer bodies are listed in `REPLACED` in the audit script:
five web functions (including `_legacy_action_job`), three legacy Jobs API
functions, five scheduler API functions. Their signatures/route metadata remain;
the implementation delegates directly to the existing authenticated refusal.
`stop_recovery_loop` remains a no-op shutdown adapter and recovery status is
explicitly false.

## Retained boundaries and evidence

`review.json` verifies 19 hard-keep source files byte-for-byte against the
pre-edit scan and 15 background facade/resource functions against baseline
source. Retained:

- `get`, `list_jobs`, `list_run_records`, original JSON/log/receipt access and
  `runs.jsonl` archival reading. `_root` retains its pre-existing directory
  creation behavior; this task does not claim it is filesystem-pure.
- Original unrecognized IDs/bytes/hash, malformed source bytes, no implicit
  settlement, and inert manual-delete dependency packets. Tests use synthetic
  60-record preservation cohorts; the real 60 source records were never opened
  or modified by this task.
- Session history/pins/retention/handoff/notification/Locker dependencies;
  shared store locks, process ownership probes, cancel/resource controls and
  manual deletion prerequisites. Ledger readers and receipt settlement helpers
  remain. No data or log file is deleted.
- Scheduler/Automation Event/Run history, independent history, execution host,
  typed receipt, locks, startup/import guards and runtime owner fencing.
- Canonical `FinalService`, `FinalRuntime`, `CreatorRuntime` and protected
  lifecycle host. No canonical implementation or owner fence is changed.

Shared `_create`/`_update`, scheduler store writers and `retry_due` are retained:
they still have callers in retained hosts/resource paths, including
`reconcile_running`, and therefore do not satisfy the zero-caller leaf gate.
This candidate does not assert `allLegacyZeroProven` or release production R6.
Automation's previously removed host is not recreated; only its retained
Action-host rejection import is redirected.

## Validation

All pytest commands run through `scripts/run_tests_isolated.py`, with inherited
application settings removed and temporary stores. JUnit files and command logs
are adjacent to this report. Baseline: the initial focused group passed 188
tests before changes.

| Layer | Result | Evidence |
|---|---:|---|
| New leaf/refusal/60-record/manual-delete/startup controls | 55 passed | `leaves.xml`, `leaves.log` |
| Owner, retired transport, typed effects, P9D migration, Automation negative controls | 188 passed | `focused.xml`, `focused.log` |
| Scheduler history, system audit, handoff, retention, inbox preservation | 101 passed | `retained.xml`, `retained.log` |
| Canonical contracts/runtime/admission/Creator/HTTP/transports/migration/independent | 392 passed, 1 existing skip | `canonical.xml`, `canonical.log` |
| Platform import/ABI, route snapshot, real isolated TCP, pins/retention/cron | 104 passed | `import-retained.xml`, `import-retained.log` |
| Total executed related tests | **840 passed, 1 skipped, 0 failed** | five JUnit files |
| Full `tests/` collection | **6,924 collected, exit 0** | `collection-with-deps.log` |
| All tracked Python source compilation | 1,085 files, zero failures | `compile.json` |
| Production package imports | 301 passed, zero failures; 34 launch/test files excluded | `import_smoke.py`, `imports-with-deps.log` |
| Static/reverse-reference/route review | 37 absent definitions; 0 surviving resolved target refs | `review.json`, before/after scans |

The existing skip is the symlink preservation test on Windows without link
creation privilege (WinError 1314). No skip/xfail or weakened assertion is added.
Retired owner/system tests now assert symbol absence and explicit refusal;
handoff and retention successor assertions cover the retained behavior without
driving the removed recovery loop.

Full collection initially failed at four browser test imports because the
default interpreter lacked Playwright. A temporary venv with system packages
and Playwright completed collection without changing project requirements or
global Python packages. Full package import initially found missing optional QQ
dependencies; installing the repository's QQ requirements in that same venv
completed import validation. The QQ plugin receives a test NoneBot driver,
matching its existing tests, without running the driver or contacting a gateway.
Import checks forbid subprocess/network operations and restrict writes to an
owned temporary root. CLI/e2e/test launch files are explicitly excluded from
library imports and compiled/collected separately. Logs retain the initial
dependency failures as well as the successful reruns.

The isolated startup smoke exercises the production owner lifespan wrapper,
real canonical/Automation startup, authenticated in-process HTTP reads, zero
legacy leases, no legacy loop and completed shutdown. OS/network/plugin service
lifespans are substituted for that focused smoke. Existing related tests also
exercise real owned loopback TCP on dynamic ports. This is not deployed-service
or browser acceptance.

## Unverified / excluded layers

- The complete 6,924-test suite was collected, not executed. Historical
  pre-canonical positive fixtures elsewhere still describe retired execution;
  they are not claimed as passing successor tests by this candidate.
- No React build, MCP registration change, overall R6/P7 combination,
  remote/three-platform CI, real profile migration/manual deletion, provider
  delivery, production HTTP/browser acceptance, deployment or human acceptance.
- No push, protected branch advancement, deployed service start/stop, or
  interaction with ports 8767/8768. Only owned isolated test runtimes run here.

Reproduction: `validation.json` records exact pytest file groups and counts;
run commands through the isolation wrapper. `scripts/audit_r6_leaves.py OUTPUT`
recreates the current reference inventory; baseline evidence is pinned to the
original source SHA and captured before leaf removal.
