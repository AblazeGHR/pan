# Shared error notification exclusions candidate

Base: `5c39815bd43048140e37b164f4324fecbfa9d4d9`.
Branch: `feature/notification-locker-error-20261027`.
Worktree: `D:/project/pan-worktrees/notification-locker-error-20261027`.
The candidate commit contains this report; source blob identities are in
`source-manifest.json`. MA independently reviews/integrates. No push, merge,
deployment, protected-port operation or existing-service operation was performed.
Read-only ref observation during this task: main/practical both
`0025c9d67c6ef29bf034e10ae1443a052bc62d64`; these were advanced externally.

## Result and interface

- One persisted object: `ui.errorNotificationExclusions = {enabled, types}`.
  Both entrances use the same store action, settings endpoint and React component.
  Disabling exclusions retains selections. Writes are pessimistic; failed saves
  leave committed values unchanged. Successful saves broadcast `settings.uiUpdated`.
- Authenticated `GET /api/notifications/error-types` supplies 25 exact source/code
  identities. Vocabulary reuses the existing structured runtime code sets, plus
  existing Codex native variants `usageLimitExceeded`, `serverOverloaded` and
  `responseStreamDisconnected`. It is not the six coarse admission kinds and is
  not an exhaustive catalog of all possible provider errors.
- Matching reads `cli.errorSource` and `cli.errorCode`, `cli.error.code` or
  `cli.error.codexErrorInfo`; one-key native enum objects use only their stable
  tag. It never classifies prose or payload/status text. Unknown/missing types
  forward by default. Invalid legacy data defaults to disabled/empty. Strict
  writes reject arbitrary unknown types, malformed fields and client epochs.
- The existing `PUT /api/settings/ui` path requires Automation UserAuth for this
  new field; other settings preserve their existing access behavior. The frontend
  uses the existing authenticated request helper on the same update path.
- Server-managed `ui.errorNotificationExclusionsEpoch` rotates only when the
  canonical object changes. Existing ledger gains additive `exclusion_epoch` with
  an empty legacy default. No second ledger, receiving Session or Subscribe was added.
- Admission applies one shared predicate before constructing either origin's
  routes. Excluded events retain identity with empty targets/body. Earlier events
  retain original targets/epoch: changing exclusions does not revoke or replay
  them. Existing channel/visibility checks still apply. Locker unread is unaffected.
- Shared UI supports search, selected counts, stable labels and a 160px scroll
  window. Error history/samples and manager reports are unchanged. Original done,
  Browser/System routing, QQ report-only and per-path dedup remain intact.

## Validation commands and evidence

All commands run from the worktree unless a frontend cwd is stated. Original
`artifacts/notification-locker-error/` evidence (125 backend / 218 frontend / HTTP)
is retained unchanged. No new skip/xfail or weakened assertion was added.

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_error_notification_exclusions.py tests/test_source_notifications.py tests/test_locker_messages.py tests/test_session_error_log.py tests/test_interaction_cards.py tests/test_notifications_reminders.py tests/test_notification_closed_browser.py tests/test_plugin_qq_integration.py -q --junitxml=artifacts/notification-error-exclusions/backend-frozen.xml

# cwd packages/web
pnpm exec vitest run src/components/layout/ErrorNotificationExclusions.test.tsx src/components/layout/SourceNotificationPanel.test.tsx src/components/session/PostboxModal.test.tsx src/components/layout/AppSettingsModal.test.tsx src/stores/appSettingsStore.test.ts src/hooks/useWebSocket.test.tsx src/stores/lockerUnreadStore.test.tsx --reporter=default --reporter=junit --outputFile=D:/project/pan-worktrees/notification-locker-error-20261027/artifacts/notification-error-exclusions/frontend.xml
pnpm exec tsc --noEmit
pnpm build

# cwd worktree
python scripts/run_tests_isolated.py -- python -m pytest tests/test_notification_http.py -q --junitxml=artifacts/notification-error-exclusions/http-frozen.xml
```

Final command exits/counts are recorded in `validation-summary.json` and the
corresponding JUnit/text files:

| Layer | Exit | Passed | Failed/errors | Skipped |
| --- | --- | --- | --- | --- |
| Frozen Python | 0 | 169 (125 original + 44 added) | 0 | 0 |
| React/Vitest | 0 | 222 (218 original + 4 added) | 0 | 0 |
| Authenticated HTTP/Chromium | 0 | 1 extended end-to-end case | 0 | 0 |
| TypeScript | 0 | typecheck | 0 | n/a |
| Production build | 0 | 90 compressed assets verified | 0 | n/a |

The added Python module covers every catalog
identity, disabled retained selections, malformed legacy/strict values,
unknown/missing/prose default forwarding, Session/global origin combinations,
Locker independence, durable no-replay, pre-admitted targets across exclusion
changes/cold reopen, additive schema migration and real error-sample retention.
Frontend adds four cases for shared reads/writes, 80-type search/scroll behavior,
failure state retention and legacy defaults. Existing source/route tests remain.

The HTTP test owns its temporary authenticated profile, random port and process
birth identities. It launches the built React app in Chromium, performs real
UserAuth login, tests anonymous rejection and schema/epoch validation, edits in
Behavior and reads in Notifications, verifies disabled retained selections and
real scroll dimensions, checks Browser event dedup and Session Subscribe/Report,
then closes Chromium and validates server-owned QQ/System forwarding. It checks
known exclusions, unknown forwarding, restart persistence and no history replay.
QQ, System and browser Notification providers are explicitly recording fakes.
CLI process spawn is forbidden. Cleanup and released port are asserted.

## Retained failures and limits

- `backend-red.xml`, `exclusions-focused-red.xml`: a new real-sample test omitted
  the existing required `error_text_source`; corrected the test input.
- `build-red.txt`: strict indexed access rejected optional destructured DOM nodes;
  changed test queries to scoped required nodes.
- `frontend-red.xml`: five existing modal error tests lacked the new catalog
  fixture response and saw an extra alert; added that response without changing assertions.
- `http-first.*`: Playwright `check()` demanded immediate checked state during a
  pessimistic save. Uses click plus an awaited checked assertion now.
- `http-second-red.*`: expected list order did not match canonical sorted storage;
  now submits and asserts the exact canonical list.
- `http-third-red.*`, `http/config-write-third-red.txt`: real isolated config
  replacement hit Windows `PermissionError [WinError 5]`; browser received 500
  and preserved the saved setting. The generic writer was not changed. This is a
  retained observed limitation; no automatic retry was added to product or tests.
- `http-fourth-red.*`: the probe picked the last browser ref from an unordered
  query, which could be an older correctly invalidated grant (404). The fixture
  now exposes identity/ref pairs; the test selects the exact current event.
- Existing build chunk-size and Python dependency warnings remain in logs.
- Not verified: real QQ contacts/provider delivery, actual OS toast display,
  human receipt, full repository test suite, remote CI or Linux/macOS runtime.
  No real CLI Worker, real profile/data, deployed service or main/practical
  advancement is part of this candidate. Small catalog additions require an
  explicit server update; unknown codes continue to forward in the meantime.
