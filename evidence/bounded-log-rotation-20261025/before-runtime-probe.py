"""Baseline mechanism probe: a launcher-style inherited stdout descriptor.

Parent opens pan-console.out.log in "a" mode exactly like the old launcher did,
passes it as the child's stdout, then closes its own copy. The child keeps the
descriptor and writes past any size limit. The parent's cleanup rename then fails
while that descriptor is alive, which is why the old pipeline could only ever
append to one ever-growing file.
"""
import os, subprocess, sys, tempfile
from pathlib import Path

work = Path(tempfile.mkdtemp(prefix="pan-baseline-probe-"))
log = work / "pan-console.out.log"
limit = 64 * 1024
with log.open("a", encoding="utf-8") as handle:
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import sys\n"
         "for i in range(4000):\n"
         "    print('baseline-line-%05d-' % i + 'x'*40, flush=True)\n"
         "import time; time.sleep(2.0)\n"],
        stdout=handle, stderr=handle)
print("size_after_launch_handles_closed", log.stat().st_size)
try:
    os.replace(log, log.with_name(log.name + ".1"))
    print("rename_while_child_holds_fd", "succeeded")
except OSError as exc:
    print("rename_while_child_holds_fd", type(exc).__name__, exc.errno)
print("size_while_child_alive", log.stat().st_size, "limit", limit,
      "over_limit", log.stat().st_size > limit)
child.wait(timeout=60)
print("backups_after_child_exit", [p.name for p in work.iterdir() if ".1" in p.name])
print("final_size", log.stat().st_size, "unbounded", log.stat().st_size > limit)
