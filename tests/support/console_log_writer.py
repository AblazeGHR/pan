"""Subprocess writer for multi-process console-log rotation tests."""

from __future__ import annotations

import sys

from packages.core.console_logs import RotatingByteLog


def main() -> int:
    path, tag, count, payload_chars, max_bytes, backups = sys.argv[1:7]
    writer = RotatingByteLog(
        path, max_bytes=int(max_bytes), backup_count=int(backups), label="test")
    payload = "y" * int(payload_chars)
    for index in range(int(count)):
        writer.write(f"{tag}-{index:05d}-{payload}\n".encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
