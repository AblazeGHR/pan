"""Run the focused browser-code regression, typecheck and production build."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[1]
web = root / 'packages/web'
steps = [
    ['exec', 'vitest', 'run', 'src/services/api.test.ts', 'src/components/chat/InputRow.test.tsx',
     'src/components/chat/SendQueuePanel.pendingOnly.test.tsx', '--reporter=dot'],
    ['exec', 'tsc', '-b'],
    ['run', 'build'],
]
results = []
for args in steps:
    started = time.monotonic()
    proc = subprocess.run(['cmd', '/d', '/c', 'pnpm', *args] if os.name == 'nt' else ['pnpm', *args],
                          cwd=web, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    results.append({'argv': ['pnpm', *args], 'exitCode': proc.returncode,
                    'seconds': round(time.monotonic() - started, 2)})
    if proc.returncode:
        print(json.dumps(results), flush=True)
        sys.exit(proc.returncode)
print(json.dumps(results), flush=True)
