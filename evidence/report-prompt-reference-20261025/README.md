# Report prompt reference — candidate 2026-10-25

Candidate for MA acceptance. No push, tag, release, deployment or restart of any
existing service was performed. Ports 8767/8768 were never bound; the real chain
used an owned dynamic port (50952 in the recorded run) from
`scripts/run_tests_isolated.py`, a temporary data root, the deterministic
fakeCLI and a temporary Chromium profile. The real Pan profile, real providers
and user processes were not touched.

## History and workspace

- Branch: `feature/report-prompt-reference-20261025`.
- Worktree: `D:/project/pan-worktrees/report-prompt-reference-20261025`.
- Fixed baseline: `be46c1448a0a0e157d21fc0a9b0722c3411e6ec1` (main/practical unchanged; not moved).
- Product/test/docs commit: `55fb6c0cc1d2d8691dbd81db0e37d63b2c0df24f`.
- This evidence-only commit is the deliverable tip; confirm it with `git rev-parse HEAD`.

## Behavior delivered

- Automatic answer reports, terminal records (`terminal_results`), `lastResult`
  and manual explicit reports carry `promptMessageId` and `promptSummary`. Both
  keys are always present; both are `null` when the input identity is unproven.
- `promptMessageId` is the canonical Pan history `messageId` that
  `session.append_history` assigned to the input row at the successful provider
  hand-off (or accepted Steer). It is never the queue transaction id
  (`queueItemId`/`id`), never a client id, and never a synthesized id.
- The reference is frozen at actual admission and copied into the terminal
  record before terminal awaits. Queued-but-unexecuted rows, late HTTP
  enqueues, later history appends and provider frames cannot retarget it.
- Merged report/channel batches are one provider-visible message: members share
  the row's `messageId`, and the last admitted member in actual order is the
  single reference that supplies the summary.
- An accepted Steer replaces this turn's reference with the Steer input row.
  Rejected/unknown receipts and late receipts for another Worker,
  generation/execution leave it unchanged.
- `ui.autoReport.summary` is a second, independent policy with the same four
  origins (system/ma/user/plugin). All switches default to false; a plugin
  override never inherits `message_source.auto_report_default`.
  `promptSummary` is the verbatim first 100 Unicode characters of the same
  input body (no model call, no concatenation). Off ⇒ `null`, and the input
  body is not attached to the report.
- Behavior renders a separate "Prompt summary" section with per-core-source and
  per-plugin switches and its own reset, leaving the answer-report switches and
  reset independent. `GET /api/plugins/message-sources` adds
  `effectiveSummaryEnabled` (override or false); `PUT /api/settings/ui`
  validates `summary` strictly and keeps omitted members at the all-false
  defaults.
- Null/no-guess cases: native-TUI admission, preflight failure before hand-off,
  a zombie report for a run that never handed off, legacy terminal/report rows
  and legacy `autoReportAdmission` decisions. Old rows are read as `null` and
  are never rewritten or backfilled.
- Report dedupe, unknown-no-retry, original queue ids, owner/generation checks
  and terminal diagnostics for error/done/cancelled are unchanged.

Contract details are in `docs/source-aware-auto-report.md` ("Prompt reference on
reports") and `docs/skills/pan/references/http-api.md`.

## Reproducible validation

Prefix used for backend/real runs (independent ports/data; inherited `PAN_*`
removed by the wrapper):

```powershell
E:/software/miniforge/python.exe scripts/run_tests_isolated.py -- E:/software/miniforge/python.exe -m pytest <selection> -q -o addopts= --tb=short --junitxml=<file>
```

| Command / selection | Result | Evidence |
| --- | --- | --- |
| Focused backend selection: `tests/test_report_prompt_reference.py tests/test_source_aware_auto_report.py tests/test_source_auto_report_main_integration.py tests/test_report_subscription.py tests/test_manual_task_report.py tests/test_report_error_context.py tests/test_force_steer.py tests/test_settings_ui.py tests/test_steer_history_consistency.py tests/test_queue_migration.py tests/test_session_queue_api.py tests/test_background_system_notice.py tests/test_auto_force_sources.py packages/qq/test_qq_api.py` | Exit 0; 331 passed, 2 pre-existing skips (`test_report_error_context.py:273`, `:295`, Oneshot retired by user) | `backend-regression.xml`, `backend-regression.txt` |
| Extra related: `tests/test_report_prompt_reference.py tests/test_delivery_semantics.py tests/test_task_id_inheritance.py tests/test_plugin_platform.py tests/test_source_aware_auto_report_real.py` (includes the pre-existing real source-policy chain) | Exit 0; 50 passed, 1 pre-existing skip (`test_task_id_inheritance.py`, Oneshot) | `backend-related-extra.txt` |
| Real chain: `tests/test_report_prompt_reference_real.py` | Exit 0; 1 passed in 8.54s | `real-chain.xml`, `real-chain.txt`, `real-chain.json`, `prompt-summary-settings.png` |
| Frontend: `pnpm exec vitest run --no-cache src/components/layout/AutoReportSettingsGroup.test.tsx src/stores/appSettingsStore.test.ts src/components/layout/AppSettingsModal.test.tsx --reporter=junit` | Exit 0; 93 tests, 0 failures | `frontend.xml`, `frontend.txt` |
| `pnpm build` in `packages/web` | Exit 0; `tsc -b` clean, 92 compressed production assets (pre-existing chunk-size warning) | `build.txt` |
| Red evidence: same directed file against the fixed baseline (product files stashed) | Exit 1; 13 failed | `red-baseline.txt` |

The directed file covers: four origins default-off and independently enabled,
plugin summary never inheriting the declaration, strict HTTP validation, an
exact 100-Unicode-character Chinese/emoji excerpt, user/agent/notification/
plugin hand-offs, cold-load retention plus legacy null compatibility, merged
batch last-input rule, a newcomer enqueued before hand-off, accepted vs
rejected/unknown/late Steer, native-TUI null, manual-report reuse of the
terminal evidence, and "summary off attaches no input body".

## Real chain and browser evidence

`real-chain.json` records: owned dynamic port 50952, the three owned Sessions,
owned Pan/conhost PID identities, `pageErrors: []`, and fakeCLI PID identities
cleaned to exit. It proves:

- the child's frozen `promptMessageId` equals the canonical history row of its
  admitted input and `promptSummary` equals that body's first 100 characters;
- the subscribed manager received the report with the same two fields;
- after real fakeCLI consumption, the manager's own terminal references its
  report-batch row and carries `promptSummary: null` (Report origin never adds a
  summary), and no report echoed further up.

The production browser step (temporary Chromium profile, built assets) verifies
all Prompt-summary switches are unchecked by default while QQ/Prompt plugin
answer-report switches are checked (no inheritance), toggle + persisted reload,
and that each reset leaves the other policy intact. `prompt-summary-settings.png`
is the captured Behavior page after reset.

## Process and environment cleanup

The owned uvicorn/fakeCLI/conhost tree was stopped by the existing
identity-bound fixture; `ownedPortReleased: true`, and the recorded fakeCLI PIDs
are gone. No port 8767/8768 listener, real data root, real provider, paid model
call, or user process was used. `packages/web/node_modules` was installed from
the warm local pnpm store in this worktree; `packages/web/dist` is the ignored
build output. The Playwright Chromium revision required by the installed Python
package was downloaded to the user browser cache.

## Limits

No full local CI, paid provider, real native provider TUI, real QQ network,
mobile browser or deployment was exercised. Crash windows between hand-off and
terminal persistence are represented by null/no-guess behavior (a replacement
Worker has no durable prompt reference and must not invent one). MA performs
independent review/acceptance and any authorized fast-forward.
