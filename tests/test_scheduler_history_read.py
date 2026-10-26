"""R6-E original-ID HTTP/MCP acceptance over owned synthetic archives only."""
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.automation.auth import UserAuth
from packages.jobs.migration_history import rows_for
from packages.scheduler import api


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else None for p in root.rglob('*')}


@pytest.fixture
def history_env(tmp_path, monkeypatch):
    legacy, canonical = tmp_path / 'background_jobs', tmp_path / 'job-final'
    monkeypatch.setenv('PAN_SCHEDULER_DIR', str(legacy))
    monkeypatch.setenv('PAN_BACKGROUND_JOBS_DIR', str(legacy))
    bundle = {'entries': [], 'preserved': [], 'sourceBackups': {},
              'authorizationAudit': {'receiptDigest': 'synthetic-authorized'},
              'authorizationContext': {'historyLogs': {}}}
    log = b'original historical log\x00\xff\n'
    (tmp_path / 'logs').mkdir()
    (tmp_path / 'logs' / 'original.log').write_bytes(log)
    for i in range(111):
        identity = f'original_{i:03}'
        source = {'jobId': identity, 'taskId': f'sch_{i:03}', 'kind': 'scheduled-task',
                  'targetSessionId': 'ses_owned', 'createdAt': '2026-01-01',
                  'status': 'unknown' if i >= 51 else 'completed', 'lastStatus': 'unknown',
                  'enabled': True, 'schedule': [{'kind': 'interval', 'intervalSec': 60}],
                  'terminalEventId': f'event_{i}', 'notificationState': 'delivered'}
        if i >= 51:
            source = {'id': identity, 'opaque': ['unknown', i], 'status': 'unknown'}
        raw = json.dumps(source).encode()
        digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
        relative = f'background_jobs/jobs/{identity}.json'
        entry = {'id': identity, 'originalId': identity, 'migrationId': f'migration_{i}',
                 'targetIds': [f'canonical_{i}'] if i < 51 else [],
                 'sourceFile': relative, 'originalDigest': digest,
                 'recognition': 'unrecognized', 'executable': False, 'reasonCodes': ['opaque']}
        bundle['entries' if i < 51 else 'preserved'].append(entry)
        bundle['sourceBackups'][relative] = {'bytes': base64.b64encode(raw).decode(), 'sha256': digest}
        bundle['authorizationContext']['historyLogs'][entry['migrationId']] = {
            'relativePath': 'logs/original.log', 'sha256': hashlib.sha256(log).hexdigest()}
    dbpath = canonical / 'event-creators' / 'creators.sqlite3'
    dbpath.parent.mkdir(parents=True)
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE final_documents(kind TEXT,id TEXT,document TEXT,PRIMARY KEY(kind,id))')
        db.executemany('INSERT INTO final_documents VALUES(?,?,?)',
                       [(k, i, json.dumps(d)) for k, i, d in rows_for(bundle)])
    legacy.mkdir()
    runs = [{'run_id': f'run_{i}', 'task_id': 'sch_000', 'status': 'unknown',
             'fire_at': f'2026-01-01T00:00:{i:02}'} for i in range(12)]
    (legacy / 'runs.jsonl').write_text('\n'.join(json.dumps(r) for r in runs), encoding='utf-8')
    auth = UserAuth(tmp_path / 'auth')
    headers = {'Authorization': 'Bearer ' + auth.login(auth.key())}
    app = FastAPI()
    app.state.automation = SimpleNamespace(auth=auth)
    app.state.job_final_service = SimpleNamespace(root=canonical)
    app.include_router(api.router)
    # Any runtime/store construction would fail this acceptance suite.
    from packages.scheduler import store
    from packages.jobs.final_service import FinalService
    from packages.scheduler.creators import CreatorStore
    from packages.jobs.runtime_owner import OwnerFence
    def forbidden(*a, **kw):
        pytest.fail('Historical read initialized legacy/canonical runtime')
    for name in ('list_tasks', 'get_task', 'list_runs', '_job_for_task', 'migrate_legacy_tasks', 'create_task', 'append_run'):
        monkeypatch.setattr(store, name, forbidden)
    for cls in (FinalService, CreatorStore, OwnerFence):
        monkeypatch.setattr(cls, '__init__', forbidden)
    with TestClient(app) as client:
        yield client, headers, bundle, log, dbpath


@pytest.mark.parametrize('index', range(111))
def test_all_original_ids_hash_status_log_receipt_and_no_effect(history_env, tmp_path, index):
    client, headers, bundle, log, _ = history_env
    before = snapshot(tmp_path)
    result = client.get(f'/api/scheduler/tasks/original_{index:03}', headers=headers).json()
    assert result['ok'], result
    task = result['task']
    evidence = task['history']
    assert task['id'] == evidence['originalId'] == f'original_{index:03}'
    assert not task['executable'] and not evidence['executable']
    raw = base64.b64decode(evidence['rawBytesBase64'])
    assert evidence['sourceSha256'] == 'sha256:' + hashlib.sha256(raw).hexdigest()
    assert evidence['source'] == json.loads(raw)
    if index < 51:
        assert task['status'] == 'completed' and task['lastStatus'] == 'unknown'
        assert base64.b64decode(result['log']['bytesBase64']) == log
        assert result['receipt']['originalKey'] == f'event_{index}'
        assert result['receipt']['deliveryAttempted'] is False
    else:
        assert task['status'] == 'unknown' and evidence['recognition'] == 'unrecognized'
        assert evidence['targetIds'] == []
        assert result['log'] is None and result['receipt'] is None
    assert snapshot(tmp_path) == before


def test_list_runs_pagination_aliases_and_mcp_transport(history_env, monkeypatch, tmp_path):
    client, headers, _, _, _ = history_env
    before = snapshot(tmp_path)
    pages = [client.get(f'/api/scheduler/tasks?limit=20&offset={i}', headers=headers).json() for i in range(0, 120, 20)]
    ids = [t['id'] for page in pages for t in page['tasks']]
    assert len(ids) == len(set(ids)) == 111
    assert all(p['total'] == 111 for p in pages)
    assert ids == [f'original_{i:03}' for i in range(51, 111)] + [f'original_{i:03}' for i in range(51)]
    runs = client.get('/api/scheduler/tasks/original_000/runs?limit=3&offset=2', headers=headers).json()
    assert runs['total'] == 12 and [r['runId'] for r in runs['runs']] == ['run_9', 'run_8', 'run_7']
    assert all(r['status'] == 'unknown' for r in runs['runs'])
    assert client.get('/api/scheduler/tasks/sch_000', headers=headers).json()['task']['id'] == 'original_000'
    assert client.get('/api/scheduler/tasks/original_051/runs', headers=headers).json()['runs'] == []
    from packages.mcp import server as mcp
    monkeypatch.setattr(mcp, '_scheduler_auth', lambda *a, **kw: None)
    monkeypatch.setattr(mcp, '_api', lambda method, path: client.request(method, path, headers=headers).json())
    assert mcp.scheduler_get('original_000')['receipt']['originalKey'] == 'event_0'
    assert mcp.scheduler_list(limit=20, offset=20)['tasks'] == pages[1]['tasks']
    assert mcp.scheduler_runs('original_000', limit=3, offset=2) == runs
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize('suffix', ['', '/runs'])
def test_missing_and_unauthenticated(history_env, suffix):
    client, headers, *_ = history_env
    assert client.get('/api/scheduler/tasks/missing' + suffix, headers=headers).json()['error']['code'] == 'not_found'
    assert client.get('/api/scheduler/tasks/original_000' + suffix).status_code == 401


@pytest.mark.parametrize('query', ['limit=0', 'limit=501', 'offset=-1'])
@pytest.mark.parametrize('path', ['/tasks', '/tasks/original_000/runs'])
def test_bad_pages(history_env, query, path):
    client, headers, *_ = history_env
    assert client.get('/api/scheduler' + path + '?' + query, headers=headers).json()['error']['code'] == 'invalid_argument'


@pytest.mark.parametrize('fault', ['hash', 'log_hash', 'log_missing', 'log_escape', 'ambiguous', 'database'])
def test_faults_are_explicit_and_never_success(history_env, tmp_path, fault):
    client, headers, _, _, dbpath = history_env
    with sqlite3.connect(dbpath) as db:
        doc = json.loads(db.execute("SELECT document FROM final_documents WHERE kind='migration_history' AND id='migration_0'").fetchone()[0])
        if fault == 'hash': doc['sourceSha256'] = 'sha256:bad'
        if fault == 'log_hash': doc['log']['sha256'] = 'bad'
        if fault == 'log_missing': (tmp_path / 'logs/original.log').unlink()
        if fault == 'log_escape': doc['log']['relativePath'] = '../outside'
        db.execute("UPDATE final_documents SET document=? WHERE kind='migration_history' AND id='migration_0'", (json.dumps(doc),))
        if fault == 'ambiguous': db.execute("INSERT INTO final_documents VALUES('migration_history','duplicate',?)", (json.dumps(doc),))
    if fault == 'database': dbpath.write_bytes(b'corrupt sqlite')
    before = snapshot(tmp_path)
    result = client.get('/api/scheduler/tasks/original_000', headers=headers).json()
    assert result['ok'] is False and result['error']['code'] == 'history_unavailable'
    assert snapshot(tmp_path) == before


def test_missing_roots_never_created(tmp_path, monkeypatch):
    from packages.scheduler.history import History
    monkeypatch.setenv('PAN_SCHEDULER_DIR', str(tmp_path / 'absent/jobs'))
    before = snapshot(tmp_path)
    history = History(tmp_path / 'absent/canonical')
    assert history.tasks() == [] and history.get('missing') is None
    assert snapshot(tmp_path) == before


def test_import_and_read_without_store_module(tmp_path):
    code = """
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == 'packages.scheduler.store': raise AssertionError('store imported')
sys.meta_path.insert(0, Block())
from packages.scheduler.api import router
from packages.scheduler.history import History
assert History().tasks() == []
assert 'packages.scheduler.store' not in sys.modules
"""
    import os
    env = {**os.environ, 'PAN_SCHEDULER_DIR': str(tmp_path / 'absent')}
    result = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_archive_overrides_legacy_and_unrecognized_has_no_ownerless_runs(history_env, tmp_path):
    client, headers, *_ = history_env
    directory = tmp_path / 'background_jobs/jobs'
    directory.mkdir()
    (directory / 'stale.json').write_text(json.dumps({'kind': 'scheduled-task', 'taskId': 'sch_000',
        'jobId': 'original_000', 'status': 'running'}), encoding='utf-8')
    with (directory.parent / 'runs.jsonl').open('a', encoding='utf-8') as f:
        f.write('\n' + json.dumps({'run_id': 'ownerless', 'status': 'unknown'}))
    before = snapshot(tmp_path)
    listed = client.get('/api/scheduler/tasks', headers=headers).json()
    assert listed['total'] == 111
    assert client.get('/api/scheduler/tasks/sch_000', headers=headers).json()['task']['status'] == 'completed'
    assert client.get('/api/scheduler/tasks/original_051/runs', headers=headers).json()['runs'] == []
    assert snapshot(tmp_path) == before


def test_opaque_unrecognized_bytes_and_wal_refusal(history_env, tmp_path):
    client, headers, _, _, path = history_env
    raw = b'not valid json\xff'
    with sqlite3.connect(path) as db:
        doc = json.loads(db.execute("SELECT document FROM final_documents WHERE kind='migration_unrecognized' LIMIT 1").fetchone()[0])
        doc['rawBytesBase64'] = base64.b64encode(raw).decode()
        doc['sourceSha256'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
        db.execute("UPDATE final_documents SET document=? WHERE kind='migration_unrecognized' AND id=?", (json.dumps(doc), doc['migrationId']))
    result = client.get('/api/scheduler/tasks/' + doc['originalId'], headers=headers).json()
    assert result['ok'] and result['task']['status'] == 'unknown'
    assert base64.b64decode(result['task']['history']['rawBytesBase64']) == raw
    # Do not silently ignore WAL facts or let SQLite create/modify shared memory.
    path.with_name(path.name + '-wal').write_bytes(b'owned WAL sentinel')
    before = snapshot(tmp_path)
    assert client.get('/api/scheduler/tasks', headers=headers).json()['error']['code'] == 'history_unavailable'
    assert snapshot(tmp_path) == before


def test_action_archive_visibility_preserves_job_read_grant(history_env, monkeypatch):
    import asyncio
    import httpx
    from packages.automation.authorization import _MARKER, _SCOPE_KEY
    client, _, _, _, dbpath = history_env
    with sqlite3.connect(dbpath) as db:
        raw = db.execute("SELECT document FROM final_documents WHERE kind='migration_history' AND id='migration_0'").fetchone()[0]
        doc = json.loads(raw)
        source = json.loads(base64.b64decode(doc['rawBytesBase64']))
        source['action'] = {'api': 'action_reference'}
        raw = json.dumps(source).encode()
        doc['source'] = source
        doc['rawBytesBase64'] = base64.b64encode(raw).decode()
        doc['sourceSha256'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
        db.execute("UPDATE final_documents SET document=? WHERE kind='migration_history' AND id='migration_0'", (json.dumps(doc),))
    auth = client.app.state.automation.auth
    monkeypatch.setattr(auth, 'permission_allowed', lambda *a: (False, 'denied'))
    async def scoped(scope, receive, send):
        scope[_SCOPE_KEY] = _MARKER
        await client.app(scope, receive, send)
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(scoped), base_url='http://owned') as http:
            result = (await http.get('/api/scheduler/tasks')).json()
            assert 'original_000' not in [t['id'] for t in result['tasks']]
            for suffix in ('', '/runs'):
                assert (await http.get('/api/scheduler/tasks/original_000' + suffix)).status_code == 403
    asyncio.run(check())
