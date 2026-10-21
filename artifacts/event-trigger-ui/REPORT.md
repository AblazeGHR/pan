# Automation production UI candidate — 2026-10-22

TA handoff only. MA integration and human acceptance remain pending.

Worktree: `D:/project/pan-worktrees/event-trigger-ui-20261022`.
Branch: `feature/event-trigger-ui-20261022`. Initial baseline: `b295773d325c0b543f865953ad9ccf2aaf43e4b4`.
At MA request, accepted core fix `f8bcf34c` was cherry-picked as
`89133274c6d4e4c65748d4d28418912d04bbbeac` without disturbing in-flight frontend work.
The HTTP/data contract is unchanged. No TA edits to automation core, Worker,
business routes, plugin lifecycle or release/pin behavior were made.

The exact committed source tested at handoff is recorded by the post-commit
run in `packages/web/test-results/automation-final/browser-results.json` (`head`).
The final response supplies that SHA. Versioned evidence in this directory is
the development acceptance run; post-commit results under `test-results/automation-final/`
are the authoritative final rerun. These generated runtime files are gitignored.

## Delivered behavior and files

- `src/views/AutomationView.tsx`, `router.tsx`, `components/layout/viewEntries.ts`:
  existing Pan navigation adds Automation, with Triggers / Runs / Diagnostics
  and an explicit link to existing Actions. English UI and existing Button/theme tokens.
- `components/automation/TriggerEditor.tsx`, `fields.tsx`: all Trigger configuration,
  Event OR multi-select, optional recursive AND/OR/NOT State comparisons, direct
  Action selection and schema, typed JSON literals and Event paths, source metadata,
  serial-only execution, disabled creation. No scripts or fake State fields.
  Enable has a separate review of the Action and complete arguments. Dependencies,
  missing/disabled Actions, and unavailable Event definitions remain visible.
- `stores/automationStore.ts`, `types/automation.ts`: memory-only key exchange/token,
  12-hour expiry, logout/revocation handling, Bearer on automation management writes,
  no credentials in storage/URL/logs. Disabled drafts work before login. A 401 clears
  the matching token and keeps the draft; a stale 401 cannot clear a newer login.
  Independent Trigger/Run revisions are sent. 409 retains local fields, refreshes
  server truth and offers explicit comparison/reload/rebase. No automatic overwrite.
- Cached snapshots render immediately, concurrent reads deduplicate, mutation invalidates
  and refreshes, obsolete reads cannot replace newer data. Section changes retain
  the editor; unmounted editors ignore delayed mutation responses. No polling.
- `components/automation/RunList.tsx`: manual Refresh Runs with fetch time, status/filter,
  linked Run/Event/Trigger/Action identities, frozen arguments/State, actual Action
  snapshot/digest, fixed targets, per-target outcomes, aggregate result, errors and
  uncertain warning. Only queued cancellation is offered; no retry/resend effects.
- `components/automation/PauseTemplate.tsx`: ordinary `session_operation` Action creation
  through existing Actions API. Hand-picked Sessions or an Event field target;
  query selection explicitly unimplemented and required follow-up. Creation alone
  executes no effect. Template Action is enabled, new Trigger remains disabled.
- `types/actions.ts`, small `views/ActionsView.tsx` compatibility change: existing Action
  editing safely displays Session Operation selectors and preserves them on save.
  Existing API/command/Group behavior remains covered by nine prior Actions tests.
- `e2e/automation.server.py`, `e2e/automation.e2e.mjs`: isolated real FastAPI + production
  Chromium harness, dynamic port, temporary data, deterministic fake CLI only.

## Validation

All commands run in `packages/web` unless otherwise stated. No full test suite run.

1. `pnpm install --frozen-lockfile --ignore-scripts --offline`: exit 0, isolated local
   dependency tree. No shared node_modules junction or install into another checkout.
2. `pnpm exec vitest run src/views/AutomationView.test.tsx src/views/ActionsView.test.tsx src/components/layout/ViewEntryGroups.test.tsx --reporter=default --reporter=junit --outputFile.junit=../test-results/automation-vitest.xml`:
   **23 passed**, 0 skipped, exit 0. JUnit: `vitest.xml`; log: `vitest.log`.
   Includes disabled serial drafts, Event OR/mappings, AND/OR/NOT, explicit enable
   review, 401/409 draft preservation, opt-in rebase, strict payload metadata filtering,
   late-save editor isolation, stale-read invalidation, dedup, Event target template,
   late-401/new-login isolation, existing Actions/Groups and navigation badge behavior.
3. `pnpm exec tsc -b`: exit 0 (also required by build).
4. Directed ESLint on all changed/new TS/TSX and the browser harness: exit 0,
   zero warnings. `lint.log` is empty on success.
5. `pnpm build`: exit 0; production assets and **76** compressed variants verified.
   Existing router dynamic/static import and large vendor chunk warnings remain;
   no suppression/config changes. Log: `build.log`.
6. `node e2e/automation.e2e.mjs`: exit 0, **12 scenarios passed** in real Chromium.
   No frontend mocks or replaced business routes. Evidence: `browser-results.json`,
   `browser.log`, `http-evidence.json`, `desktop-runs.png`, `editor-{1440,390,320}.png`
   and `editor-bottom-{1440,390,320}.png`.
7. Root: deployment interpreter `-m py_compile packages/web/e2e/automation.server.py`
   and `git diff --check`: exit 0.

Browser coverage: production template explicit/event creation; disabled draft and
boolean State editing; disabled negative control; memory login + explicit enable;
real fake-CLI running→idle Event→Run→existing Pause Everything; persisted target
and control states; queued work remains after unlocking under Pause; queued cancel;
recovered uncertain fixture; real partial target failure plus literal/Event argument
resolution; real concurrent HTTP 409; 1440/390/320px geometry; existing Actions
compatibility; refresh loses auth; disabled delete retains history; real revoked
Bearer 401 preserves draft. No browser page errors. No real provider calls.

Queued/unavailable and interrupted-running records are seeded test fixtures. Their
display/recovery is verified; this frontend phase does not repeat the core's full
crash/race acceptance. The successful and partial-failure Runs come from actual
fake-CLI Worker status transitions through the production core observer and executor.

## Isolation and failed experiments

Before every HTTP/browser management write, the harness verifies the owned identity
endpoint: checkout, runtime, Session directory, port, fake CLI, and PID ancestry.
Windows venv launcher and server child PIDs are distinguished. The protected
8767/8768 ports are never requested. Shutdown validates the same identity, kills
only the owned process tree and confirms port release. Exit code 1 for the server
is the expected taskkill termination, separate from the browser harness exit 0.
The final JSON records cleanup and `checkoutDataUnchanged`.

Early failed runs exposed a test label lookup, the venv PID distinction, a test-only
Session projection field mismatch and a duplicate main landmark selector. Product
accessibility/landmark fixes and harness fixes are included. Initial failure JUnit
and browser result are preserved here; detailed per-attempt logs/runtime remain under
`packages/web/test-results/automation-*/`. Development build's initial test typing
errors were corrected; final TypeScript/build pass.

The generic browser fixture also wrote startup recovery records and a default QQ
manifest into **this disposable checkout's** ignored `data/`, never user data or
another checkout. The dedicated harness now redirects these paths, including the
main lifecycle Job registry, into its runtime and hashes checkout data before/after
to detect escape. Earlier auxiliary files are retained, not deleted as if they
were known user data. There is no active test service left at handoff.

## MA / user acceptance steps

1. In this isolated checkout, build with `pnpm build`. Run
   `node e2e/automation.e2e.mjs` for the reproducible automated production-browser
   acceptance. Set `PAN_E2E_PYTHON` only if the deployment interpreter differs;
   set `PAN_AUTOMATION_E2E_OUT` to direct evidence into another owned directory.
2. For manual review, launch `e2e/automation.server.py` with a new owned runtime
   **inside `packages/web/test-results`**, an unused non-8767/8768 `PAN_PORT`, and
   `PAN_E2E_FAKE_CLI=<this checkout>/tests/support/frontend_stream_cli.py`.
   Use a clean child environment without inherited PAN settings. Verify
   `/__e2e/identity` matches PID/ancestry, checkout, roots and fake CLI before writing.
   Open `http://127.0.0.1:<owned port>/react/automation` in production Chromium.
3. Before login, create a Trigger named Idle pause, select `workers.all_idle`,
   optionally State `workers.runningCount eq 0`; save disabled. Inspect that it
   stays disabled and enable/delete are unavailable before login.
4. Open Pause Everything template, select **Selected target**, create Action.
   Choose it in the Trigger. Check explicit IDs in its Action schema/details.
   An Event target template may instead reference `payload.sessionIds`; the
   built-in all-idle Event contains only runningCount, so a suitable trusted
   Event definition/payload is required to exercise Event Session targets.
5. Read only this runtime's `automation/user.key` locally. Enter it in User
   authorization. Review and enable the Trigger; inspect the full Action and
   arguments before Confirm enable. Reload and confirm login is required again.
6. Send a short message to **Event producer** through the existing Chat composer
   (the harness resolves its CLI to the deterministic local fixture). After it
   finishes, open Runs and click Refresh Runs. Inspect succeeded status, Event
   and Trigger IDs/revision, actual Action digest, fixed selected target and
   target result. Confirm Selected target's existing Pause Everything state;
   Control target must retain its earlier state. Default-off control must be
   exercised before enabling. Do not use the MA's controlling Session as target.
7. Unlock the seeded Selected target queue row while Pause remains active;
   confirm it stays queued. Use a separate unpaused control Session to check
   ordinary delivery still works. This controlled fake-CLI evidence does not
   replace final human acceptance in the intended integrated product environment.
8. Compare two editor/browser configurations: save one, save the stale revision
   in the other. Expect 409, preserved draft, server comparison and explicit
   reload/rebase. Revoke the current token, attempt an enabled edit and expect
   401 + draft preservation. Log in again, disable, then delete; Run history stays.
9. Inspect unavailable fixture dependencies, queued cancellation and recovered
   uncertain Run. Observe no retry or resend control. Inspect a partial failure
   with a missing Session and retain the successful target result separately.
10. Check Triggers, editor, template and Run details at desktop/390px/320px.
    Confirm both bottom controls and long identifiers are reachable. End only
    the owned fixture and verify its listener/process tree is gone.

## Unverified / deferred boundaries

- Integrated main/practical, MA acceptance, deployment, external plugin transport,
  real provider consumption and overall human Pause/queue acceptance are pending.
- Event Session target creation and Event argument resolution are covered; successful
  Event selector target execution with an external plugin payload is not a browser
  scenario in this phase. Core selector tests are not re-claimed as frontend evidence.
- Actual 12-hour wall-clock expiry was not waited out; timer/401 behavior and real
  revocation are covered. Token validity remains server-authoritative.
- Query Session selector, additional State fields, parallelism, retention/paging,
  large-ledger load, durable Event replay and effect reconciliation remain follow-ups.
- No main/practical movement, push, release, existing service operations or child
  delegation. Stop after this candidate handoff and wait for MA.
