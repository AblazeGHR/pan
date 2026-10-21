# Action Panel cache, list/full-panel UI and Duplicate Action — TA candidate

**Status: clean candidate for MA independent acceptance.** No `main`/`practical`
ref was moved, nothing was pushed, no existing service/port was operated, and no
model/Job was executed. The worktree branch is
`feature/action-panel-perf-ui-20261021`. The current baseline `main`
`e4f718a5` was merged into the branch as `5472f97d`; the role-guidance docs
`docs/skills/pan/references/{ta,ma}.md` and the updated Pan skill were retained.
`main` is an ancestor of the tested candidate. The approved interactive mock remains at
`audit/action-panel-mock-20261021/`; this directory records the real product
implementation evidence.

## Scope delivered

- The approved Action Panel design is now the real implementation: compact
  Actions/Groups lists, full-panel Action/Group editors replacing the list,
  breadcrumb/back, sticky Save/Cancel, dirty-draft guard, loading/error/empty
  states, English UI, real Pan theme/tokens and 320/390/768/1440 responsive
  layout.
- Shared Action registry cache with request dedupe, cache freshness, targeted
  invalidation, late-response guards and refresh failure that preserves the
  usable cached list. MCP catalog, Session list and Session detail are lazy,
  separate caches. No polling was added.
- Duplicate Action through the existing creation/validation path: server
  allocates a new stable ID, the copy opens in the full-panel editor, original
  Action and Group references are unchanged, no Group membership/build/binding
  is copied, and a failed secondary refresh cannot recreate the Action.
- Action/Job boundary documentation in `docs/plugins/ACTIONS.md` and
  `docs/design/PAN_ACTION_GROUP_MCP_PLUGIN_DESIGN.md`: Action is a reusable,
  parameterizable operation definition; Job is a task instance with goal,
  execution content and trigger schedule fixed at creation time. Future Jobs
  reference Actions without snapshots by default; users copy an Action for
  isolation; a successful Action call only means Job creation succeeded.
- This round does not implement future Job execution of Actions and does not
  change Group immutable release, Session pin, build gates, unbuild blockers or
  Worker apply semantics.

## Real production Chromium measurements

Evidence: `cache.json`, `browser.log`, `screenshots/`. The probe starts an
owned isolated HTTP server on an ephemeral port, creates idle Sessions without
Workers, uses production assets and a real browser, then retires the server.

| Check | Measured result |
| --- | --- |
| First `/react/actions` registry requests | **1** `GET /api/actions` |
| First load catalog requests | **0** `GET /api/mcp/servers` |
| First load registry latency | **2934 ms** (cold isolated server, browser observed) |
| First load to Action list visible | **3994 ms** |
| Client-side re-entry registry requests | **0** (fresh cache) |
| Manual Refresh | **1** `GET /api/actions`, **41 ms** |
| Duplicate Action | **1** `POST /api/actions/actions`, no `item.id`; original unchanged, Group refs unchanged, copy ID `action_9ea9ab40dbe945a59f64b80f` |
| Action save | **1** `PUT`; **0** secondary registry/catalog/session GETs |
| Group build | **1** `POST .../build`; **0** eager catalog GETs |
| Use in Session | catalog and sessions fetched lazily when modal opens; **1** `PATCH`, **1** background `GET /api/actions` for blockers |
| Unbuild | **1** `POST .../unbuild`; **0** eager catalog GETs |
| Refresh failure | cached list and duplicate copy remain visible; **0** duplicate POSTs |
| First-load failure | blocking error + Retry recovery |
| Late registry response | editor draft `Late editor draft` preserved |
| Refresh/save interleaving | a delayed pre-save `GET /api/actions` ends after Save; saved text remains, Refresh re-enables |
| Late Session detail response | selected Session not replaced; PATCH uses the selected Session revision |
| Narrow layout | 390 and 320 document/panel `scrollWidth == clientWidth` |
| Page errors | **0** |
| Console errors | only the two intentionally injected aborted requests |

The two shell Session requests on first load are the existing dashboard/Sidebar
snapshot, not the Action binding store. The Action-specific cache still avoided
the MCP catalog until Group binding needed it and avoided a second registry
request on re-entry.

## Verification

- Frontend: `pnpm exec tsc -b` exit 0.
- Frontend tests: `pnpm exec vitest run src/views/ActionsView.test.tsx
  src/stores/actionRegistryStore.test.ts
  src/components/session/PluginSelection.test.tsx` -> **31 passed**.
  New coverage includes registry dedupe/TTL/stale/late GET/mutation
  serialization, load-owner busy convergence, refresh→mutation→old-GET,
  applyBinding interleaving, lower-revision responses, catalog/Session request
  generations and invalidation/reset races, Session-detail reset epoch,
  duplicate behavior, build/unbuild, three-mode binding preservation and dirty
  guards.
- Lint: targeted ESLint exit 0; one Fast Refresh warning in the shared actions
  UI module.
- Build: `pnpm build` exit 0, 76 compressed assets.
- Backend: isolated targeted pytest `tests/test_action_ids.py`,
  `tests/test_action_groups.py`, `tests/test_plugin_session_config.py` ->
  **84 passed**.
- Real probe: `python scripts/run_tests_isolated.py -- python
  scripts/verify_action_panel_cache.py` exit 0. Owned server PID/port/data root
  are in `result.json`; `serverStopped` is true, the port was ephemeral and was
  not 8767/8768.

## Evidence inventory

- `checks.json` — command/result summary and model note.
- `cache.json` — browser request counts, timings and behavior booleans.
- `result.json` — owned server PID, port, data root, probe plugin/sessions.
- `browser.log` — production browser stdout.
- `screenshots/` — desktop lists, duplicate editor, Group/binding modal, refresh
  failure, 390/320 Actions/Groups and Action editor.
- `source-diff-name-status.txt`, `source-diff-stat.txt` — source delta since
  `e4f718a5`.

## Boundaries

- No MA acceptance, fast-forward, push, release or user-skill synchronization is
  claimed here.
- No real paid Worker/provider CLI restart, POSIX process cleanup, crash
  recovery or multi-process registry coordination was exercised in this probe.
- Job execution of Actions remains a documented future reference model only.
- No shared dependency was modified.
