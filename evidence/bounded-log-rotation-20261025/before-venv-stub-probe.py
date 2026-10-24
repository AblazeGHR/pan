"""Why the launcher must not hand the console log to the managed child.

Both interpreters are started with stdout/stderr = the log file (the old
launcher shape). The child then does what the relay does: dup2 a pipe over fd
1/2, which releases *its own* file handles. Renaming the active file afterwards
succeeds for a real interpreter and fails for the venv redirector, because the
stub process keeps its own copy of the inherited handle alive.
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CHILD = (
    "import os, sys, time\n"
    "r, w = os.pipe()\n"
    "os.dup2(w, 1)\n"
    "os.dup2(w, 2)\n"
    "os.close(w)\n"
    "print('ready', flush=True)\n"
    "time.sleep(3)\n"
)


def probe(label, interpreter, work):
    log = work / f"{label}.log"
    log.write_bytes(b"")
    with log.open("a", encoding="utf-8") as handle:
        child = subprocess.Popen([interpreter, "-c", CHILD], stdout=handle, stderr=handle)
    # The launcher closes its own copy immediately after spawn, like _start_main.
    try:
        time.sleep(1.0)
        try:
            os.replace(log, log.with_name(log.name + ".1"))
            outcome = "renamed"
        except OSError as exc:
            outcome = f"{type(exc).__name__}({exc.errno})"
        print(f"{label:>22}: child_released_fd=yes via=dup2 rename_while_child_alive={outcome}")
    finally:
        child.kill()
        child.wait(timeout=30)


def main():
    work = Path(tempfile.mkdtemp(prefix="pan-handle-probe-"))
    base = sys.base_prefix
    base_python = str(Path(base) / ("python.exe" if os.name == "nt" else "bin/python3"))
    print("base_interpreter    ", base_python)
    probe("base-interpreter", base_python, work)
    venv = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if venv and venv.is_file():
        print("venv_redirector     ", venv, f"{venv.stat().st_size} bytes")
        probe("venv-redirector", str(venv), work)
    else:
        print("no venv redirector supplied; skipping")


main()
