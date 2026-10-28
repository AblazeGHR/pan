"""Owned production launcher; observe default Adapter calls without replacing them."""
import json
import os
from pathlib import Path
import runpy
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
data = Path(os.environ['PAN_E2E_DATA_ROOT']).resolve()

# Instrumentation is confined to this owned test process. Delegate unchanged
# unavailable behavior, recording no arguments, accounts, credentials or payloads.
from packages.automation.quota_policy import Adapter


def observed(name, original):
    async def call(self, *args, **kwargs):
        with (data / 'quota-default-calls.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'method': name, 'pid': os.getpid(), 'at': time.time()}) + '\n')
        return await original(self, *args, **kwargs)
    return call


for method in ('query', 'inventory', 'perform'):
    setattr(Adapter, method, observed(method, getattr(Adapter, method)))

runpy.run_path(str(ROOT / 'tests/support/automation_http_server.py'), run_name='__main__')
