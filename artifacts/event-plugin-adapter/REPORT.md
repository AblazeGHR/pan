# TA candidate: external plugin event adapter

Worktree: `D:/project/pan-worktrees/event-plugin-adapter-20261022`  
Branch: `feature/event-plugin-adapter-20261022`  
Assignment baseline: `b295773d325c0b543f865953ad9ccf2aaf43e4b4`  
MA-requested core fix: `f8bcf34c`, cherry-picked as `d0f93baa`.

The adapter uses unchanged Service.plugin_enabled/plugin_ready/plugin_disabled
hooks. Its own diff after d0f93baa does not change automation engine.py, store.py,
or events.py. It does not edit React. Candidate completion is not MA acceptance.

Implemented behavior and wire details: [EVENTS.md](../../docs/plugins/EVENTS.md).
The dependency-free Node example has a valid ordinary Catalog manifest and two
capability documents. The event-role peer is service-wide per plugin, with
membership from enabled Session bindings in all three modes. Tool/Panel provider
connections do not own/revoke it. No public arbitrary event publication endpoint
or separate Action Group service is introduced.

## Evidence before frozen-SHA verification

Commands ran through `python scripts/run_tests_isolated.py -- python -m pytest`,
using Python 3.14 and Node from the existing environment; no dependency writes.
Both commands used `-o addopts= -q --junitxml=<named XML>`:

1. `tests/test_plugin_events.py tests/test_plugin_events_http.py
   tests/test_automation_concurrency.py tests/test_automation_core.py
   tests/test_automation_http.py tests/test_plugin_platform.py
   tests/test_plugin_session_config.py tests/test_plugin_panels.py
   tests/test_plugin_provider_window.py`
   — **93 passed, 0 skipped, exit 0**, 52.82 seconds.
   [Log](regression.log), [JUnit](regression.xml), [exit](regression.exit).
2. `tests/test_plugin_events.py tests/test_plugin_events_http.py
   tests/test_action_groups.py tests/test_action_ids.py
   tests/test_plugin_developer_help.py`
   — **86 passed, 0 skipped, exit 0**, 38.20 seconds.
   [Log](action-compat.log), [JUnit](action-compat.xml), [exit](action-compat.exit).

Counts overlap; they are not 179 unique tests. Warnings include the existing
environment lacking pytest-timeout, Pydantic settings and WebSocket deprecations.
No warning was hidden and no assertions weakened. New asynchronous waits use
explicit asyncio deadlines; the HTTP harness has bounded requests/waits.

Initial failures are retained: integration-first is a test collection syntax
error; integration-second used null transport/type fields in a synthetic manifest,
causing the existing server validation to reject the Session selection. The test
now omits optional nulls and asserts the settings result has no error. http-third
and the later batches pass. No product validation was relaxed.

Static commands all exit 0:

```powershell
python tools/validate_plugin.py examples/event-plugin/manifest.json --json
python -m compileall -q packages/plugins tests/test_plugin_events.py tests/test_plugin_events_http.py packages/web/server.py
node --check examples/event-plugin/provider.mjs
git diff --check
```

[Manifest output](manifest-check.json), [static exits](static-exits.json).

## Actual process and lifecycle verification

The new tests spawn actual Node stdio peers, not in-process Python stand-ins.
They verify handshake/registration, publication/schema validation, durable Run,
Action completion, independent subscriber fanout, connection identity, attempted
source/core namespace/generation forgery, oversize payload/frame, EOF revocation,
bounded queue-full rejection, slow-consumer timeout, and restoration of a
persisted queued Run only after actual plugin ready. The accepted core concurrency
suite runs alongside these paths, including wake/deferral ownership regressions.

Three mode-specific tests also spawn actual generated MCP stdio projections over
local Named Pipe IPC, call the Node capability provider, and publish an event
through the still-shared event peer. This separates tool process lifecycle from
event owner lifecycle and proves they coexist. Closing/removing one of two
Session members preserves the other member's peer; removing the last retires it;
re-enable uses a different generation. No Worker/tool discovery is needed to
start event peers.

[HTTP evidence](http-evidence.json) records the actual ephemeral port, temporary
data root, PID/create-time/command identities, both server generations, event
records and final Run state. HTTP checks use owned FastAPI and Node processes,
with the existing fake-CLI launcher and no paid provider. Selected Pause state
is read through the real Session queue API and an unselected Session stays
unpaused. Disabled plugin definitions and retained Trigger dependencies are read
through the real Automation API. Full server restart restores persisted bindings
and the external publication path without tool use or replaying the previous Run.

`ownedProcessesRemaining: []` and `portReleased: true` are asserted after cleanup.
Adjacent simulated practical sentinel stays byte-identical. The mode evidence
JSONs additionally contain publications and deliveries from actual external peers.
The existing core HTTP queue test also runs; its current outputs were copied to
[core HTTP evidence](core-http-evidence.json), preserving older core artifacts.
An empty server log means the harness emitted no server log lines; it is not a
substitute for JSON process/HTTP evidence.

## Frozen candidate verification and scope

After committing this candidate, run the union of the two batches at that exact
HEAD. New logs/XML, source SHA and refreshed process evidence are saved below the
existing gitignored `.validation/event-plugin-final/` directory. The final TA
message supplies that exact SHA and actual outcome. The earlier committed
artifacts remain immutable evidence; tests' automatically rewritten artifacts are
copied to `.validation` and restored to their committed bytes after verification.
No product source is edited during or after that final run.

Not tested: real paid providers, user browsers/mobile/OS UI, user data, production
services, reliable delivery/replay, or large-ledger retention. POSIX process-group
cleanup is implemented but this run exercises Windows Job cleanup. New extension
transport is local stdio only; existing HTTP/SSE/MCP and API provider compatibility
are covered by targeted regressions. External subscriptions are deliberately
realtime only. Configuration changes outside normal Pan revisions converge by the
documented watcher, without a transactional external-file disable/enable promise.

No ports 8767/8768, main/practical movement, shared dependency writes, push,
release, delegation, or existing service operations. Stop after candidate delivery
and await MA integration.
