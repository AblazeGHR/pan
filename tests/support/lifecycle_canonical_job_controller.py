"""Real FinalService dispatch with a test gate before the runner's claim."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

import psutil

from packages.core.platform.execution_host import launch_detached
from packages.jobs.final_api import CanonicalUserAuth
from packages.jobs.final_service import FinalService
from packages.jobs.independent_executor import ExecutionHost

root, auth_root, runtime, workdir = map(Path, sys.argv[1:5])
runner = Path(__file__).with_name("lifecycle_prepublication_runner.py")
original_init = ExecutionHost.__init__

def gated_launch(execution_root, identity):
    return launch_detached([str(runtime), str(runner), "--root", str(execution_root),
        "--effect-id", identity, "--ready", str(workdir / "runner-ready.json"),
        "--release", str(workdir / "runner-release"), "--abort", str(workdir / "runner-abort")],
        receipt_root=execution_root)

def own_host(self, store, launch=None):
    original_init(self, store, launch=gated_launch)

ExecutionHost.__init__ = own_host
service = FinalService(root, CanonicalUserAuth(auth_root))
asyncio.run(service.dispatch_once())
marker = workdir / "controller-ready.json"
marker.write_text(json.dumps({"pid": os.getpid(), "birth": psutil.Process().create_time(),
                              "stage": "dispatch_returned"}), encoding="utf-8")
while True:
    time.sleep(.1)
