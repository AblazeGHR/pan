# New-main history terminal candidate

This report supersedes the baseline/SHA/validation status in `../history-terminal/REPORT.md`. That directory preserves the earlier implementation and real-browser evidence; its counts are not current-run counts.

- New baseline: `04b1d3a30305631b99d24df25ffb443fa21738bb`.
- Worktree/branch: `D:/project/pan-worktrees/history-terminal-events-20261027`, `feature/history-terminal-events-20261027`.
- Rebase completed without conflicts: prior history implementation `23740cf0` became `48945a59`; prior evidence commit became `a31698a5`.
- Current source/test commit: `bf189d08` (`Test history terminal and source notification coexistence`). Final full SHA is recorded in the task delivery. The final evidence commit changes only reports/logs/XML.
- Working tree was clean before rebase; no dirty work was discarded. No other candidate was merged, no protected refs advanced, and no push/release/service operation was performed.

## Notification compatibility

The new-main notification implementation is retained. QQ completion outbox preparation remains `status == done`; error admission remains the existing `session_error_log` FIFO executor to `source_notifications`. `notification.source` remains the separate frontend handler. Relative to new main, there are no changes to `source_notifications.py`, `session_error_log.py`, `notifications.py`, or `SourceNotificationPanel.tsx`. Notification UI/routing semantics were not edited.

Original history behavior is retained: done/error rows in canonical history, stable event ID/terminalKey/order/execution identity and fixed backend anchor, independent React terminal rendering, missing metadata compatibility and known terminal preservation during reimport. Full original contract/path list is in the previous report.

New tests only:

- `tests/test_history_terminal_notifications.py`: actual stream parser, terminal commit, error-log FIFO and SQLite notification ledger; Session-only and Session+global union; original assistant body/error anchor, manager error report and native code, exactly one browser/QQ attempt per route, no error completion outbox, normal next done outbox/report, cold history and notification reopen, and replay of old error after done without another event/report/notification. External senders are captured local test functions; no real QQ/OS notification is sent.
- `packages/web/src/hooks/useWebSocket.test.tsx`: duplicate `notification.source` plus durable error worker.result triggers one authenticated-content lookup and one tagged browser notification; canonical error event retains its original body, no temporary ERROR text marker appears. This test changes no production hook code.

## Current-run checks

All commands below exited 0, against the source bytes committed as `bf189d08`. Python test subprocess uses the isolated runner and canonical Python, with pytest temporary stores. No service instance was launched in this follow-up, respecting the new instruction not to operate services.

```powershell
# cwd repository root
D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest tests/test_history_terminal_notifications.py tests/test_source_notifications.py tests/test_history_terminal_events.py tests/test_terminal_report_diagnostic.py tests/test_report_error_context.py tests/test_codex_reimport_origins.py tests/test_reimport_confirmation.py tests/test_manual_task_report.py tests/test_worker_history.py tests/test_delivery_semantics.py tests/test_session_unread_ack.py tests/test_legal_offline.py tests/test_backend_perf_opt.py tests/test_codex_worker_integration.py tests/test_codex_native_completion.py tests/test_worker_oneshot_usage.py tests/test_notifications_reminders.py tests/test_notification_closed_browser.py -q --junitxml=artifacts/history-terminal-rebase/python.xml

# cwd packages/web
pnpm exec vitest run --cache=false src/hooks/useWebSocket.test.tsx src/stores/sessionStore.historyTerminal.test.ts src/components/chat/HistoryTerminal.test.tsx src/components/chat/manualTaskReport.test.ts src/stores/sessionStore.terminalTransient.test.ts src/stores/sessionStore.interleaving.test.ts src/stores/reaudit.history.test.ts src/components/layout/SourceNotificationPanel.test.tsx src/stores/appSettingsStore.test.ts --reporter=default --reporter=junit --outputFile=../../../artifacts/history-terminal-rebase/react.xml
pnpm exec tsc -b --pretty false
pnpm build
```

| Layer | Current result | Evidence |
| --- | --- | --- |
| Python history/report/notification regressions | 375 collected, 369 passed, 6 existing retired-oneshot skips, 0 errors/failures; exit 0 | [JUnit](python.xml), [log](python.log) |
| React history/transport/settings | 247 passed, no skips/errors/failures; exit 0 | [JUnit](react.xml), [log](react.log) |
| Explicit TypeScript and normal pre-commit hook | Passed, exit 0 | [tsc log](tsc.log), commit hook task output |
| Production build | Passed, 90 compressed assets verified, exit 0 | [build log](build.log) |
| New-baseline real HTTP/WS/browser/service | Not rerun, per no-service instruction | Earlier-baseline only: [original browser evidence](../history-terminal/http/browser.json) |

No full suite, remote CI, real supplier/account, actual QQ/OS notification, real user data, Linux/macOS runtime, deployment or human acceptance was verified. Original browser evidence remains tied to its original source baseline and must not be treated as new-main live acceptance. MA may independently choose service/browser acceptance.

## Preserved red evidence

`coexist-first.xml`, `coexist-second.xml`, `coexist-third.xml` preserve test-fixture failures: the reused EOF fixture produced a distinct worker_crash incident in addition to terminal_error, correctly admitting a second notification. Merely changing the process return code did not remove the separate unexpected-EOF incident. The final fixture keeps its stream logically open and ends the test reader by explicit CancelledError after asserting all frames consumed, terminal latch set and Worker idle; it does not suppress product crash detection. [Focused final](coexist-final.xml) is 38 passed. No production fix, weakened assertion, skip or notification behavior change was used to resolve these fixture failures.

Old red evidence and the earlier baseline's browser screenshots remain under `../history-terminal/`. MA's notification candidate acceptance counts supplied in the task are external evidence, not included in this run's counts.

## Protected refs and delivery

Read at start and completion: main/practical both `04b1d3a30305631b99d24df25ffb443fa21738bb`. Only this task branch was rebased/committed. Final candidate descends from the new baseline and is intended for independent fast-forward acceptance by MA. Existing dependency junction continues to point to this tree's owned ignored frozen-lockfile installation; canonical dependencies were not written.
