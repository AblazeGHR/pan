# cc-switch console leak candidate

Worktree: `D:/project/pan-worktrees/cc-switch-console-leak-20261021`
Branch: `fix/cc-switch-console-leak-20261021`
Integrated main: `4e3170e8` (fast-forward from original `b6502ea9`, no conflict).
Candidate SHA: the commit containing this report; obtain with `git rev-parse HEAD`.

## Change and confirmed defect

`packages/plugins/providers.py:ApiProvider._rpc` started piped stdio helpers without a Windows creation policy. It now uses existing `command_window_options()` at that creation edge. This is a background pipe helper contract; explicit Pan CLI debug/show/hide and user Terminal creation paths are unchanged. No reconnect, failure classification or replay logic changed.

Checked-in cc-switch configuration selects `${PAN_PYTHON} -m packages.cc_switch_bridge.provider`. The source chain is Pan host -> ApiProvider helper -> cc-switch CLI. The bridge already uses CREATE_NO_WINDOW for its own CLI child; the missing policy was at the host -> helper edge. Other stdio API plugins share that edge and receive the same narrow fix. MCP providers use the SDK's separate launcher, which normally uses NO_WINDOW; its exception fallback remains outside this change.

HostConsole.initialize FreeConsole's a managed windowless host before its input reader begins. After that detach, an unflagged console executable may acquire a console association. The negative-control executable in this environment gets a nonzero PseudoConsoleWindow HWND. This establishes an association difference, **not a visible terminal or the identity of the user's original popup**.

## Final evidence, after integrating main

Command (exit **0**):

```powershell
python scripts/run_tests_isolated.py -- python -m pytest tests/test_plugin_provider_window.py tests/test_plugin_platform.py tests/test_plugin_panels.py tests/test_pan_cli.py -q -s -o addopts= --tb=short --junitxml=audit/cc-switch-console-leak-20261021/final-targeted.xml
```

**58 passed, 0 skipped, 4 warnings, 30.83 seconds.** Full output: [final-targeted.log](final-targeted.log); JUnit: [final-targeted.xml](final-targeted.xml). Warnings are missing pytest-timeout configuration support and dependency deprecations, not ignored test failures.

- Real owned Windows hosts run actual HostConsole.initialize/FreeConsole. Patched exe, cmd /d /c and PowerShell -NoProfile -NonInteractive launch paths return HWND=0, no owned non-surrogate visible window in the observation snapshots, consecutive RPC replies and exit code 0. The probe records child/parent IDs, HWND/class, window-owner PID and observed windows. No user windows are hidden or closed by the observer.
- Host show -> hide -> show retains its existing state contract while its pipe provider still has HWND=0. These state checks do not prove that the delegated Terminal UI is human-visible.
- Provider close waits for the shell/executable process, and retained native handles to the actual probe descendants signal termination within 5 seconds. Final read-only process scan found no console-host/probe/disconnect-probe residual.
- A real provider durably appends one owned side effect, then exits with code 23 before replying. Runtime returns execution_unconfirmed/Do not automatically replay. Two subsequent requests return explicit reconnect required. The same process object remains; the durable start count and side-effect count remain exactly 1. This proves disconnect/unknown does not automatically respawn or replay. No actual account switch is involved.
- Existing Panel durable duplicate/unknown handling and existing CLI contracts pass in the targeted suite.

Other commands (each exit **0**):

```powershell
python -m compileall -q packages/plugins/providers.py tests/fixtures/provider_console_host.py tests/fixtures/provider_console_probe.py tests/fixtures/provider_disconnect_probe.py tests/test_plugin_provider_window.py tests/test_plugin_platform.py
git diff --check
```

## Original failures preserved and experiment correction

[INITIAL_REPORT.md](INITIAL_REPORT.md), [initial-windows.log](initial-windows.log), [initial-windows.xml](initial-windows.xml), [failed-conhost-host.py](failed-conhost-host.py) and [failed-console-probe.py](failed-console-probe.py) retain the failed experiment and its method. The old log's 1 failed / 5 passed is historical, not the final suite.

The original close experiment treated GetConsoleWindow's PseudoConsoleWindow as a closable real terminal window. WM_CLOSE/SC_CLOSE did not terminate the client. The attempted conhost.exe wrapper then incorrectly relied on conhost preserving the provider JSON-RPC stdio streams: provider.initialize timed out at 30 seconds. This is a harness failure, not evidence against the provider creation-flag fix.

The corrected test separates console association/window snapshots from transport semantics. It replaces the invalid conhost-stdio closure method with an actual provider process exiting after a durable effect but before its reply. Assertions still require unknown, connection failed, one start/one effect, no replay, continuous successful calls on healthy providers and owned cleanup. No failed assertion was converted to a skip or accepted success. Original window-close causality remains explicitly unverified.

## Boundaries

- Original现场 popup HWND/PID/parent chain and historical close event remain unconfirmed. The earlier read-only snapshot identified main launcher chain 31484 -> 24200 -> 23792, but no bridge module command was positively identified. This is not the reported popup's proven process chain.
- No actual user-input-desktop terminal closure E2E. No claim that IsWindowVisible on a surrogate proves a visible terminal. Snapshot observations are not a system-wide transient-window/third-party broker guarantee.
- No full Pan HTTP-service startup E2E, real account operations, `.cmd`/npm-specific matrix, full suite or build. No frontend changed, so no frontend build needed.
- No calls to 8767/8768, field service/plugin restart or reconnect, user window closure, shared dependency writes, push/release or main/practical movement. All native probe hosts and data belong to these tests.
- Previous Session error terminal had no specific exception in the available transcript; no pre-interruption owned processes or edits existed. The error cause remains unknown.
