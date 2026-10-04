# Ctrl-C inherited-ignore hypothesis, MA probe

The old negative probes did not explicitly clear the inheritable ignore-Ctrl-C
attribute. Microsoft documents this attribute separately from registering an
application handler:
https://learn.microsoft.com/en-us/windows/console/setconsolectrlhandler

`probe.py` uses only owned ConPTY children and targeted, verified-console
helpers. Baseline ignores raw 0x03 and helper-generated CTRL_C_EVENT, but receives
CTRL_BREAK_EVENT (positive control). Explicit `SetConsoleCtrlHandler(NULL,FALSE)`
in the child enables both raw and helper Ctrl-C delivery. Both retained-handle
and Job cleanup reports converge; original evidence is not overwritten.

`production_probe.py` uses the real service/launcher/default shell and a local
foreground child with an actual Windows handler, **without** an explicit child
reset. In this pre-fix production layout Ctrl-C still does not deliver; the
same runner identity remains and final cleanup converges with secret removal.
Its result is `production_result.json`. Thus the standalone positive probe is
not yet production acceptance.

The first helper attempt exited on its own Ctrl-Break (test-tool issue): the
helper now installs a handler for both events before signalling. This is not
a backend defect and no failed-attempt log is manufactured.

No existing console, foreign Job, account, provider or service is modified.
The next candidate is to clear the inherited ignore flag only in the dedicated
production launcher process, before it spawns children; never change the MA or
Pan host's global control-handler state.

## Production repair and acceptance

The dedicated `python -m ...launcher` entry now explicitly clears the inherited
ignore flag before spawning engine or PTY. A borrowed `TerminalLauncher.run()`
or ordinary programmatic `main()` does not mutate its caller's console state;
only the dedicated entry opts into `initialize_console=True`. Setup failure
returns the static internal-error path before any child spawn.

`production_post_result.json` confirms actual Windows CTRL_C_EVENT(0) reaches
a foreground child that does not reset its own inherited flag, then the same
shell executes another command (file witness, not command echo). Runner PID
and raw FILETIME remain unchanged. Final cleanup converges and removes secret.

The real Chromium browser repeats this with actual Control+C through xterm,
WS, service, IPC and ConPTY. `browser/ctrl-c-production/result.json` has both
`osCtrlCEventVerified` and `foregroundInterruptedShellPreserved=true`; harness
exit 0, 37 events, no page errors. Original negative evidence remains intact.

Final uv launcher + interrupt suites: **87 passed** (`launcher-final-uv.xml`).
Final uv backend + interrupt suites: **33 passed** (`interrupt-backend-uv.xml`).
The earlier direct run (`launcher-direct.xml`, 85 passed/1 skipped) predates
the final borrowed-host isolation adjustment and is retained as intermediate
evidence, not claimed as final-source acceptance.

Acceptance is for the production dedicated-launcher/default-shell layout on
this Windows build. Applications may deliberately disable processed input or
ignore Ctrl-C themselves. Arbitrary borrowed-host backend use is not normalized
automatically, and this is not a guarantee for every TUI or Windows build.
