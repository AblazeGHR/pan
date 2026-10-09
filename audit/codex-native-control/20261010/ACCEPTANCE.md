# Final validation and baseline comparison

Base/main: `4b166bd3`; practical is out of merge scope.

## Frozen full run (`evidence-r2`)

- Python: **2910 passed, 18 failed, 7 skipped**, 1103.61 seconds.
- Frontend full Vitest, lint, build and Chromium fixture all exit 0.
- Source hashes unchanged throughout the durable Job run.
- Browser fixture proves UI/control routing with intercepted API responses;
  real Codex kernel/turn continuity is separately covered by local synthetic-provider CLI tests.
  Neither is a claim of live provider/deployed-service acceptance.

## Main comparison (read-only, same isolated Python)

Executed the exact 18 failing node IDs on unmodified main `4b166bd3`:
**17 failed, 1 passed**, 14.20 seconds. The 17 failures have the same causes:
outdated handoff/flush/restart/receipt test doubles, missing completionBridge
default expectations, and the terminal gate Request double lacking `app`.
Production paths implicated in these pre-existing failures were not repaired or weakened
as part of this feature. Full-suite green is explicitly **not** claimed.

The remaining test, `test_detach_semantics_same_pty_reconnect_and_shell_variable`,
passed on main and then passed on the candidate in isolation (**1 passed, 7.94 seconds**).
The full-run handler-error remains a recorded intermittent observation; a single rerun
does not establish its root cause or all-environment stability.

Earlier report-subscription doubles lacked the current `on_handoff` callback on main too;
the candidate aligns those four doubles and preserves their original assertions.
The report-subscription suite passed all 28 tests after that correction.

## Scope and limits

Codex native-control is opt-in. Legacy Session defaults and other adapters retain
their existing paths. Native UI hides history/composer and retains the bottom queue;
control-mode changes require receipt acknowledgement, do not restart the kernel,
and queued work stays fenced while native/uncertain/external-turn ownership applies.

No practical merge, production restart, push, provider account use or 8768 listener
was performed. Job default-window policy is a separate verified commit, to be combined
before the main merge. Existing main untracked user artifacts remain untouched.

## Combined candidate (`17ad3b8f`)

After combining Job window policy with native control: **177 Python tests passed**
(26.33 seconds); **39 frontend tests passed** across 9 files; TypeScript/Vite build
and 62 precompressed asset checks passed. Chromium fixture returned `passed:true`
and `page_errors:[]`. Source/test/document diff whitespace checks passed; raw historical
pytest logs/XML retain their original output whitespace rather than being rewritten.
The full frontend run preceding the Job addition had **1376 passed** across 127 files.

These focused checks do not erase the 17 main baseline failures or the one intermittent
full-run detach observation above. Main-only merge is the authorized deployment boundary;
no practical build/restart is implied by this delivery.
