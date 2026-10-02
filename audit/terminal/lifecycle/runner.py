"""Probe runtime owner: owns a ConPTY process tree and a loopback control endpoint.

This is the minimal shape of the recommended Terminal runtime layout:

* The runner owns the PTY from the start (launched by the host, but never
  placed inside a host-lifetime KILL_ON_JOB_CLOSE job).
* The runner itself holds a KILL_ON_JOB_CLOSE Job Object over the PTY tree, so
  runner death (graceful, crash, or hard kill) always cleans the tree.
* The PTY/control endpoint outlives any single controller connection; browser
  disconnects only drop a socket.
* A supervisor lease governs the default lifetime: when the lease is lost and
  the runtime is not explicitly detached, the runner self-stops after a grace
  period.  ``detach`` makes the runtime durable and stops requiring a lease.
* ``stop`` performs explicit whole-tree termination.

Protocol: newline-delimited JSON over 127.0.0.1.  Commands:
  hello {token, role, clientId}   attach (roles: lease | controller | admin)
  state                           -> runtime state snapshot
  input {data}                    write raw bytes to the PTY
  read {from}                     -> output slice since byte offset
  wait {marker, from, timeout}    -> wait until marker appears in output
  snapshot {tail}                 -> last N characters of output
  detach                          -> durable mode (survives lease loss)
  stop                            -> explicit whole-tree stop
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_lib as lib  # noqa: E402

from winpty import PTY, Backend  # noqa: E402

MAX_OUTPUT_CHARS = 8 * 1024 * 1024
LEASE_NEVER_CONNECTED_TIMEOUT = 20.0
READ_POLL_SECONDS = 0.005


class Runtime:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.data_root = Path(args.data_root)
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.runtime_id = f"rt_{args.tag}_{os.getpid()}"
        self.token = secrets.token_hex(16)
        self.lock = threading.Lock()
        self.endpoint_lock = threading.Lock()
        self.output = ""
        self.output_base = 0
        self.connections: list[dict] = []
        self.controller_connects = 0
        self.lease_client_id: str | None = None
        self.lease_attached_at: float | None = None
        self.lease_lost_at: float | None = None
        self.detached = False
        self.detached_at: float | None = None
        self.stop_event = threading.Event()
        self.stop_reason: str | None = None
        self.started_at = time.time()

        # 1. Runner-owned guard job over the PTY tree (KILL_ON_JOB_CLOSE).
        self.job = lib.Job.create(kill_on_close=True)

        # 2. PTY root, then immediate guard-job assignment of the root tree.
        self.pty = PTY(args.cols, args.rows, backend=int(Backend.ConPTY))
        spawned = self.pty.spawn(args.shell, args.shell_args or None, args.cwd or None)
        if not spawned:
            raise RuntimeError("PTY.spawn returned False")
        self.pty_root_pid = self.pty.pid
        members: list[dict] = []
        failures: list[dict] = []
        injections = [p.lower() for p in (getattr(args, "fail_inject_assign", None) or [])]
        for item in lib.descendants([os.getpid()]):
            info = lib.identity(item["pid"])
            injected = any(pattern in item["exe"].lower() for pattern in injections)
            if injected:
                ok, err, note = False, 0, "injected_assign_failure"
            else:
                ok, err = self.job.assign(item["pid"])
                note = None
            entry = {
                "pid": item["pid"], "exe": item["exe"],
                "createTime": info["createTime"] if info else None,
                "createTimeFiletime": info["createTimeFiletime"] if info else None,
                "assigned": ok, "assignError": err, "note": note,
            }
            members.append(entry)
            if not ok:
                failures.append(entry)
        self.initial_members = members

        # A guard-job assignment failure must never publish a running runtime:
        # start-up fails closed, cleans only its own processes and exits.
        if failures or not members:
            if not members:
                failures = [{"note": "no PTY descendants discovered after spawn"}]
            self._startup_failure(failures)

        # 3. Loopback control endpoint (OS-assigned, guaranteed-free port).
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(8)
        self.port = int(self.server.getsockname()[1])

        self._write_endpoint()
        self.reader = threading.Thread(target=self._read_loop, daemon=True, name="pty-reader")
        self.reader.start()
        self.acceptor = threading.Thread(target=self._accept_loop, daemon=True, name="control")
        self.acceptor.start()

    # ── state / endpoint ────────────────────────────────────────────────────
    def state(self) -> dict:
        with self.lock:
            return {
                "runtimeId": self.runtime_id,
                "status": "stopping" if self.stop_event.is_set() else "running",
                "pid": os.getpid(),
                "startedAt": self.started_at,
                "port": self.port,
                "detached": self.detached,
                "detachedAt": self.detached_at,
                "lease": {
                    "attached": self.lease_client_id is not None,
                    "clientId": self.lease_client_id,
                    "attachedAt": self.lease_attached_at,
                    "lostAt": self.lease_lost_at,
                    "graceSeconds": self.args.lease_grace,
                },
                "pty": {
                    "rootPid": self.pty_root_pid,
                    "backend": "conpty",
                    "isAlive": bool(self.pty.isalive()),
                },
                "job": {
                    "killOnClose": self.job.kill_on_close,
                    "initialMembers": self.initial_members,
                    "activeProcesses": self.job.active_processes(),
                },
                "outputChars": self.output_base + len(self.output),
                "controllerConnects": self.controller_connects,
                "connections": list(self.connections),
            }

    def _write_endpoint(self, extra: dict | None = None) -> None:
        info = lib.identity(os.getpid())
        root_info = lib.identity(self.pty_root_pid)
        payload = {
            "kind": "terminal-runtime",
            "status": "running",
            "runtimeId": self.runtime_id,
            "pid": os.getpid(),
            "processCreatedAt": info["createTime"] if info else None,
            "processCreatedAtFiletime": info["createTimeFiletime"] if info else None,
            "port": self.port,
            "token": self.token,
            "detached": self.detached,
            "detachedAt": self.detached_at,
            "leaseGraceSeconds": self.args.lease_grace,
            "pty": {
                "rootPid": self.pty_root_pid,
                "rootCreatedAt": root_info["createTime"] if root_info else None,
                "rootCreatedAtFiletime": root_info["createTimeFiletime"] if root_info else None,
                "shell": self.args.shell,
            },
            "job": {
                "killOnClose": True,
                "initialMembers": self.initial_members,
            },
            "startedAt": self.started_at,
            "updatedAt": time.time(),
            "scope": {"workspaceId": None, "sessionId": None},
        }
        if extra:
            payload.update(extra)
        with self.endpoint_lock:
            lib.write_json(self.data_root / "runtime.json", payload)

    def _startup_failure(self, failures: list[dict]) -> None:
        """Fail closed before any running endpoint is published.

        The runtime record is written with status=startup_failed (no port, no
        token), then only probe-owned descendants are terminated and the guard
        job handle is closed.  Never returns.
        """
        info = lib.identity(os.getpid())
        failure_at = time.time()
        lib.write_json(self.data_root / "runtime.json", {
            "kind": "terminal-runtime",
            "status": "startup_failed",
            "runtimeId": self.runtime_id,
            "pid": os.getpid(),
            "processCreatedAtFiletime": info["createTimeFiletime"] if info else None,
            "port": None,
            "error": "guard_job_assign_failed",
            "failures": failures,
            "members": self.initial_members,
            "pty": {"rootPid": self.pty_root_pid},
            "at": failure_at,
        })
        lib.write_json(self.data_root / "runner_startup_failure.json", {
            "runtimeId": self.runtime_id,
            "pid": os.getpid(),
            "reason": "guard_job_assign_failed",
            "failures": failures,
            "members": self.initial_members,
            "ptyRootPid": self.pty_root_pid,
            "at": failure_at,
        })
        if self.job.active_processes():
            self.job.terminate()
        swept: list[dict] = []
        for item in lib.descendants([os.getpid()]):
            owned = lib.identity(item["pid"])
            if owned and lib.kill_verified(item["pid"], owned["createTimeFiletime"]):
                swept.append({"pid": item["pid"], "exe": item["exe"], "killed": True})
            else:
                swept.append({"pid": item["pid"], "exe": item["exe"], "killed": False})
        try:
            self.pty.cancel_io()
        except Exception:
            pass
        job_active_before_close = self.job.active_processes()
        self.job.close()
        leftover = [item["pid"] for item in lib.descendants([os.getpid()])]
        lib.write_json(self.data_root / "runner_startup_failure_final.json", {
            "runtimeId": self.runtime_id,
            "swept": swept,
            "leftoverAfterSweep": leftover,
            "jobActiveBeforeClose": job_active_before_close,
            "finishedAt": time.time(),
        })
        print(json.dumps({"runner": "startup_failed", "failures": failures,
                          "leftover": leftover}), flush=True)
        os._exit(2)

    # ── PTY reader ──────────────────────────────────────────────────────────
    def _read_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                data = self.pty.read(blocking=False)
            except Exception as exc:  # PTY torn down
                with self.lock:
                    self.output += f"\n<pty-read-error {type(exc).__name__}: {exc}>\n"
                break
            if data:
                with self.lock:
                    self.output += data
                    if len(self.output) > MAX_OUTPUT_CHARS:
                        drop = len(self.output) - MAX_OUTPUT_CHARS
                        self.output = self.output[drop:]
                        self.output_base += drop
            else:
                time.sleep(READ_POLL_SECONDS)
            if not self.pty.isalive():
                break

    # ── control server ──────────────────────────────────────────────────────
    def _accept_loop(self) -> None:
        self.server.settimeout(0.2)
        while not self.stop_event.is_set():
            try:
                client, address = self.server.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(
                target=self._serve, args=(client, address), daemon=True,
                name="control-conn").start()

    def _serve(self, client: socket.socket, address) -> None:
        conn = lib.LineConn(client)
        record = {
            "role": None, "clientId": None, "peer": f"{address[0]}:{address[1]}",
            "connectedAt": time.time(), "disconnectedAt": None, "commands": 0,
        }
        with self.lock:
            self.connections.append(record)
        role: str | None = None
        try:
            hello = conn.recv(timeout=5.0)
            if not hello or hello.get("cmd") != "hello" or hello.get("token") != self.token:
                conn.send({"ok": False, "error": "bad_hello_or_token"})
                return
            role = str(hello.get("role") or "")
            client_id = str(hello.get("clientId") or "")
            if role == "lease":
                with self.lock:
                    if self.lease_client_id is not None and record is not None:
                        conn.send({"ok": False, "error": "lease_already_attached",
                                   "leaseClientId": self.lease_client_id})
                        return
                    self.lease_client_id = client_id
                    self.lease_attached_at = time.time()
                    self.lease_lost_at = None
                self._write_endpoint()
            elif role == "controller":
                with self.lock:
                    self.controller_connects += 1
            elif role != "admin":
                conn.send({"ok": False, "error": "unknown_role"})
                return
            record["role"] = role
            record["clientId"] = client_id
            conn.send({"ok": True, "role": role, "runtime": {
                "runtimeId": self.runtime_id, "pid": os.getpid(),
                "port": self.port, "ptyRootPid": self.pty_root_pid,
            }})
            while not self.stop_event.is_set():
                message = conn.recv(timeout=1.0)
                if message is None:
                    if not self._socket_alive(client):
                        break
                    continue
                record["commands"] += 1
                if not self._handle(conn, role, message):
                    break
        finally:
            record["disconnectedAt"] = time.time()
            if role == "lease":
                with self.lock:
                    self.lease_client_id = None
                    self.lease_lost_at = time.time()
                self._write_endpoint()
            conn.close()

    @staticmethod
    def _socket_alive(sock: socket.socket) -> bool:
        try:
            sock.setblocking(False)
            try:
                data = sock.recv(1, socket.MSG_PEEK)
                return bool(data)
            except BlockingIOError:
                return True
            except OSError:
                return False
            finally:
                sock.setblocking(True)
        except OSError:
            return False

    def _handle(self, conn: lib.LineConn, role: str, message: dict) -> bool:
        cmd = message.get("cmd")
        if cmd == "state":
            conn.send({"ok": True, "state": self.state()})
        elif cmd == "input":
            data = message.get("data") or ""
            written = self.pty.write(data)
            conn.send({"ok": True, "written": len(data), "ptyWriteResult": written})
        elif cmd == "read":
            start = int(message.get("from") or 0)
            with self.lock:
                if start < self.output_base:
                    start = self.output_base
                text = self.output[start - self.output_base:]
                end = self.output_base + len(self.output)
            conn.send({"ok": True, "from": start, "to": end, "data": text})
        elif cmd == "wait":
            marker = str(message.get("marker") or "")
            start = int(message.get("from") or 0)
            timeout = float(message.get("timeout") or 10.0)
            deadline = time.monotonic() + timeout
            found = False
            while time.monotonic() < deadline:
                with self.lock:
                    lo = max(start, self.output_base)
                    text = self.output[lo - self.output_base:]
                if marker and marker in text:
                    found = True
                    break
                if self.stop_event.is_set():
                    break
                time.sleep(0.05)
            with self.lock:
                lo = max(start, self.output_base)
                text = self.output[lo - self.output_base:]
                end = self.output_base + len(self.output)
            conn.send({"ok": True, "found": found, "from": lo, "to": end, "data": text})
        elif cmd == "snapshot":
            tail = int(message.get("tail") or 2000)
            with self.lock:
                text = self.output[-tail:]
            conn.send({"ok": True, "data": text})
        elif cmd == "detach":
            with self.lock:
                self.detached = True
                self.detached_at = time.time()
            self._write_endpoint({
                "history": [{"event": "detached", "at": self.detached_at}]})
            conn.send({"ok": True, "detached": True, "state": self.state()})
        elif cmd == "stop":
            if role not in {"lease", "admin", "controller"}:
                conn.send({"ok": False, "error": "role_cannot_stop"})
                return True
            self._request_stop("explicit_stop")
            conn.send({"ok": True, "stopping": True, "reason": "explicit_stop"})
            return False
        elif cmd == "ping":
            conn.send({"ok": True})
        else:
            conn.send({"ok": False, "error": f"unknown_command:{cmd}"})
        return True

    # ── lifecycle ───────────────────────────────────────────────────────────
    def _request_stop(self, reason: str) -> None:
        with self.lock:
            if self.stop_reason is None:
                self.stop_reason = reason
        self.stop_event.set()

    def run(self) -> int:
        """Housekeeping loop; returns exit code."""
        self._write_endpoint({"status": "running"})
        while not self.stop_event.is_set():
            time.sleep(0.05)
            if not self.pty.isalive():
                self._request_stop("pty_exit")
                break
            with self.lock:
                detached = self.detached
                lost = self.lease_lost_at
                attached = self.lease_client_id
            if not detached:
                if attached is None and lost is not None:
                    if time.time() - lost >= self.args.lease_grace:
                        self._request_stop("lease_expired")
                        break
                if attached is None and lost is None:
                    if time.time() - self.started_at >= LEASE_NEVER_CONNECTED_TIMEOUT:
                        self._request_stop("lease_never_connected")
                        break
        reason = self.stop_reason or "unknown"
        if reason == "pty_exit":
            # Nothing left to guard; still report and clean up.
            pass
        return self._stop(reason)

    def _stop(self, reason: str) -> int:
        state_before = self.state()
        prev_active = self.job.active_processes()
        self._write_endpoint({
            "status": "stopping", "stopReason": reason,
            "stoppingAt": time.time()})
        if self.job.active_processes():
            self.job.terminate()
        deadline = time.monotonic() + 3.0
        active = self.job.active_processes()
        while active not in (0, None) and time.monotonic() < deadline:
            time.sleep(0.05)
            active = self.job.active_processes()
        try:
            self.pty.cancel_io()
        except Exception:
            pass
        # Best-effort safety sweep of remaining descendants (identity-checked).
        leftovers: list[dict] = []
        for item in lib.descendants([os.getpid()]):
            info = lib.identity(item["pid"])
            if info and lib.kill_verified(item["pid"], info["createTimeFiletime"]):
                leftovers.append({"pid": item["pid"], "exe": item["exe"], "killed": True})
            else:
                leftovers.append({"pid": item["pid"], "exe": item["exe"], "killed": False})
        self.job.close()
        with self.lock:
            output_tail = self.output[-4000:]
        lib.write_json(self.data_root / "runner_final.json", {
            "runtimeId": self.runtime_id,
            "pid": os.getpid(),
            "stopReason": reason,
            "stoppedAt": time.time(),
            "detachedAtStop": self.detached,
            "jobActiveBeforeTerminate": prev_active,
            "jobActiveAfterTerminate": active,
            "leftoverSweep": leftovers,
            "controllerConnects": self.controller_connects,
            "connections": list(self.connections),
            "outputTail": output_tail,
            "stateBeforeStop": state_before,
        })
        print(json.dumps({
            "runner": "stopped", "reason": reason,
            "jobActiveAfter": active, "leftovers": leftovers,
        }), flush=True)
        os._exit(0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--tag", default="probe")
    parser.add_argument("--shell", default="cmd.exe")
    parser.add_argument("--shell-args", default="/q /d")
    parser.add_argument("--cwd", default=None)
    parser.add_argument("--cols", type=int, default=120)
    parser.add_argument("--rows", type=int, default=30)
    parser.add_argument("--lease-grace", type=float, default=2.0)
    parser.add_argument("--fail-inject-assign", action="append", default=None,
                        help="probe-only: simulate guard-job assign failure for "
                             "descendants whose exe name contains this substring")
    args = parser.parse_args()
    runtime = Runtime(args)
    return runtime.run()


if __name__ == "__main__":
    raise SystemExit(main())
