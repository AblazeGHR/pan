"""Focused immutable-source shell and native ownership regression evidence."""
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
proof_root = 'evidence/cp-x2-windows-hidden-20261028/proof'
tracked_proofs = {name: subprocess.check_output(['git', 'show', 'HEAD:' + name], cwd=root)
                  for name in subprocess.check_output(['git', 'ls-files', proof_root], cwd=root, text=True).splitlines()}
groups = {
    'shell': ['tests/test_retirement_rev15_shell_serializer.py',
              'tests/test_retirement_rev14_shell_diagnostic.py',
              'tests/test_retirement_rev14_templates.py', 'tests/test_retirement_rev11_compat.py'],
    'ownership': ['tests/test_platform_process_windows_hidden.py',
                  'tests/test_hidden_job_token.py', 'tests/test_cpx1_creation_edges.py'],
}
metadata = {'sourceSha': sha, 'python': subprocess.check_output([py, '--version'], text=True).strip(), 'checks': []}
exit_code = 0
for name, files in groups.items():
    argv = [py, 'scripts/run_tests_isolated.py', '--', py, '-m', 'pytest', *files,
            '-q', '-s', '--junitxml=' + str(out / (name + '.xml'))]
    started = time.monotonic()
    with (out / (name + '.log')).open('w', encoding='utf-8') as log:
        result = subprocess.run(argv, cwd=root, stdout=log, stderr=subprocess.STDOUT)
    rows = list(ET.parse(out / (name + '.xml')).getroot().iter('testcase'))
    failed = [r.attrib['classname'] + '::' + r.attrib['name'] for r in rows if r.find('failure') is not None or r.find('error') is not None]
    skipped = [r.attrib['classname'] + '::' + r.attrib['name'] for r in rows if r.find('skipped') is not None]
    check = {'name': name, 'argv': argv, 'exit': result.returncode, 'seconds': round(time.monotonic() - started, 3),
             'counts': {'total': len(rows), 'passed': len(rows) - len(failed) - len(skipped), 'failed': len(failed), 'skipped': len(skipped)},
             'failedNodes': failed, 'skippedNodes': skipped}
    metadata['checks'].append(check)
    (out / 'commands.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(check), flush=True)
    exit_code = max(exit_code, result.returncode)
# Existing platform tests use fixed proof paths. Preserve this run separately
# and restore historical tracked bytes before the final whole-tree invariant.
archive = out / 'native-proof'
archive.mkdir(exist_ok=True)
for name, old in tracked_proofs.items():
    path = root / name
    if path.read_bytes() != old:
        (archive / path.name).write_bytes(path.read_bytes())
        path.write_bytes(old)
subprocess.run(['git', 'diff', '--exit-code', 'HEAD'], cwd=root, check=True)
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip() == sha
raise SystemExit(exit_code)
