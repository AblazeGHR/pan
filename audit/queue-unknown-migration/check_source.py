"""Read-only source hash verification; emits no source names or content."""
import hashlib
import json
from pathlib import Path
import subprocess
from collections import Counter

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
GATE = ROOT / 'audit/practical-data-gate'
PRIVATE = Path((GATE / 'private-root.txt').read_text(encoding='utf-8').strip())
SOURCE = Path('D:/project/Pan')


def sha(path, size=None):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while size is None or size > 0:
            data = stream.read(1048576 if size is None else min(size, 1048576))
            if not data:
                break
            result.update(data)
            if size is not None:
                size -= len(data)
    return result.hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, encoding='utf-8').strip()


rows = json.loads((PRIVATE / 'manifest.json').read_text(encoding='utf-8'))
counts = Counter()
changes = []
for row in rows:
    if 'sha256' not in row:
        continue
    path = SOURCE / 'data' / row['relative']
    if not path.exists():
        counts['missing'] += 1
        continue
    stat = path.stat()
    prefix_equal = sha(path, row['copiedBytes']) == row['sha256']
    same = (prefix_equal and stat.st_size == row['sourceBytesAfter']
            and stat.st_mtime_ns == row['sourceMtimeAfter'])
    counts['unchanged' if same else 'changedSinceSample'] += 1
    if not same:
        changes.append({'key': row['key'], 'suffix': path.suffix,
                        'samplePrefixEqualNow': prefix_equal,
                        'sizeDelta': stat.st_size - row['sourceBytesAfter']})

snapshot = json.loads((GATE / 'snapshot.json').read_text(encoding='utf-8'))
result = {'sourcePracticalHead': git('-C', str(SOURCE), 'rev-parse', 'HEAD'),
          'mainRef': git('rev-parse', 'main'), 'practicalRef': git('rev-parse', 'practical'),
          'sourceTrackedStatus': git('-C', str(SOURCE), 'status', '--porcelain=v1', '--untracked-files=no'),
          'sourceConfigHashUnchanged': sha(SOURCE / 'config.json') == snapshot['config']['configSha256'],
          'sampleFilePostcheck': dict(counts), 'activeSourceChanges': changes,
          'sourceOpenModes': 'rb only; source stat; git read commands',
          'limits': 'active source; no global transaction snapshot; changes not attributed by hashes alone'}
(OUT / 'source-after.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
