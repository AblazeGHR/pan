"""Separate owned controller process for lifecycle crash regression."""
import json
from pathlib import Path
import sys
import time
import os
import psutil

from packages.jobs.independent_executor import ExecutionHost, ExecutionStore, launch_execution

store = ExecutionStore(Path(sys.argv[1]))
request = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
stage, marker = sys.argv[3], Path(sys.argv[4])

def publish():
    marker.write_text(json.dumps({"pid": os.getpid(), "birth": psutil.Process().create_time(),
                                  "stage": stage}), encoding="utf-8")

def launch_then_pause(root, effect):
    launch_execution(root, effect)
    if stage == "before-submit-return":
        publish()
        while True:
            time.sleep(.1)

ExecutionHost(store, launch=launch_then_pause).submit_execution(request)
publish()
while True:
    time.sleep(.1)
