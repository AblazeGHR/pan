"""Immutable focused stages; preserve actual remaining behavioral red exit."""
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
    'mcp': ['tests/test_retirement_rev16_mcp_stdio.py', 'tests/test_retirement_rev12_mcp.py', 'tests/test_authorization_mcp.py'],
    'behavior': ['tests/test_retirement_rev16_behavior.py'],
    'accepted-delta': ['tests/test_retirement_rev6_session_validation.py',
        'tests/test_retirement_rev6_parity.py::test_real_product_qq_invalid_fixed_fields_never_commit_or_invoke_plugin',
        'tests/test_retirement_rev7_write_validation.py::test_product_qq_legal_text_and_id_bytes_create_edit_and_frozen_plan',
        'tests/test_retirement_rev9_qq_http.py::test_original_plugin_real_onebot_http_success_exact_receipt_single_send',
        'tests/test_retirement_rev9_qq_http.py::test_edit_after_admission_keeps_actual_original_bot_target_text',
        'tests/test_retirement_rev10_qq_retry.py::test_original_http_cold_retry_duplicate_control_cas_same_effect'],
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
    (out / 'commands.json').write_text(json.dumps(meta, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in check.items() if k != 'nodes'}), flush=True)
    code = max(code, result.returncode)
subprocess.run(['git', 'diff', '--exit-code', 'HEAD'], cwd=root, check=True)
assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip() == sha
raise SystemExit(code)
