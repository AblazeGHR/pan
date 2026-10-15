# Selected Session lifecycle actions — TA evidence

Baseline: `4a4872ec` (`fix(jobs): attempt all hidden cleanup and preserve failure diagnostics`).
Branch: `feature/select-lifecycle-actions-20261015`.
Workspace: `D:/project/pan-worktrees/select-lifecycle-actions-20261015`.

The Session multi-select "Actions" menu now also offers **Restart**, **Delayed restart** and **Kill**, named exactly like the single-Session Worker menu in `SettingsPopover` (`⟳ Restart`, `◷ Delayed restart`, `✕ Kill`). The three actions reuse the existing lifecycle semantics through `workerStore` (`restart` / `delayedRestart` / `killCurrent` → `restartOrStartWorker` / `requestDelayedRestart` / `killSessionWorker`); no new lifecycle implementation was added and the backend was not changed.

Shared production changes: `SelectedSessionActions.tsx` (menu items, confirm, disabled rules), `selectedSessionActions.ts` (action union, click-time worker snapshot, per-target details, honest partial results), plus the browser harness and the isolated Chromium script. Existing rules are preserved: the click-time snapshot (ids/names/worker view) never follows a later selection; the in-flight busy guard blocks duplicate submission and keeps the selection bar blocked; per-target results report partial success/failure honestly and every failure is retryable; Kill keeps the single-Session confirmation (`Kill workers for N selected session(s)?`) and is unavailable while no target has a live worker; Delayed restart is unavailable while any target already has a pending/restarting intent (the existing idle-boundary intent/cancel mechanism, not a client-side delay timer) and re-enables once the intent settles.

## Measured

- `vitest.log`: 7 files, 79 tests passed, zero skips. Covers the three lifecycle actions through the worker-store path, click-time snapshot/worker view, Kill confirmation gate, worker-less no-op, per-target partial failure and retry, vanished target, delayed-restart pending/terminal gating and start-vs-restart reporting, busy propagation, plus Sidebar selection controls, select-mode interactions, SessionItem/SessionMenu and the workerStore lifecycle contracts.
- `eslint.log`: changed production files, new tests/harness and the Chromium script passed ESLint.
- `build.log`: TypeScript + Vite production build passed; 66 compressed assets verified. Vite retains its large-chunk advisory.
- `results.json`: raw intercepted API requests and Chromium results at 1280 / 390 / 320 pixels. Three isolated browser contexts, dynamic loopback Vite port (52220), fixture-only API, zero page errors. External font requests are fulfilled locally, protected ports 8767/8768 are rejected before forwarding.
- `chromium.log`: delayed-restart intent reuse with start-vs-restart action reporting, pending block and post-settle retry; restart click-time snapshot across a selection switch with the in-flight duplicate guard; Kill confirmation dismissal (zero requests), worker-backed requests, the worker-less honest no-op, the no-live-worker disabled boundary, and per-target partial failure with retry — plus the pre-existing read/bell/workspace/broadcast/menu cases.
- `*-menu.png` / `*-broadcast.png`: stable desktop/mobile screenshots.
- `dependency-hashes.log`: manifest/lock unchanged; the missing `@xterm` gap was closed by an independent frozen install inside the worktree after safely removing the prep junction (canonical untouched).

Reproduce from packages/web:

```powershell
pnpm exec vitest run src/components/layout/Sidebar.test.tsx src/components/session/selectedSessionActions.test.ts src/components/session/SelectedSessionActions.test.tsx src/components/session/SessionList.selectMode.test.tsx src/stores/workerStore.test.ts src/components/session/SessionItem.test.tsx src/components/session/SessionMenu.test.tsx
pnpm run build
node e2e/selected-session-actions.e2e.mjs
```

## Code inference and unverified layers

The browser test drives the real menu, snapshot, busy and toast paths against a controllable API stand-in; it does not exercise a live Pan service, real Worker processes, WebSocket intent broadcasts, or backend persistence. The delayed-restart gating reads the worker-store intent map kept fresh by `worker.delayed_restart` events (the single-Session popover additionally refreshes on open; the batch menu intentionally reads the shared store without per-open GETs). Kill decides worker presence from the same effective-worker rule as the single-Session menu (durable workerId+status, or a live worker-store entry); a worker that appears after the snapshot is not retargeted, matching the documented click-time contract. Restart or start is decided atomically by the session-level backend endpoint, so per-target feedback says "Restarted or started" exactly like the single-Session toast.

Intermediate verification failures were corrected before final logs: the e2e fixture initially lacked the worker fields (delayed restart reported the start action for a worker-backed Session), the kill failure stand-in used HTTP 409 (transport error shape) instead of the backend's 200 `{error}` application rejection, and test mocks carried an `ok` field absent from `ApiGenericResponse`. No assertions, thresholds or test identities were removed or skipped. The pre-existing `TopBar.test.tsx` "Terminal TUI" failure reproduces on the clean baseline and is unrelated.

No real service was started/restarted or requested, no real user Worker was operated, native user data was untouched, and no main/practical advance or push was performed.
