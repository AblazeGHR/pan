# Terminal integrated acceptance ledger

This ledger describes the isolated `implement/terminal-mcp-ma-20261004` tree,
not main/practical, the running Pan instance, a deployment, or human acceptance.
Real Windows evidence comes from build 26200.9451, the declared Python/Node
dependencies, and owned loopback/temporary-data fixtures. No model/provider
account, user Session, production port 8768, or foreign Job was used.

## Feature and evidence mapping

| Plan scenario | Current evidence | Remaining boundary |
| --- | --- | --- |
| T1 create/cwd | service real tests; REST/WS and real Pan chains | Workspace/Session IDs are optional labels, not authorization or lifecycle coupling |
| T2 input | real Chromium Chinese input; native Control+C reaches foreground handler event 0 and shell survives; native msvcrt receives Left/Tab/CJK/Enter exactly; real ClipboardEvent with bracketed paste reaches native program; long writes in backend/runner suites | Not every TUI, keyboard layout, paste program, or Ctrl+D EOF convention has been verified |
| T3 resize/TUI | native console 137×46→91×30; browser alternate-screen entry/return; installed real less 685 navigation and exit to same shell; headless mode matrix | Engine confirmation and PTY acceptance remain separate; less used explicit TERM=xterm-256color; universal TUI snapshot fidelity is not accepted |
| T4 output/backpressure | bounded raw log; WS in-flight queue accounting/slow-client isolation; browserless 309254-byte output and explicit eviction gap | Long-running natural slow-client/throughput stress remains unverified |
| T5 exit/tail | retained-handle exit code 7 and CJK tail tests; natural-exit-not-EOF negative control | Root exit is not channel EOF; output_complete is never inferred from root death |
| T6 disconnect | real WS/browser close/reload retains PID and shell variables | This is not durability across logout/machine restart |
| T7 recovery | browser protocol A restore followed by applied-cursor continuation; partial badge; actual screen marker | Real cmd modes remain partial; no universal full recovery claim |
| T8 explicit close | real REST/browser close; backend root-dead/grandchild cleanup; injected failures preserve same owner and retry | Genuine OS CloseHandle failure and every old Windows build not measured |
| T9/T10/T11/T18/T20 Pan lifecycle | actual Pan app/lifespan graceful and crash restart tests: managed DEAD, detached ALIVE, same raw identity/variable after reconnect, secret removed on confirmed close | Retained verifier handles are part of death proof; an arbitrarily late UNKNOWN remains unconfirmed |
| T12 runner hard death | launcher/composition retained-handle hard-death layout checks | Job kernel fallback only applies to the tested owned layouts, not every environment |
| T13/T14 security/spawn | IPC handshake/schema/token/server identity tests; atomic suspend/assign/member/resume failure gates | Cross-user and remote-host rejection remain structural rather than dual-account/network acceptance |
| T15 lease revocation | real two-browser takeover; stale generation rejected; durable detach revokes all old attachments before fresh issuance | Stable client_id is not identity proof; browser never receives IPC credentials |
| T16 PR6 | original seven-file 62 tests plus real ConPTY fake-CLI restore/cleanup and five owner gates; integrated terminal/rewind selection | Actual CBC/provider rollback was not invoked; failed cleanup has object retry, not a new public retry endpoint |
| T17 browserless | all browser pages close; producer exceeds raw retention; reopen displays final marker, original PID, raw cursor-zero read reports gap; final harness exit 0 | Partial restoration is honest; this does not prove all native TUI modes |
| T19 degraded engine | bounded feed-lag/reset-unconfirmed gates, runner/REST/WS/client no-upgrade assertions | No silent reset; user must explicitly request a new view; cross-sidecar restart unsupported |
| T21 Web gates | denied Origin/Host/Fetch/remote-binding cases have zero service calls and legal positive controls; MCP local caller/scope gate | Remote enable switch is not remote authentication; browser Origin matrix across engines not fully exercised |

## Two formerly blocked Windows paths

The dedicated launcher clears its inherited ignore-CtrlC attribute before child
creation via SetConsoleCtrlHandler(NULL,FALSE). Programmatic use does not change
a borrowed host's console settings. Foreground CtrlC is now positively measured
through service and native Chromium keyboard paths; this is not a guarantee of
every application's signal handling.

Production spawn requests breakaway from the ancestor Job; only access-denied
falls back to the non-breakaway layout. The runner still proves actual ambient
Job suitability before detach. PTY and sidecar Jobs keep their no-breakaway and
kill-on-close safety policy. Actual detached shells survive service and Pan exit,
including crash, and reconnect with exact original PID/FILETIME and shell state.
Restricted hosts still refuse detach without state/credential mutation.

## Reproducible regression layers

* Integrated Terminal/rewind selection at `8314421d`: 791 passed, 1774 deselected,
  zero failed/skipped. Original failed backend test history is retained.
* Frontend at `db294fd5`: all 112 Vitest files, 1229 passed, zero failed. Earlier
  19-failure and intermediate 2-failure JSON reports are retained. TypeScript,
  production build, and 36 compressed assets pass; scoped Terminal/Rewind ESLint
  passes. No test repair changed frontend production behavior.
* Real Chromium `browserless-recovery-final/result.json`: passed=true,
  harnessExit=0, Chinese/CtrlC/takeover/resize/reload/durable/browserless/close
  assertions. The prior timeout report is retained as a non-overall-pass.
* Real Pan lifecycle final uv selection: 14 passed, 73 deselected, including both
  graceful and crash/restart cases and bounded shutdown retry gates.
* Final whole-repository Python run is separate and must be reported from its
  actual result; an earlier 2558-pass/9-fail/10-skip run was not retroactively
  converted to a pass. See the repository-regression ledger for attribution.

Final whole run at `db294fd5`: **2569 passed / 2 failed / 10 skipped**. Both
failures pass in the subsequent affected selection (98 passed / one new pressure
fixture failure). The final bounded-pressure rerun passes with 5.34 MB, zero fast
connection gaps, original PID, and confirmed explicit close. Strict cleanup-record
gates are 11/11 direct and uv. These are targeted post-fix results, **not** a claim
of a subsequent all-green whole-repository run.

Subsequent frozen-source whole-repository confirmation at `4823fc0e`:
**2581 passed / 0 failed / 10 skipped**, 837.54 seconds. See
`full-repository-4823-final-uv.xml` and the repository-regression ledger for all
ten skip reasons. One detach test explicitly skips on its measured restrictive
ancestor Job; it is not counted as a durable-detach positive. The result covers
the last cleanup/pressure fixes without rewriting any earlier failed evidence.

## Limits and handoff

The feature is implemented in an isolated tree and its measured local subset is
accepted. Do not equate this with deployment, PR/main integration, user acceptance,
all-platform support, all-mode full fidelity, or long-steady-state certification.
Write budgets initiate cancellation rather than providing an unconditional OS
SLA. F5 machine fields are conservative approximations without a historical
snapshot-generation atomic binding. Windows Job policies can still prevent
durability, in which case the product explicitly refuses it.

The bilingual user manual now covers creation, control takeover, observe/reload,
partial views, detach/reconnect, explicit close, dependencies, and these limits.
No main/practical advance, push, practical build, service restart, or memory write
was performed. Later integration/deployment remains a separately authorized step.

## Real browser native interaction control

`audit/terminal/implementation/browser/native-tui.mjs` drives the actual React
panel and production REST/WS/ConPTY path. The owned native console child records
`[224,75,9,20013,25991,13]` for Left, Tab, Chinese characters and Enter, and reads
its actual console size before and after viewport resizing (137×46→91×30).
Screenshots show alternate-screen content and return to the primary screen.
Installed Git/MSYS less 685 navigates a 500-line file to line 499, exits, and the
original shell successfully executes a file witness command. Explicit close and
test-harness exit both succeed, with no browser errors.

`native-tui-uppercase-control/result.json` is the overall passing run. The first
run retains less's missing-terminal-declaration warning; the TERM-controlled run
retains a test-driver mistake (`Shift+g` produced the lowercase pager command).
The passing run uses explicit `TERM=xterm-256color` and uppercase `G`. These are
environment/test controls, not silent production fixes. No global TERM setting
or borrowed host console was changed. This evidence does not establish every
keyboard layout, every native TUI, bracketed-paste semantics or full TUI restore.

The subsequent clipboard control exposed a real frontend omission: xterm's
default Ctrl-V key handler sent byte 0x16 and prevented the browser paste action.
`terminalKeyHandler` now leaves Ctrl-V (including Ctrl-Shift-V) to the browser;
xterm's existing ClipboardEvent handler supplies program-mode-aware paste framing.
`native-clipboard-fixed/result.json` records the native child receiving exactly
`ESC[200~PASTE_中文ESC[201~`, successful explicit close and harness exit 0.
The failing real run is retained (`native-clipboard-initial`). Ordinary Ctrl-C/D,
arrow and Tab handling is unchanged. Keyboard/stream fixtures pass 25/25;
TypeScript/build (36 compressed assets) and scoped ESLint pass. This is a local
Chromium/program-mode control, not a guarantee for every platform or TUI.

The further `native-paste-reload/result.json` control receives Ctrl-D as byte 4
in the native child's raw input mode (not an assertion of POSIX EOF). Reloading
the browser while that child has bracketed paste enabled preserves the exact
PTY PID/FILETIME; the subsequently pasted Chinese text still arrives with the
correct delimiters. The fixture restores the prior browser clipboard text in
`finally` without logging it. Explicit close and harness exit both succeed.

## Browser-generated origin negative controls

`browser/origin-security.mjs` uses actual Chromium page contexts from a different
port, a different loopback hostname, and an opaque sandbox iframe (`Origin:null`).
All three close attempts receive HTTP 403 at the wire and all three WebSocket
attempts fail before opening, with zero received frames. The exact original
runner PID/FILETIME remains running. A legal same-origin positive receives hello
and claim-result; explicit close then confirms exited, and harness exit is 0.
This measures browser-generated headers, not a forged Origin option.

The initial fixture incorrectly expected a Playwright response event for a CORS
failure and is retained as failed. The passing CDP control reads HTTP status from
`Network.responseReceivedExtraInfo`, without recording headers or credentials.
Cross-origin JavaScript still cannot read the denial response; that is not a
failure of the gate. This control covers local Chromium only, not all browser
engines or remote/cross-account authentication.

The complete post-clipboard frontend run passes **1242 tests / zero failures /
zero pending**, `frontend-after-clipboard.json`. It is separate from the earlier
1229-test frozen result and from any Python regression result.

## Sustained pressure confirmation

The three-minute natural producer/slow-observer fixture passes in uv: 111.5 MB
producer payload, 120.5 MB observed PTY bytes, zero fast gaps, original identity,
180 queue/RSS/heartbeat samples, slow peer close frame 1013, and confirmed
explicit close. Application queue peaks at 1.78 MB and remains below its 4 MiB
limit; TCP/library buffers are separate. A completed-task exception handling
repair is supported by three pre-fail/post-pass WS gates. See
`PAN_TERMINAL_STEADY_PRESSURE_MA_20261004.md` for exact metrics, fixture failures,
transport ping isolation, and the finite-duration limitations. This is stronger
than the earlier 5.34 MB check, but still not hours-long certification.

## Confirmed-close display reset

A new component gate exposes a stale UI: confirmed close clears selection but
previously left "control mode" and the release button active. The close-success
branch now also resets display state. An unconfirmed close still preserves the
same selected terminal and enabled retry. Pre: one failed / one passed;
post: panel + stream + keyboard **27/27**. The actual browser control at
`panel-close-native-final` takes control immediately before explicit close,
then confirms empty selection, observer/choose-terminal status and a disabled
release button; all prior Chinese/Ctrl-C/takeover/resize/durable/browserless
controls remain passing. Harness exit is 0. TypeScript/build and 36 compressed
assets pass, as does scoped ESLint. No Python source/test was changed during
the ongoing frozen `ed1fbddd` whole-repository Python run.
