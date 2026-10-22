"""Owned deterministic Job Action command, never an external provider."""
import json
import os
from pathlib import Path
import sys
import time

root=Path(sys.argv[1]).resolve()
assert (root/'fixture.marker').read_text(encoding='utf-8')=='job-action-fixture'
label=sys.argv[2]


def record(kind):
    fd=os.open(root/'trace.jsonl',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    try:os.write(fd,(json.dumps({'kind':kind,'label':label,'pid':os.getpid(),'at':time.time()})+'\n').encode('utf-8'))
    finally:os.close(fd)


record('start')
deadline=time.monotonic()+30
while not (root/'release').exists():
    assert time.monotonic()<deadline,'owned fixture release deadline'
    time.sleep(.02)
record('finish')
print(json.dumps({'ok':True,'label':label}),flush=True)
