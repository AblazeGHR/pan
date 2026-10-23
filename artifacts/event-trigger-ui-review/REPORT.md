# Automation independent-review follow-up

TA candidate only; MA acceptance is pending. Same worktree and branch:
`D:/project/pan-worktrees/event-trigger-ui-20261022`,
`feature/event-trigger-ui-20261022`. Parent candidate:
`fddd4eb97b6e3c4f377b62be94cf6f1d15eff31f`.

## Reproduced before changing product code

`before.xml` / `before.log`: **15 tests, 5 failures, exit 1**.

- Two conditions held 11 and 22. Removing the first left visible text **11**,
  while the surviving condition object already held **22**.
- External literal replacement left invalid `11e` visible instead of 33 and
  retained obsolete custom validity.
- Deleting the preceding row lost the surviving row's unfinished `22e` buffer.
- New Trigger replaced a dirty draft without asking.
- The integrated delete-then-POST regression stopped at the same 11/22 display mismatch.

All five failures are deterministic; no timing sleeps or providers are involved.

## Changes

- `AutomationView.tsx`: New/Edit consult the active editor's leave guard. Editor
  keys use a monotonic sequence. These controls are disabled while saving/deleting.
- `TriggerEditor.tsx`: dirty changes, including invalid raw JSON changes, are
  protected by a discard confirmation on Close/New/Edit and router departure.
  Cancel preserves the editor and input buffers. `beforeunload` registers the
  native browser protection for refresh/tab close. Switching Runs/Diagnostics
  retains the mounted draft and requires no discard. Successful save and explicit
  server-load clear dirty; rebase keeps it. Synchronous busy admission prevents
  repeated submits before React has rendered disabled controls, and blocks departure
  until the current mutation completes. Forced host unmount still ignores late saves.
- `fields.tsx`: condition rows retain UI identity through immutable edits and
  predecessor deletion. WeakMap keys are UI-only and never enter API JSON. A
  structure change resets the replaced subtree. Literal buffers track the last
  emitted value separately from their raw text: local `22.0`/unfinished `22e`
  survive unrelated renders; an external value replaces the buffer and resets
  obsolete validity. Save/server-load explicitly reset buffers; revision-only
  rebase does not remount them.
- `fields.test.tsx`, `AutomationView.test.tsx`: deterministic display/model/POST,
  raw validity, structural replacement, conflict rebase/server-load, dirty
  cancellation/acceptance, successful-save cleanup, beforeunload, duplicate-submit
  and forced-unmount/late-response coverage. View tests now use a data router to
  exercise the same `useBlocker` contract as production.
- `e2e/automation-review.e2e.mjs`: narrow real HTTP + production Chromium review
  harness using the existing dedicated isolated server/fake-CLI fixture. No core
  or business API changes. This run invokes no provider or Session effects.

## Validation and reproduction

Commands from `packages/web`:

```powershell
pnpm exec vitest run src/components/automation/fields.test.tsx src/views/AutomationView.test.tsx --reporter=default --reporter=junit --outputFile.junit=../../../artifacts/event-trigger-ui-review/after.xml
pnpm exec tsc -b
pnpm exec eslint src/components/automation/fields.tsx src/components/automation/fields.test.tsx src/components/automation/TriggerEditor.tsx src/views/AutomationView.tsx src/views/AutomationView.test.tsx e2e/automation-review.e2e.mjs
pnpm build
node e2e/automation-review.e2e.mjs
```

- Related Vitest: **18 passed**, 0 skipped, exit 0 (`after.xml`, `after.log`).
- Typecheck: exit 0 (`typecheck.log`). Directed ESLint: zero warnings/errors,
  exit 0 (`lint.log`). Production build: exit 0; 76 compressed resources verified
  (`build.log`). Existing large vendor chunk / router import warnings remain.
- Real production Chromium: **5 focused scenarios passed**, exit 0
  (`browser-results.json`, `browser.log`, `http-evidence.json`).
  1. Delete condition 11; visible 22 equals actual POST and durable ledger value.
  2. Dismiss New/Edit/Close/router-leave confirms; invalid text and validity stay.
  3. Real concurrent 409; rebase retains invalid `22e`; explicit server-load displays
     33, clears obsolete invalidity and dirty state.
  4. Accept discard and switch editor; replacing condition structure displays the
     actual replacement value 0.
  5. Delay a real save request; New/Close stay disabled and duplicate native form
     submissions do not create a second POST. Save completion allows Close without
     a discard prompt. An accepted dirty router departure reaches Actions.
- Screenshots: `loaded-server.png` and `saved-320.png`, visually inspected.

The exact **post-commit** rerun is under the gitignored directory
`packages/web/test-results/automation-review-final/`. Its `browser-results.json`
records the final tested SHA; the final response supplies that SHA and evidence
links. Versioned evidence here records the development run before commit.

## Isolation and limits

Every HTTP/browser write verifies the fixture identity (PID/Windows venv ancestry,
checkout, dynamic non-8767/8768 port, runtime, Session root, fake CLI). The owned
process tree is stopped, port release is checked, and checkout auxiliary data
hashes remain unchanged. The JSON records these facts. Server exit 1 is deliberate
owned `taskkill` cleanup, distinct from the harness exit 0.

No core suite, existing Actions suite or navigation suite was repeated: MA already
accepted their previous 23-test run. Build is necessary to test the production
browser bundle. Native beforeunload registration is covered in jsdom; the real
browser review covers in-app router confirmation, not an actual browser tab close.
Overall integrated human acceptance remains pending. No main/practical movement,
push/release, existing-service operations, business backend edits or delegation.

`after-first.xml` preserves the intermediate failure of the former delayed-save
test, which attempted to type into an editor while its fieldset was now disabled.
That test now forces a host unmount/remount to retain its original late-response
invariant; a separate test verifies the newly required pending-operation UI block.
