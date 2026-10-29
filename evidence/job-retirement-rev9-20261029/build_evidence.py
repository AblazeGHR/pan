"""Rebuild owned parity evidence from actual JUnit commands and Git objects."""
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
BASE = '93fc1d931c2754565d8667fe41f7c036fe7c1ec0'
SOURCE = '0a9e00e74900ef33c3c24d9e7b5f27b085d4f3a5'
OLD = '43655680f030765de5bce5ea5b675c7a0e2672d5'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


groups = {label: json.loads((OUT / (label + '-command.json')).read_text()) for label in (
    'immutable-qq-http', 'immutable-runtime', 'immutable-validation')}
assert all(row['testedSourceSha'] == SOURCE and row['sourceStable'] for row in groups.values())
changed = git('diff', '--name-only', BASE, SOURCE).decode().splitlines()
assert changed == ['packages/actions/qq_channel_provider.py', 'packages/actions/typed_executor.py',
                   'tests/test_retirement_rev9_qq_http.py']
assert not git('diff', BASE, SOURCE, '--', 'packages/qq', 'packages/jobs', 'scripts',
               'tests/conftest.py', 'tests/support')
(OUT / 'product.diff').write_bytes(git('diff', '--unified=0', BASE, SOURCE, '--', 'packages'))
sources = {}
for name, path, symbol in [
    ('original-backlog', 'packages/core/background_jobs.py', 'async def _redeliver_undelivered('),
    ('original-backlog-test', 'tests/test_jobs_api.py', 'def test_scheduled_qq_backlog_retry_runs_without_session_target('),
]:
    data = git('show', OLD + ':' + path)
    text = data.decode('utf-8')
    start = text.index(symbol)
    end = text.find('\ndef ', start + 1)
    async_end = text.find('\nasync def ', start + 1)
    end = min([n for n in (end, async_end, len(text)) if n >= 0])
    (OUT / (name + '.txt')).write_text(text[start:end].rstrip() + '\n', encoding='utf-8')
    sources[name] = {'sha': OLD, 'path': path, 'symbol': symbol,
                     'fullFileSha256': hashlib.sha256(data).hexdigest()}
save('original-source.json', sources)
nodes = {}
for label, group in groups.items():
    for node in group['nodes']:
        assert node['node'] not in nodes
        nodes[node['node']] = {'status': node['status'], 'command': label}
save('parity-matrix.json', {
    'schema': 'pan.retirement.qq-parity/revision9', 'acceptedBaseline': BASE,
    'testedSourceSha': SOURCE, 'changedSourceFiles': changed,
    'groups': {label: {k: value[k] for k in ('exit', 'counts', 'elapsedSeconds')} for label, value in groups.items()},
    'uniqueCounts': {s: sum(n['status'] == s for n in nodes.values()) for s in ('passed', 'failure', 'error', 'skipped')},
    'nodes': nodes,
    'originalNodeMapping': {
        'tests/test_jobs_api.py::test_scheduled_qq_registered_callback_posts_only_to_plugin_send_route': {
            'status': 'bounded-real-http-preserved', 'successors': [n for n in nodes if 'http_success_exact_receipt_single_send' in n],
            'layer': 'real owned plugin + real NoneBot OneBot HTTP fixture, no live gateway/account'},
        'tests/test_jobs_api.py::test_scheduled_qq_run_now_is_targetless_and_records_plugin_failure': {
            'status': 'preserved', 'successors': [n for n in nodes if 'failure_manual_receipt_and_no_replay' in n]},
        'tests/test_jobs_api.py::test_scheduled_qq_backlog_retry_runs_without_session_target': {
            'status': 'open-trusted-producer-evidence-required',
            'successors': [n for n in nodes if 'local_disconnected_no_send_manual_retry_retained_gap' in n],
            'reason': 'Raw error code is not identity-bound trusted no-send proof; manual boundary deliberately unchanged.'},
        'tests/test_jobs_api.py::test_patch_text_updates_dispatch_body': {
            'status': 'frozen-admitted-send-preserved', 'successors': [n for n in nodes if 'edit_after_admission_keeps_actual' in n]},
    },
    'publicationChanged': False, 'manualRetryEnabled': False,
    'remainingM08': 'Trusted producer proof coordination pending; no-send manual retry is not closed.',
    'unverified': ['real accounts/gateway delivery', 'remote CI for this SHA', 'real migration', 'deployment',
                   'successful no-send retry/CAS replay/cold retry: blocked on producer evidence'],
})
save('environment.json', {
    'interpreter': 'D:/project/Pan-main/.venv/Scripts/python.exe',
    'version': subprocess.check_output(['D:/project/Pan-main/.venv/Scripts/python.exe', '--version'], text=True).strip(),
    'dependencies': json.loads(subprocess.check_output(['D:/project/Pan-main/.venv/Scripts/python.exe', '-c',
        'import importlib.metadata as m,json; print(json.dumps({n:m.version(n) for n in ("nonebot2","nonebot-adapter-onebot","httpx","uvicorn","tzlocal")}))'], text=True)),
    'installedThisRevision': False,
})
files = [p for p in OUT.iterdir() if p.is_file() and p.name not in {
    'raw-artifacts.zip', 'artifact-hashes.json', 'raw-archive.json'}]
hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
save('artifact-hashes.json', hashes)
with zipfile.ZipFile(OUT / 'raw-artifacts.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for p in files:
        archive.write(p, p.name)
with zipfile.ZipFile(OUT / 'raw-artifacts.zip') as archive:
    assert all(hashlib.sha256(archive.read(name)).hexdigest() == digest for name, digest in hashes.items())
save('raw-archive.json', {'sha256': hashlib.sha256((OUT / 'raw-artifacts.zip').read_bytes()).hexdigest(),
                         'entries': len(hashes), 'verified': True})
print(json.dumps({label: row['counts'] for label, row in groups.items()}))
