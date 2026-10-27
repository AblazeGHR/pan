"""Rebuild the UI repair evidence index without running effects or tests."""
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = 'bdd4c778d55daff58529635b2adc464c03a1393c'
INTEGRATION = 'bac726cf84c0510dff8618afb82e227e660f590c'
SOURCE = '9aece5bbbe3729acb35ac7d292a6f01318aae68d'

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT).decode('utf-8').strip()

def methods(commit):
    tree = ast.parse(git('show', commit + ':packages/jobs/final_service.py'))
    result = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    result[node.name + '.' + child.name] = ast.dump(child, include_attributes=False)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result[node.name] = ast.dump(node, include_attributes=False)
    return result

before, after = methods(BASE), methods(SOURCE)
changed_methods = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
assert changed_methods == ['FinalService.actions'], changed_methods
assert git('diff', '--name-only', BASE, SOURCE, '--', 'packages/jobs/final_api.py',
    'packages/jobs/final_runtime', 'packages/core', 'packages/scheduler', 'tests/conftest.py') == ''
retained = ['packages/scheduler/' + name for name in ('store.py', 'time_events.py', 'legacy_bridge.py')]
for path in retained:
    assert git('rev-parse', BASE + ':' + path) == git('rev-parse', SOURCE + ':' + path)
assert git('diff', '--name-only', BASE, '--', 'evidence/r6i-repair',
    'evidence/r6i-resume', 'evidence/r6i-time-events') == ''
subprocess.run(['git', 'merge-base', '--is-ancestor', INTEGRATION, SOURCE], cwd=ROOT, check=True)

def matrix(path):
    root = ET.parse(path).getroot()
    nodes = root.findall('.//testcase')
    values = {'tests': len(nodes), 'passed': 0, 'failed': 0, 'errors': 0, 'skipped': 0}
    rows = []
    for node in nodes:
        state = ('failed' if node.find('failure') is not None else 'errors' if node.find('error') is not None
                 else 'skipped' if node.find('skipped') is not None else 'passed')
        values[state] += 1
        rows.append({'class': node.get('classname'), 'name': node.get('name'), 'status': state})
    return {**values, 'nodes': rows}

all_xml = {p.name: matrix(p) for p in sorted(OUT.glob('*.xml'))}
backend = all_xml['backend-final-v2.xml']
browser = all_xml['browser-complete-final.xml']
assert backend['passed'] == backend['tests'] == 91 and not backend['skipped']
assert browser['passed'] == browser['tests'] == 3 and not browser['skipped']
vitest_text = (OUT / 'vitest-final-v2.log').read_text(encoding='utf-8')
vitest_nodes = re.findall(r'^\s*✓ (.+ > .+)$', vitest_text, re.MULTILINE)
assert len(vitest_nodes) == 90, len(vitest_nodes)
web = str(ROOT / 'packages/web')
backend_files = ['test_canonical_form_metadata', 'test_job_final_http_service', 'test_job_final_clients',
                 'test_scheduler_creator_management', 'test_scheduler_dependency_cutover']
front_files = ['src/components/automation', 'src/views/AutomationWorkspace.test.tsx',
    'src/views/CanonicalJobsView.test.tsx', 'src/views/AutomationView.test.tsx',
    'src/services/canonicalJobs.test.ts', 'src/stores/automationSession.test.tsx', 'src/router.retirement.test.tsx']
commands = [
    {'label': 'backend-final-v2', 'cwd': str(ROOT), 'exit': 0,
     'argv': ['python', 'scripts/run_tests_isolated.py', '--', 'python', '-m', 'pytest',
        *['tests/' + n + '.py' for n in backend_files], '-o', 'addopts=', '-q',
        '--junitxml=evidence/r6i-forms/backend-final-v2.xml'], 'matrix': 'backend-final-v2.xml'},
    {'label': 'browser-complete-final', 'cwd': str(ROOT), 'exit': 0,
     'argv': ['python', 'scripts/run_tests_isolated.py', '--', '.validation/r6i-venv/Scripts/python.exe',
        '-m', 'pytest', 'tests/test_scheduler_structured_browser.py', 'tests/test_scheduler_management_browser.py',
        '-o', 'addopts=', '-q', '--junitxml=evidence/r6i-forms/browser-complete-final.xml'], 'matrix': 'browser-complete-final.xml'},
    {'label': 'vitest-final-v2', 'cwd': web, 'exit': 0, 'tests': 90, 'files': 8, 'skipped': 0,
     'argv': ['pnpm', 'exec', 'vitest', 'run', *front_files, '--reporter=verbose'], 'nodes': vitest_nodes},
    {'label': 'tsc-final', 'cwd': web, 'exit': 0, 'argv': ['pnpm', 'exec', 'tsc', '-b', '--noEmit']},
    {'label': 'build-final', 'cwd': web, 'exit': 0, 'argv': ['pnpm', 'build'], 'compressedAssets': 88},
    {'label': 'dependencies-final', 'cwd': str(ROOT), 'exit': 0,
     'argv': ['python', 'scripts/check_scheduler_dependencies.py', '--output', 'evidence/r6i-forms/dependencies-final.json']},
    {'label': 'source-commit', 'cwd': str(ROOT), 'exit': 0,
     'argv': ['git', 'commit', '-m', 'fix(web): restore canonical scheduler and job structured forms'],
     'hook': 'React frontend OK'},
]
paths = []
for path in git('diff', '--name-only', BASE, SOURCE).splitlines():
    item = ROOT / path
    paths.append({'path': path, 'blob': git('rev-parse', SOURCE + ':' + path),
                  'sha256': hashlib.sha256(item.read_bytes()).hexdigest()})
with zipfile.ZipFile(OUT / 'raw-output.zip', 'w', compression=zipfile.ZIP_DEFLATED) as archive:
    for item in sorted(OUT.glob('*.log')):
        archive.write(item, item.name)
raw_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(OUT.glob('*.log'))}
with zipfile.ZipFile(OUT / 'raw-artifacts.zip') as archive:
    for name in archive.namelist():
        raw = archive.read(name)
        clean = b''.join(line.rstrip(b' \t\r\n') + (b'\r\n' if line.endswith(b'\r\n') else b'\n' if line.endswith(b'\n') else b'')
                         for line in raw.splitlines(keepends=True))
        assert (OUT / name).read_bytes() == clean, name
browser_results = {p.name: json.loads(p.read_text(encoding='utf-8')) for p in
                  (OUT / 'browser-result-full.json', OUT / 'browser-result-sources.json', OUT / 'advanced-browser-result.json')}
for result in browser_results.values():
    assert result['portReleased'] and result['providerCalls'] == 0 and result['pageErrors'] == []
    assert result['port'] not in (8767, 8768)
manifest = {'baseSha': BASE, 'integrationAncestor': INTEGRATION, 'sourceSha': SOURCE,
    'branch': git('branch', '--show-current'), 'sourceChangedPaths': paths,
    'sharedBackendChangedMethods': changed_methods, 'retainedHostsUnchanged': retained,
    'commands': commands, 'allXmlRunsIncludingRed': all_xml,
    'finalPytestUnique': {'tests': 94, 'passed': 94, 'failed': 0, 'errors': 0, 'skipped': 0},
    'finalFrontend': {'tests': 90, 'passed': 90, 'files': 8, 'skipped': 0},
    'browserResults': browser_results,
    'inherited43Red': {'rerunThisUiRepair': False, 'unchangedEvidence': 'evidence/r6i-repair/original-red-assessment.json',
        'additional2': 'evidence/r6i-repair/baseline-additional-command.json', 'noSkipOrAssertionEdits': True},
    'rawLogSha256': raw_hashes, 'rawArchiveSha256': hashlib.sha256((OUT / 'raw-output.zip').read_bytes()).hexdigest(),
    'rawArtifactsSha256': hashlib.sha256((OUT / 'raw-artifacts.zip').read_bytes()).hexdigest(),
    'reviewCopyNormalization': 'Only trim trailing spaces/tabs on XML/HTML/patch review lines; original bytes archived and verified.'}
(OUT / 'validation.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'sourceSha': SOURCE, 'paths': len(paths), 'backend': 91, 'browser': 3,
    'vitest': len(vitest_nodes), 'legacyHostsRetained': len(retained), 'methodDiff': changed_methods}))
