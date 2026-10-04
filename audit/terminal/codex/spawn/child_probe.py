"""Test child for the ConPTY atomic-spawn spike. Self-created by the driver only.

Modes of evidence it can produce:
  * an immediate on-start marker file -- proves whether the process executed at all
    (used to show that a CREATE_SUSPENDED child has *not* run);
  * UTF-8 output including non-ASCII text, written to the ConPTY output stream;
  * the console size reported by the child itself -- proves ResizePseudoConsole
    reached the hosted application and not just our own struct;
  * a tail marker written immediately before exit -- proves the last output is
    still drainable;
  * an optional descendant process that outlives this one -- used to check that
    the guard job kills the whole tree.

Everything is written into a caller-provided temp directory; the child never
looks at anything outside its own scratch root.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time


def write_marker(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def console_size() -> str:
    try:
        size = os.get_terminal_size()
        return f"{size.columns}x{size.lines}"
    except Exception as exc:
        return f"unavailable({exc.__class__.__name__})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--marker", required=True, help="file appended to the instant this process runs")
    ap.add_argument("--exit-code", type=int, default=0)
    ap.add_argument("--hold", type=float, default=0.0, help="seconds to stay alive before finishing")
    ap.add_argument("--grandchild-marker", default=None,
                    help="if set, spawn a long-lived descendant and record its identity there")
    ap.add_argument("--grandchild-hold", type=float, default=120.0)
    args = ap.parse_args()

    # First observable action: if the process was never resumed, this never runs.
    write_marker(args.marker, f"STARTED pid={os.getpid()} t={time.time():.3f}")
    print(f"CHILD_STARTED pid={os.getpid()}", flush=True)

    if args.grandchild_marker:
        code = (
            "import os,sys,time;"
            f"open({args.grandchild_marker!r},'a',encoding='utf-8').write('GRANDCHILD pid=%d\\n' % os.getpid());"
            "sys.stdout.flush();"
            f"time.sleep({args.grandchild_hold!r})"
        )
        proc = subprocess.Popen([sys.executable, "-c", code], close_fds=True)
        write_marker(args.marker, f"SPAWNED_GRANDCHILD pid={proc.pid}")
        print(f"GRANDCHILD pid={proc.pid}", flush=True)

    # Non-ASCII round trip through the pseudoconsole (UTF-8 on the wire).
    print("UNICODE 中文测试 ✓ αβγ — utf8-roundtrip", flush=True)

    # Console size as seen by the hosted application; re-read after each resize.
    print(f"CONSOLE_SIZE {console_size()}", flush=True)

    if args.hold:
        # Allow the driver to resize while we are running, then report again.
        deadline = time.time() + args.hold
        last = console_size()
        while time.time() < deadline:
            time.sleep(0.2)
            cur = console_size()
            if cur != last:
                print(f"CONSOLE_SIZE {cur}", flush=True)
                last = cur
        print(f"CONSOLE_SIZE_FINAL {console_size()}", flush=True)

    print("TAIL_MARKER end-of-output", flush=True)
    return args.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
