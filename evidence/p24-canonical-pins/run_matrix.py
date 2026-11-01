"""Record bounded isolated P24 test invocations without changing old evidence."""
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
label, *nodes = sys.argv[1:]
command = [sys.executable, 'scripts/run_tests_isolated.py', '--', sys.executable,
           '-m', 'pytest', *nodes, '-q', '--tb=short',
           '--junitxml=' + str(OUT / (label + '.xml'))]
with (OUT / (label + '.log')).open('wb') as stream:
    result = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
(OUT / (label + '.json')).write_text(json.dumps({
    'command': command, 'cwd': str(ROOT), 'exit': result.returncode,
}, indent=2) + '\n', encoding='utf-8')
print(label, 'exit', result.returncode)
sys.exit(result.returncode)
