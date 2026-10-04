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
| T2 input | real Chromium Chinese input; native Control+C reaches foreground handler event 0 and shell survives; long writes in backend/runner suites | Not every TUI, keyboard layout, bracketed-paste program, or Ctrl+D EOF convention has been verified |
| T3 resize/TUI | browser resize-result; backend console-size evidence; headless main/alternate-screen matrix | Engine confirmation and PTY acceptance remain separate; real less/full-browser TUI fidelity is not accepted |
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
