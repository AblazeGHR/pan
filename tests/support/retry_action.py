"""Deterministic owned Action CLI: fail once, then wait at an explicit barrier."""
import json
import os
from pathlib import Path
import sys
import time
import uuid

root = Path(sys.argv[1]).resolve()
assert (root / 'fixture.marker').read_text(encoding='utf-8') == 'retry-fixture'
key = str(int(sys.argv[2]))
counter = root / (key + '.count')
attempt = int(counter.read_text(encoding='utf-8')) + 1 if counter.exists() else 1
counter.write_text(str(attempt), encoding='utf-8')


def record(kind):
    row = dict(kind=kind, key=key, attempt=attempt, pid=os.getpid(), at=time.time())
    # Each parallel command owns its event files. A shared append descriptor
    # is not a cross-process record lock on Windows.
    trace = root / 'trace'
    trace.mkdir(exist_ok=True)
    # Unique event names retain duplicate executions as extra rows; they must
    # fail the unchanged count assertions rather than overwrite prior proof.
    path = trace / f'{key}-{attempt}-{kind}-{uuid.uuid4().hex}.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(row), encoding='utf-8')
    temporary.replace(path)


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
