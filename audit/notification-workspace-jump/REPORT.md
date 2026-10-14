# Notification → workspace jump — TA evidence (T-NOTIFICATION-WORKSPACE-JUMP-20261014/1)

Worktree: `D:/project/pan-worktrees/notification-workspace-jump-20261014`
Branch: `fix/notification-workspace-jump-20261014`
Baseline: `1db8d9bd893be7f764619e12bcd2d09a5f3eba00`
Strategy: fast-forward (this TA does NOT touch main/practical/push/restart/release).

Deliverable (two commits): the round-1 jump plus the two round-2 narrow fixes
requested by MA review.

## Scope

A notification click (hot) and a cold `?session=<id>` deep link select the target
Session **and** its effective workspace together, in the same deep-link chain. No
new notification channel, no second ownership source. Success means BOTH applied.
Effective ownership resolves through the manager chain (a managed child inherits
its root's workspace). The virtual `无工作区` scope is used ONLY when ownership is
**provable**; anything unprovable — a dangling manager / cycle, an
unavailable/timed-out workspace list, or a membership id the loaded list no
longer contains — is `unknown`, which preserves the deep link (cold) or surfaces
honest feedback (hot) instead of claiming `无工作区`. Deleted / unknown / loading
/ routing-failure feedback and all async guards (click intent, selection epoch,
workspace-selection epoch, URL generation) are preserved; a user workspace switch
during any wait, including A → B → A, wins. The target is re-verified at the final
apply, so a session deleted while waiting reports the existing deletion feedback
and applies nothing.

## Round 2 — MA review fixes

1. **Final-apply existence + explicit result.** `commitTarget` (void) is replaced
   by `resolveWorkspaceTarget` (`workspace | ungrouped | unknown | missing`) plus
   `applyTarget`; at the final apply the target is re-verified through the
   existing `lookupSessionRef` (`found | missing | unknown`). Deleted →
   `MISSING` toast and not applied; session list unavailable → `LIST_UNAVAILABLE`;
   unprovable workspace → new `WORKSPACE_UNAVAILABLE` toast and not applied. The
   cold `applyLink` clears the link only on a resolved/missing outcome and keeps
   it on `unknown`, so a preserved link recovers when the workspace list later
   loads.
2. **`ungrouped` only when provable.** `sessionFilters.ts` gains
   `WorkspaceOwnership` (`none | workspace | indeterminate`) and
   `resolveWorkspaceOwnership` / `createOwnershipResolver` — the single chain
   walk. `createWorkspaceResolver` now delegates to it and keeps its historical
   fail-closed ids-only result (determinate root id, else `[]`), so
   `effectiveWorkspaceIds`, `workspaceMemberIds`, the tree scope and the rail are
   byte-for-byte unchanged. A dangling manager or cycle is `indeterminate`, never
   "no workspace".

## Changed files

- `packages/web/src/utils/session-deeplink.ts` — resolve/settle/apply workspace
  from the latest state; workspace generation in every takeover guard; final
  existence re-verify; unknown handled honestly (cold keeps the link, hot gives
  feedback); no false success.
- `packages/web/src/utils/sessionFilters.ts` — determinacy-aware ownership
  resolver; `createWorkspaceResolver` delegates (behavior preserved).
- `packages/web/src/stores/uiStore.ts` — `_workspaceSeq` monotonic workspace
  generation (race guard only, not an ownership source); `?panE2E=1`-gated
  read-only inspection seam `window.__panUIStore` (mirrors `__panSessionStore`).
- `packages/web/src/utils/session-deeplink.test.tsx` — 29 targeted cases.
- `packages/web/e2e/notification-workspace-jump.{real.e2e.mjs,server.py}` —
  isolated real-backend / real-Chromium harness (10 checks).

## Commands and results

Targeted vitest:
- baseline: `src/utils/session-deeplink.test.tsx` → 14 failed | 11 passed (25).
- candidate: 29 passed (29); with `workspaceStore.test.ts` +
  `WorkspaceRail.searchFilter.test.tsx` → 54 passed.

Full frontend suite (vitest, isolated cacheDir):
- **1621 passed | 1 failed (1622)**, 145 files. The single failure
  (`components/layout/TopBar.test.tsx` → Terminal-TUI) **also fails at baseline**
  with the identical dependency set (verified via `git stash`), so it is
  pre-existing and unrelated.

Static checks:
- `tsc -b` → exit 0; scoped `eslint` on all changed source/e2e files → exit 0.
- `vite build` → exit 0; `node e2e/verify-precompressed-assets.mjs` → exit 0
  ("Verified 66 compressed production assets").

Real isolated browser + backend E2E (headless Chromium, real production bundle +
real FastAPI app, owned dynamic port, disposable runtime root, system-notification
channel off): **10/10 checks passed**, `foreignRequests: 0`, `pageErrors: 0`.
- cold `?session` → session+workspace (desktop); managed child inherits root
  workspace; ungrouped → `无工作区` with no workspace selected; hot notification
  onclick from another view → session+workspace, back on chat, no leaked param;
  user workspace A→B→A during a slow lookup wins; cold `?session` with the
  workspace list unavailable keeps the link + honest feedback and recovers on a
  fresh load; **cold `?session` whose target is deleted during the workspace-list
  wait is surfaced and NOT applied**; **hot click with an unavailable workspace
  list applies nothing and reports honestly (target not force-selected,
  workspace unchanged)**; mobile 390×844 cold `?session` → session+workspace; no
  external requests / no page errors.
- Raw evidence: `e2e-notification-workspace-jump-real.json` (this directory).

## Boundary (not claimed)

- The browser `Notification` **display layer is stubbed**; the app-attached
  `onclick` is invoked exactly as a delivered click would invoke it. OS-level
  notification display/focus/real OS click is **NOT** asserted.
- Desktop DOM scoping is asserted (visible session cards per scope); the mobile
  check asserts store state (session + active workspace) under a real mobile
  viewport.
- **Hot target deleted during the workspace-list wait** is pinned by the
  deterministic test (cold + hot). It is not reproducible in the browser because
  the hot path's workspace wait only exists while the list is still unloaded —
  i.e. during the initial load, when no completion notification can yet exist. In
  the browser the hot deletion case is covered by the phase-1 spec
  (deleted-before-click → deletion toast) and this run's hot "unavailable
  workspace → honest feedback, no false success" check.
- When the delete broadcast's refresh supersedes the jump's own authoritative
  fetch, the existing contract legitimately reports "deleted" or "list
  unavailable"; both are honest, and the deleted target is never selected. The
  exact branch is pinned by the deterministic tests.
- No Python regression run (backend unchanged); the venv only launches the
  isolated E2E fixture server.

## Dependency note

The prepared junction (`Pan-main`) was stale (missing `@xterm/*`) so tsc/build
failed on untouched files. Worktree lock/pkg are byte-identical to
`D:\project\Pan\packages\web`; per the dependency rule the junction was released
with `prepare_validation_env.ps1 -Undo` and re-pointed read-only at the complete
practical deps — no install, no write-through. Vitest/Vite cache was kept
worktree-local. No `8767`/`8768` service, test or userdata was touched.
