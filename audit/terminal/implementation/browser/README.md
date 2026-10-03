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
