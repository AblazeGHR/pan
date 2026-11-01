"""Platform-owned detached executor bootstrap and supervisor identity probes."""
from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys
import time
import secrets


def _spawn(argv):
    from packages.jobs.window import command_window_options
    repo = Path(__file__).resolve().parents[3]
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("PAN_") and key.upper() != "PYTHONPATH"}
    env["PYTHONPATH"] = str(repo)
    options = command_window_options()
    if os.name == "nt":
        options["creationflags"] |= subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    return subprocess.Popen(argv, cwd=repo, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            close_fds=True, **options)


def launch_detached(argv, *, receipt_root):
    """A short-lived bootstrap breaks the main->supervisor ancestry chain.

    The worker waits for a retained-handle exit receipt for the bootstrap before
    effect admission. CREATE_NEW_PROCESS_GROUP alone does not break a Windows
    process-tree relationship. No shell command is constructed from Job data.
    """
    nonce = secrets.token_hex(24)
    directory = Path(receipt_root) / ".bootstrap"
    directory.mkdir(parents=True, exist_ok=True)
    receipt_path = directory / (nonce + ".json")
    bootstrap = _spawn([sys.executable, "-m", "packages.core.platform.execution_host",
                        "--bootstrap", str(receipt_path), nonce, *argv])
    code = bootstrap.wait(timeout=10)
    if code != 0:
        raise RuntimeError("detached executor bootstrap failed; query original intent")
    receipt = {"bootstrapPid": bootstrap.pid, "exitCode": code, "exited": True, "nonce": nonce}
    temporary = receipt_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt), encoding="utf-8")
    temporary.replace(receipt_path)
    return bootstrap


def read_bootstrap_receipt(path, nonce, timeout=10):
    """No fresh PID lookup can replace the host's retained Popen wait proof."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            receipt = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            receipt = None
        if receipt is not None:
            if receipt.get("nonce") != nonce or receipt.get("exited") is not True or receipt.get("exitCode") != 0:
                raise RuntimeError("bootstrap exit receipt does not match launch")
            return receipt
        time.sleep(.02)
    raise RuntimeError("bootstrap exit could not be proven; no effect admitted")


def supervisor_identity():
    from packages.core.platform.process import default_process_owner
    owner = default_process_owner()
    identity = owner.identity(os.getpid())
    if identity is None:
        raise RuntimeError("supervisor process birth identity unavailable")
    return {"schema": "pan.process-identity/1", "platform": owner.platform, **identity.as_dict()}


def supervisor_alive(record):
    from packages.core.platform.process import default_process_owner
    check = default_process_owner().verify(record)
    status = getattr(check.status, "value", check.status)
    return status == "alive" and check.ours is True


def supervisor_exit_evidence(record):
    """Read-only proof for the ORIGINAL supervisor; unknown never means dead."""
    import psutil
    from packages.core.platform.process import default_process_owner, ProcessStatus
    try:
        check = default_process_owner().verify(record)
        if check.status is ProcessStatus.DEAD and check.ours is True:
            return {"confirmed": True, "reason": "original_handle_signaled", "identity": dict(record)}
        if check.status is ProcessStatus.ALIVE and check.ours is False:
            return {"confirmed": True, "reason": "pid_reused_original_exited", "identity": dict(record)}
        if check.verified_alive:
            return {"confirmed": False, "reason": "original_supervisor_alive"}
        # A native handle cannot be reopened after process object deletion on
        # Windows. Proven PID absence establishes exit, never kill authority.
        psutil.Process(record["pid"])
    except psutil.NoSuchProcess:
        return {"confirmed": True, "reason": "pid_absent", "identity": dict(record)}
    except Exception:
        pass
    return {"confirmed": False, "reason": "supervisor_exit_unknown"}


def service_offline_evidence(*, root, port, old_pid, old_pid_created_at):
    """psutil distinguishes absence/reused PID from access-denied/unknown."""
    import psutil
    owner, listener_status = None, "offline"
    try:
        for connection in psutil.net_connections(kind="tcp"):
            address = connection.laddr
            local_port = getattr(address, "port", None)
            if local_port is None and isinstance(address, (tuple, list)) and len(address) > 1:
                local_port = address[1]
            if local_port == port and str(connection.status).upper() in {"LISTEN", "LISTENING"}:
                owner, listener_status = connection.pid, "listening"
                break
    except (psutil.Error, OSError):
        listener_status = "unknown"
    old_gone = False
    try:
        process = psutil.Process(old_pid)
        observed_birth = process.create_time()
        old_gone = observed_birth != old_pid_created_at
        old_status = "reused" if old_gone else "alive"
    except psutil.NoSuchProcess:
        old_gone, old_status = True, "dead"
    except (psutil.AccessDenied, OSError):
        old_status = "unknown"
    return {"ok": listener_status == "offline" and old_gone, "root": root, "port": port,
            "listenerOwner": owner, "listenerStatus": listener_status, "oldPid": old_pid,
            "oldPidCreatedAt": old_pid_created_at, "oldProcessStatus": old_status}


def main():
    if len(sys.argv) < 5 or sys.argv[1] != "--bootstrap":
        raise ValueError("fixed executor bootstrap arguments required")
    # Internal argv comes only from launch_execution, never from a Job plan.
    _spawn([*sys.argv[4:], "--bootstrap-receipt", sys.argv[2], "--bootstrap-nonce", sys.argv[3]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
