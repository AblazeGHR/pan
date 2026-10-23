"""Record command provenance and retain byte-exact logs before whitespace cleanup."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
PY = 'D:/project/Pan-main/.venv/Scripts/python.exe'
prefix = [PY, 'scripts/run_tests_isolated.py', '--', PY, '-m', 'pytest']
tests = ['tests/test_locker_independent_review.py', 'tests/test_interaction_runtime.py',
         'tests/test_interaction_cards.py', 'tests/test_user_locker_lifecycle.py']
commands = [
    ('red', ['tests/test_locker_independent_review.py', '-o', 'addopts=', '-q', '-s'], 1,
     'e863eb16a5f80d637842e7de2bf0a52a5bf0365a + initial independent 4-case tests'),
    ('green', tests + ['-k', 'not fixed_choice_must_not_execute_changed_action', '-o', 'addopts=', '-q'], 0,
     'Final receipt repair + tests before adding copy-migration case'),
    ('final', tests + ['-o', 'addopts=', '-q', '-s'], 1,
     '939ea97c00abed1c0f43700b90c221247028a4da'),
    ('http', ['tests/test_locker_independent_http.py', '-o', 'addopts=', '-q'], 0,
     'Final product and HTTP test bytes before source commit; later change was migration test only'),
]
rows = []
for name, args, code, source in commands:
    suite = ET.parse(OUT/(name+'.xml')).find('.//testsuite')
    rows.append({'name': name, 'cwd': str(ROOT), 'argv': prefix + args + ['--junitxml=audit/locker-independent/'+name+'.xml'],
                 'exitCode': code, 'source': source, 'junit': dict(suite.attrib)})
rows.append({'name': 'ui', 'cwd': str(ROOT/'packages/web'), 'argv': [
    'D:/node_npm/node_global/pnpm.cmd', 'exec', 'vitest', 'run', 'src/views/lockerModel.auth.test.ts',
    'src/views/LockerView.test.tsx', '--reporter=default', '--reporter=junit', '--outputFile='+str(OUT/'ui.xml')],
    'exitCode': 0, 'source': '939ea97c00abed1c0f43700b90c221247028a4da'})
for name in ['http-fixture-failure', 'http-projection-failure', 'http-batch-failure']:
    rows.append({'name': name, 'cwd': str(ROOT), 'argv': prefix + ['tests/test_locker_independent_http.py',
        '-o', 'addopts=', '-q', '--junitxml=audit/locker-independent/http.xml'], 'exitCode': 1,
        'source': 'Earlier independent fixture version; see REPORT.md, not a product red test'})
logs = sorted([*OUT.glob('*.log'), *OUT.glob('*.xml')])
with zipfile.ZipFile(OUT/'raw-logs.zip', 'a', zipfile.ZIP_DEFLATED) as archive:
    for path in logs:
        if path.name not in archive.namelist():
            archive.write(path, path.name)
for path in logs:
    path.write_text('\n'.join(line.rstrip() for line in path.read_text(encoding='utf-8-sig').splitlines())+'\n', encoding='utf-8')
result = {'reviewedCandidate': 'e863eb16a5f80d637842e7de2bf0a52a5bf0365a',
    'reviewedProductSource': '980fae907a077476e193f1aa9dd556b0ea906a24',
    'localSource': '939ea97c00abed1c0f43700b90c221247028a4da', 'commands': rows,
    'sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [ROOT/'packages/interactions/store.py', ROOT/'tests/test_locker_independent_review.py',
                  ROOT/'tests/test_locker_independent_http.py', *OUT.glob('*.xml'), *OUT.glob('*.log'),
                  OUT/'http-evidence.json', OUT/'raw-logs.zip']}}
(OUT/'commands.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print(json.dumps({'localSource': result['localSource'], 'results': [(r['name'], r['exitCode']) for r in rows]}))
