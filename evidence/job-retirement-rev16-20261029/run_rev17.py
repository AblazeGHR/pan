"""Final owned stages at one immutable source; never erase prior run evidence."""
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
groups = {
    'r17-mcp': ['tests/test_retirement_rev16_mcp_stdio.py', 'tests/test_retirement_rev12_mcp.py', 'tests/test_authorization_mcp.py'],
    'r17-behavior': ['tests/test_retirement_rev16_behavior.py', 'tests/test_retirement_rev17_backlog.py', 'tests/test_retirement_rev17_process_notice.py'],
}
meta = {'sourceSha': sha, 'baseline': '4b577633d2d9ffcdc5bf8a44b12836724c18804e',
        'python': subprocess.check_output([py, '--version'], text=True).strip(), 'checks': []}
code = 0
for name, nodes in groups.items():
    argv = [py, 'scripts/run_tests_isolated.py', '--', py, '-m', 'pytest', *nodes,
            '-q', '-s', '--tb=short', '--junitxml=' + str(out / (name + '.xml'))]
    started = time.monotonic()
    with (out / (name + '.log')).open('w', encoding='utf-8') as log:
        result = subprocess.run(argv, cwd=root, stdout=log, stderr=subprocess.STDOUT)
    rows = list(ET.parse(out / (name + '.xml')).getroot().iter('testcase'))
    entries = [{'node': r.attrib['classname'].replace('tests.', 'tests/', 1) + '.py::' + r.attrib['name'],
                'status': 'failed' if r.find('failure') is not None or r.find('error') is not None
                    else 'skipped' if r.find('skipped') is not None else 'passed'} for r in rows]
    check = {'name': name, 'argv': argv, 'exit': result.returncode,
             'seconds': round(time.monotonic() - started, 3), 'total': len(entries),
             'counts': {status: sum(e['status'] == status for e in entries) for status in ['passed', 'failed', 'skipped']},
             'nodes': entries}
    meta['checks'].append(check)
    (out / 'rev17-commands.json').write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in check.items() if k not in {'nodes', 'argv'}}), flush=True)
    code = max(code, result.returncode)
subprocess.run(['git', 'diff', '--exit-code', 'HEAD'], cwd=root, check=True)
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip() == sha
raise SystemExit(code)
