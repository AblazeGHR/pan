import json
import os
from pathlib import Path
import shutil
import subprocess
import time

root = Path(__file__).resolve().parents[2]
web = root / 'packages/web'
source = Path('D:/project/Pan-main/packages/web/node_modules')
dest = web / 'node_modules'
links = []
files = 0

def copy_dir(src, dst):
    global files
    dst.mkdir(parents=True, exist_ok=True)
    for entry in os.scandir(src):
        p, q = Path(entry.path), dst / entry.name
        if p.is_junction() or p.is_symlink():
            target = p.resolve()
            relative = target.relative_to(source)
            links.append((q, dest / relative))
        elif entry.is_dir(follow_symlinks=False):
            copy_dir(p, q)
        elif not q.exists():
            shutil.copy2(p, q)
            files += 1

started = time.perf_counter()
copy_dir(source, dest)
for link, target in links:
    if not link.exists():
        if not target.is_relative_to(dest):
            raise RuntimeError('Refusing shared dependency link')
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)], capture_output=True)
        if result.returncode:
            raise RuntimeError(result.stderr.decode(errors='replace'))
print(json.dumps({'isolated_dependency_copy': {'files': files, 'local_links': len(links),
    'seconds': round(time.perf_counter()-started, 2), 'shared_links': 0}}), flush=True)

# Canonical dependencies predate the terminal manifest update. Copy the two
# already-present exact-version package bodies from practical; no install and
# no dependency link points outside this worktree.
for package, version in [('xterm', '6.0.0'), ('addon-fit', '0.11.0')]:
    existing = Path('D:/project/Pan/packages/web/node_modules/@xterm') / package
    meta = json.loads((existing / 'package.json').read_text(encoding='utf-8'))
    if meta['version'] != version:
        raise RuntimeError('Existing xterm version mismatch')
    shutil.copytree(existing.resolve(), dest / '@xterm' / package, dirs_exist_ok=True)
print('Copied exact existing xterm versions into isolated dependency tree', flush=True)

commands = [
    ['node', 'node_modules/eslint/bin/eslint.js', 'src/components/chat/SettingsPopover.tsx', 'src/components/layout/TopBar.tsx',
     'src/hooks/useWebSocket.ts', 'src/services/api.ts', 'src/stores/workerStore.ts', 'src/types/index.ts'],
    ['node', 'node_modules/typescript/bin/tsc', '-b'],
    ['node', 'node_modules/vite/bin/vite.js', 'build'],
    ['node', 'e2e/verify-precompressed-assets.mjs'],
]
results = []
for command in commands:
    started = time.perf_counter()
    result = subprocess.run(command, cwd=web)
    row = {'argv': command, 'exit_code': result.returncode,
           'seconds': round(time.perf_counter()-started, 2)}
    results.append(row)
    print(json.dumps(row), flush=True)
    if result.returncode:
        break
(Path(__file__).parent / 'frontend-results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
raise SystemExit(results[-1]['exit_code'])
