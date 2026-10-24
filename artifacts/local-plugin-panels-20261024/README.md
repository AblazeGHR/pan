# Local plugin panels — acceptance evidence

Baseline source: `9f3bea3e8c5f35e77d7da3fd999f1bf627caacd5`.
Previously reviewed candidate (preserved, not amended): `525fb7de7db934f01229b258acb7a977ff854687`.
Tested implementation commit: `5b643e582a8089eda86b7e05930493635ae5fded`, tree `e1fb4ba24930905cb33bc8c037622d7414b28e89`.

The browser run used the production React bundle, a real Vite + React example built through the GUI,
an owned server process on a random assigned port (last run `63004`), a temporary data root and a
plugin copy under the same temporary root. Ports 8767/8768 and any existing Pan profile/service were
not used. `scripts/run_tests_isolated.py` cleared inherited `PAN_*` and assigned the port.

## Commands and results

| Command | Result |
| --- | --- |
| `pnpm --dir packages/web build` | exit 0; `tsc -b` + Vite build, 92 compressed assets |
| `pytest tests/test_plugin_panel_builds.py tests/test_plugin_panels.py tests/test_plugin_platform.py tests/test_plugin_session_config.py tests/test_plugin_developer_help.py -q` | 65 passed (1 unrelated pydantic warning) |
| `vitest run PluginPanelView.test.tsx LocalPluginPanelsSettings.test.tsx AppSettingsModal.test.tsx` | 98 passed |
| `scripts/run_tests_isolated.py -- pytest tests/test_local_plugin_panels_http.py -q` | 1 passed; port released; no owned descendants; no page errors |
| baseline `525fb7de` archive: `vitest run PluginPanelView.test.tsx -t "reveals an active plugin route…"` | 1 failed (stale 3-group fixture), see `baseline-frontend-test.txt` |
| old candidate `525fb7de`: process-tree green test with `pytest -o timeout=8` | timeout / child leak, see `red-process-tree-timeout.txt` |

The frontend fixture failure was measured against an exact `525fb7de` archive (not inferred from git
blame); the only change is the group identity `3/3` → `4/4` for the current seven built-in entries plus
three fixture panels. The exact label assertion is unchanged.

## Process-tree fix

`_BuildProcess` creates the build command suspended, assigns it to a host-owned
`JobObjectGuard` (`KILL_ON_JOB_CLOSE`, no breakaway), proves membership and only then resumes it.
Timeout, cancellation, host close and root-exit-with-live-child all terminate the whole Job and verify
`member_pids()` is empty before a build may be published. Cleanup runs in a worker rather than on the
event loop; a blocked `BufferedReader` is interrupted with `CancelSynchronousIo` (or a POSIX wake pipe)
and is never closed before its reader finishes. Deterministic negative controls force unconfirmed
termination with a live reader, assert the heartbeat continues, `abort`/`host.close` stay bounded, and
the panel remains visibly failed with no published build. No PID scan or root-only kill is used for
tree cleanup. Tests use a real Python parent + child where the child inherits stdout; the child PID is
read from the plugin directory and checked with psutil. The old candidate timed out and left that child
running (cleaned after the red run).

POSIX spawn saves the session leader PID as the owned PGID immediately. Termination and verification
use that saved group even after the root exits; `PermissionError`/unknown group queries are reported as
unconfirmed rather than empty success. Branch tests mock `os.killpg` for root-gone and permission paths;
no real Linux host was available, so that platform remains untested end-to-end.

## Immutable builds

Asset URLs now include the build ID:
`/panel-assets/react-console/console/<buildId>/`. On success the store persists the new `activeBuild`
first, then prunes older copies; it retains the newest three successful builds plus the previous active
build. A failure keeps the pointer and artifact. Tests cover an open page requesting its delayed lazy
chunk after A→B, a fresh page reading B, cold host reconstruction, and a simulated state-save failure
that leaves A active with no new copy.

## Browser sequence

1. Fresh panel `Not built`; Enable disabled.
2. Build → `Built`; build log; Enable enabled.
3. Enable → React view entry; iframe loads the real Vite/React bundle.
4. `Call /api/health` → `ok · Pan 0.4.0` without a bearer token.
5. Rebuild B while A stays open; A's delayed `lazy-*.js` loads `Bundle A` from A's immutable URL.
6. Reload → new page/iframe uses B; lazy chunk loads `Bundle B`.
7. Failing rebuild keeps B served (`Build failed · previous build kept`), B's URL returns 200.
8. Disable stops serving; Unbuild returns `Not built`, A/B URLs 404, source remains.
9. MCP catalog and message sources are byte-identical across the lifecycle.

Screenshots: `01-unbuilt.png`, `02-built.png`, `03-react-panel.png`,
`04-old-chunk-after-rebuild.png`, `05-new-build.png`, `06-failed-retained.png`, `07-unbuilt.png`.
Machine-readable result: `evidence.json` (port, builds A/B, asset URLs, page errors, cleanup).
Server log: `server.log` with owned paths redacted.

## Boundaries

- Build tree cleanup is implemented and tested on Windows with the repository's Job Object guard;
  the POSIX branch saves the session leader PGID at spawn, uses it after the root is gone, and treats
  PermissionError/unknown group checks as unconfirmed. POSIX behavior is covered by mocked branch tests
  but was not exercised on a real Linux host here.
- A build command that deliberately escapes its Job (breakaway) is rejected by the guard contract;
  third-party daemons installed outside the build tree are not owned or killed.
- No real user profile, provider, account, port 8767/8768, main/practical branch, push or CI was used.
