"""Record exact clean CI source and fresh production bundle digests."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
paths = ['packages/web/dist/index.html', 'packages/web/pnpm-lock.yaml', 'minimal-requirements.txt', 'packages/qq/requirements.txt']
hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in paths}
dist = root / 'packages/web/dist'
assets = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
          for p in sorted(dist.rglob('*')) if p.is_file()}
if not assets:
    raise RuntimeError('fresh production bundle missing')
source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
versions = {}
for name, argv in {'python': [sys.executable, '--version'], 'node': ['node', '--version'],
                   'pnpm': ['pnpm', '--version']}.items():
    versions[name] = subprocess.check_output(argv, cwd=root, text=True).strip()
(out / 'provenance.json').write_text(json.dumps({'sourceSha': source, 'sha256': hashes, 'bundleSha256': assets,
    'versions': versions}, indent=2) + '\n', encoding='utf-8')
