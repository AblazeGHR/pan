"""Owned test gate after genuine bootstrap proof and before runner claim."""
import argparse
import json
import os
from pathlib import Path
import time

import psutil
import sys

from packages.core.platform.execution_host import read_bootstrap_receipt
from packages.jobs.independent_execution_runner import run

parser = argparse.ArgumentParser()
parser.add_argument("--root", required=True)
parser.add_argument("--effect-id", required=True)
parser.add_argument("--ready", required=True)
parser.add_argument("--release", required=True)
parser.add_argument("--abort", required=True)
parser.add_argument("--bootstrap-receipt", required=True)
parser.add_argument("--bootstrap-nonce", required=True)
args = parser.parse_args()
receipt = read_bootstrap_receipt(args.bootstrap_receipt, args.bootstrap_nonce)
ready = Path(args.ready)
temporary = ready.with_suffix(".tmp")
temporary.write_text(json.dumps({"pid": os.getpid(), "birth": psutil.Process().create_time(),
    "stage": "after_bootstrap_before_claim", "bootstrapExited": receipt["exited"],
    "bootstrapPid": receipt["bootstrapPid"], "bootstrapExitCode": receipt["exitCode"],
    "prefix": sys.prefix, "basePrefix": sys.base_prefix, "executable": sys.executable}), encoding="utf-8")
temporary.replace(ready)
deadline = time.monotonic() + 60
while not Path(args.release).exists():
    if Path(args.abort).exists() or time.monotonic() >= deadline:
        raise SystemExit(1)
    time.sleep(.02)
raise SystemExit(run(args.root, args.effect_id, receipt))
