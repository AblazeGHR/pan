# Browser terminal MA acceptance

This stage implements the global React `/terminals` panel with exact
`@xterm/xterm@6.0.0` and `@xterm/addon-fit@0.11.0` dependencies.
Browser display is not authoritative. Snapshot serialization is applied first;
raw output resumes at the exact applied decimal-string cursor. Output ACK follows
the renderer write callback. Gaps stop consumption and require explicit recovery.
Control is explicit, initially observer-only, and input is never replayed on reconnect.
Leaving the page closes only the connection, not the terminal process.

## Checks

- Protocol client: 12 tests; related Sidebar: 20 tests (32 passed).
- MA WS and REST: 48 passed, including the isolated real REST chain.
- TypeScript/Vite production build: successful; 36 compressed assets verified.
- `check.mjs`: real Chromium against `harness.py`, real ConPTY, DPAPI, named pipe,
  headless emulator and product REST/WS. Only unrelated Dashboard endpoints are
  mocked. Native browser security headers are not forged.
- Browser created a terminal, claimed control, submitted Chinese input, observed
  the actual shell response, reloaded into observer mode, recovered the server
  screen, and explicitly closed with bounded retries until confirmed.
- `result.json`: passed=true, harnessExit=0. `screen.png` visually inspected:
  real Chinese input/output visible and partial recovery clearly labeled.

The browser check uncovered the legitimate same-origin GET path: Chromium omits
Origin and sends Sec-Fetch-Dest as the literal `empty`. The GET exception requires
same-origin Fetch metadata plus the existing configured origin/Host match. POST
and WS retain their Origin requirement. Missing metadata, wrong Host and cross-site
negative controls remain denied.

Harness development corrections: unrelated Dashboard CLI mock shape was fixed;
the close retry originally observed a stale error before the request settled.
The final test waits for completion before retrying, rather than treating an
unconfirmed close as successful. Both failed runs shut down their owned harness.

## Limits

This is not full Pan startup, provider, authentication or browser-matrix acceptance.
Real terminal snapshots remain partial; OS Ctrl-C, durable detach, cross-user/host,
long-running backpressure, POSIX and sidecar restart recovery remain unverified.
The harness owns an isolated data root and loopback ephemeral listener; existing
Pan services and ports are untouched. No main/practical advance or deployment.

Reproduce: build `packages/web`, install sidecar exact-pinned dependencies, then
run `node audit/terminal/implementation/browser/check.mjs <python-executable>`.

## Additional browser ownership stage

The real Chromium check now also opens a second connection to the same terminal,
claims control there, and verifies the first connection's stale input is rejected
with stale-generation and its UI returns to observer mode. Closing the second tab
leaves the terminal running (real GET positive control). A viewport change produces
a resize-result. Reload recovery and explicit close still pass afterwards.
`result.json` records all three additional assertions and harnessExit=0.
Harness lifecycle ordering now closes WS before the REST service, matching the
production nesting. An initial Playwright setup error (implicit single-page context)
was fixed by using an explicit context; it was not a product defect.

## Optional association metadata

The creation form accepts optional Workspace and Session IDs and lists their
returned scope metadata. Empty fields are omitted. These values are labels, not
caller identities, authorization proofs or lifecycle coupling. The real browser
check submits both labels and reads back the exact persisted scope through the
product GET API. Build and focused ESLint pass; the complete browser chain passes.

## Combined regression and test repair (2026-10-04)

The previous full Terminal uv run is retained as `terminal-regression.xml`:
701 passed / 2 failed. Direct controls are retained in
`regression-direct-control.xml` (2 passed). No failed history was overwritten.

The cancellation-ownership test now gates a completed real WriteFile until the
budget canceller enters, rather than relying on an 8 MiB write being slow enough.
The paired worker/handle refusal and retry assertions are unchanged.
The Job root-death test uses `sys._base_executable` for its standard-library-only
children: uv's launcher is a different process and terminating that shim can also
change console lifetime. The real root PID equality and surviving-grandchild
membership/cleanup assertions remain intact; no assertion was weakened.

Affected backend+guard suites: 35 passed direct and 35 passed uv (new
`regression-repair-direct.xml` / `regression-repair-uv.xml`). This is not a claim
that the 703-test combined run was rerun after the repair.

## Integrated PR6 and terminal regression (2026-10-04)

At production commit `8314421d`, uv with declared minimal requirements and
pytest/pytest-timeout executed `tests/ -k 'terminal or rewind'`: **791 passed,
1774 deselected, 0 failed, 0 skipped**, 470.04 seconds. Evidence is
`terminal-rewind-combined.xml`. This supersedes the two historical 703-run
failures for this integrated selection, not for the entire repository.

After the PR6 import/build, real Chromium `check.mjs` again passed (28 observed
terminal events; harness exit 0), with output in `pr6-integration/`. The optional
label argument preserves earlier browser results/screenshots instead of
overwriting them. No full Pan service/provider or 8768 listener was used.

## Browserless recovery beyond retained raw output (2026-10-04)

`browserless-recovery-final/` exercises the real Chromium/REST/WS/ConPTY path.
After durable reconnect, all browser pages close while a child emits 3500 lines.
Reopening restores the final marker from the continuously fed headless screen;
the raw read from zero reports an explicit gap and a nonzero first-retained
offset, with more than 256 KiB produced. The same terminal PID is preserved.
Explicit close and harness exit 0 are both required for overall success.

The first run (`browserless-recovery/`) passed the display assertions but its
harness exceeded the 30-second exit wait; it is not an overall pass. The owned
harness subsequently exited. Its temporary diagnostic root was retained. The
fixture now sets uvicorn's graceful connection wait to five seconds, after which
the existing WS/REST lifecycle cleanup still runs. No production shutdown policy
was changed. The final run passes with 66 observed terminal events and exit 0.
