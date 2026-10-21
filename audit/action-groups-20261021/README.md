# Action Groups implementation and validation — 2026-10-21

Worktree: `D:/project/pan-worktrees/action-groups-20261021`

Branch: `feature/action-groups-20261021`

Baseline: `6178efe0b7104473ba5968b1fbd7b7b7c0028d75`

Implemented the frozen [Action Group design](../../docs/design/PAN_ACTION_GROUP_MCP_PLUGIN_DESIGN.md).
The [implementation guide](../../docs/plugins/ACTIONS.md) documents API shapes,
scope, data, execution safety and lifecycle. The repository Pan skill is synchronized.

## Delivered behavior

- Core English Actions Panel and source abstraction; Action/Group CRUD, typed input
  fields, enable/disable, shared references, validation, optimistic registry revision.
- Agent core MCP `action_drafts` CRUD surface. Agent puts force disabled Actions;
  build/unbuild and approval remain user operations under the existing local API trust boundary.
- Fixed internal API GET scope; direct executable + argv commands with whole-argument
  scalar interpolation, no shell, explicit cwd, bounded time/output and tool errors.
  Windows commands enter an existing kill-on-close Job before user code launches;
  timeout and exit retire descendants. Confirmed errors do not poison the provider.
- Immutable build snapshots registered as ordinary API capability plugins. Session
  bindings, all three modes, physical MCP descriptors, provider ownership, IPC and
  idle/deferred Worker restart reuse the generic plugin system.
- Worker startup pins releases. Source editing and rebuild preserve in-flight and
  loaded execution definitions; rebuild asks the standard deferred restart to load
  the next version. Exposed Actions and loaded/published versions are displayed separately.
- Unbuild rejects only enabled live `running` Sessions and lists names/IDs. Idle is
  allowed. Bindings/descriptors are cleared, release files removed, registration
  retired, and stale saved descriptors invalidated. Persistence failure restores
  prior bindings. Cleanup failure is reported after safe unregister. Registration,
  settings commits and startup share a gate; no separate Worker lifecycle is added.

## Validation results

| Check | Result | Evidence |
| --- | --- | --- |
| Targeted pytest, 10 files | **206 passed, 0 failed, 0 skipped**, exit 0 | [JUnit](final-backend.xml), [log](final-backend.log.gz) |
| Targeted Vitest, 4 files | **46 passed, 0 failed, 0 skipped**, exit 0 | [JUnit](final-frontend.xml.gz), [log](final-frontend.log.gz) |
| TypeScript | `tsc -b` through production build, exit 0 | [build log](build.log.gz) |
| Targeted ESLint, changed UI/helpers | exit 0, no findings | [lint log](lint.log.gz) |
| Production build/assets | exit 0; **74 compressed assets verified** | [build log](build.log.gz) |
| Python compileall / Git whitespace | exit 0 | Executed against changed Python modules and working diff |
| Real HTTP + production Chromium | Action/Group CRUD, shared command selection, build, binding, mode switch, idle unbuild, delete; 0 page errors | [browser result](browser.json), [desktop](panel-desktop.png), [mobile](panel-mobile.png) |
| Actual stdio MCP | **6 successful calls**: API + command in standalone, native and gateway, using adapter-produced descriptors | [runtime](runtime.json), [HTTP/probe log](live.log.gz) |

Backend command:

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_action_groups.py tests/test_plugin_session_config.py tests/test_plugin_platform.py tests/test_plugin_panels.py tests/test_mcp_isolation.py tests/test_mcp_port_propagation.py tests/test_worker_system_prompt_spawn.py tests/test_test_isolation.py tests/test_plugin_developer_help.py tests/test_mcp_handbook.py -o addopts= -q --junitxml=.validation/action-groups/final-backend.xml
```

Frontend command:

```powershell
pnpm exec vitest run src/views/ActionsView.test.tsx src/components/session/PluginSelection.test.tsx src/views/PluginPanelView.test.tsx src/components/layout/ViewEntryGroups.test.tsx --reporter=junit --outputFile=D:/project/pan-worktrees/action-groups-20261021/.validation/action-groups/final-frontend.xml
pnpm exec eslint src/views/ActionsView.tsx src/views/ActionsView.test.tsx src/components/session/PluginSelection.tsx src/components/session/PluginSelection.test.tsx src/types/actions.ts src/components/layout/viewEntries.ts src/router.tsx src/services/api.ts e2e/action-groups.e2e.mjs e2e/plugin-selection.e2e.mjs
pnpm build
```

Real probe: `python scripts/run_tests_isolated.py -- python scripts/verify_action_groups.py`.
It uses temporary data under `.validation/action-groups/`, starts only an owned HTTP
server and generic plugin runtime on a random port, and cleans them up. It does not
start the normal gateway/controller lifespan. Exact PID, port and root are recorded
in `runtime.json`; final listener/PID inspection confirmed the owned runtime and its
providers had exited. Protected port 8768 remained owned by original PID 6032;
8767 was not used. `main` and `practical` remained at the baseline commit.

Focused tests cover malformed inputs/defaults, disallowed API paths/redirects,
literal shell metacharacters in argv, failure/timeout/output bounds, descendant
retirement, source persistence, shared Actions, three-mode/Session isolation,
in-flight and subsequent pinned calls across rebuild, actual startup-path admission
versus unbuild, concurrent revision conflicts, running/idle decisions, and failure
rollback. A poisoned-parent subprocess proves test writes stay under fixture roots
and the decoy production sentinel remains byte-identical. UI tests cover editing,
typed fields, errors, shared references/version display and stale Session loads.

## Evidence boundaries

- This is targeted validation, not a local full-suite run or human acceptance.
  No paid model or real CLI model conversation was started. Worker admission/version
  logic uses controlled subprocess/Worker fixtures; actual MCP processes use the real
  generic adapter descriptors and IPC against real HTTP. A paid-provider Session
  conversation or live CLI restart was not used as proof.
- Windows Job containment was exercised with a real descendant. The POSIX process
  group cleanup path was not exercised on this Windows host.
- Initial internal API scope is intentionally the documented three read-only routes;
  arbitrary internal writes/URLs and high-risk approval workflows are not exposed.
  Definitions are versioned; executable bytes, external files and returned API data
  are not frozen. Commands are reviewed local programs, not an OS permission sandbox.
- Pytest emitted four environment/dependency warnings: unavailable pytest-timeout
  config support, a pydantic forward-reference warning and two WebSocket deprecations.
  Build retained the existing mixed router-import and large Markdown-chunk warnings.
  These were not hidden, converted to skips or used to weaken checks.
- Dependency access is a local ignored junction to the existing web node_modules;
  no dependency manifests/locks were changed. No push, merge, release, protected
  service operation or work on another task was performed.

Source hashes for the verified implementation/tests/docs are in
[source-sha256.json](source-sha256.json). Mutable runtime stores remain ignored;
only reports, logs, JUnit and synthetic screenshots/results are committed here.
