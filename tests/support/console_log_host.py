"""Subprocess host that exercises the real console stdout/stderr relay.

Used by ``tests/test_console_log_rotation.py`` both directly (no launcher shim:
it performs the same two stages itself) and as the ``main.py`` of a temporary
checkout driven by ``launcher._start_main`` (the shim already did the takeover,
so the install call here is a no-op).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def main() -> int:
    from packages.core import console_bootstrap, console_logs

    limit = os.environ.get("PAN_TEST_CONSOLE_MAX_BYTES")
    backups = os.environ.get("PAN_TEST_CONSOLE_BACKUPS", "3")
    config = {"logging": {"console_max_bytes": int(limit or 20 * 1024 * 1024),
                          "console_backup_count": int(backups)}}
    if not console_logs.status()["active"]:
        # Standalone run: mirror the managed entry (bootstrap opens the files,
        # then the relay takes them over).
        console_bootstrap.open_console_logs()
        console_logs.install(config=config)
    if not console_logs.status()["active"]:
        print("relay-not-installed", file=sys.stderr, flush=True)
        return 2

    payload = "x" * int(os.environ.get("PAN_TEST_CONSOLE_PAYLOAD", "48"))
    count = int(os.environ.get("PAN_TEST_CONSOLE_LINES", "10"))
    for index in range(count):
        print(f"host-{index:05d}-{payload}", flush=True)
    print("host-stderr-line", file=sys.stderr, flush=True)

    # The grandchild inherits the OS standard handles; its output must still be
    # relayed into the same file (that is the behaviour a launcher-started QQ or
    # provider child relies on).
    child = subprocess.run(
        [sys.executable, "-c", "print('child-stdout-line', flush=True)"],
        stdin=subprocess.DEVNULL, timeout=120)
    if child.returncode != 0:
        print("grandchild-failed", file=sys.stderr, flush=True)
        return 3
    state = console_logs.status()
    print("relay-status=" + json.dumps({
        "active": state["active"],
        "logs": {name: {"rotations": item["rotations"],
                        "degraded": item["degraded"],
                        "sizeBytes": item["sizeBytes"]}
                 for name, item in state["logs"].items()},
    }, ensure_ascii=False), flush=True)
    console_logs.shutdown()
    time.sleep(float(os.environ.get("PAN_TEST_CONSOLE_SLEEP", "0")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
