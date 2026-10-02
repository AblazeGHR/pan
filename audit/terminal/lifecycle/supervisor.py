"""Probe host: minimal stand-in for the Pan service process.

Owns one runner (spawned detached, mirroring
``packages/core/background_jobs._spawn_background_runner``), holds the
supervisor lease, and dies in one of three ways on driver command:

  STOP_AND_EXIT  graceful host shutdown: explicit ``stop`` lease command, wait
                 for the runner to die, exit 0  (``worker.shutdown_all`` shape)
  EXIT_KEEP      close sockets without stopping the runner, exit 0
                 (used after an explicit detach: host exit must not kill it)
  CRASH          os._exit(17) with no cleanup at all (hard process death;
                 the kernel closes the lease socket exactly like a crash)

This process is probe-owned.  It never touches the real Pan service, its
sessions, or any process it did not spawn itself.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import probe_lib as lib  # noqa: E402

RUNNER = Path(__file__).resolve().parent / "runner.py"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--tag", default="probe")
    parser.add_argument("--lease-grace", type=float, default=2.0)
    parser.add_argument("--max-lifetime", type=float, default=300.0)
    parser.add_argument("--log", default=None)
    args = parser.parse_args()
    root = Path(args.data_root)
    root.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.log) if args.log else root / "runner_stdout.log"

    python, py_env = lib.probe_python()
    with open(log_path, "ab") as log:
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | getattr(
            subprocess, "DETACHED_PROCESS", 0)
        runner = subprocess.Popen(
            [python, str(RUNNER), "--data-root", str(root), "--tag", args.tag,
             "--lease-grace", str(args.lease_grace)],
            cwd=str(Path(__file__).resolve().parent),
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            close_fds=True, creationflags=creationflags, env=py_env,
        )
    runner_identity = lib.identity(runner.pid)

    try:
        endpoint = lib.read_endpoint(root, timeout=15.0)
    except TimeoutError as exc:
        lib.write_json(root / "supervisor_exit.json", {
            "mode": "startup_failure", "error": str(exc), "runnerPid": runner.pid,
        })
        return 4
    if endpoint.get("pid") != runner.pid:
        lib.write_json(root / "supervisor_exit.json", {
            "mode": "startup_failure", "error": "endpoint pid mismatch",
            "endpointPid": endpoint.get("pid"), "runnerPid": runner.pid,
        })
        return 4

    conn, endpoint = lib.connect_runtime(root, endpoint["token"], "lease",
                                         f"supervisor-{os.getpid()}")
    # The lease socket is merely held open (no reader thread: the single stop
    # request below must not race another reader on the same socket).

    lib.write_json(root / "supervisor_ready.json", {
        "pid": os.getpid(),
        "processCreatedAtFiletime": lib.process_create_time_raw(os.getpid()),
        "processCreatedAt": lib.process_create_time(os.getpid()),
        "runnerPid": runner.pid,
        "runnerIdentity": runner_identity,
        "endpoint": {k: v for k, v in endpoint.items() if k != "token"},
        "at": time.time(),
    })

    def stop_and_exit(code: int, mode: str) -> int:
        ack = conn.request({"cmd": "stop"}, timeout=5.0)
        died, waited = lib.wait_until(
            lambda: not lib.identity_matches(runner.pid, runner_identity["createTimeFiletime"]),
            timeout=10.0)
        leave = lib.tree_identity([runner.pid])
        lib.write_json(root / "supervisor_exit.json", {
            "mode": mode, "exitCode": code, "stopAck": ack,
            "runnerPid": runner.pid, "runnerDied": died,
            "runnerDeadAfterSeconds": round(waited, 3),
            "runnerTreeAtExit": list(leave.values()),
        })
        return code

    deadline = time.monotonic() + args.max_lifetime
    while True:
        if time.monotonic() > deadline:
            return stop_and_exit(6, "max_lifetime")
        line = sys.stdin.readline()
        if line == "":
            # Driver died without a command: fail-safe graceful stop.
            return stop_and_exit(5, "stdin_eof")
        command = line.strip()
        if command == "STOP_AND_EXIT":
            return stop_and_exit(0, "normal_stop")
        if command == "EXIT_KEEP":
            lib.write_json(root / "supervisor_exit.json", {
                "mode": "exit_keep", "exitCode": 0, "runnerPid": runner.pid,
                "note": "host exited without stopping the runtime (external detach)",
            })
            return 0
        if command == "CRASH":
            lib.write_json(root / "supervisor_crash.json", {
                "mode": "crash", "runnerPid": runner.pid,
                "note": "os._exit(17); no cleanup, lease socket closed by kernel",
            })
            sys.stdout.flush()
            os._exit(17)
        # ignore unknown lines


if __name__ == "__main__":
    raise SystemExit(main())
