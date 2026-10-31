"""Real local queue, current HTTP/CAS and existing outbox runtime lifecycle."""
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess

import pytest

from packages.core import session, worker
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService, definition
from packages.jobs.notification_delivery import NOTIFICATION_RUNTIME, binding, enqueue_session_notice
from tests.job_retirement_native_fixture import h as native_h
from tests.test_retirement_rev16_behavior import h
from tests.test_session_product_closure import config
from tests.test_job_final_http_service import enabled, launch


async def sender(row):
    if row['channel'] == 'ws':
        return {'outcome': 'succeeded'}
    return await enqueue_session_notice(row)


def completed(h, target=None, actor=None):
    before_workers = set(worker.workers)
    value = config(h, 'assign', sessionId='ses_a', text='original frozen work')
    value['notification'] = {'on': ['completed'], 'runtime': copy.deepcopy(NOTIFICATION_RUNTIME),
        'recipients': [{'recipientId': 'completion', 'channelId': 'session', 'targetRef': target}]}
    job = enabled(h, value)
    if actor is None:
        launch(h, job)
    else:
        h.service.create_run(job['id'], {'expectedRevision': job['configRevision'], 'requestKey': 'agent-run',
            'input': {}, 'cause': {'type': 'manual'}}, actor=actor)
    asyncio.run(h.service.dispatch_once())
    assert h.service.final.history(job['id'])['runs'][0]['state'] == 'completed'
    assert set(worker.workers) == before_workers
    return job


def notice(service, jid):
    return next(row for row in service.final.history(jid)['outbox'] if row['channel'] == 'session')


def test_actual_local_shutdown_notice_remains_pending_then_same_outbox_enqueues_once(h):
    job = completed(h, 'ses_b')
    original = notice(h.service, job['id'])
    before = worker._shutdown_started
    h.service.outbox_sender = sender
    try:
        worker._shutdown_started = True
        asyncio.run(h.service.deliver_outbox())
        pending = notice(h.service, job['id'])
        assert pending['status'] == 'pending'
        assert pending['receipt']['error']['code'] == 'pan_shutting_down'
        assert pending['receipt']['binding'] == binding(pending)
        assert pending['receipt']['attempted'] is False
        assert not session.get('ses_b').queue_pending
        worker._shutdown_started = False
        asyncio.run(h.service.deliver_outbox())
        accepted = notice(h.service, job['id'])
        assert accepted['outboxId'] == original['outboxId'] and accepted['status'] == 'acked'
        assert len(session.get('ses_b').queue_pending) == 1
        asyncio.run(h.service.deliver_outbox())
        assert notice(h.service, job['id']) == accepted
        assert len(session.get('ses_b').queue_pending) == 1
    finally:
        worker._shutdown_started = before


def edit(h, job, target, key='retarget'):
    value = definition('job', job)
    value['notification']['recipients'][0]['targetRef'] = target
    body = {'definition': value, 'expectedRevision': job['configRevision'], 'requestKey': key}
    response = h.client.put('/api/jobs/' + job['id'], json=body)
    return response, body


def test_null_target_real_no_attempt_cold_explicit_retarget_same_outbox_receipt(h):
    job = completed(h)
    original = notice(h.service, job['id'])
    frozen = copy.deepcopy(h.service.final.store.get(job['id'])['finalRuntime']['plans'])
    h.service.outbox_sender = sender
    asyncio.run(h.service.deliver_outbox())
    pending = notice(h.service, job['id'])
    assert pending['status'] == 'pending'
    assert pending['receipt']['phase'] == 'before-enqueue'
    assert pending['receipt']['binding'] == binding(pending)
    assert not session.get('ses_notice').queue_pending and not session.get('ses_b').queue_pending
    cold = FinalService(h.root, h.automation.auth)
    cold.outbox_sender = sender
    try:
        assert notice(cold, job['id']) == pending
        response, body = edit(h, job, 'ses_b')
        assert response.status_code == 200, response.text
        audit = response.json()['result']['runtimeAudit']
        assert audit['before']['notification']['recipients'][0]['targetRef'] is None
        assert audit['after']['notification']['recipients'][0]['targetRef'] == 'ses_b'
        assert audit['actor'] == {'type': 'user', 'id': 'local'}
        assert audit['fromRevision'] == job['configRevision']
        again = h.client.put('/api/jobs/' + job['id'], json=body)
        assert again.json()['operationId'] == response.json()['operationId']
        stale = h.client.put('/api/jobs/' + job['id'], json={**body, 'requestKey': 'stale'})
        assert stale.status_code == 409
        asyncio.run(cold.deliver_outbox())
        accepted = notice(cold, job['id'])
        assert accepted['outboxId'] == original['outboxId'] and accepted['status'] == 'acked'
        assert accepted['targetRef'] == 'ses_b'
        assert accepted['runtimeOperationId'] == audit['operationId']
        assert accepted['receipt']['binding'] == binding(accepted)
        assert accepted['payloadDigest'] == fc.digest(accepted['summary'])
        run = cold.final.history(job['id'])['runs'][0]
        public = h.client.get('/api/job-runs/' + run['runId'])
        assert public.status_code == 200, public.text
        assert public.json()['notifications'][0]['receipt']['binding'] == binding(accepted)
        queued = session.get('ses_b').queue_pending
        assert len(queued) == 1 and queued[0]['eventId'] == original['outboxId']
        payload = json.loads(queued[0]['result'])
        assert payload['targetSessionId'] == 'ses_b' and payload['creator'] == audit['actor']
        assert cold.final.store.get(job['id'])['finalRuntime']['plans'] == frozen
        current = response.json()['result']['object']
        changed, _ = edit(h, current, 'ses_notice', 'after-success')
        assert changed.status_code == 200
        asyncio.run(cold.deliver_outbox())
        assert notice(cold, job['id']) == accepted
        assert len(session.get('ses_b').queue_pending) == 1 and not session.get('ses_notice').queue_pending
    finally:
        asyncio.run(cold.close())


def test_concurrent_cas_then_two_service_reservations_send_one_notice(h):
    job = completed(h)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda pair: edit(h, job, pair[0], pair[1])[0],
                             [('ses_b', 'b'), ('ses_notice', 'notice')]))
    assert sorted(row.status_code for row in rows) == [200, 409]
    winner = next(row.json()['result']['object'] for row in rows if row.status_code == 200)
    target = winner['notification']['recipients'][0]['targetRef']
    cold = FinalService(h.root, h.automation.auth)
    cold.outbox_sender = h.service.outbox_sender = sender
    try:
        async def concurrent():
            await asyncio.gather(h.service.deliver_outbox(), cold.deliver_outbox())
        asyncio.run(concurrent())
        assert notice(cold, job['id'])['status'] == 'acked'
        assert len(session.get(target).queue_pending) == 1
        assert sum(len(session.get(s).queue_pending) for s in ['ses_b', 'ses_notice']) == 1
    finally:
        asyncio.run(cold.close())


def test_runtime_notice_authority_revoked_before_reservation_stays_suppressed(h):
    grants = h.automation.auth._default_permissions()
    grants['job']['execute'] = True
    grants['action']['manageSystemActions'] = True
    h.automation.auth.change_permissions(grants, 0)
    job = completed(h, 'ses_b', actor={'type': 'agent', 'id': 'owned-notice-author', 'revision': 1})
    h.automation.auth.change_permissions(h.automation.auth._default_permissions(), 1)
    h.service.outbox_sender = sender
    asyncio.run(h.service.deliver_outbox())
    original = notice(h.service, job['id'])
    assert original['status'] == 'suppressed'
    assert not session.get('ses_b').queue_pending
    response, _ = edit(h, job, 'ses_notice')
    assert response.status_code == 200
    asyncio.run(h.service.deliver_outbox())
    assert notice(h.service, job['id']) == original and not session.get('ses_notice').queue_pending


def test_accepted_notice_lost_response_retarget_remains_unknown_no_resend(h):
    job = completed(h, 'ses_b')
    calls = []
    async def lose(row):
        if row['channel'] == 'ws': return {'outcome': 'succeeded'}
        receipt = await enqueue_session_notice(row)
        assert receipt['outcome'] == 'succeeded'
        calls.append(row['outboxId'])
        raise ConnectionError('owned lost response after REAL queue acceptance')
    h.service.outbox_sender = lose
    asyncio.run(h.service.deliver_outbox())
    original = notice(h.service, job['id'])
    assert original['status'] == 'uncertain' and len(session.get('ses_b').queue_pending) == 1
    response, _ = edit(h, job, 'ses_notice')
    assert response.status_code == 200
    cold = FinalService(h.root, h.automation.auth)
    cold.outbox_sender = sender
    try:
        asyncio.run(cold.deliver_outbox())
        assert notice(cold, job['id']) == original and len(calls) == 1
        assert not session.get('ses_notice').queue_pending
    finally:
        asyncio.run(cold.close())


@pytest.mark.parametrize('kind', ['dictionary-spoof', 'wrong-binding', 'partial'])
def test_custom_or_corrupt_no_attempt_proof_never_authorizes_retry(h, kind):
    job = completed(h)
    async def adversarial(row):
        if row['channel'] == 'ws': return {'outcome': 'succeeded'}
        receipt = await enqueue_session_notice(row)  # actual local pre-send rejection
        assert receipt['outcome'] == 'pending'
        if kind == 'dictionary-spoof': return dict(receipt)
        if kind == 'partial': return {'outcome': 'partial'}
        receipt['binding']['outboxId'] = 'outbox_unrelated'
        return receipt
    h.service.outbox_sender = adversarial
    asyncio.run(h.service.deliver_outbox())
    original = notice(h.service, job['id'])
    assert original['status'] == 'uncertain'
    response, _ = edit(h, job, 'ses_b')
    assert response.status_code == 200
    h.service.outbox_sender = sender
    asyncio.run(h.service.deliver_outbox())
    assert notice(h.service, job['id']) == original and not session.get('ses_b').queue_pending


def test_fresh_production_browser_runtime_edit_and_original_notification_receipt(h):
    from starlette.staticfiles import StaticFiles
    root = Path(__file__).resolve().parents[1]
    runner = root / 'packages/web/e2e/retirement-notification-runtime.mjs'
    built = subprocess.run(['node', str(runner), '--build'], cwd=root,
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)
    assert built.returncode == 0, built.stdout + built.stderr
    h.app.mount('/react', StaticFiles(directory=root / 'packages/web/test-results/retirement-r14-production', html=True))
    job = completed(h)
    h.service.outbox_sender = sender
    asyncio.run(h.service.deliver_outbox())
    original = notice(h.service, job['id'])
    run = h.service.final.history(job['id'])['runs'][0]
    config_path = h.tmp / 'private-browser.json'
    cfg = {'url': str(h.client.base_url).rstrip('/'), 'token': h.token, 'name': job['name'],
        'jobId': job['id'], 'runId': run['runId'], 'outboxId': original['outboxId'], 'target': 'ses_b'}
    evidence = []
    for phase in ['edit', 'receipt']:
        config_path.write_text(json.dumps({**cfg, 'phase': phase}), encoding='utf-8')
        result = subprocess.run(['node', str(runner), str(config_path)], cwd=root,
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr
        evidence.append(json.loads(result.stdout.strip().splitlines()[-1]))
        if phase == 'edit':
            asyncio.run(h.service.deliver_outbox())
            assert notice(h.service, job['id'])['status'] == 'acked'
            assert len(session.get('ses_b').queue_pending) == 1
    assert notice(h.service, job['id'])['outboxId'] == original['outboxId']
    assert len(session.get('ses_b').queue_pending) == 1
    print('NOTIFICATION_RUNTIME_BROWSER=' + json.dumps(evidence))
