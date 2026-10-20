"""Subprocess helper for multi-process session-error-log tests."""

from __future__ import annotations

import json
import os
import sys
import time


def main() -> int:
    path = sys.argv[1]
    mode = sys.argv[2]
    writer = sys.argv[3]
    count = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    chars = int(sys.argv[5]) if len(sys.argv) > 5 else 0
    gate = sys.argv[6] if len(sys.argv) > 6 else ""

    os.environ["PAN_SESSION_ERROR_LOG_PATH"] = path
    from packages.core import session_error_log

    if mode == "partial":
        # Simulate a writer that crashed after a half line without a newline.
        with open(path, "ab") as handle:
            handle.write(b'{"partial":')
            handle.flush()
        return 0

    def record(index: int, tag: str) -> None:
        session_error_log.record_session_error(
            kind="terminal_error",
            error_text=("中" * chars) + f"|{writer}|{index}|{tag}",
            error_text_source="pan.runtime",
            terminal=True,
            session_id=f"ses_{writer}",
            session_name=writer,
            worker_id=f"w_{writer}",
            generation=1,
            task_seq=index,
            dedupe_token=f"{writer}-{index}-{tag}",
        )

    if mode == "gated":
        record(0, "A1")
        while gate and not os.path.exists(gate):
            time.sleep(0.01)
        record(1, "A2")
        return 0

    for index in range(count):
        record(index, "W")
    print(json.dumps(session_error_log.stats(), ensure_ascii=True,
                     separators=(",", ":")), file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
