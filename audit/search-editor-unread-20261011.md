# Search, Editor and WorkspaceRail TA delivery

Base: `6919bf98c6adf08fbc7fd78c77ec714e6628a085`.
Branch: `feature/search-editor-unread-20261011`.
Checkout: `D:/project/pan-worktrees/search-editor-unread-20261011`.

## Source changes

- Global search uses a text input to remove the browser's duplicate clear X. The remaining clear button uses Eraser, keeps its accessible label, has an explicit input-clearing tooltip, and restores input focus. Close buttons retain X with explicit Esc tooltips.
- Session/global query inputs, ordinal input and the shared popup ignore composing keys, including keyCode 229. The popup stops composing events reaching the outer dock. Existing Enter/Shift+Enter navigation and ordinal Enter-to-jump contracts remain; Esc closes from the popup's other focused controls.
- FileTree deletion uses Trash2 at the original size with Delete title/aria-label. Delete handler, confirmation, hover styling and tab-close controls are unchanged.
- WorkspaceRail aggregates normalized unreadDoneCount using effectiveWorkspaceIds (manager inheritance). A separate yellow dot has a permanently reserved width beside the existing count/status badge. All and Ungrouped virtual scopes share the indicator component. No unread acknowledgement or workspace mutation logic changed.
- No Sidebar or other TA-owned source changed. AGENTS.md was not found in this checkout or its ancestor directories. Pan skill was read.

## Measured evidence

- Focused Vitest: 3 files, 27 tests passed; no existing assertions/skips changed.
- TypeScript/production build passed; 62 compressed production assets verified. Existing large-chunk warning remains.
- Targeted ESLint and git diff --check passed.
- `node e2e/searchEditorUnread.mjs`: real Chromium renders production Session/global search, WorkspaceRail and FileTree components against an isolated fixture. Random localhost port, independent browser contexts and local Vite cache; API responses for search are fixture data. Desktop and 390px mobile touch contexts passed focus, Enter/Shift+Enter wrap, composing Enter/Esc, Esc from role checkbox, global clear/focus, inherited workspace unread, separate count/status and exact count-badge geometry after read. Desktop Trash2 opens confirmation; cancel preserves file and makes no delete request.
- Raw local evidence: `packages/web/test-results/search-editor-unread/{evidence.json,browser.log,unit.log,build.log,lint.log}`; screenshots `desktop-unread.png`, `mobile-unread.png`, `desktop.png`, `mobile.png`; traces `desktop-trace.zip`, `mobile-trace.zip`, `search-desktop-trace.zip`, `search-mobile-trace.zip`.
- Full `historySearchAcceptance.mjs` did not pass. First attempt hit a pre-existing dock pointer interception/folding path. A diagnostic keyboard-opening attempt reached its old `textContent === '1 / 8'` wait, while the ordinal is now an input value; it timed out. The original script and all its assertions were restored unchanged. Final failed raw evidence is in `packages/web/e2e/test-results/history-search-content-completion/{evidence.json,failure.png,server.log}` and its disposable runtime `C:/Users/14709/AppData/Local/Temp/pan-search-completion-iRH8X0`. HTTP counts, roles, paging, stable-ID seek, >500 global results and the initial dock checks completed before failure. This is not complete application acceptance.

## Inference and unverified layers

Search transport/loading/cache/navigation algorithms were not edited; existing unit tests support preservation but complete mounted ChatMessages scroll/location acceptance remains unverified because the full suite stopped early. Fixture scrollToMessage is stubbed. Native IME candidate UI was not driven; composing browser events were exercised. Mobile FileTree retains the existing hover-only deletion affordance; deletion interaction was verified on desktop only. Real service, native user data, deployment and MA acceptance were not exercised.

## Environment boundary

package.json SHA256: `C719902161F24170FAB65116F7BCEDC77E8499A316B5366CDFFCCC0C8946FFC3`.
pnpm-lock.yaml SHA256: `249D2FC3A1A0FD511EF440B4F51224F946F1F58DD850060B0BAE7D78A164B149`.
Both matched `D:/project/Pan/packages/web` before and after validation. No package install or dependency manifest/lock change occurred. The first Vite fixture attempt used the default cache and triggered shared node_modules Vite cache re-optimization; this is disclosed rather than claiming the shared directory was entirely untouched. Subsequent fixture runs explicitly use this checkout's test-results cache and no API proxy. No requests to 8767/8768, push, main/practical movement or real service restart was performed. Integration into main remains MA's separate acceptance step.

Post-validation junction cleanup was rejected by automatic tool policy (`blocked by policy`, no further reason supplied). The existing local node_modules junction and its untracked `.pan-validation-node-modules.junction` marker remain. Tracked files are clean after commit; full `git status --short` still contains that pre-existing marker. No policy bypass or shared-target deletion was attempted.

## Follow-up: complete search acceptance

The full real-backend/ChatMessages suite now passes (`PAN_SEARCH_TEST_PORT=19352 PAN_COMPARE_PR=0 node e2e/historySearchAcceptance.mjs`). Dock opening uses keyboard focus and toggles only if focus did not already open it. Ordinal assertions retain exact N/M checks but read the editable input's value. Driver seeks explicit ordinals instead of assuming initial viewport-nearest selection is newest; the selected legacy assistant has exact ordinal 6/8, then Next 7/8. Offscreen thinking is explicitly located before its expanded DOM assertion. Mobile popups are closed/reopened at the new viewport anchor before settings interaction, preserving active-search hot-unload checks.

Added desktop/mobile Enter/Shift+Enter checks against real search responses, history loading and ChatMessages; active highlighted words must be inside the real scroll viewport. Their message indices and bounding rectangles are captured under `locations` in the raw JSON. Mobile also checks clear removes highlight and restores focus, and Esc removes the popup. No scrollToMessage stub is involved. All nine acceptance groups, zero console/page errors and zero protected-port requests passed. Raw evidence and traces remain in `packages/web/e2e/test-results/history-search-content-completion`; stdout is `packages/web/test-results/search-editor-unread/full-search.log`.

The pinned PR comparison remains enabled by default. This run explicitly disables only those three ancillary performance measurements because GitHub `gh api` timed out during TLS handshake; all current-product HTTP and browser result assertions still execute. Native IME candidate UI remains outside this suite (composing events were covered by the separate component browser test).
