"""Exact successor for the user-cancelled unknown legacy API fallback."""
import asyncio
import copy
import hashlib
import json

import pytest

from packages.core import session, worker
from packages.jobs.api import _normalize_scheduled_action
from packages.jobs.final_service import FinalService
from scripts.job_final_migration.inventory import build_inventory, PRESERVED
from tests.test_job_final_http_service import create
from tests.test_job_final_migration import _scheduled_job, SCHED_JOB_ID
from tests.test_retirement_rev14_templates import h, env, counts  # noqa: F401
from tests.test_session_product_closure import config


@pytest.mark.parametrize('api', ['create_session', 'owned_unknown_api'])
def test_unknown_legacy_api_explicit_rejection_is_not_default_assign(api):
    original = {'api': api}
    normalized, error = _normalize_scheduled_action(original)
    assert normalized is None
    assert error == ('action.api must be assign, send_session, '
                     'resume_legal_running, send_qq, or shell')
    assert original == {'api': api}


@pytest.mark.parametrize('shape', ['legacy-api', 'unknown-action-reference'])
def test_unknown_api_or_reference_post_put_rejects_before_write_or_effect(h, shape):
    value = config(h, 'assign', sessionId='ses_a', text='owned legal assign', source='user')
    item = create(h, value=value)
    baseline = counts(h.service)
    stored = copy.deepcopy(h.service.final.store.get(item['id']))
    workers = set(worker.workers)
    invalid = copy.deepcopy(value)
    if shape == 'legacy-api':
        invalid['action'] = {'api': 'create_session'}
    else:
        invalid['steps'][0]['actionRef']['id'] = 'owned_unknown_action'
    for method, url, revision in [
        ('post', '/api/jobs', 0), ('put', '/api/jobs/' + item['id'], item['configRevision'])]:
        response = getattr(h.client, method)(url, json={'definition': invalid,
            'expectedRevision': revision, 'requestKey': shape + '-' + method})
        assert response.status_code == (400 if shape == 'legacy-api' else 409), response.text
        assert response.json()['error']['code'] == ('invalid_schema' if shape == 'legacy-api' else 'reference_conflict')
        assert counts(h.service) == baseline
        assert h.service.final.store.get(item['id']) == stored
    asyncio.run(h.service.tick())
    history = h.service.final.get(item['id'])
    assert not history['runs'] and not history['attempts'] and not history['effects']
    assert not session.get('ses_a').queue_pending
    assert set(worker.workers) == workers


def test_unknown_legacy_source_bytes_preserved_inventory_and_cold_start_zero_assign(h):
    source = h.tmp / 'owned-legacy-source'
    path = source / 'background_jobs' / 'jobs' / (SCHED_JOB_ID + '.json')
    path.parent.mkdir(parents=True)
    record = _scheduled_job()
    record.update(targetSessionId='ses_a', targetStruct={'sessionId': 'ses_a'})
    record['action'] = {'api': 'create_session'}
    raw = (json.dumps(record, indent=3, ensure_ascii=False) + '\r\n').encode('utf-8')
    path.write_bytes(raw)
    workers = set(worker.workers)
    report = build_inventory(source)
    finding = next(i for i in report['instances'] if i['sourceFile'] == path.relative_to(source).as_posix())
    assert finding['classification'] == PRESERVED
    assert any('unknown scheduler action' in reason for reason in finding['rejectionReasons'])
    assert any('create_session' in reason for reason in finding['rejectionReasons'])
    assert finding['sha256'].removeprefix('sha256:') == hashlib.sha256(raw).hexdigest()
    cold = FinalService(source / 'canonical', h.auth)
    try:
        cold.recover()
        asyncio.run(cold.tick())
        with cold.transaction(readonly=True) as db:
            assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0
            assert db.execute('SELECT count(*) FROM final_operations').fetchone()[0] == 0
        assert path.read_bytes() == raw
        assert not session.get('ses_a').queue_pending
        assert set(worker.workers) == workers
    finally:
        asyncio.run(cold.close())
    assert path.read_bytes() == raw
