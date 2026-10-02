# Terminal lifecycle / detach probe

Probe-owned evidence for T-TERMINAL-PTY-20261003 (worktree
`terminal-lifecycle-explore-20261003`, branch `explore/terminal-lifecycle-20261003`).
Nothing here touches the real Pan service: every process is spawned by these
scripts, every control endpoint is a fresh ephemeral `127.0.0.1` port, and all
runtime state lives under `%TEMP%`.

## Files

| file | role |
| --- | --- |
| `probe_lib.py` | Win32 identity (PID + exact 100ns FILETIME creation time + exit-code liveness), Toolhelp32 tree, Job Object wrapper, JSON-line protocol, single-process venv spawn helper |
| `runner.py` | PTY runtime owner: ConPTY child tree + runner-owned `KILL_ON_JOB_CLOSE` job + loopback control server + lease / durable (detach) lifecycle |
| `supervisor.py` | probe host: detached runner spawn (mirrors `_spawn_background_runner`), lease socket, `STOP_AND_EXIT` / `EXIT_KEEP` / `CRASH` death paths |
| `controller.py` | scripted "browser connection" client; process exit = disconnect |
| `tick.py` | background counter program started inside the PTY (running-program evidence) |
| `driver.py` | s1–s5 scenario matrix: assertions, identity checks, cleanup sweep, evidence JSON |
| `job_object_probe.py` | Job Object ownership-layout evidence, incl. production `packages.core.takeover_job` reused read-only with probe children |
| `evidence/` | machine-readable output of the committed run |

## Environment (prepared by this TA, not installed globally)

* uv 0.9.14
* Python 3.14.5 from `C:/Users/14709/AppData/Local/Programs/Python/Python314/python.exe`

```bat
uv venv --python C:/Users/14709/AppData/Local/Programs/Python/Python314/python.exe ^
    C:/Users/14709/AppData/Local/Temp/pan-term-lifecycle-probe-venv
uv pip install --python C:/Users/14709/AppData/Local/Temp/pan-term-lifecycle-probe-venv/Scripts/python.exe ^
    pywinpty==3.0.5
```

The uv venv `python.exe` is a trampoline that starts the base interpreter as a
child, so `Popen.pid` would not be the PID the child reports. The probes spawn
the base interpreter with `__PYVENV_LAUNCHER__` (CPython's documented venv
launcher protocol) to keep the venv prefix in a single process and keep PID
identity checks exact — see `probe_lib.probe_python()`.

## Run

```bat
cd audit/terminal/lifecycle
set PY=C:/Users/14709/AppData/Local/Temp/pan-term-lifecycle-probe-venv/Scripts/python.exe
%PY% driver.py --scenario all
%PY% driver.py --scenario s3_detach_normal_exit --keep
%PY% job_object_probe.py --out evidence/job_object_probe.json
```

Scenarios:

* `s1_default_normal_exit` — default runtime dies on host normal exit
* `s2_default_crash` — default runtime dies on host crash (lease expiry)
* `s3_detach_normal_exit` — detached runtime survives host normal exit; new
  controller process reconnects to the same PIDs, shell variable and running program
* `s4_controller_churn` — browser disconnects change nothing; output and execution
  continue across connection gaps
* `s5_detach_crash_tree` — detached runtime survives host crash; hard-killing the
  runner still cleans the whole PTY tree (runner-owned kill-on-close job)

Runtime roots are created under `%TEMP%\pan-term-lifecycle-<ts>\<scenario>\` and
deleted after each scenario unless `--keep` is passed.  Evidence lands in
`evidence/s*.json`, `evidence/summary.json`, `evidence/job_object_probe.json`.

## Result of the committed run (2026-10-03)

* `driver.py --scenario all`: 58/58 checks pass (s1 11, s2 8, s3 16, s4 14, s5 9)
* `job_object_probe.py`: 6/6 sub-probes pass
* cleanup: no owned process alive after any scenario, no temp roots left, all
  control ports refused after every runtime stop; verified by
  `Scenario.cleanup()` identity sweep and `wait_port_closed()` checks

Full write-up: `docs/design/PAN_TERMINAL_LIFECYCLE_JOBS_20261003.md`.
