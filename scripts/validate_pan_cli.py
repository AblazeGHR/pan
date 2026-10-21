"""Owned Windows CLI integration probe; temporary Unicode/space profile only.

Run with the repository interpreter. No provider calls, no protected ports, no
user PATH writes. Artifacts remain in the printed temporary profile for review.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.core import launcher
from packages.core.cli import request
from register_pan_cli import register, unregister


def main():
    if os.name != "nt":
        raise SystemExit("Windows integration probe")
    repo = Path(__file__).resolve().parents[1]
    profile = Path(tempfile.mkdtemp(prefix="Pan CLI 中文 空格 "))
    root = profile / "安装 root"
    root.mkdir()
    # Full source copy gives all hard-coded module-relative stores a private root.
    shutil.copytree(repo / "packages", root / "packages", ignore=shutil.ignore_patterns(
        "node_modules", "__pycache__", "dist", ".git", "emulator_sidecar", "e2e"))
    shutil.copytree(repo / "scripts", root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(repo / "main.py", root / "main.py")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    config = {"python": sys.executable, "port": port, "startup": {"console_hidden": True},
              "qq": {"enabled": False}, "wechat": {"enabled": False}, "remote": {"enabled": False},
              "mcp_remote": {"enabled": False}, "plugin_manifests": [],
              "session_lifecycle": {"exit_strategy": "offline"}}
    (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
    config_bytes = (root / "config.json").read_bytes()
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAN_") and k.upper() != "PYTHONPATH"}
    env.update(PYTHONPATH=str(root), PYTHONIOENCODING="utf-8:backslashreplace")
    directory = profile / "命令 bin"
    register(directory, root, Path(sys.executable), search_path="")
    shim = directory / "pan.cmd"
    report = {"root": str(root), "port": port, "python": sys.executable, "checks": []}
    flags = subprocess.CREATE_NO_WINDOW
    def run(*args, ok=True):
        started = time.monotonic()
        result = subprocess.run([str(shim), *args], cwd=profile, env=env,
                                capture_output=True, text=True, encoding="utf-8", errors="replace",
                                timeout=110, creationflags=flags)
        check = {"argv": list(args), "exitCode": result.returncode,
                 "seconds": round(time.monotonic() - started, 3), "stdout": result.stdout, "stderr": result.stderr}
        report["checks"].append(check)
        print(json.dumps(check, ensure_ascii=True), flush=True)
        assert (result.returncode == 0) == ok, check
        return result
    def identity():
        value = request(port, "/api/main/identity")
        assert value["root"] == str(root)
        assert request(port, "/api/sessions?summary=1") is not None
        return value
    def windows():
        user = ctypes.WinDLL("user32", use_last_error=True)
        rows = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user.IsWindowVisible.argtypes = [wintypes.HWND]
        user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        def visit(hwnd, unused):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            title = ctypes.create_unicode_buffer(4096)
            user.GetWindowTextW(hwnd, title, len(title))
            if user.IsWindowVisible(hwnd):
                rows.append({"hwnd": int(hwnd), "pid": pid.value, "title": title.value})
            return True
        user.EnumWindows(callback_type(visit), 0)
        return rows
    injector = profile / "console_probe.py"
    injector.write_text('''import ctypes,sys,time
from ctypes import wintypes as w
k=ctypes.WinDLL("kernel32",use_last_error=True)
k.FreeConsole()
assert k.AttachConsole(int(sys.argv[1])), ctypes.WinError(ctypes.get_last_error())
if sys.argv[2] == "interrupt":
 assert k.GenerateConsoleCtrlEvent(0,0)
 time.sleep(1)
else:
 class Key(ctypes.Structure):
  _fields_=[("down",w.BOOL),("repeat",w.WORD),("virtual",w.WORD),("scan",w.WORD),("char",w.WCHAR),("control",w.DWORD)]
 class Event(ctypes.Union):
  _fields_=[("key",Key),("padding",ctypes.c_byte*16)]
 class Record(ctypes.Structure):
  _fields_=[("type",w.WORD),("event",Event)]
 k.CreateFileW.restype=w.HANDLE
 handle=k.CreateFileW("CONIN$",0xc0000000,3,None,3,0,None)
 records=(Record*4)()
 for index,char in enumerate("qqq\\r"):
  records[index].type=1
  records[index].event.key=Key(True,1,ord(char.upper()),0,char,0)
 written=w.DWORD()
 k.WriteConsoleInputW.argtypes=[w.HANDLE,ctypes.POINTER(Record),w.DWORD,ctypes.POINTER(w.DWORD)]
 assert k.WriteConsoleInputW(handle,records,4,ctypes.byref(written)), ctypes.WinError(ctypes.get_last_error())
 assert written.value == 4
k.FreeConsole()
''', encoding="utf-8")
    def console_signal(pid, action):
        subprocess.run([sys.executable, str(injector), str(pid), action], env=env,
                       check=True, timeout=10, creationflags=flags, capture_output=True)
    active = False
    try:
        run("--help")
        run("--version")
        run("doctor")
        run("status")
        start = run("start")
        active = True
        assert "ready" in start.stdout
        initial = identity()
        report["initial"] = initial
        assert initial["console"]["state"] == "absent"
        visible = windows()
        report["hiddenStartupVisibleOwnedWindows"] = [w for w in visible if w["pid"] == initial["pid"] or str(root) in w["title"]]
        assert not report["hiddenStartupVisibleOwnedWindows"]
        run("start", ok=False)
        run("status")
        for _ in range(2):
            run("show", "console")
            shown = identity()
            assert shown["pid"] == initial["pid"] and shown["console"]["interactive"]
            assert any(w["hwnd"] == shown["console"]["hwnd"] for w in windows())
            run("hide", "console")
            hidden = identity()
            assert hidden["pid"] == initial["pid"] and hidden["console"]["state"] == "hidden"
            assert not any(w["hwnd"] == hidden["console"]["hwnd"] for w in windows())
        run("logs", "-n", "3")
        # The follower is independent of the host. Stop only this owned helper.
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        follower = subprocess.Popen([sys.executable, str(root / "scripts/pan_cli.py"), "logs", "-f"],
                                    cwd=profile, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=startup)
        time.sleep(0.7)
        try:
            assert follower.poll() is None
            console_signal(follower.pid, "interrupt")
            assert follower.wait(timeout=5) == 130
        finally:
            if follower.poll() is None:
                follower.terminate()  # retained owned follower handle only; never a Pan host
                follower.wait(timeout=5)
        report["logsFollowCtrlC"] = {"exitCode": 130, "servicePidAfter": identity()["pid"]}
        assert identity()["pid"] == initial["pid"]
        run("restart", "--running", "offline")
        restarted = identity()
        report["restarted"] = restarted
        assert restarted["pid"] != initial["pid"]
        run("exit", "--running", "offline")
        active = False
        assert launcher.listener_owner(port) is None
        assert not launcher.process_alive(restarted["pid"], restarted["createdAt"])
        run("status")
        # A stale record or foreign PID must not authorize an operation.
        state_path = root / "data/process.json"
        stale = {"root": str(root), "port": port, "main": {"pid": 2147483646, "createdAt": 1}}
        state_path.write_text(json.dumps(stale))
        run("show", "console", ok=False)
        run("exit", ok=False)
        run("start")
        active = True
        live = identity()
        saved = state_path.read_bytes()
        stale["main"] = {"pid": os.getpid(), "createdAt": launcher.process_create_time(os.getpid())}
        state_path.write_text(json.dumps(stale))
        run("exit", "--running", "offline", ok=False)
        assert identity()["pid"] == live["pid"]
        state_path.write_bytes(saved)
        run("exit", "--running", "offline")
        active = False
        # Visible startup is also independent of the invoking terminal.
        config["startup"]["console_hidden"] = False
        (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
        run("start")
        active = True
        visible = identity()
        assert visible["console"]["state"] == "visible"
        run("hide", "console")
        assert identity()["pid"] == visible["pid"]
        run("show", "console")
        console_signal(visible["pid"], "input")
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            jobs = [launcher._read_json(p) or {} for p in (root / "data/background_jobs/jobs").glob("job_*.json")]
            completed = any(j.get("oldPid") == visible["pid"] and j.get("phase") == "offline"
                            and not j.get("error") for j in jobs)
            if (completed and launcher.listener_owner(port) is None and
                    not launcher.process_alive(visible["pid"], visible["createdAt"])):
                break
            time.sleep(0.1)
        assert completed and not launcher.process_alive(visible["pid"], visible["createdAt"]), "console qqq must complete legal Exit"
        active = False
        report["interactiveQqq"] = {"pid": visible["pid"], "exited": True}
        (root / "config.json").write_bytes(config_bytes)
        # Foreground main.py stays attached to its caller; no permanent config edit.
        debug_log = (profile / "debug.log").open("w", encoding="utf-8")
        debug_process = subprocess.Popen([sys.executable, str(root / "scripts/pan_cli.py"), "debug"],
                                         cwd=profile, env=env, stdout=debug_log, stderr=debug_log,
                                         creationflags=flags)
        active = True
        deadline = time.monotonic() + 15
        debug_identity = None
        while time.monotonic() < deadline:
            if launcher.listener_owner(port):
                try:
                    debug_identity = identity()
                    break
                except OSError:
                    pass
            time.sleep(0.1)
        assert debug_identity and debug_process.poll() is None
        assert debug_identity["console"]["managed"] is False
        run("hide", "console", ok=False)
        run("exit", "--running", "offline")
        active = False
        assert debug_process.wait(timeout=10) == 0
        debug_log.close()
        report["foregroundDebug"] = {"pid": debug_identity["pid"], "exitCode": 0, "configUnchanged": True}
        assert (root / "config.json").read_bytes() == config_bytes
        unregister(directory)
        assert not shim.exists()
        report["ok"] = True
    finally:
        if active:
            # Only the existing explicit legal API is allowed, even on failure.
            try:
                run("exit", "--running", "offline")
            except Exception as exc:
                report["cleanupError"] = repr(exc)
        report["remainingListener"] = launcher.listener_owner(port)
        (profile / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Evidence: " + str(profile / "report.json"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
