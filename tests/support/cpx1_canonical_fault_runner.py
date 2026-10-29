"""Run the real canonical runner with one controlled owner wait fault."""
import sys
from pathlib import Path

from packages.core.platform import process
from packages.jobs.independent_execution_runner import run

root, effect, tree_root, fault = sys.argv[1:]
if fault == "wait-error":
    real = process.default_process_owner()

    class FaultOwner:
        def __getattr__(self, name):
            return getattr(real, name)

        def wait(self, handle, *, timeout):
            if all((Path(tree_root) / f"tree-{i}.json").exists() for i in range(3)):
                raise OSError("injected owned wait failure")
            return real.wait(handle, timeout=timeout)

    process.default_process_owner = lambda: FaultOwner()

raise SystemExit(run(root, effect))
