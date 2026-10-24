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
- Product/test/docs commits: `55fb6c0cc1d2d8691dbd81db0e37d63b2c0df24f` (feature)
  and `dc53933291944ab29011b17754952bd43a1d2857` (MA-review batch-summary
  correction).
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
  the row's `messageId`. The summary is that same row's canonical content
  prefix, attached only when **every** contributor's frozen decision enabled
  summaries; any disabled/unknown/Report contributor makes it `null`.
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

## 2026-10-25 batch-summary correction (MA review)

MA review found that a merged batch reported the canonical row ID while taking
the summary from `items[-1]`, so the summary was not a prefix of the body that
ID resolves to, and one enabled source could preview a disabled source's body.

`dc539332` corrects this without changing batching, the single canonical ID, or
any hand-off/Steer gate:

- `promptSummary` is now exactly `history_row.content[:100]` for both single and
  merged inputs (task body, or the formatted notice/report batch text).
- A merged batch attaches that summary only when every contributing item's
  frozen `autoReportAdmission.summary` is explicitly `true`; any off/unknown/
  Report contributor yields `null`.
- Recovery (`_delivered_prompt_row`) reuses a history row only when that one row
  carries every member's delivery key; a partial-key row yields no reference.

| Command / selection | Result | Evidence |
| --- | --- | --- |
| Directed file against the pre-fix candidate (`packages/core/worker.py` stashed) | Exit 1; 6 failed (mixed batch, all-on prefix, formatted notice, recovery proof, automation hand-off, batch race) | `red-batch-summary.txt` |
| Directed file after the fix | Exit 0; 18 passed | `green-batch-summary.txt` |
| `tests/test_report_prompt_reference.py tests/test_source_aware_auto_report.py tests/test_delivery_semantics.py tests/test_steer_history_consistency.py tests/test_manual_task_report.py tests/test_report_subscription.py` | Exit 0; 189 passed | `batch-fix-regression.xml`, `batch-fix-regression.txt` |
| Real chain re-run | Exit 0; 1 passed in 8.22s; owned port 61286, `pageErrors: []`, port released | `real-chain-batch-fix.xml`, `real-chain-batch-fix.txt`, refreshed `real-chain.json` |

New deterministic assertions: a mixed batch with the first source off and the
last on carries `promptMessageId` but `promptSummary: null` and does not leak the
disabled body; an all-on batch summary equals `canonical content[:100]`; a single
formatted notice summary equals its canonical row prefix and the ID lookup
returns that prefix; disabled and Report-only inputs stay null; a partial
delivery-key row is never accepted while a row with all keys is.

The 331-test focused selection and the frontend build were intentionally not
re-run for this delta: no frontend file changed and the fix is confined to
`_freeze_prompt_ref`/`_delivered_prompt_row` plus its directed tests. The
earlier measurements above remain valid for the unchanged frontend.

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

`real-chain.json` records: the owned dynamic port (the file now reflects the
batch-fix re-run, 61286; the first run used 50952), the three owned Sessions,
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
