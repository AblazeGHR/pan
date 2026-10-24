# Local plugin panels — acceptance evidence

Source SHA: `9f3bea3e8c5f35e77d7da3fd999f1bf627caacd5` (branch `feature/local-plugin-panels-20261024`).
Tested implementation commit: `f94a36a032bd6417e7b9bce3dce5f5860e38b486`, tree `0472bb28f9e495dd3eb6d22e68df4d5b52908e5c`.

The browser run used the production React bundle and a full installed Chromium (revision 1243 fallback
for the pinned Python Playwright), an owned server process on a random assigned port (last run `62874`),
a temporary data root, and a copy of `examples/plugin-webapp`. Ports 8767/8768 and any existing Pan
profile/service were not used. `scripts/run_tests_isolated.py` cleared inherited `PAN_*` and assigned
the port.

## Commands and results

| Command | Result |
| --- | --- |
| `pnpm --dir packages/web build` | exit 0; `tsc -b` + Vite build, 92 compressed assets |
| `pytest tests/test_plugin_panel_builds.py tests/test_plugin_panels.py tests/test_plugin_platform.py tests/test_plugin_events.py tests/test_plugin_session_config.py tests/test_plugin_developer_help.py -q` | 65 passed (1 unrelated pydantic warning) |
| `scripts/run_tests_isolated.py -- pytest tests/test_local_plugin_panels_http.py -q` | 1 passed; port released; no page errors |
| `vitest run LocalPluginPanelsSettings.test.tsx PluginPanelView.test.tsx` | 37 passed, 1 pre-existing failure |
| `python tools/validate_plugin.py examples/plugin-webapp/manifest.json` | static validation passed |

Pre-existing failure: `PluginPanelView.test.tsx` → “reveals an active plugin route after async catalog
load and handles removal” expects 3 view-entry groups, while the 7 built-in entries plus 3 fixture
panels form 4 groups. The assertion predates this work (`viewEntries.ts` last changed 2026-10-23,
test last changed 2026-10-19) and its diff is untouched. It is not caused by `web-app/v1`.

## Browser sequence

1. Fresh panel is `Not built`; Enable is disabled.
2. Build → `Built`; build log visible; Enable enabled.
3. Enable → view entry appears; opening it renders the plugin iframe.
4. The iframe calls `GET /api/health` without a bearer token and shows `ok · Pan 0.4.0`.
5. Replacing `build.mjs` with a failing command keeps `Build failed · previous build kept`, the asset
   URL returns 200 with the previous HTML, and the panel still opens.
6. Disable removes the entry (no serving). Unbuild returns to `Not built`, assets return 404, source
   files remain. Panel lifecycle did not change `/api/mcp/servers` or `/api/plugins/message-sources`.

Screenshots: `01-unbuilt.png`, `02-built.png`, `03-panel.png`, `04-failed-retained.png`,
`05-unbuilt.png`; machine-readable summary in `evidence.json`; server log in `server.log`.
