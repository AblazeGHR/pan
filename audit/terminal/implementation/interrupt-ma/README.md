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
