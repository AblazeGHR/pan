# TA delivery evidence — source-aware auto report

Branch: `feature/source-aware-auto-report-20261019`.
Base: `ba51b72ec95a08e9c64fd289f189d46746033000`.
Implementation contract: [docs/source-aware-auto-report.md](../../docs/source-aware-auto-report.md).

This is an isolated TA implementation delivery, not MA acceptance. Excluded
from the current 0.3.4 release; no main/practical integration, fetch/rebase/merge,
push/tag/release or production service restart was performed.

## Verification

| Layer | Result | Evidence |
|---|---|---|
| Python directed regression | 363 passed, 0 failed, 0 skipped | `targeted-regression.xml` |
| New source contract and real chain batch | 46 passed | `source-chain.xml` |
| Final real HTTP/WS + built-app Chromium | 1 passed | `real-chain.xml`, `real-chain.json`, `auto-report-settings.png` |
| React/jsdom directed tests | 94 passed, 4 files | `frontend.xml` |
| TypeScript | `tsc -b` exit 0 | Also included in `pnpm build` |
| Production build | exit 0, 72 compressed assets verified | `build.log` |

Python regression selection:

```text
tests/test_source_aware_auto_report.py
tests/test_settings_ui.py
tests/test_report_subscription.py
tests/test_task_id_inheritance.py
tests/test_force_steer.py
tests/test_manual_task_report.py
tests/test_session_queue_api.py
tests/test_queue_migration.py
tests/test_queue_restart_hardening.py
tests/test_self_queue.py
tests/test_steer_history_consistency.py
tests/test_codex_native_admission.py
tests/test_plugin_platform.py
packages/qq/test_qq_api.py
```

Commands use `D:/project/Pan-main/.venv/Scripts/python.exe
scripts/run_tests_isolated.py -- <python> -m pytest <selection> -q -o addopts=
--junitxml=<evidence>`. All suites exited 0. Three existing dependency warnings
remain (Pydantic forward-reference and Starlette/AnyIO deprecations). Build
reports existing chunk-size and mixed router import warnings.

Frontend selection: `AutoReportSettingsGroup.test.tsx`,
`AppSettingsModal.test.tsx`, `appSettingsStore.test.ts`, `messageFilter.test.ts`.
Full local CI was not run.

The real test uses an owned dynamic non-protected port, isolated persistent
data, ordinary stream fakeCLI stdin/stdout and real HTTP/WebSocket connections.
The JSON records final port, server PID/create-time/command identities,
fakeCLI cleanup identities and released-port result. It does not call remote
models or touch 8767/8768. Browser checks cover Behavior defaults, an optional
default omitted by a dynamically registered plugin, explicit enable, persistence
after reload, reset to false, QQ's declared true default, and no page errors.
The screenshot was visually reviewed.

The service test proves a MA execution retains its original enabled decision
after settings change and a later user queue item arrives; that user execution
does not report. A manager's real Report-triggered answer produces terminal
events without another automatic report, even with every Core switch enabled.
All three explicit upstream send entry points retain Report provenance. A
registered new plugin sends a real task whose enabled answer reports normally.

Shared canonical node_modules lacked xterm. Validation used the complete
lock-matching dependency cache `D:/project/pan-dependency-cache/249D2FC3A1A0FD51`
through the worktree junction; no dependency installation or package edits.
Build output is ignored and remains in this worktree only.

## Limits and review boundary

No real provider model, real QQ bridge/network, mobile browser, OS notification,
full local CI or remote CI verification. Native force receipt/unknown/late
acceptance behavior is covered by directed in-process tests; the real fakeCLI
exercise uses the capability-absent route. Restart recovery is verified with
actual durable reservation, disk reload and queue migration; the new browser
test does not crash/restart the whole Pan service. Main/practical integration
and developer/MA acceptance remain pending until the current release is
actually complete.

The initial native reservation rollback regression was repaired in source;
the existing full-object assertion remains intact. Test harness cleanup was
corrected to `/worker/kill` and protected with nested finally blocks; the
temporary failed-run processes were identity-checked and cleaned, then the
complete real chain was rerun successfully.
