# Notification TA candidate — 2026-10-27

- Implementation commit: `fdcbfdf30373b66867d97b07b4683aa86e087833`; branch `feature/notification-locker-error-20261027`.
- Base: `7f303faa355bfc763f0452ad68d31b920e4dc6e1`; initial worktree clean. No other in-flight edits were present in the shared files before this TA's changes.
- This report/evidence is a follow-up commit on the same branch. It changes no implementation files. MA owns independent acceptance and main/practical advancement; this TA did not merge or push.
- Source/API/schema contract: [notification-sources-v1.md](../../docs/design/notification-sources-v1.md). Exact reviewed files and SHA-256 hashes: [source-manifest.json](source-manifest.json).

## Delivered behavior

Behavior has **Session errors → msgBridge**, governing use of each Session's System, Browser and QQ Report connections. The default is on to retain existing QQ error reports. Notifications has independent **Locker new unread** and **New errors** sources, both off by default, each with Enabled, System, Browser, QQ Report and bot/contact selection. QQ on these new sources is report-only, with no hidden receiving Session and no Subscribe/inbox configuration. Existing Session Subscribe/Report, completion notifications, Browser permission controls and Locker desktop count-poll behavior remain available.

The error paths union their grants into one durable `(source,event identity,target)` attempt. Unit tests exercise all four Session/global on/off combinations. In the owned HTTP run, both System settings enabled for one error produced exactly **one** recording System call, and the matching QQ contact/bot received one call. Global-only notifications work with Behavior off. Separate QQ targets remain separate routes; legacy unqualified and explicitly bot-qualified QQ identities retain their existing distinct configuration semantics.

Locker sources are transactional new interaction cards, dedicated messages, and first answers to user cards. Read/unread toggles, polling, duplicate creates, reopening settings and restart do not generate new event identities. Error coverage is the existing structured `terminal_error`, `worker_crash`, `worker_start_failure`, `native_operation_error`, `transient_provider_error`, and `tool_failure` events across Sessions, including a structured product error without a Session. Cancelled events, arbitrary logging calls, historical text, HTTP validation rejections and the independent Job lifecycle are excluded. Different authoritative error event IDs remain separate events.

Bodies contain source/item/event/task identifiers and bounded text/summary, with existing credential redaction, a 1200-character limit and neutralized CQ markup. No raw provider payload, history snapshot, scratch diagnostics or attachment bytes are forwarded. Missing/readonly Session sources and nonlocal Locker recipients fail closed. Pending grants are checked again before a durable claim. Failure to admit/send a notification cannot reject the content/error sample.

The server owns QQ/System draining, independent of open pages. Browser wakeups contain only a hashed reference; the content GET requires existing UserAuth and rechecks grants/source visibility. Error QQ forwarding uses this shared ledger instead of a second terminal QQ outbox. Successful task reports keep the old outbox. `attempted`, `queued`, `unknown`, `failed`, `cancelled`, and provider-reported `sent` remain distinguishable. Unknown/queued/attempted effects never auto-resend. A crash after claim can lose a notification, preserving at-most-one external attempt. An in-flight claimed send cannot be recalled by subsequently turning a switch off.

## Final validation

All commands below exited **0** on the frozen implementation above. Test totals overlap; they must not be added together as unique coverage.

| Check | Result | Evidence |
|---|---:|---|
| Backend final subset | 125 passed, 0 failed, 0 skipped | [JUnit](backend-frozen.xml), [log](backend-frozen.log) |
| Frontend final subset | 218 passed, 0 failed, 0 skipped, 6 files | [JUnit](frontend-frozen.xml), [log](frontend-frozen.log) |
| Real isolated authenticated HTTP + production Chromium | 1 passed, 0 failed, 0 skipped | [JUnit](http-frozen.xml), [log](http-frozen.log), [browser](http/browser.json), [process/delivery](http/evidence.json) |
| TypeScript | `pnpm exec tsc --noEmit`, exit 0 | [log](tsc-frozen.log) |
| Production React build | `pnpm build`, exit 0; 90 precompressed assets verified | [log](build-frozen.log) |
| Normal source commit hook | React TypeScript hook passed | [commit/hook log](implementation-commit.log) |
| Diff whitespace | `git diff --check`, exit 0 | Rechecked before commit |

Backend command from repository root:

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_source_notifications.py tests/test_locker_messages.py tests/test_session_error_log.py tests/test_interaction_cards.py tests/test_notifications_reminders.py tests/test_notification_closed_browser.py tests/test_plugin_qq_integration.py -q --junitxml=artifacts/notification-locker-error/backend-frozen.xml
```

Frontend command from `packages/web`:

```text
pnpm exec vitest run src/components/layout/SourceNotificationPanel.test.tsx src/components/session/PostboxModal.test.tsx src/components/layout/AppSettingsModal.test.tsx src/stores/appSettingsStore.test.ts src/hooks/useWebSocket.test.tsx src/stores/lockerUnreadStore.test.tsx --reporter=default --reporter=junit --outputFile=D:/project/pan-worktrees/notification-locker-error-20261027/artifacts/notification-locker-error/frontend-frozen.xml
pnpm exec tsc --noEmit
pnpm build
```

HTTP/browser command from repository root:

```text
python scripts/run_tests_isolated.py -- python -m pytest tests/test_notification_http.py -q --junitxml=artifacts/notification-locker-error/http-frozen.xml
```

Chromium `153.0.8010.12` used the production bundle at 1280×1000. Real login, authenticated HTTP saves, reload persistence, source controls without Subscribe, Session Postbox QQ Subscribe/Report and old System/Browser controls, live authenticated Browser event fetch/construction, duplicate Browser count=1, closed-page QQ/System forwarding, config validation, disabled/reopened events and restart without replay passed. QQ and System providers were explicit recording fakes; Browser construction was also explicitly recorded. No CLI Worker was started; the fixture rejects provider spawning. Temporary root, non8767/8768 port, retained PID/birth identities, cleanup and port release are in `http/evidence.json`. [Screenshot](http/notifications-desktop.png).

An earlier broader subset (`test_source_notifications`, `test_session_error_log`, `test_notifications_reminders`, `test_notification_closed_browser`, `test_report_error_context`, `test_plugin_qq_integration`, `test_unread_recording_policy`) exited 0 with **184 passed and 2 existing skips**. It predates the final small refinements; it is supplemental regression evidence, not an extra final total. [JUnit](backend-expanded-1.xml), [log](backend-expanded-1.log). Both skips are existing retired oneshot cases: `test_oneshot_error_empty_result_report_carries_last_assistant_message` and `test_error_report_keeps_existing_error_result_and_adds_context`, reason `Oneshot mode retired by user; implementation retained`. No skip/xfail was added.

## Retained failures and fixes

- `backend-1.xml`: 12 failures from an accidentally duplicated admission block in the response path. The block was removed, preserving the response tests and assertions.
- `backend-2.xml`: 2 tests still asserting the prior internal grants shape / rejecting System before the user's scope clarification. Updated to the actual channel contract and added the four-case union matrix.
- `backend-3.xml`: 1 FIFO error-log ordering regression from parallel notification admission. Admission now shares the existing FIFO writer; final ordering regression passes.
- `frontend-1.xml`: 2 default-object mismatches. Added source defaults to the settings defaults instead of weakening the existing assertions.
- `http-1.xml`: the fixture used a missing Session ID without first validating creation success. `http-2.xml` identified the invalid spaced Session name; the fixture now uses a valid name and explicit adapter/model. `http-3.xml`: Playwright expected an asynchronous saved checkbox to change synchronously. The fixture uses a valid explicit Session and waits for the actual save. All failed JUnit/logs remain; `http-4/5` and `http-frozen` passed.

Known build warnings concern existing mixed dynamic/static imports and large chunks. Backend warnings concern the installed Starlette TestClient deprecation and Pydantic forward reference. No assertions, thresholds, source error propagation or checks were weakened to suppress them.

## Unverified / handoff boundaries

Real QQ gateway delivery, OS banner/notification-center display/click behavior, real CLI/provider error production, practical-data migration, Linux/macOS/remote CI, deployment, existing-service restart and human acceptance were not exercised. Source System forwarding reuses the existing platform sender and its explicit unsupported result; no platform implementation was added. Provider receipt `sent` does not prove human receipt or OS display.

Main remained `7f303faa355bfc763f0452ad68d31b920e4dc6e1` and practical remained `5b6165ca7d1b2751b5ddf736a9679bcefb24a068` at TA handoff checks. This task did not incorporate Job integration `1785f994`, change jobs/final_service/coordinator/scheduler code, advance protected refs, push, or operate a real service. MA must independently review the implementation and decide how to produce the common fast-forward candidate for main/practical under the user's authorization.
