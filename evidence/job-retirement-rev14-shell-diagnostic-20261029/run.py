"""Reproduce the retained red and a real owned creation-edge comparison."""
import json
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET

out = Path(__file__).resolve().parent
root = out.parents[1]
py = 'D:/project/Pan-main/.venv/Scripts/python.exe'
sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
subprocess.run(['git', 'diff', '--exit-code', 'HEAD'], cwd=root, check=True)
argv = [py, 'scripts/run_tests_isolated.py', '--', py, '-m', 'pytest',
    'tests/test_retirement_rev14_shell_diagnostic.py',
    'tests/test_retirement_rev14_templates.py::test_production_shell_form_real_process_exit_and_original_lookup',
    '-q', '-s', '--junitxml=' + str(out / 'focused.xml')]
start = time.monotonic()
with (out / 'focused.log').open('w', encoding='utf-8') as stream:
    result = subprocess.run(argv, cwd=root, stdout=stream, stderr=subprocess.STDOUT)
rows = list(ET.parse(out / 'focused.xml').getroot().iter('testcase'))
tests = [{'node': r.attrib['classname'] + '::' + r.attrib['name'],
          'result': 'failed' if r.find('failure') is not None else 'passed'} for r in rows]
meta = {'testedSourceSha': sha, 'argv': argv, 'exit': result.returncode,
        'seconds': round(time.monotonic() - start, 3), 'nodes': tests,
        'python': subprocess.check_output([py, '--version'], text=True).strip(),
        'claim': 'Diagnostic pass confirms observed mismatch, not capability success; original positive red remains failed'}
(out / 'command.json').write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
subprocess.run(['git', 'diff', '--exit-code', 'HEAD'], cwd=root, check=True)
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip() == sha
print(json.dumps(meta))
raise SystemExit(result.returncode)
