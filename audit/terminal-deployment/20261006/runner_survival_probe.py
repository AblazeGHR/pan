"""Owned-process-only reproduction of the background Runner spawn flags."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from packages.core.terminal.guard import JobObjectGuard


def host(path):
    child = subprocess.Popen([sys.executable, __file__, "runner"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
    Path(path).write_text(str(child.pid), encoding="ascii")
    time.sleep(60)


def run(case, root):
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    marker = root / (case + ".pid")
    # Held at stdin until Job assignment completes; no child can escape the gate.
    argv = [sys.executable, "-c", "import sys;sys.stdin.readline();from pathlib import Path;__file__=sys.argv[1];exec(Path(__file__).read_text(encoding='utf-8'))", __file__, "host", str(marker)]
    p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    guard = JobObjectGuard() if case == "kill_on_job_close" else None
    handle = None
    try:
        if guard:
            guard.assign(int(p._handle))
        p.stdin.write(b"go\n")
        p.stdin.flush()
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        pid = int(marker.read_text(encoding="ascii"))
        handle = k.OpenProcess(0x100000 | 1 | 0x1000, False, pid)
        assert handle
        member = guard.is_member(int(handle)) if guard else None
        if guard:
            guard.close()
        elif case == "taskkill_tree":
            # Exact self-created host, retained Popen handle checked alive.
            assert p.poll() is None
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, check=True)
        else:
            p.terminate()  # retained original host handle, not arbitrary PID
        p.wait(timeout=10)
        time.sleep(.2)
        alive = k.WaitForSingleObject(handle, 0) == 258
        return {"case": case, "runner_in_owned_job": member,
                "host_exited": True, "detached_runner_survived": alive}
    finally:
        if handle:
            if k.WaitForSingleObject(handle, 0) == 258:
                k.TerminateProcess(handle, 1)
                k.WaitForSingleObject(handle, 5000)
            k.CloseHandle(handle)
        if p.poll() is None:
            p.terminate()
            p.wait(timeout=10)
        if guard:
            guard.close()


if __name__ == "__main__":
    # The -c host loader passes script path as argv[1].
    mode = sys.argv[-2] if len(sys.argv) > 2 else sys.argv[-1]
    if mode == "host":
        host(sys.argv[-1])
    elif mode == "runner":
        time.sleep(60)
    else:
        with tempfile.TemporaryDirectory(prefix="pan-owned-runner-survival-") as temp:
            results = [run(case, Path(temp)) for case in ("parent_only", "kill_on_job_close", "taskkill_tree")]
        out = Path(__file__).with_name("runner_survival.json")
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(out.read_text(encoding="utf-8"))
