# Selected Session actions — TA evidence

Baseline: `6919bf98c6adf08fbc7fd78c77ec714e6628a085`.
Branch: `feature/selected-session-actions-20261011`.
Workspace: `D:/project/pan-worktrees/selected-session-actions-20261011`.

The Sidebar selection toolbar now opens Workspace / Read all / 群发 / Bell on / Bell off from one compact menu. Selection and deletion controls remain. Click-time IDs, names and unread generations are captured and never read back from later selection during an operation. In-flight actions guard duplicate clicks and prevent cancelling selection while persistence is unresolved. Dialog cancellation before submission performs no mutation. Failed broadcast submission retains text and schedule for retry.

Shared production changes: Sidebar.tsx, SelectedSessionActions.tsx, selectedSessionActions.ts, and NewJobForm.tsx. NewJobForm has an optional fixed broadcast target entry point, keeps existing once-at/delay, interval and weekly schedule serialization, and does not set source identity. It is loaded lazily. Workspace actions call the existing workspaceStore.moveSessions, including its management subtree and cross-workspace confirmation semantics. Read all uses the existing unread ACK API and store version reconciliation. Bell directions call the existing atomic msg-bridge API, including expected-state checks. No WorkspaceRail, JobsView ordering, backend scheduler, dependency or lock changes.

## Measured

- `vitest.log`: 11 files, 80 tests passed, zero skips. Covers Sidebar selection/deletion/navigation, fixed broadcast targets and all schedule contracts, existing Job templates, workspace tree movement/confirmation, SessionItem, unread done indicators and target-level partial failure. Existing Sidebar test fixtures log failed fresh-history fetches; the assertions pass.
- `eslint.log`: changed production files, new tests/harness and Chromium script passed ESLint.
- `build.log`: TypeScript + Vite production build passed; 64 compressed assets verified. Vite retains its large-chunk advisory.
- `results.json`: raw intercepted API requests and Chromium results at 1280 / 390 / 320 pixels. Three isolated browser contexts, dynamic loopback Vite port, fixture-only API, zero page errors. External font requests are fulfilled locally, protected ports 8767/8768 are rejected before forwarding.
- `chromium.log`: assertions for partial ACK failure and generation race; Bell default validation/off connection request; workspace target snapshot, manager-detach cancellation with zero workspace writes, partial save failure and retry; broadcast target snapshot, save failure preserving content, retry, duplicate guard, Escape during submission, cancellation without submission; menu Escape and viewport geometry; stable mobile dialog geometry.
- `*-menu.png` / `*-broadcast.png`: stable desktop/mobile screenshots, visually reviewed at 320px.
- `dependency-hashes.log`: package.json and pnpm-lock.yaml hashes match the prepared shared dependency source. Dependencies were neither installed nor changed.

Reproduce from packages/web with the prepared validation junction:

```powershell
pnpm exec vitest run src/components/layout/Sidebar.test.tsx src/components/jobs/NewJobForm.window.test.tsx src/components/jobs/NewJobForm.scheduled-qq.test.tsx src/components/jobs/NewJobForm.resume-legal-running.test.tsx src/components/jobs/NewJobForm.selected-broadcast.test.tsx src/components/session/selectedSessionActions.test.ts src/stores/workspaceStore.test.ts src/components/session/SessionWorkspaceDrop.test.tsx src/components/layout/WorkspaceManagerChangeConfirmationModal.test.tsx src/components/session/SessionItem.test.tsx src/stores/sessionStore.doneIndicator.test.ts
pnpm run build
node e2e/selected-session-actions.e2e.mjs
```

## Code inference and unverified layers

Current backend msg-bridge implementation validates Notification completion defaults and QQ bot/contact availability before enabling, and clears QQ subscriptions/report targets/outbox plus browser/system notification connections when disabling. The frontend reuses that API; the browser test injects matching responses. Actual QQ connectivity, notification delivery, scheduled Job execution at due time, the production App mobile wrapper, live backend persistence and MA/main integration were not exercised.

Workspace error feedback reports the current store membership matching the target, rather than claiming that all selected targets moved. After a transport error, the server may have committed an unacknowledged operation; the UI asks the user to review membership before retrying. Existing moveSessions refresh recovery is retained.

Intermediate verification failures were corrected before final logs: external font isolation; fixture error response shape matching the existing API; an immediate disabled-state assertion changed to Playwright's retrying disabled assertion; missing fixture Workspace order/toastQueue fields; a JSX closing brace omitted during formatting. No assertions, thresholds or test identities were removed or skipped.

No real service was started/restarted or requested, native user data was untouched, no OSJob was created, and no main/practical advance or push was performed. Pan skill was read. No AGENTS.md was present in the worktree or its drive/project/worktree ancestors.
