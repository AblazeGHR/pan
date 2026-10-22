"""Deterministic owned Action CLI: fail once, then wait at an explicit barrier."""
import json
import os
from pathlib import Path
import sys
import time

root = Path(sys.argv[1]).resolve()
assert (root / 'fixture.marker').read_text(encoding='utf-8') == 'retry-fixture'
key = str(int(sys.argv[2]))
counter = root / (key + '.count')
attempt = int(counter.read_text(encoding='utf-8')) + 1 if counter.exists() else 1
counter.write_text(str(attempt), encoding='utf-8')


def record(kind):
    row = dict(kind=kind, key=key, attempt=attempt, pid=os.getpid(), at=time.time())
    fd = os.open(root / 'trace.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, (json.dumps(row) + '\n').encode('utf-8'))
    finally:
        os.close(fd)


record('start')
if attempt == 1:
    record('failed')
    sys.exit(7)
deadline = time.monotonic() + 15
while not (root / 'release').exists():
    assert time.monotonic() < deadline, 'fixture barrier timed out'
    time.sleep(.02)
record('succeeded')
print(json.dumps({'ok': True, 'key': key, 'attempt': attempt}), flush=True)
