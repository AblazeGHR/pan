# Codex Session mode conversion acceptance

Baseline: `1ad4fb1f`. Session configuration changes preserve Session identity,
Codex thread, history and queued messages. A live Worker stages the change and
uses the existing delayed restart coordinator after terminal persistence and real
idle; no immediate interruption is requested. Failed old-runtime shutdown retains
ownership and refuses a replacement. Offline changes do not spawn a Worker.

All Codex Sessions expose the Terminal TUI entry beside View. A stdio Session
asks for confirmation before upgrade. Worker Settings exposes Session downgrade.
Pending conversion remains visible rather than pretending that conversion is done.

## Verification

- `python-rerun/python-full.xml`: 2990 cases, 2983 passed, 7 existing skips,
  zero failures/errors; 1132.58 seconds. Durable Job
  `job_fb4524c31b679cec4eaa884b`, exit 0.
- `evidence/frontend-vitest.xml`: 1384 cases, all passed, zero skips/errors.
- Frontend lint, build and isolated Chromium native-control harness: exit 0.
  Browser evidence exercises real components with intercepted API responses, not
  a real provider-backed conversion.
- Production/test/document hashes match `python-rerun/summary.json` using
  SHA256 of LF-normalized bytes. The temporary dependency-junction ownership
  marker is environment-only and is not committed; its raw-byte hash matches.

## Preserved failure and limitations

The initial parallel Python/frontend run had 2982 passes, 7 skips and one failure:
`test_real_http_history_coldload_does_not_stall_dashboard_websocket` exceeded its
consecutive heartbeat-delay threshold. No threshold or assertion was changed.
The exact test passed serially both on the candidate and unchanged main baseline
(`coldload-check/summary.json`), then passed in the complete serial Python rerun.
The root cause is not established. Initial logs/XML remain preserved.

The seven skips remain environment/opt-in dependent; they are not counted as
passes. No live Session data was migrated, no protected service was restarted,
and no real-provider/browser conversion was claimed. Loading new backend code
requires a user-controlled Pan restart. The window-policy batch is independently
accepted; combined integration receives an additional focused regression.

## Combined integration

Conversion commit `33d878f0` merged the accepted window-policy commit `b01396a8`
without conflicts at `61ffb265`. The only merged change to conversion's worker
module is the window helper import and kwargs at the two existing launch edges.
On this combined source, six test files (conversion, native API, window policy,
WeChat spawn, real native kernel and real HTTP coldload) passed: **63 passed**,
zero skipped/failures/errors (`combined.xml`, 28.79 seconds). Chromium native
control harness passed again, including stdio confirmation/delayed entry and
visible queue. This is an incremental combined check, not a second combined
full-suite run. Lint retains 19 existing warnings with zero errors.
