"""New Ctrl-C hypothesis only: explicitly clear inherited ignore state.

Own ConPTY children only. Never broadcasts to the caller console. Helper
attaches after FreeConsole and verifies target membership before signalling.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from packages.core.terminal.backend import ConPtyBackend
from packages.core.terminal.contracts import OwnershipMode, OwnershipPolicy
from packages.core.terminal.ownership import build_runtime

CHILD = r'''
import ctypes, json, sys, time
from pathlib import Path
k = ctypes.WinDLL("kernel32", use_last_error=True)
reset = sys.argv[3] == "reset"
ready, events = Path(sys.argv[1]), Path(sys.argv[2])
reset_ok = bool(k.SetConsoleCtrlHandler(None, False)) if reset else None
HANDLER = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
def callback(event):
    with events.open("a", encoding="ascii") as stream:
        stream.write(str(event) + "\n")
    return 1
handler = HANDLER(callback)
registered = bool(k.SetConsoleCtrlHandler(handler, True))
mode = ctypes.c_uint()
k.GetConsoleMode(k.GetStdHandle(-10), ctypes.byref(mode))
ready.write_text(json.dumps({"reset_ok": reset_ok, "registered": registered,
                            "mode": mode.value}), encoding="utf-8")
while True:
    time.sleep(0.1)
'''
HELPER = r'''
import ctypes, json, sys, time
k = ctypes.WinDLL("kernel32", use_last_error=True)
pid, event = int(sys.argv[1]), int(sys.argv[2])
assert k.FreeConsole()
assert k.AttachConsole(pid)
k.SetConsoleCtrlHandler(None, True)
HANDLER = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
handler = HANDLER(lambda event: 1)
assert k.SetConsoleCtrlHandler(handler, True)
pids = (ctypes.c_uint * 64)()
count = k.GetConsoleProcessList(pids, 64)
assert 0 < count <= 64 and pid in list(pids)[:count]
ok = bool(k.GenerateConsoleCtrlEvent(event, 0))
print(json.dumps({"ok": ok, "event": event, "target_in_console": True}), flush=True)
time.sleep(0.1)
'''


def main():
    python = getattr(sys, "_base_executable", sys.executable)
    results = []
    with tempfile.TemporaryDirectory(prefix="pan-interrupt-ma-") as temporary:
        root = Path(temporary)
        for mode in ("baseline", "reset"):
            ready, events = root / (mode + ".ready"), root / (mode + ".events")
            backend = ConPtyBackend.spawn([python, "-c", CHILD, str(ready), str(events), mode])
            runtime = build_runtime(
                "interrupt_" + mode, backend,
                ownership=OwnershipPolicy(mode=OwnershipMode.SERVICE, lifecycle_owner="probe",
                                          tree_guard_kind="job", tree_guard=backend.guard),
                identity=backend.identity, identity_probe=backend.probe, output_cap=65536)
            runtime.start(rows=24, cols=80, gate=backend.gate)
            result = {"case": mode, "identity": backend.identity.as_dict()}
            try:
                deadline = time.monotonic() + 5
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                assert ready.exists(), "child not ready"
                result["ready"] = json.loads(ready.read_text(encoding="utf-8"))
                backend.write(b"\x03")
                time.sleep(0.4)
                result["after_raw"] = events.read_text() if events.exists() else ""
                for event in (0, 1):
                    helper = subprocess.run([python, "-c", HELPER, str(backend.pid), str(event)],
                                            capture_output=True, text=True, timeout=4,
                                            creationflags=subprocess.CREATE_NEW_CONSOLE)
                    assert helper.returncode == 0, helper.stderr
                    time.sleep(0.4)
                    result["helper_" + str(event)] = json.loads(helper.stdout)
                    result["after_helper_" + str(event)] = events.read_text() if events.exists() else ""
                result["root_alive"] = backend.alive()
            finally:
                report = runtime.close(reason="probe-complete", interrupt=False)
                if not report.ok:
                    report = runtime.close(reason="probe-retry", interrupt=False)
                assert report.ok, report.as_dict()
                result["cleanup"] = report.as_dict()
            results.append(result)
    output = {"hypothesis": "inherited-ctrl-c-ignore", "results": results,
              "ctrl_c_delivered": any("0" in row.get("after_helper_0", "").splitlines()
                                      for row in results), "cleanup_confirmed": True}
    destination = Path(__file__).with_name("result.json")
    destination.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
