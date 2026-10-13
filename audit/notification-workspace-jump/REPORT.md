# Notification → workspace jump — TA evidence (T-NOTIFICATION-WORKSPACE-JUMP-20261014/1)

Worktree: `D:/project/pan-worktrees/notification-workspace-jump-20261014`
Branch: `fix/notification-workspace-jump-20261014`
Baseline: `1db8d9bd893be7f764619e12bcd2d09a5f3eba00`
Strategy: fast-forward (this TA does NOT touch main/practical/push/restart/release).

## Scope

A notification click (hot) and a cold `?session=<id>` deep link now select the
target Session **and** its effective workspace together, in the same deep-link
chain. No new notification channel. Effective ownership is resolved through the
manager chain (a managed child inherits its root's workspace); a session with no
provable membership lands in the virtual `无工作区` scope. A missing root or an
unavailable workspace list never picks an arbitrary workspace. Deleted / unknown
/ loading / routing-failure feedback and all async guards (click intent,
selection epoch, new workspace-selection epoch, URL generation) are preserved; a
user workspace switch during any wait — including A → B → A — wins, and a late
notification never overrides it.

## Changed files

- `packages/web/src/utils/session-deeplink.ts` — resolve target workspace scope
  from the latest state after every await; settle the workspace list (bounded)
  before applying; apply workspace + session together; add the workspace
  generation to every takeover guard; cold-start `applyLink` (gated by
  `committed` + URL/selection/workspace generations).
- `packages/web/src/stores/uiStore.ts` — `_workspaceSeq` monotonic workspace
  generation (bumped by `setActiveWorkspace`) as a race guard only, not a second
  ownership source; plus a `?panE2E=1` read-only inspection seam
  (`window.__panUIStore`), mirroring the existing `__panSessionStore` seam.
- `packages/web/src/utils/session-deeplink.test.tsx` — 16 new targeted cases
  (red at baseline, green at candidate).
- `packages/web/e2e/notification-workspace-jump.real.e2e.mjs` +
  `packages/web/e2e/notification-workspace-jump.server.py` — isolated
  real-backend / real-Chromium harness.

## Commands and results

Targeted (baseline red → candidate green), vitest:
- baseline `1db8d9bd`: `src/utils/session-deeplink.test.tsx` → **14 failed | 11 passed (25)**.
- candidate: same file → **25 passed (25)**; with
  `src/stores/workspaceStore.test.ts` and
  `src/components/layout/WorkspaceRail.searchFilter.test.tsx` → **54 passed**.

Full frontend suite (vitest, isolated cacheDir):
- **1617 passed | 1 failed (1618)**, 145 files. The single failure
  (`components/layout/TopBar.test.tsx` → "does not offer Terminal TUI for other
  adapters") **also fails at baseline** with the identical dependency set
  (verified via `git stash` of the three source files), so it is pre-existing
  and unrelated to this change.

Static checks:
- `tsc -b` → exit 0.
- scoped `eslint` on the three changed source files → exit 0.
- `vite build` → exit 0; `node e2e/verify-precompressed-assets.mjs` → exit 0
  ("Verified 66 compressed production assets").

Real isolated browser + backend E2E (`notification-workspace-jump.real.e2e.mjs`,
headless Chromium, real production bundle + real FastAPI app, owned dynamic
port, disposable runtime root, system-notification channel off):
- **7/7 checks passed**; `foreignRequests: 0`, `pageErrors: 0`.
- Checks: cold `?session` → session+workspace (desktop); managed child inherits
  root workspace; ungrouped → `无工作区` with no workspace selected; hot
  notification onclick from another view → session+workspace, back on chat, no
  leaked param; user workspace A→B→A during a slow lookup wins; mobile viewport
  (390x844) cold `?session` → session+workspace; no external requests / no page
  errors.
- Raw evidence: `e2e-notification-workspace-jump-real.json` (this directory).

## Boundary (not claimed)

- The browser `Notification` **display layer is stubbed**; the app-attached
  `onclick` handler is invoked exactly as a delivered click would invoke it.
  OS-level notification display/focus/real OS click is **NOT** asserted.
- Desktop DOM scoping is asserted (visible session cards per scope); the mobile
  check asserts store state (session + active workspace) under a real mobile
  viewport, not DOM card scoping.
- No Python regression was run (backend unchanged); Python 3.12 venv is used
  only to launch the isolated E2E fixture server.

## Dependency note

The prepared junction pointed at `D:\project\Pan-main\packages\web\node_modules`,
whose install is stale (missing `@xterm/*`) so `tsc`/`build` failed on untouched
files. The worktree `pnpm-lock.yaml`/`package.json` are byte-identical to
`D:\project\Pan\packages\web`, whose install is complete. Per the dependency
rule, the junction was released with `tools/prepare_validation_env.ps1 -Undo`
and re-pointed read-only at the complete practical dependencies; **no install
was performed and nothing was written through the junction**. Vitest/Vite cache
was kept in a worktree-local `cacheDir`. No `8767`/`8768` service, test or
userdata was touched.
