"""Small user-facing CLI. Internal launcher stop primitives are not user Exit."""
from __future__ import annotations

import argparse
from collections import deque
import contextlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import Request, ProxyHandler, build_opener
from urllib.error import HTTPError

from . import __version__, launcher


def request(port, path, payload=None, instance=None):
    headers = {"Content-Type": "application/json"}
    if instance:
        headers.update({"X-Pan-Expected-Pid": str(instance["pid"]),
                        "X-Pan-Expected-Created-At": str(instance["createdAt"])})
    req = Request(f"http://127.0.0.1:{port}{path}",
                  data=None if payload is None else json.dumps(payload).encode(),
                  headers=headers)
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=5) as response:
            return json.load(response)
    except HTTPError as exc:
        raise launcher.LauncherError(f"HTTP {exc.code}: {exc.read().decode(errors='replace')}") from exc


@contextlib.contextmanager
def start_lock(root):
    path = root / "data" / ".cli-start.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            handle.seek(0)
            if not handle.read(1):
                handle.write(b"0"); handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise launcher.LauncherError("Another pan start/debug is in progress") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def verified(root):
    port = launcher.configured_port(root, environ={})
    state = launcher.load_state(root)
    record = state.get("main") or {}
    if not record.get("pid") or record.get("createdAt") is None or state.get("port") != port:
        raise launcher.LauncherError("No matching recorded instance; run pan status/doctor")
    if not launcher.service_process_identity(record["pid"], root, record["createdAt"]).get("ok"):
        raise launcher.LauncherError("Recorded PID is stale or belongs to another instance")
    if launcher.listener_owner(port) != record["pid"]:
        raise launcher.LauncherError("Port listener does not match the recorded instance")
    remote = request(port, "/api/main/identity")
    if (remote.get("pid") != record["pid"] or
            launcher._normal_path(remote.get("root", "")) != launcher._normal_path(root) or
            remote.get("createdAt") != record["createdAt"]):
        raise launcher.LauncherError("HTTP instance identity mismatch")
    return port, record, remote


def status(root):
    port = launcher.configured_port(root, environ={})
    base = {"root": str(root), "port": port, "cliVersion": __version__,
            "runningVersion": None, "console": {"state": "unknown"}, "pid": None}
    try:
        _, record, remote = verified(root)
        return {**base, "state": "running", "pid": record["pid"],
                "runningVersion": remote.get("version"), "console": remote.get("console")}
    except (launcher.LauncherError, OSError, ValueError) as exc:
        owner = launcher.listener_owner(port)
        record = launcher.load_state(root).get("main") or {}
        alive = record.get("pid") and launcher.process_alive(record["pid"], record.get("createdAt"))
        return {**base, "state": "unknown" if owner or alive else "stopped",
                "listenerOwner": owner, "error": str(exc)}


def lifecycle(root, operation, timeout, choice=None, *, fallback=False):
    """Run exit/restart through the fixed system Action.

    The old HTTP/JSON implementation remains available only through the
    explicit ``fallback`` switch.  A failed canonical admission is reported;
    it is never followed by a second launch implicitly.
    """
    from . import lifecycle_action
    operation = "exit" if operation == "exit" else "restart" if operation == "restart" else operation
    if operation not in lifecycle_action.PROTECTED_OPERATIONS:
        raise launcher.LauncherError(f"unsupported protected lifecycle operation: {operation}")
    try:
        port, record, _ = verified(root)
        payload = {"operation": operation,
                   "options": {"markRunningSessionsOffline": choice}} if choice is not None else {"operation": operation}
        result = request(port, lifecycle_action.ACTION_ROUTE, payload, record)
        effect_id = result.get("effectId") if isinstance(result, dict) else None
        if not result.get("accepted") or not isinstance(effect_id, str):
            raise launcher.LauncherError(f"{operation} canonical Action failed/not accepted: {result}")
        print(f"{operation} accepted; waiting for canonical receipt: {effect_id}", flush=True)
        receipt = lifecycle_action.run_wait_for_receipt(
            root=root, effect_id=effect_id, operation=operation, timeout=timeout,
        )
        if receipt.get("outcome") != "succeeded":
            raise launcher.LauncherError(f"{operation} failed: {receipt}")
        evidence = receipt.get("receipt") or {}
        if operation == "exit":
            offline = evidence.get("offlineProof") or {}
            if (evidence.get("phase") != "offline" or offline.get("ok") is not True
                    or offline.get("root") != str(root)
                    or offline.get("port") != port
                    or offline.get("oldPid") != record["pid"]
                    or offline.get("oldPidCreatedAt") != record["createdAt"]):
                raise launcher.LauncherError(f"exit completed without offline proof: {receipt}")
        else:
            proof = evidence.get("readyProof") or {}
            identity = proof.get("identity") or {}
            if (evidence.get("phase") != "ready" or proof.get("ok") is not True
                    or proof.get("root") != str(root) or proof.get("port") != port
                    or proof.get("http") is not True
                    or proof.get("listenerOwner") != proof.get("newPid")
                    or identity.get("ok") is not True or identity.get("root") != str(root)):
                raise launcher.LauncherError(f"restart completed without ready proof: {receipt}")
            new_pid = proof.get("newPid")
            new_birth = proof.get("newPidCreatedAt")
            if new_pid == record["pid"] or new_birth is None:
                raise launcher.LauncherError(f"restart receipt reused the old service identity: {receipt}")
        return {"ok": True, "phase": evidence.get("phase"),
                "effectId": effect_id, "receipt": receipt}
    except (launcher.LauncherError, OSError, ValueError, TypeError, TimeoutError):
        if not fallback:
            raise
        # Explicit migration escape hatch: the fallback is called only after
        # Action admission failed, and never in parallel with it.
        from . import lifecycle_legacy
        return lifecycle_legacy.run(root, operation, timeout, choice,
                                    verified=verified, request=request)


def logs(root, follow=False, lines=50):
    config = launcher.load_config(root)
    path = Path(config.get("logging", {}).get("file", "data/logs/pan.log"))
    path = path if path.is_absolute() else root / path
    print(f"Log: {path} (Ctrl+C stops following only)", flush=True)
    identity = None
    offset = 0
    first = True
    while True:
        try:
            # Close between reads so Windows log rotation can rename the file.
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                stat = path.stat()
                current = (stat.st_dev, stat.st_ino)
                if identity != current or stat.st_size < offset:
                    offset = 0
                identity = current
                if first:
                    for line in deque(stream, maxlen=lines):
                        print(line, end="", flush=True)
                    first = False
                else:
                    stream.seek(offset)
                    print(stream.read(), end="", flush=True)
                offset = stream.tell()
        except FileNotFoundError:
            if not follow:
                raise launcher.LauncherError(f"Log does not exist: {path}")
        if not follow:
            return
        time.sleep(0.25)


def doctor(root):
    checks = {"root": str(root), "entry": (root / "main.py").is_file(),
              "cli": str(root / "scripts/pan_cli.py"), "readOnly": True}
    try:
        config = launcher.load_config(root)
        argv, source = launcher.resolve_python_argv(root, probe=False)
        checks.update(python=argv, pythonSource=source, port=launcher.configured_port(root, environ={}),
                      consoleHidden=launcher._console_hidden(root))
        result = subprocess.run([*argv, "-c", "import fastapi, uvicorn, psutil; print('ready')"],
                                cwd=root, capture_output=True, text=True, timeout=20,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                                **launcher.command_window_options())
        checks["dependencies"] = "ready" if result.returncode == 0 else "missing or interpreter failed"
        checks["listenerOwner"] = launcher.listener_owner(checks["port"])
        path = Path(config.get("logging", {}).get("file", "data/logs/pan.log"))
        path = path if path.is_absolute() else root / path
        checks.update(logPath=str(path), logDirectoryExists=path.parent.is_dir(),
                      configPath=str(root / "config.json"), configExists=(root / "config.json").is_file())
        checks["ok"] = checks["entry"] and result.returncode == 0
    except (launcher.LauncherError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        checks.update(ok=False, error=str(exc))
    return checks


def debug(root, timeout):
    with start_lock(root):
        observed = status(root)
        if observed["state"] != "stopped":
            raise launcher.LauncherError(f"Refusing debug startup: {observed}")
        python, _ = launcher.resolve_python_argv(root)
        port = launcher.configured_port(root, environ={})
        print("Foreground main.py debug: this terminal stays occupied; Ctrl+C is an illegal host exit.\n"
              "Use pan exit in another shell for legal shutdown. Logs: data/logs/pan.log. Config is unchanged.", flush=True)
        child = subprocess.Popen([*python, str(root / "main.py"), f"--pan-root-marker={root}",
                                  f"--pan-port-marker={port}"], cwd=root,
                                 env={**os.environ, "PAN_PORT": str(port)})
        created = launcher.process_create_time(child.pid)
        record = launcher._record(child.pid, root, "main", child.args, created, f"--pan-root-marker={root}")
        launcher.save_state(root, {"main": record, "port": port})
        checks = launcher.wait_ready(root=root, port=port, expected_pid=child.pid,
                                     expected_created_at=created, timeout=timeout)
        if not checks.get("ok"):
            raise launcher.LauncherError(f"Foreground debug did not become ready: {checks}; process retained for diagnosis")
        if checks["listenerOwner"] != child.pid:
            record.update(pid=checks["listenerOwner"], createdAt=checks["listenerOwnerCreatedAt"])
            launcher.save_state(root, {"main": record, "port": port})
    return child.wait()


def parser():
    p = argparse.ArgumentParser(prog="pan", description="Pan host CLI; commands operate on the registered checkout")
    p.add_argument("--version", action="version", version=f"Pan {__version__}")
    p.add_argument("--root", type=Path, default=launcher.checkout_root(), help="installation root (independent of cwd)")
    sub = p.add_subparsers(dest="command", required=True)
    from packages.jobs.final_cli import add_parser
    add_parser(sub)
    for name, help_text in (("start", "Start independently, honor startup.console_hidden, wait for ready"),
                            ("debug", "Run main.py in the foreground; terminal stays occupied; no config changes"),
                            ("exit", "Explicit legal Exit API; wait for durable offline + old PID gone"),
                            ("restart", "Explicit legal Restart API; wait for new PID + ready")):
        item = sub.add_parser(name, help=help_text, description=help_text)
        item.add_argument("--timeout", type=float, default=90 if name in {"exit", "restart"} else 30)
        if name in {"exit", "restart"}:
            item.add_argument("--running", choices=("offline", "preserve"), help="required if saved exit policy is ask")
    sub.add_parser("status", help="Report running/stopped/unknown, PID, port, runtime version and console")
    sub.add_parser("doctor", help="Read-only paths, config, interpreter/dependencies, port and log diagnostics")
    item = sub.add_parser("logs", help="Show recent persistent log; Ctrl+C ends follow without stopping Pan")
    item.add_argument("-f", "--follow", action="store_true")
    item.add_argument("-n", "--lines", type=int, default=50)
    for name in ("show", "hide"):
        item = sub.add_parser(name, help=f"{name.capitalize()} Pan's own interactive console (never a shared shell)")
        item.add_argument("target", choices=("console",))
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "final":
        from packages.jobs.final_cli import run
        return run(args, args.root.expanduser().resolve())
    # Registered CLI is independent of the invoking Pan Worker/Job profile.
    for key in list(os.environ):
        if key.upper().startswith("PAN_"):
            os.environ.pop(key)
    root = args.root.expanduser().resolve()
    try:
        if hasattr(args, "timeout") and (not math.isfinite(args.timeout) or args.timeout <= 0):
            raise launcher.LauncherError("--timeout must be finite and positive")
        if args.command == "start":
            with start_lock(root):
                from . import lifecycle_action
                lifecycle_action.execute_local("start", root=root, timeout=args.timeout)
        elif args.command == "debug":
            return debug(root, args.timeout)
        elif args.command in {"exit", "restart"}:
            choice = None if args.running is None else args.running == "offline"
            print(json.dumps(lifecycle(root, args.command, args.timeout, choice), ensure_ascii=False))
        elif args.command in {"show", "hide"}:
            port, record, _ = verified(root)
            from . import lifecycle_action
            result = request(port, lifecycle_action.ACTION_ROUTE,
                             {"operation": args.command, "pid": record["pid"],
                              "createdAt": record["createdAt"]}, record)
            if not result.get("ok"):
                raise launcher.LauncherError(str(result))
            print(json.dumps(result, ensure_ascii=False))
        elif args.command == "status":
            result = status(root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result["state"] == "unknown" else 0
        elif args.command == "doctor":
            result = doctor(root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        elif args.command == "logs":
            if args.lines < 1:
                raise launcher.LauncherError("--lines must be positive")
            if args.follow and os.name == "nt":
                # A launcher/Job may have inherited the Windows ignore-Ctrl+C
                # flag. Following is a foreground interaction in this process.
                import ctypes
                import signal
                ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
                signal.signal(signal.SIGINT, signal.default_int_handler)
                signal.signal(signal.SIGBREAK, signal.default_int_handler)
            logs(root, args.follow, args.lines)
        return 0
    except KeyboardInterrupt:
        return 130
    except (launcher.LauncherError, OSError, ValueError, TypeError, AttributeError) as exc:
        print(f"pan: {exc}", file=sys.stderr)
        return getattr(exc, "exit_code", 1)


if __name__ == "__main__":
    raise SystemExit(main())
