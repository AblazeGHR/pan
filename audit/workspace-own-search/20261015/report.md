# Workspace independent search — 2026-10-15

Branch: feature/workspace-own-search-20261015; baseline: 49df71191cef2ed7cdb62a161d171639b658b3f9.

## Behavior

WorkspaceRail owns uiStore.workspaceSearchQuery; Session retains searchQuery. Both controls reuse the extracted existing Sidebar SearchInput styling and their own accessible clear buttons. Expanded desktop/mobile renders Search workspaces between the title and all row. Collapse removes the input without resetting the store query, including component unmounts. Input key events stop propagation to prevent navigation.

Workspace names use trimmed, case-insensitive substring matching. State definitions, manager inheritance and the full-member per-filter any then AND aggregation remain unchanged. Session candidate filtering is unchanged. Virtual all/ungrouped rows ignore the workspace keyword (no real workspace name); existing per-Session status AND/content-existence rules remain, including original empty-scope visibility with no filters. Queries remain in-memory across panel collapse, not full-page reload.

## Validation

- 91/91 Vitest tests across Sidebar (21), rail search (16), rail read-all (8), sessionFilters (8), workspace scope (13), workspace search (24), history candidate regression (1).
- Tests cover two query/clear directions, real SessionList independence, desktop/mobile collapse/unmount, keys/focus, four statuses, all 15 nonempty state combinations and removal of each required carrier, inheritance, complete-member aggregation and virtual scope semantics.
- tsc -b, scoped ESLint and build passed. Build verified 66 compressed assets; existing Vite import/chunk-size warnings remain.
- Isolated runner: powershell -NoProfile -ExecutionPolicy Bypass -File packages/web/e2e/run-workspace-shared-search.ps1. Chromium 153.0.8010.12: 8/8 scenarios passed, desktop 1440x900 and mobile 390x844. Real typing/clear, Session cards, collapse retention, Enter/arrows/Ctrl+k/1/2/B/Escape focus, input placement and viewport overflow were checked, plus live state projections and different-member combinations.
- Attached browser-results/lifecycle/cleanup JSON and desktop/mobile screenshots. Full local traces/logs: packages/web/test-results/pan-e2e-workspace-shared-search (ignored).
- Verified owned port 43654, server PID 40372, checkout and cleanup with no remaining listener. No protected-port browser requests/page errors. Queues stayed paused through teardown to avoid provider handoff. No real provider/user data/production operations, main/practical movement or push.
- Server stderr included Windows connection-reset traces on browser WebSocket closure; all assertions and page-error checks passed. Physical devices and OS mobile keyboards were not tested.

## Dependencies

Prepared junction lacked xterm packages, causing initial tsc/build failure before browser launch. Safe Undo removed only the junction; independent frozen install used this worktree and its local store/cache without modifying shared dependencies. Independent node_modules remains available for review.

Manifest SHA-256: 514A64A254E9F1A2E4EC7A31EE588A4CFC15174D8E07F1120DAAD402E89AED70.
Lock SHA-256: 249D2FC3A1A0FD511EF440B4F51224F946F1F58DD850060B0BAE7D78A164B149.
Both matched canonical and stayed unchanged.

MA acceptance and subsequent main to practical integration remain MA responsibilities.
