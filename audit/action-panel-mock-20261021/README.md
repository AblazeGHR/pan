# Action Panel redesign — interactive mock (2026-10-21)

**Status: mock-first review. No product implementation, no merge, no push.**
The worktree `D:/project/pan-worktrees/action-panel-perf-ui-20261021` (baseline
`883406f7`) contains only a standalone mock, its build config, evidence and this
report. Product source under `packages/web/src/` is untouched.

## Open the preview

Open this file directly in Chromium:

`audit/action-panel-mock-20261021/preview/preview.html`

It is a single self-contained HTML file (~483 KiB) with React, the mock CSS and
the Inter font embedded. It makes **zero network requests** and works from
`file://` with no dev server. If a URL is preferred:

```powershell
# Optional; any free port except 8767/8768. Never needed on the workspace server.
python -m http.server 8811 --directory audit/action-panel-mock-20261021/preview
# then open http://127.0.0.1:8811/preview.html
```

A **MOCK PREVIEW** ribbon is always visible. The sidebar footer also says
“Mock preview · Local fixtures”. The preview opens in the Pan dark theme.

### Reproducible preview parameters

Append any of these to the preview URL:

| Parameter | Values | Purpose |
| --- | --- | --- |
| `view` | `groups`, `new-action`, `edit-action`, `edit-group`, `use` | Open the list tab, full-panel editor, or Use in Session dialog directly |
| `id` | a fixture Action/Group ID | Select `edit-action`, `edit-group`, `use` target |
| `state` | `ready`, `loading`, `error`, `empty`, `stale` | Review loading/error/empty/cached-refresh states |
| `theme` | `dark`, `light` | Theme review |
| `controls` | `1`, `0` | Open the preview-state panel, or hide the floating pill |
| `ribbon` | `0` | Hide the mock ribbon (clean design shots only) |

Examples:

- `preview.html?view=groups&controls=0`
- `preview.html?view=edit-group&id=group_browser_tools&controls=0`
- `preview.html?view=use&id=group_browser_tools`
- `preview.html?state=stale&controls=0`
- `preview.html?view=edit-action&id=action_7f2c91a4d83b6e05a1c47d92&theme=light`

## What to review

| Screenshot | What it shows |
| --- | --- |
| `screenshots/00-before-actions-desktop.png` | Before: current Actions tab, large cards |
| `screenshots/00-before-groups-320.png` | Before: Groups at 320 px |
| `screenshots/00-before-new-action.png` | Before: New Action screen |
| `screenshots/01-actions-desktop.png` | **New:** compact Actions list, 10 representative rows |
| `screenshots/02-groups-desktop.png` | **New:** compact Groups list with release state and gates |
| `screenshots/03-action-editor.png` | **New:** Action editor replaces the list, sticky Save/Cancel |
| `screenshots/04-group-editor.png` | **New:** Group editor with action picker, release rail and build gates |
| `screenshots/05-use-in-session.png` | Three binding modes and apply flow |
| `screenshots/05b-use-in-session-top.png` | Same dialog at the top (summary + Session picker) |
| `screenshots/06-new-action.png` | Coordinated New Action experience and auto-ID note |
| `screenshots/07-dirty-guard.png` | Leaving a dirty draft asks before discarding |
| `screenshots/08-state-loading.png` | First-load skeleton |
| `screenshots/09-state-error.png` | Blocking first-load error with Retry |
| `screenshots/10-state-empty.png` | Valid empty registry (not an error) |
| `screenshots/11-state-stale.png` | Cached list remains visible when refresh fails |
| `screenshots/12-light-theme.png` | Same design with Pan light tokens |
| `screenshots/13-actions-390.png` … `13b-sidebar-390.png` | 390 px list and real mobile drawer geometry |
| `screenshots/14-groups-390.png` | 390 px Groups list |
| `screenshots/15-actions-320.png`, `15b-groups-320.png` | 320 px lists, no horizontal scroll |
| `screenshots/16-action-editor-320.png`, `17-group-editor-320.png` | 320 px full-panel editors |
| `screenshots/18-use-390.png`, `18b-use-320.png` | Use in Session top at 390/320 |
| `screenshots/18c-use-modes-390.png`, `18d-use-modes-320.png` | Three modes at 390/320 |
| `screenshots/19-preview-controls.png`, `19b-preview-controls-320.png` | State/theme/reset review panel |
| `screenshots/20-group-built.png` | Build succeeds; status and release rail update |

The three `00-before-*` images are copied from the prior audit at
`audit/action-ui-20261021/` and `audit/action-groups-20261021/` (baseline
ancestor `0a2e5f04`). They are real upstream screenshots, not mock output.

## Interaction map

| Action | Result |
| --- | --- |
| Search Actions/Groups | Live filter by name, description, ID, route or Action name; count updates; no request |
| Filter All/Enabled/Draft or All/Registered/Unbuilt | Local list filter |
| Row name, Edit, or mobile row → Edit menu | Full-panel editor replaces the list; sidebar remains |
| New Action / New Group | Full-panel creation flow; stable ID assigned on save |
| Save/Cancel/Back | Sticky footer and header back; dirty drafts are guarded |
| `Ctrl`/`Cmd`+`S` | Saves while an editor is open |
| `Esc` | Backs out; opens the discard guard when dirty |
| Build Group | Runs the same gates as the backend: ≥1 Action, all selected enabled, no high-risk Action; updates local registry revision |
| Unbuild Group | Disabled when running Sessions have the binding enabled; shows the blockers |
| Use in Session | Select a Session, enable binding, choose Standalone MCP / Pan native / Discover-call, apply locally |
| Refresh | Simulated refresh feedback; no request; first-load, error and stale-cache states are reviewable |
| Preview pill | Switch Ready/First load/Error/Empty/Refresh-failed and light/dark theme; reset fixtures |

A toast always says “Mock only — nothing was sent to Pan.” The preview never
calls the Pan API, never builds a real plugin, never changes a Session and never
opens a WebSocket.

## Design decisions

1. **Actions and Groups are lists, not card grids.** Rows are 60–76 px tall and
   show name, description, type/route, effect, enabled/build state, usage and
   common actions. Ten Actions and six Groups fit in a 1440 × 900 desktop view
   without losing scanability. Columns progressively collapse below 1440,
   1280, 1120, 960 and 768 px; below 768 the row becomes a two-line mobile row.
2. **Editing owns the main panel.** Opening an Action or Group replaces the
   list and its tabs. The breadcrumb/back button, dirty badge, and sticky
   Save/Cancel footer make entry and exit unambiguous. The sidebar is never
   covered on desktop.
3. **Navigation stays recognizable.** `Actions` and `Groups` are tabs with
   counts; `New Action` / `New Group` is the primary button for the current
   tab. Search and filter sit in a sticky toolbar. A refresh button and
   revision/time readout stay in the header.
4. **Group release state is first-class.** The list shows `Registered` /
   `Unbuilt`, version, composition, running blockers and a `Build blocked`
   hint. The editor’s right rail shows plugin ID, version, exposed Actions,
   Build/Rebuild, Unbuild and the three build gates. Build changes bump the
   local revision; deletion is blocked while built.
5. **Use in Session is explicit.** The dialog shows the built release, five
   fixture Sessions, current binding state, running/idle status, the three
   modes with descriptions, and whether the change applies now or at next
   Worker start. Applying is local to the mock.
6. **States are designed, not hidden.** First load uses skeleton rows; first
   load failure is a blocking alert with Retry; empty is a valid state; refresh
   failure keeps the cached list and shows a non-blocking banner. The cache
   strategy is not implemented in product code yet.
7. **Pan fidelity.** The mock reuses the real `Button` and `SearchInput`
   components, real `@theme` color tokens from `src/index.css`, Inter (embedded
   as a local WOFF2), the Pan type scale, lucide icons, the 280 px sidebar,
   48 px view-entry geometry, and the 768 px mobile breakpoint. The sidebar is
   a faithful static shell (session actions intentionally show a preview toast).
8. **Accessibility.** Semantic lists, labelled inputs, `aria-pressed`
   segmented controls, radio groups for Session/mode, `role="status"`/`alert`
   feedback, focus-visible rings, a focus trap in dialogs, Escape/ Ctrl+S
   handling, and large mobile row hit areas. Long IDs/versions wrap instead of
   forcing horizontal scroll.

## Before / after at a glance

This is a UI comparison, not a performance claim. The “before” images are from
the prior audit at the baseline ancestor referenced above.

| Aspect | Before | After (mock) | Evidence |
| --- | --- | --- | --- |
| Actions at 1440 × 900 | Two large cards in a grid; most of the panel empty | 10 compact rows with type, effect, status, usage and actions | `00-before-actions-desktop.png` vs `01-actions-desktop.png` |
| Groups at 1440 × 900 | Card grid; release state and build gates live inside the editor (`ActionsView.tsx:114-120`) | Compact list with release version, composition, running blockers and gate hints | `02-groups-desktop.png` |
| Editing | Editor is appended below the list (`ActionsView.tsx:101-103`); New Action is a separate tab | Editor replaces the list and tabs; back + sticky Save/Cancel; New uses the same shell | `03-action-editor.png`, `04-group-editor.png`, `06-new-action.png` |
| Leaving a draft | No dirty-state feedback or guard | Dirty badge + discard confirmation; Escape/Ctrl+S are handled | `07-dirty-guard.png` |
| Loading | One blocking “Loading Actions and Groups…” for three coupled requests (`ActionsView.tsx:33-40`) | Skeleton rows; catalog/session concepts are separate; stale refresh keeps the list | `08-state-loading.png`, `11-state-stale.png` (design only) |
| 320 px Groups | Large cards, narrower controls | Two-line rows, status in the metadata line, actions stay reachable | `00-before-groups-320.png` vs `15b-groups-320.png` |

## Baseline: what the current implementation actually does

This is code reading of `packages/web/src/views/ActionsView.tsx` at baseline
`883406f7`; it is **not** a performance measurement.

- Mount runs a single `reload()` that requests **three independent resources
  together** (`actions`, `mcp/servers`, `sessions`) via `Promise.all`
  (`ActionsView.tsx:33-40`). The whole panel stays behind one `loading` flag
  until all three finish (`ActionsView.tsx:91`). A slow catalog or Session list
  therefore blocks an already-available Action registry.
- There is no shared store or cache. Navigating away and back remounts the
  view and repeats all three requests.
- Every mutation (`mutate`, `ActionsView.tsx:56-72`) re-requests the MCP catalog,
  and if a Session is selected, re-requests its detail. Save/Create/Delete/
  Build/Unbuild all inherit this extra work.
- Changing a plugin binding (`ActionsView.tsx:152-158`) performs `PATCH` and
  then `reload()` again, i.e. three registry/catalog/session requests. A mode
  toggle therefore costs four requests in total.
- Session detail is fetched with an `AbortController`
  (`ActionsView.tsx:41-49`) and guarded by a current-session ref, but the
  registry reload has no in-flight dedupe or generation guard. Two overlapping
  mutations can apply older registry responses last.
- Editing is appended **below** the list in the same tab
  (`ActionsView.tsx:101-103` for Actions, `:121-145` for Groups). New Action is
  a separate top-level tab (`:105-110`). There is no dirty-state tracking, no
  full-panel editing, and no unsaved-leave guard.
- There is no cache invalidation/authority policy beyond “replace state with
  whatever response finished last”; a failed secondary read after a successful
  create can make the create path ambiguous even though `mutate` commits the
  returned identity first.

### Analytical request counts (baseline vs proposed)

These are expected counts from the current call graph and the proposed cache
policy. They are **not measured latency or request logs**.

| Interaction | Current | Proposed target (not implemented) |
| --- | --- | --- |
| First open `/actions` | 3 (actions + catalog + sessions), list waits for all | 1 (registry); catalog/sessions lazy |
| Leave and re-enter | 3 again; blank/loading | 0 immediate from cache; background revalidate when stale |
| Open a Group editor | 0 after initial list | +1 catalog only if not cached/shared |
| Open Use in Session | sessions already fetched on mount | +1 sessions only when needed; shared/deduped |
| Save/Create/Delete Action | 1 mutation + 1 catalog (+ session) | 1 mutation returning authoritative registry; invalidate only affected slices |
| Build/Unbuild Group | 1 mutation + 1 catalog (+ session) | 1 mutation; invalidate catalog membership/session detail selectively |
| Plugin binding PATCH | 1 PATCH + 3 reload requests | 1 PATCH + targeted session refresh/response merge |
| Manual Refresh | 3 | 1 registry (force revalidate), other resources only if visible |

## Proposed cache/request strategy (brief, for approval)

Not implemented in product code in this round.

- One shared Action registry slice with `data`, `revision`, `status`, `error`,
  `lastLoadedAt`, `inflight` promise. `ensureRegistry()` returns the in-flight
  promise (dedupe) and `refresh()` is explicit; no polling and no fetch in
  render.
- Stale-while-revalidate: cached registry renders immediately; a background
  revalidation runs when the view re-enters or the cache is older than a small
  threshold. Errors keep the cached data and show a non-blocking banner.
- Independent resource slices: MCP catalog only for Group build/Use in Session;
  session list only for the Use in Session picker; Session detail cached per ID.
  None of them can hold the registry behind a combined loading flag.
- Mutation authority: apply the registry returned by the mutation immediately;
  ignore any response whose revision is older than the applied revision or
  whose request generation is not current. Serialize registry mutations so
  save/build/unbuild cannot interleave stale snapshots.
- Invalidation: build/unbuild invalidates catalog membership and affected
  Session bindings; creating/deleting an Action invalidates Group composition;
  binding PATCH refreshes only that Session; manual Refresh forces registry.
  Never clear valid cache on a failed refresh.
- First load uses a list skeleton; Refresh shows an inline spinner and keeps
  rows; failure injection uses the same surfaces. The mock’s
  Ready/First load/Error/Empty/Refresh-failed states are the visual contract for
  this work, not a performance claim.

**Performance measurement is deferred until the product implementation is
approved.** The real run will use production Chromium, an isolated temporary
profile/port, request counts on first and repeat entry, actual timings, throttled
and failed responses, late/out-of-order responses, cache invalidation after
mutations, and 409/error handling. No mock timing will be reported as upstream
performance.

## Verification performed

- Mock build: `pnpm exec vite build --config vite.mock.config.ts` succeeded.
- Typecheck: `pnpm exec tsc -p mock/action-panel/tsconfig.json --noEmit` passed.
- Lint: targeted `eslint` passed with one Fast Refresh warning in `ui.tsx`.
- Evidence capture: `node mock/action-panel/capture.mjs` passed.
  - **27 screenshots**, **15 viewport checks** at 1440/768/390/320, all with
    `scrollWidth == clientWidth`.
  - **0 external requests**, **0 console errors / page errors**.
  - Interactions: search returns 1 Echo row; 6 group rows; 3 mode radios with
    Discover selected; dirty dialog visible; Escape opens the guard; Ctrl+S
    saves and closes; focus stays inside the dialog after 20 Tabs; empty state
    reads `0 of 0 Actions`; mobile drawer opens at x=0; new Action receives a
    31-character stable ID; build toast updates the release; Unbuild is disabled
    for Native QA Toolkit with the running-Session reason.
- Data: 10 Actions, 6 Groups, 6 sidebar Sessions, representative long names and
  descriptions. All data is in `packages/web/mock/action-panel/fixtures.ts`.

`metrics.json` is the machine-readable evidence summary. It deliberately says:
“No Pan API, Session, Group, port 8767/8768 or user data was contacted. UI
states are simulated for design review; these numbers are not upstream
performance measurements.”

## Files

- Inter is embedded from the SIL OFL-licensed Inter project; the license is in
  `packages/web/mock/action-panel/FONT-LICENSE.txt`.
- Mock source: `packages/web/mock/action-panel/`
  - `app.tsx` — shell, lists, preview states, toasts, guards
  - `editors.tsx` — full-panel Action/Group editors
  - `use-in-session.tsx` — binding dialog and three modes
  - `fixtures.ts` — representative local data and mirrored build gates
  - `ui.tsx`, `model.ts`, `mock.css` — shared presentation and theme
  - `capture.mjs` — reproducible screenshot/metrics capture
  - `inline.mjs` — single-file build step (also strips the remote font import)
  - `tsconfig.json` — mock-only typecheck
- Build config: `packages/web/vite.mock.config.ts` (does not participate in the
  production bundle).
- Evidence: this directory (`preview/preview.html`, `screenshots/`,
  `metrics.json`).

`git status` for product code is clean: only the mock directory, the mock Vite
config and this artifact directory are new.

## Next step — waiting for confirmation

Please review the interactive preview and the screenshots. On approval I will:

1. Implement the list + full-panel editor UI in `src/views/ActionsView.tsx` and
   split shared rows/editors as needed, preserving ID generation, schema editing,
   build gates, bindings and revision semantics.
2. Add the cache/dedupe/invalidation layer described above.
3. Run the real production Chromium measurements and the required targeted
   frontend/backend checks, then report before/after real request counts,
   timings, failure/late-response behavior and screenshots.
4. Leave the clean candidate for MA independent acceptance and fast-forward.

No main/practical branch has been moved, and nothing has been pushed.
