"""Out-of-process dashboard WebSocket heartbeat probe for the BE-3 E2E.

The E2E measures how responsive the Pan event loop stays while history/list
cold reads run.  Doing that from a thread inside the pytest process mixes in
client-side scheduling noise, so this probe runs as its own process with its
own GIL and writes only ping→pong samples.

Usage: the caller passes ``--out`` for the sample file, ``--ready`` to be
notified once the socket is subscribed, and ``--release`` to stop cleanly.
Samples are flushed after every heartbeat so a killed probe still leaves the
raw evidence behind.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from websockets.sync.client import connect


def _write_samples(path: Path, samples: list[dict]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(samples, ensure_ascii=False), encoding="utf-8")
    # The parent test process reads the current sample file while this child
    # refreshes it. Windows may deny replacement while that reader has the file
    # open; keep the last complete snapshot and retry the atomic replace rather
    # than letting the probe exit with stale heartbeat evidence.
    for attempt in range(20):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.005)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--ready", type=Path, default=None)
    parser.add_argument("--release", type=Path, default=None)
    parser.add_argument("--interval", type=float, default=0.02)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    samples: list[dict] = []
    last_pong: float | None = None
    deadline = time.time() + args.timeout
    with connect(args.url, open_timeout=10, close_timeout=2) as ws:
        if args.ready is not None:
            args.ready.write_text("ready", encoding="ascii")
        while time.time() < deadline:
            if args.release is not None and args.release.exists():
                break
            sent = time.perf_counter()
            ws.send(json.dumps({"type": "ping"}))
            while True:
                raw = ws.recv(timeout=5)
                if json.loads(raw).get("type") == "pong":
                    break
            now = time.perf_counter()
            samples.append({
                "at": time.time(),
                "rttMs": round((now - sent) * 1000.0, 3),
                "gapMs": None if last_pong is None else round(
                    (now - last_pong) * 1000.0, 3),
            })
            last_pong = now
            _write_samples(args.out, samples)
            time.sleep(args.interval)
    _write_samples(args.out, samples)


if __name__ == "__main__":
    main()
