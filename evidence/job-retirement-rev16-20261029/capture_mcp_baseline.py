"""Run the exact new stdio assertion against the baseline production server."""
import hashlib
import json
from pathlib import Path
import subprocess

out = Path(__file__).resolve().parent
root = out.parents[1]
source = root / 'packages/mcp/server.py'
current = source.read_bytes()
baseline = '4b577633d2d9ffcdc5bf8a44b12836724c18804e'
old = subprocess.check_output(['git', 'show', baseline + ':packages/mcp/server.py'], cwd=root)
py = 'D:/project/Pan-main/.venv/Scripts/python.exe'
argv = [py, 'scripts/run_tests_isolated.py', '--', py, '-m', 'pytest',
    'tests/test_retirement_rev16_mcp_stdio.py', '-q', '--tb=short', '--junitxml=' + str(out / 'baseline-mcp.xml')]
try:
    source.write_bytes(old)
    with (out / 'baseline-mcp.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(argv, cwd=root, stdout=log, stderr=subprocess.STDOUT)
finally:
    source.write_bytes(current)
assert source.read_bytes() == current
(out / 'baseline-mcp-command.json').write_text(json.dumps({'baselineServerSha': baseline,
    'serverSha256': hashlib.sha256(old).hexdigest(), 'argv': argv, 'exit': result.returncode,
    'restoredCurrentBytes': True, 'scope': 'Real stdio child uses baseline server.py; new exact dedicated test retained'}, indent=2) + '\n', encoding='utf-8')
print('baseline actual exit=' + str(result.returncode))
