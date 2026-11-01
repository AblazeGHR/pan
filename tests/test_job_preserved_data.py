"""Explicit user deletion of nonexecuting data uses the existing owner/receipts."""
import hashlib
import json

import pytest

from tests.test_job_final_http_service import h  # noqa: F401


@pytest.fixture
def preserved(h):
    from packages.jobs.maintenance import install
    install(h.app)
    h.service.owner.claim(0)
    h.service.owner.set_state('active')
    source = h.root.parent / 'background_jobs/jobs/original-unknown.json'
    source.parent.mkdir(parents=True)
    raw = b'opaque unknown old payload; never parse or execute'
    source.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    row = {'id': 'preserved-one', 'originalId': 'original-unknown',
           'sourceFile': 'background_jobs/jobs/original-unknown.json', 'sha256': digest,
           'revision': 1, 'reasonCodes': ['unknown_schema'], 'executable': False}
    (h.root.parent / 'job-dispositions.json').write_text(json.dumps({'schemaVersion': 1, 'records': [row]}))
    return h, source, raw, row


def delete(h, row, **changes):
    return h.client.request('DELETE', '/api/job-maintenance/records/' + row['id'], json={
        'expectedRevision': row['revision'], 'expectedSha256': row['sha256'],
        'expectedSourceFile': row['sourceFile'],
        'requestKey': 'explicit-delete', **changes})


def test_normal_owner_unknown_visible_user_delete_archives_without_execution(preserved):
    h, source, raw, row = preserved
    listed = h.client.get('/api/job-maintenance/records')
    assert listed.status_code == 200
    assert listed.json()['items'][0]['originalId'] == row['originalId']
    assert listed.json()['items'][0]['deleteBlocked'] is False
    response = delete(h, row)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt['status'] == 'completed'
    assert receipt['result']['originalId'] == row['originalId']
    assert receipt['result']['unknownSettlementAttempted'] is False
    assert not source.exists()
    assert (h.root / receipt['result']['archive']).read_bytes() == raw
    replay = delete(h, row).json()
    assert replay['operationId'] == receipt['operationId'] and replay['replayed'] is True
    assert h.provider.calls == []
    with h.service.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0
    assert h.client.get('/api/job-maintenance/records').json()['items'][0]['deleted'] is True


@pytest.mark.parametrize('change', ['hash', 'revision', 'path', 'source', 'pin', 'active-resource', 'unauthenticated'])
def test_preserved_delete_conflicts_do_not_remove_or_execute(preserved, change):
    h, source, raw, row = preserved
    kwargs = {}
    if change == 'hash':
        kwargs['expectedSha256'] = '0' * 64
    elif change == 'revision':
        kwargs['expectedRevision'] = 2
    elif change == 'path':
        kwargs['expectedSourceFile'] = 'background_jobs/jobs/another.json'
    elif change == 'source':
        source.write_bytes(b'changed data')
    elif change == 'pin':
        (source.parent.parent / 'pins.json').write_text(json.dumps({row['originalId']: True}))
    elif change == 'active-resource':
        row['reasonCodes'] = ['active_resource']
        (h.root.parent / 'job-dispositions.json').write_text(json.dumps({'schemaVersion': 1, 'records': [row]}))
    else:
        h.client.headers['Authorization'] = 'Bearer invalid'
    before = source.read_bytes()
    response = delete(h, row, **kwargs)
    assert response.status_code == (401 if change == 'unauthenticated' else 409), response.text
    assert source.read_bytes() == before
    assert h.provider.calls == []


def test_delete_crash_after_unlink_reconciles_archive_by_original_key(preserved, monkeypatch):
    h, source, raw, row = preserved
    save = h.service._save
    def crash(db, kind, key, value):
        if kind == 'migration_receipt':
            raise OSError('fixture after unlink before receipt commit')
        return save(db, kind, key, value)
    monkeypatch.setattr(h.service, '_save', crash)
    failed = delete(h, row)
    assert failed.status_code == 503
    assert failed.json()['error']['code'] == 'storage_unavailable'
    assert not source.exists()
    monkeypatch.setattr(h.service, '_save', save)
    result = delete(h, row).json()
    assert result['status'] == 'completed'
    assert (h.root / result['result']['archive']).read_bytes() == raw
    original = h.client.get('/api/operations', params={'operation': 'retire', 'requestKey': 'explicit-delete'})
    assert original.json()['operationId'] == result['operationId']
    assert h.provider.calls == []


def test_shared_registry_is_never_removed_as_one_unknown_record(preserved):
    h, source, raw, row = preserved
    shared = h.root.parent / 'actions/registry.json'
    shared.parent.mkdir(exist_ok=True)
    shared.write_bytes(raw)
    row['sourceFile'] = 'actions/registry.json'
    (h.root.parent / 'job-dispositions.json').write_text(json.dumps({'schemaVersion': 1, 'records': [row]}))
    response = delete(h, row)
    assert response.status_code == 400
    assert response.json()['error']['code'] == 'unsupported_operation'
    assert shared.read_bytes() == raw and source.read_bytes() == raw


def test_reused_key_with_different_identity_is_not_a_second_delete(preserved):
    h, source, raw, row = preserved
    first = delete(h, row).json()
    second = delete(h, row, expectedSha256='0' * 64)
    assert second.status_code == 409
    assert second.json()['error']['code'] == 'idempotency_conflict'
    assert (h.root / first['result']['archive']).read_bytes() == raw
