"""Short foreground syntax/type/build validation; no suite or service calls."""
import json
from pathlib import Path
import py_compile
import subprocess
import time

root = Path(__file__).resolve().parents[2]
audit = Path(__file__).parent
scratch = root / '_e2e_tmp/worker-delayed-restart/combined-compile'
scratch.mkdir(parents=True, exist_ok=True)
results = []
for relative in ['packages/core/worker.py', 'packages/web/server.py', 'packages/mcp/server.py']:
    started = time.perf_counter()
    py_compile.compile(str(root / relative), cfile=str(scratch / (relative.replace('/', '-') + 'c')), doraise=True)
    results.append({'check': 'py_compile', 'file': relative, 'exit_code': 0,
                    'seconds': round(time.perf_counter() - started, 3)})
commands = [
    ['node', 'node_modules/typescript/bin/tsc', '-b'],
    ['node', 'node_modules/vite/bin/vite.js', 'build'],
    ['node', 'e2e/verify-precompressed-assets.mjs'],
]
for command in commands:
    started = time.perf_counter()
    completed = subprocess.run(command, cwd=root / 'packages/web')
    row = {'argv': command, 'exit_code': completed.returncode,
           'seconds': round(time.perf_counter() - started, 3)}
    results.append(row)
    print(json.dumps(row), flush=True)
    if completed.returncode:
        break
(audit / 'combination-results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
raise SystemExit(results[-1]['exit_code'])
