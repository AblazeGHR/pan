"""Background counter program started *inside* the PTY.

Evidence that a running program keeps running across controller disconnects
and detach: it appends a monotonically increasing counter line to a file.

Usage: tick.py <out_file> [interval_seconds]
"""
from __future__ import annotations

import os
import sys
import time


def main() -> int:
    out = sys.argv[1]
    interval = float(sys.argv[2]) if len(sys.argv) > 2 else 0.15
    pid = os.getpid()
    with open(out, "a", encoding="utf-8") as handle:
        handle.write(f"STARTED {pid} {time.time():.3f}\n")
        handle.flush()
        count = 0
        while True:
            count += 1
            handle.write(f"TICK {pid} {count} {time.time():.3f}\n")
            handle.flush()
            time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
