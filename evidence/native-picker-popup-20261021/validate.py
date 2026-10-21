"""Targeted validation; no installs, shared mutations or service requests."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / 'packages/web'
OUT = Path(__file__).parent / 'validation'
OUT.mkdir(exist_ok=True)
node = shutil.which('node')
commands = [
 ('backend', ROOT, [sys.executable, 'scripts/run_tests_isolated.py', '--', sys.executable, '-m', 'pytest', 'tests/test_native_file_picker.py', 'tests/test_web_directory_picker.py', 'tests/test_attachment_parts_protocol.py', '-o', 'addopts=', '-q']),
 ('frontend', WEB, [node, 'node_modules/vitest/vitest.mjs', 'run', 'src/components/chat/ServerAttachmentPicker.test.tsx', 'src/components/chat/InputRow.test.tsx']),
 ('lint', WEB, [node, 'node_modules/eslint/bin/eslint.js', 'src/components/chat/ServerAttachmentPicker.tsx', 'src/components/chat/ServerAttachmentPicker.test.tsx', 'src/components/chat/InputRow.test.tsx']),
 ('typecheck', WEB, [node, 'node_modules/typescript/bin/tsc', '-b']),
 ('build', WEB, [node, 'node_modules/vite/bin/vite.js', 'build']),
 ('assets', WEB, [node, 'e2e/verify-precompressed-assets.mjs']),
 ('diff', ROOT, ['git', 'diff', '--check']),
]
results=[]
for name, cwd, argv in commands:
 start=time.monotonic()
 with (OUT / (name + '.log')).open('wb') as log:
  result=subprocess.run(argv,cwd=cwd,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
 results.append({'name':name,'cwd':str(cwd),'argv':argv,'exitCode':result.returncode,'seconds':round(time.monotonic()-start,2)})
 print(name, result.returncode, flush=True)
 (OUT / 'results.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
 if result.returncode: sys.exit(result.returncode)
