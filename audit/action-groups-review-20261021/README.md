# Independent Action Group acceptance — 2026-10-21

**Decision: BLOCKED / do not integrate yet.** Ordinary defects were repaired in
`integration/action-groups-review-20261021`; the global lifecycle gate and event-loop
IO remain a P1 integration blocker. This is an implementation coordination defect,
not a request to expand or reopen the frozen product design.

Reviewed input: `1aa2541c12e9572e726c45b98e1f59bfbb08c66f`, against parent
`6178efe0b7104473ba5968b1fbd7b7b7c0028d75`. Worktree was clean at entry.
The repair implementation is commit `3112bdd8` on top of that reviewed input;
the following audit-only commit losslessly compresses failure-reproduction XML.
Read CODEBUDDY, constraints-and-acceptance, the frozen Action Group design and the
original audit README before reviewing all changed product code and tests.
The original source manifest was checked against Git blobs: 13 exact SHA-256
matches and 15 CRLF-equivalent matches, zero unexplained mismatches.

## Remaining blocker B1: global long gate plus synchronous IO

`packages/plugins/lifecycle.py::serialized` takes one per-event-loop gate.
`worker._create_worker` holds it across recovery, process creation, lifecycle
recording, broadcast and Session persistence. `_spawn_process` also takes it.
`api_update_session` takes the same gate for every setting, including unrelated
Sessions and metadata-only updates, across async persistence/broadcast.
Action source writes, build and unbuild do synchronous disk operations on the
event loop; unbuild holds the gate while saving every affected Session and
recursively deleting release directories.

Reproduction: `python scripts/run_tests_isolated.py -- python scripts/probe_action_group_review.py`.
The probe uses a synthetic process-launch await, synthetic Session state and an
owned TemporaryDirectory. No CLI or deployed HTTP service is started.

- A 200ms withheld launch for Session A prevented Session B's unrelated settings
  from entering their implementation; observed wait **205ms**.
- Injecting 200ms of disk latency in an Action save delayed the event-loop
  heartbeat **222ms**, despite that heartbeat being scheduled for 20ms.
- These are deterministic fault-injection observations, not production latency
  estimates. The machine-readable observation is [blockers.json](blockers.json).

The original concurrency tests prove mutual exclusion, not non-blocking behavior.
The current synchronous unbuild does avoid an async gap between its live-running
check and unregister, but its cost is global scheduling blockage. Simply moving
it to a thread or inserting `await save_async` is unsafe: an idle Worker may accept
new work while the check/commit is in flight. A follow-up repair must introduce
short in-memory admission claims/generations for overlapping Group/Session work,
fence actual task handoff as well as startup/settings, perform durable IO outside
the mutex and revalidate before publish. Unrelated Session work must continue.
The independent reviewer has not replaced the broad Worker/persistence
coordination policy in this ordinary-fix patch; MA must retain this blocker and
return the lifecycle repair for acceptance before integration.

## Repaired findings

1. API reads buffered arbitrary response sizes. They now stream at most 512 KiB
   of decoded bytes with a 30-second overall deadline, in addition to HTTP timeout.
2. A 400,000-byte NUL stdout passed the command byte limit but expanded beyond the
   generic provider's 2 MiB line reader, breaking subsequent calls. The complete
   encoded MCP result is now bounded at 1 MiB. A real provider subprocess regression
   proves a bounded tool error followed by a successful explicit call.
3. JSON Schema permits boolean property schemas. Default expansion treated them
   as dictionaries and failed when optional inputs were absent. It now handles
   boolean schemas without inventing defaults.
4. During unbuild rollback, the first failed restore write aborted restoration of
   later Sessions. All live configs are now restored first, and every restore write
   is attempted. Repeated IO failures explicitly list failed Session IDs; durable
   rollback is **not** claimed for those failed files. Registration remains intact.
5. Adding another gateway Group to an already-running gateway left the physical
   descriptor unchanged, so no restart was requested and an unpinned release became
   visible. The runtime now withholds newly enabled, unpinned Groups from live
   Workers, and settings request the standard delayed restart even with identical
   descriptors. Existing generic gateway plugins retain their dynamic behavior.
6. A valid API draft schema can omit `properties`. Selecting it crashed the Panel
   at `Object.entries(undefined)`; rendering and field insertion now tolerate this
   valid shape. The regression selects, edits and saves such a draft.

The first two output defects and the new-gateway pin defect were reproduced as
failing tests before repair; [output-reproduced.xml.gz](output-reproduced.xml.gz) and
[pin-reproduced.xml.gz](pin-reproduced.xml.gz) retain those failures as evidence, not
as current failing tests. Five backend regression cases and one frontend case
were added; none were skipped, weakened or xfailed.

## Contract assessment and boundaries

- **Reusable abstraction:** Action sources are shared by reference across Groups;
  immutable releases become ordinary capability plugins. The three fixed GET
  routes are a deliberately limited reusable API execution kind, not a general
  internal API proxy. Arbitrary URLs, writes, redirects and HTTP credentials are
  rejected. Query input still follows the target route's query semantics.
- **Commands:** actual executable/argv execution, literal metacharacters, input
  validation, error/timeout, bounded output and real Windows descendant timeout
  cleanup were rerun. This is reviewed local-program execution, not an OS sandbox;
  executable bytes and external files are outside the definition snapshot.
- **Permissions:** Agent draft puts force disabled; build/unbuild/enable are absent
  from the Agent tool. User build validates enabled definitions; high-risk build
  and execution are refused. Local HTTP trust remains unchanged, so the draft
  surface is not an authentication boundary against arbitrary local HTTP access.
- **Versions/modes:** controlled Worker startup fixtures and real provider calls
  cover loaded release pinning, in-flight/repeated calls after rebuild, all three
  modes and cross-Session isolation. The new gateway-admission regression covers
  the missing startup pin case. No paid model or actual model CLI restart occurred.
- **Unbuild:** real running status is checked through live Worker lookup, with
  blocking names/IDs; idle is allowed, persisted descriptors removed, cleanup
  failures reported, and registry failure rollback exercised. Multi-file rollback
  is best effort if the storage itself repeatedly fails; no crash-recovery journal
  is claimed. B1 prevents overall lifecycle acceptance.
- **Panel:** actual HTTP plus production Chromium covers Action/Group CRUD, shared
  selection, build, Session binding, mode change, idle unbuild/delete and mobile
  width. No page errors; screenshots inspected. Not human acceptance.

## Independent validation

| Check | Result | Evidence |
| --- | --- | --- |
| Baseline targeted Python, 7 files | 188 passed, exit 0 | baseline was run before repairs |
| Final targeted Python, original 10-file selection | **211 passed, 0 failed, 0 skipped**, exit 0 | [JUnit](final-backend.xml.gz) |
| Final targeted Vitest, 4 files | **47 passed, 0 failed, 0 skipped**, exit 0 | [JUnit](frontend.xml.gz) |
| Targeted ESLint | exit 0 | changed Action/selection helpers plus both e2e scripts |
| Typecheck and production build | exit 0; **74 compressed assets verified** | `pnpm build` runs `tsc -b`, Vite and asset verification |
| Python compileall / diff whitespace | exit 0 | changed modules and probe |
| Real stdio MCP | **6 successful Action calls**, 2 per mode | [runtime.json](runtime.json) |
| Real HTTP + production browser | CRUD/bind/mode/unbuild/delete; 0 page errors | [browser.json](browser.json), screenshots |
| Concurrency fault injection | B1 reproduced; **not acceptance success** | [blockers.json](blockers.json) |

Python command:

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_action_groups.py tests/test_plugin_session_config.py tests/test_plugin_platform.py tests/test_plugin_panels.py tests/test_mcp_isolation.py tests/test_mcp_port_propagation.py tests/test_worker_system_prompt_spawn.py tests/test_test_isolation.py tests/test_plugin_developer_help.py tests/test_mcp_handbook.py -o addopts= -q --junitxml=.validation/action-review/final-backend.xml
```

Frontend: same four files as the original audit, plus the added case in ActionsView;
the exact source/test hashes are recorded in [source-sha256.json](source-sha256.json).
The first local dependency copy omitted pnpm junctions and xterm packages; the
dependency tree was completed entirely within this worktree before final checks.
No package manifest/lock changed, and no shared dependency tree was written.
One new test initially failed TypeScript's indexed-access check; corrected before
the final successful build. Existing Python config/Pydantic/WebSocket warnings
and Vite mixed-router-import/large-chunk warnings remain visible.

No full-suite claim, remote CI, paid-model conversation, real CLI lifecycle,
POSIX process-group verification, service deployment or human acceptance is made.
All measured commands finished under three minutes; no long-command Job was needed.
Only owned ephemeral HTTP/IPC/provider/browser processes were used and retired.
8768 remained owned by original PID 6032; 8767 was unused. No deployed service was
called or changed. main/practical stayed at `6178efe0b7104473ba5968b1fbd7b7b7c0028d75`.
No push, merge, service restart or delegation was performed.
