"""Record actual bounded executions; not a runtime acceptance gate."""
from pathlib import Path
import hashlib
import json
import re
import subprocess
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PYTHON = 'D:/project/Pan-main/.venv/Scripts/python.exe'
SOURCE = '4ba377cae65a6327219340092f68bbc4eb6265ae'
groups = {
    'focused-final': [
        'tests/test_retirement_session_runtime.py', 'tests/test_retirement_notification_runtime.py',
        'tests/test_retirement_trigger_backlog.py', 'tests/test_retirement_rev16_behavior.py',
        'tests/test_retirement_rev17_backlog.py', 'tests/test_retirement_rev17_process_notice.py'],
    'affected': [
        'tests/test_job_final_contracts.py', 'tests/test_job_final_http_service.py',
        'tests/test_session_product_closure.py', 'tests/test_job_automatic_recovery.py',
        'tests/test_retirement_rev16_mcp_stdio.py'],
    'retained-delta': [
        'tests/test_retirement_rev6_resume_native.py', 'tests/test_retirement_rev6_session_validation.py',
        'tests/test_retirement_rev7_write_validation.py',
        'tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin',
        'tests/test_quota_native_admission.py'],
}
assert subprocess.check_output(['git', 'diff', SOURCE, '--', 'packages', 'tests'], cwd=ROOT) == b''
checks = []
for name, nodes in groups.items():
    xml = HERE / (name + '.xml')
    suites = list(ET.parse(xml).getroot())
    counts = {key: sum(int(s.attrib[key]) for s in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')}
    counts['passed'] = counts['tests'] - sum(counts[k] for k in ('failures', 'errors', 'skipped'))
    checks.append({'name': name, 'sourceSha': SOURCE, 'cwd': str(ROOT),
        'argv': [PYTHON, 'scripts/run_tests_isolated.py', '--', PYTHON, '-m', 'pytest', *nodes,
                 '-q', '--tb=short', '-o', 'junit_logging=all',
                 '--junitxml=' + str(xml.relative_to(ROOT)).replace('\\', '/')],
        'counts': counts, 'exit': int((HERE / (name + '.exit')).read_text(encoding='utf-8-sig').strip()),
        'seconds': sum(float(s.attrib['time']) for s in suites), 'junit': xml.name,
        'junitSha256': hashlib.sha256(xml.read_bytes()).hexdigest(),
        'log': name + '.log', 'logSha256': hashlib.sha256((HERE / (name + '.log')).read_bytes()).hexdigest()})
react = (HERE / 'react.log').read_text(encoding='utf-8-sig')
assert re.search(r'Tests\s+16 passed', react)
for name, argv in (
    ('react', ['pnpm', 'exec', 'vitest', 'run',
        'src/components/automation/RetirementRuntimeFields.test.tsx',
        'src/components/jobs/JobDetailDrawer.test.tsx', 'src/components/automation/CanonicalForms.test.tsx']),
    ('build', ['pnpm', 'build'])):
    checks.append({'name': name, 'sourceSha': SOURCE, 'cwd': str(ROOT / 'packages/web'),
        'argv': argv, 'exit': int((HERE / (name + '.exit')).read_text(encoding='utf-8-sig').strip()),
        'counts': {'passed': 16, 'tests': 16, 'skipped': 0} if name == 'react' else None,
        'log': name + '.log', 'logSha256': hashlib.sha256((HERE / (name + '.log')).read_bytes()).hexdigest()})
result = {'sourceSha': SOURCE, 'sourceAndTestsUnchangedSinceTestedSha': True, 'checks': checks,
    'sourceBinding': 'All product and test files equal immutable source SHA; later commits are owned evidence/report only.',
    'oldSourceFocused': {'sourceSha': '98cf34e93b976b95cb0583ec2763643f4586f974',
        'junit': 'focused.xml', 'tests': 53, 'exit': 0,
        'meaning': 'Preserved earlier source check, excluded from current total.'},
    'preContextRegression': {'sourceSha': '6ea00524b0e6dec54c80a9cc33c8cc7063280221',
        'focused': {'junit': 'focused-pre-context.xml', 'passed': 57, 'exit': 0},
        'affected': {'junit': 'affected-pre-context.xml', 'passed': 231, 'failed': 1, 'exit': 1},
        'node': 'tests/test_session_product_closure.py::test_product_fanout_partial_unknown_cold_reopen_never_resends',
        'fix': 'Native pre-call checks now require validated runtime reservation ContextVar; ordinary Worker call order unchanged.',
        'meaning': 'Preserved actual product regression and unchanged assertions, excluded from final total.'},
    'developmentArtifacts': 'Preserved separately; counts not added to current acceptance.',
    'layers': {'source': True, 'localWindowsNativeQueueAndProcess': True, 'ownedHttp': True,
        'actualMcpStdio': True, 'freshProductionBrowser': True,
        'remoteCI': False, 'otherPlatforms': False, 'realDataMigration': False, 'deployment': False}}
(HERE / 'commands.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps([{'name': c['name'], 'counts': c['counts'], 'exit': c['exit']} for c in checks]))
