# Source-aware automatic report integration candidate — 2026-10-21

This is a candidate for independent MA acceptance, not acceptance or a mainline merge. No push, tag, release, production restart, or operation on existing services/ports 8767 and 8768 was performed. Tests used isolated temporary data and owned dynamic ports. Some unit fixtures contain protected-port numbers as inert configuration; they do not bind them.

## History and workspace

- Candidate branch: `integration/source-aware-auto-report-20261021`.
- Worktree: `D:/project/pan-worktrees/source-aware-auto-report-integration-20261021`.
- Initial main/practical baseline: `3f601e9c66125d7a0313e4b679958bdc49eac935`.
- Latest incorporated main/practical: `b6502ea940b3d852b7e6824f6c2d4e66fe7666fc` (ancestry check exit 0).
- Original feature tip: `840c11b13e7258d1d005deb489cd9a4eb6858ad6`, including implementation `1fcffac608c4703d19c0dc72645d7ea58f4ac487`; full history is an ancestor (exit 0), not cherry-picked. Original worktree remains clean at its original tip. Historical evidence remains unchanged in the adjacent `source-aware-auto-report-20261019` directory.
- Feature merge/adaptation: `c9b8f0d0`; latest main merge: `8d7d9e31db7ab125416cd39d442d0844ed44f5a2`.
- Product source tested: `98b57b5fcc4003a194b1c44c6a5aa5314c5fb522`. The subsequent evidence-only commit is the deliverable tip; obtain its exact SHA using `git rev-parse HEAD`.
- Existing main/practical dirty files and shared dependencies were preserved. Validation preparation ran first. The worktree's owned node_modules layer uses read-only package junctions to the complete canonical cache; Vite's writable temporary directory stays in the owned layer. No install or shared-cache modification was needed.

## Source and execution semantics

Trusted sending Session plus a validated managedBy ancestor chain classifies assign/send/sendforce (and their HTTP/MCP paths). Sending to an ancestor is Report; sibling/unrelated/no-management targets remain MA. Invalid/cyclic chains fail classification safely. Text prefixes and role=user do not establish identity. Permissions, formal assignment identity/idempotency and force capability routing remain unchanged.

Core automatic answer defaults are MA=true, System=false, real user=false. Message-capable plugins declare optional defaults; omission means false. Explicit per-plugin user overrides win; reset removes overrides and returns to each current declaration. QQ formally declares true. Registration alone does not grant message-source capability. There is no plugin master switch masking independent defaults. English dynamic UI, backend effective values, persistence and reset agree.

The decision freezes at actual execution admission/reservation, with persisted provenance across restart, offline/native execution and queue/history records. Mixed inputs combine admitted eligible sources; automatic Report feedback alone cannot trigger another automatic answer report. Late accepted Steer receipts can settle the original durable transaction but cannot alter a different Worker, generation or execution. Failed persistence restores item/ledger state without publishing a new Worker decision. Registry discovery uses cancellation-safe off-loop IO while Action reservations remain held.

Any task envelope with source=report is private in own-queue reads, including missing explicitReport metadata. Explicit upward messages preserve text/parts/task IDs, Stop reports and queue behavior. report_consume handles these as single task units, never a bodyless result batch; malformed mixed restored units are rejected. Existing result-report batching, one-control admission, original IDs and unknown-no-retry remain intact. Manual sends/reports, infrastructure errors, subscriptions, ordinary output/logs and terminal state are independent of automatic answer policy. Illegal/Wake locks, Action Session/Group gates and all three Auto Force controls remain authoritative and independent.

## Reproducible validation

Backend prefix (the wrapper clears inherited PAN variables and supplies independent ports):

```powershell
D:/project/Pan-main/.venv/Scripts/python.exe scripts/run_tests_isolated.py -- D:/project/Pan-main/.venv/Scripts/python.exe -m pytest <selection> -q -rs -o addopts= --tb=short --junitxml=<evidence-file>
```

| Selection / command | Result | Evidence |
| --- | --- | --- |
| Core selection below | Exit 0; 295 passed, 22 existing dependency deprecation warnings; 145.18s | core.xml, core-output.txt, core-exit.txt |
| Compatibility selection below | Exit 0; 309 passed, 3 pre-existing skips, 3 dependency warnings; 37.76s | compat.xml, compat-output.txt, compat-exit.txt |
| `tests/test_source_aware_auto_report_real.py tests/test_source_auto_report_native_http.py` | Exit 0; 2 passed; 9.51s; one JUnit record_property format warning | real.xml, real-output.txt, real-exit.txt, real-chain.json, auto-report-settings.png |
| `pnpm --dir packages/web exec vitest run --no-cache src/components/chat/messageFilter.test.ts src/stores/appSettingsStore.test.ts src/components/layout/AutoReportSettingsGroup.test.tsx src/components/layout/AppSettingsModal.test.tsx --reporter=junit --outputFile=../../evidence/source-aware-main-integration-20261021/frontend.xml` | Exit 0; 107 passed; 8.56s. Vitest resolved output beneath packages/evidence; the owned XML was moved into this evidence directory. | frontend.xml, frontend-output.txt, frontend-exit.txt |
| `pnpm --dir packages/web build` | Exit 0; includes tsc -b; production build and 74 compressed assets verified. Existing router/chunk-size warnings. | build.txt, build-exit.txt |

Core selection:

```text
tests/test_source_aware_auto_report.py tests/test_source_auto_report_main_integration.py tests/test_report_reads.py tests/test_report_action_coexistence.py tests/test_auto_force_sources.py tests/test_illegal_queue_wake.py tests/test_sticky_wake_integration.py tests/test_codex_native_admission.py tests/test_action_groups.py tests/test_action_ids.py tests/test_mcp_remote_parity.py tests/test_mcp_remote_runtime.py
```

Compatibility selection:

```text
tests/test_settings_ui.py tests/test_report_subscription.py tests/test_report_error_context.py tests/test_task_id_inheritance.py tests/test_force_steer.py tests/test_manual_task_report.py tests/test_session_queue_api.py tests/test_queue_migration.py tests/test_queue_restart_hardening.py tests/test_self_queue.py tests/test_steer_history_consistency.py tests/test_plugin_platform.py packages/qq/test_qq_api.py
```

The three unchanged skips are test_report_error_context.py:273, :295 and test_task_id_inheritance.py:144: Oneshot mode retired by user, implementation retained. No skip or weaker assertion was introduced. The earlier final-boundaries.xml is a 130-test subset, not additional unique coverage. Totals above are 606 backend passes plus 3 existing skips, and 107 frontend passes.

Real validation used built production assets in Playwright Chromium, real owned HTTP/WS sockets and deterministic fakeCLI without remote model calls. It checks dynamic undeclared-plugin default/off-enable-reset, QQ declaration, settings reload, captured-decision stability, upstream three entries, plugin answers, report feedback suppression, explicit report consume, native single-control original-ID behavior and formal assign exclusion/idempotency. real-chain.json records browser pageErrors=[], WS events, owned process identities, cleanup and released port. Native-chain owned port/PID identities are also recorded as JUnit properties. The synthetic remote HTTP test creates an owned identity artifact; no credentials are included.

## Limits

No full local CI, paid provider, real QQ network, real native provider CLI, mobile browser or production deployment was exercised. Unknown/rejected receipts, late acceptance, cancellation during registry IO, write-failure rollback, restart/migration and Action concurrency are covered by deterministic directed tests; the new real socket chain does not simulate every OS crash or network partition. MA still needs independent review/acceptance and performs any authorized fast-forward itself.
