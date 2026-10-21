# cc-switch console leak investigation (incomplete)

Worktree: `D:/project/pan-worktrees/cc-switch-console-leak-20261021`
Branch: `fix/cc-switch-console-leak-20261021`
Base/HEAD: `b6502ea940b3d852b7e6824f6c2d4e66fe7666fc`.
No candidate commit: one native evidence case still fails. Changes remain for continuation.

## Confirmed implementation defect

`ApiProvider._rpc` created a piped `asyncio.create_subprocess_exec` child with no Windows creation policy. The checked-in cc-switch descriptor selects `${PAN_PYTHON} -m packages.cc_switch_bridge.provider`; the API provider is this helper's parent. The bridge's own CLI launch already uses `CREATE_NO_WINDOW`. Thus the missing policy is one level above the cc-switch CLI. The patch applies existing `command_window_options()` to all stdio API providers, without changing interactive terminal/CLI show/hide or reconnect logic.

HostConsole.initialize explicitly FreeConsole's the managed, windowless host before its reader starts. A console child created afterward without NO_WINDOW can get its own console. Owned real-Windows probes exercise this actual initialize method. Negative control with the previous spawn options returns a nonzero, visible HWND; the patched exe/cmd/PowerShell paths return HWND=0 and support consecutive RPC calls, normal EOF cleanup and exit code 0. Host show/hide/show also works on the test-owned desktop while its provider remains windowless.

This is not proof of the reported user's exact window. Read-only process snapshots showed Pan main chain 31484 -> 24200 (venv launcher) -> 23792 (Python314); no command positively identified as the bridge module was present in the snapshot. No original visible HWND was captured. Do not infer historical PID identity from the screenshot error.

## Concrete evidence blocker

This Windows environment delegates newly allocated consoles: GetConsoleWindow returns class `PseudoConsoleWindow`, with window PID equal to the probe Python PID. Posting WM_CLOSE or SC_CLOSE to that HWND does not terminate the console client (10-second wait timeout). This is a surrogate HWND, so IsWindowVisible alone does not establish an actual terminal window on the input desktop. Earlier assertions of visible HWND must be read with this limitation.

A separate explicit conhost.exe negative control was attempted to obtain a real ConsoleWindowClass and close it. It does not forward the provider's JSON-RPC standard streams in this setup: `provider.initialize` times out after 30 seconds. The close case remains a failing test, not skipped or relaxed. Therefore actual terminal closure -> provider connection failure -> no replay has not been demonstrated. The code's existing explicit reconnect/failure behavior is unchanged.

The failing harness is preserved in tests/fixtures/provider_console_host.py (`mode=close`) for continuation. It is unsuitable for merging until fixed. All probe processes exited through provider.close; final process scan for provider_console_host/probe found no residual.

## Checks

- `python scripts/run_tests_isolated.py -- python -m pytest tests/test_plugin_platform.py tests/test_plugin_panels.py tests/test_pan_cli.py -q -o addopts= --junitxml=audit/cc-switch-console-leak-20261021/regression.xml`: exit 0; 52 passed, no skips, 4 warnings (pytest-timeout absent / dependency deprecations).
- `python scripts/run_tests_isolated.py -- python -m pytest tests/test_plugin_provider_window.py -q -s -o addopts= --tb=short --junitxml=audit/cc-switch-console-leak-20261021/windows.xml`: exit 1; native evidence and failure details in windows.log/XML.
- `python -m compileall -q packages/plugins/providers.py tests/fixtures/provider_console_host.py tests/fixtures/provider_console_probe.py tests/test_plugin_provider_window.py`: exit 0.
- `git diff --check`: exit 0.

No full suite/build; no production endpoint calls, no restart, no plugin reconnect, no user window closed, no shared dependencies written, no push/main/practical change. No isolated full Pan HTTP service E2E or real account operation was performed. MCP stdio SDK's installed Windows launcher already uses NO_WINDOW normally, but has its own unflagged exception fallback; it has not been modified or claimed fixed. `.cmd`/npm launch paths remain unverified.

The previous error terminal has no specific exception in the visible transcript. Before resumption no edits or owned processes had been created, so its cause cannot be reconstructed from tool evidence.
