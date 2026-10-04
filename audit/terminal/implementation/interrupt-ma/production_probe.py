"""Actual production launcher/shell Ctrl-C, no provider or test-only reset."""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from packages.core.terminal.service import CleanupUnconfirmed, TerminalService

CHILD = r'''
import ctypes, os, sys, time
from pathlib import Path
k = ctypes.WinDLL("kernel32", use_last_error=True)
HANDLER = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
interrupted = False
def callback(event):
    global interrupted
    Path(sys.argv[2]).write_text(str(event), encoding="ascii")
    interrupted = True
    return 1
handler = HANDLER(callback)
assert k.SetConsoleCtrlHandler(handler, True)
Path(sys.argv[1]).write_text(str(os.getpid()), encoding="ascii")
while not interrupted:
    time.sleep(0.1)
print("CHILD_INTERRUPTED", flush=True)
'''


def main():
    result = {"layout": "production-service-launcher-default-shell", "explicit_child_reset": False}
    with tempfile.TemporaryDirectory(prefix="pan-prod-interrupt-") as temporary:
        root = Path(temporary)
        script, ready, event, after = [root / name for name in ("child.py", "ready", "event", "after")]
        script.write_text(CHILD, encoding="utf-8")
        service = TerminalService(root / "terminals", log_stderr=False)
        tid = None
        try:
            view = service.create(cwd=str(root))
            tid = view["terminal_id"]
            token = service.attach(tid, "ma-interrupt", role="control")
            argv = [getattr(sys, "_base_executable", sys.executable), "-X", "utf8",
                    str(script), str(ready), str(event)]
            service.input(tid, token, (subprocess.list2cmdline(argv) + "\r").encode("utf-8"))
            deadline = time.monotonic() + 8
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert ready.exists(), "real child command did not start"
            result["child_pid"] = int(ready.read_text())
            service.input(tid, token, b"\x03")
            deadline = time.monotonic() + 2
            while not event.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            result["ctrl_c_delivered"] = event.exists() and event.read_text() == "0"
            if result["ctrl_c_delivered"]:
                # This cannot be an echo false-positive: only an executing shell
                # writes the next file after its foreground child was interrupted.
                service.input(tid, token, f'echo SHELL_STILL_ALIVE>"{after}"\r'.encode("utf-8"))
                deadline = time.monotonic() + 3
                while not after.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                result["shell_still_usable"] = after.exists() and "SHELL_STILL_ALIVE" in after.read_text()
            result["same_runner_identity"] = (service.get(tid)["pid"] == view["pid"]
                and service.get(tid)["process_created_at_filetime"] == view["process_created_at_filetime"])
        finally:
            if tid is not None:
                deadline = time.monotonic() + 25
                while True:
                    try:
                        closed = service.close(tid)
                        result["cleanup_status"] = closed.get("status")
                        break
                    except CleanupUnconfirmed:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.2)
                assert service.get(tid)["status"] == "exited"
                result["secret_removed"] = not service._store().exists(tid)
    Path(__file__).with_name("production_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
