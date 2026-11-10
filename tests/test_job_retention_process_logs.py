"""Retention against real HTTP, detached execution, and owned process files."""
import asyncio
import copy
import json
import os
import subprocess
import sys

import pytest

from packages.jobs import retention, final_contracts as fc
from packages.jobs.independent_executor import ExecutionConflict
from tests.test_job_process_logs import live, start, terminal, wait
from tests.test_job_global_retention import configure, POINT


def settled(c, *, failed=False):
    start(c, "open('effects','a').write('once\\n')\nprint('original business output')\n"
          + ('raise SystemExit(7)\n' if failed else ''))
    doc = terminal(c, 'failed' if failed else 'completed')
    # Flush every original execution phase/terminal notice through the host's
    # real receipt reconciler before asserting the resources are unreferenced.
    asyncio.run(c.service.recover_external_receipts())
    state = c.service.final.store.get(c.job['id'])['finalRuntime']
    for row in state['outbox'].values():
        c.service.final.settle_notification(c.job['id'], row['legacyId'], outbox_id=row['outboxId'],
            receipt={'outcome': 'suppressed', 'originalKey': row['legacyId'],
                     'outboxId': row['outboxId'], 'reason': 'Owned retention fixture without dashboard'})
    c.service.final.store.mutate(c.job['id'], lambda job:
        job['finalRuntime']['runs'][c.rid].update(finishedAt=POINT - 10 * retention.DAY))
    return doc, c.root / 'executions' / 'logs' / (doc['logRef']['id'] + '.log')


def test_lost_put_response_original_key_http_lookup_is_readonly(live, monkeypatch):
    import httpx
    c = live
    body = {'expectedRevision': 0, 'requestKey': 'owned-lost-retention-response',
        'expectedRequestScope': c.client.get('/api/job-maintenance/retention-settings').json()['requestScope'],
        'rules': {'logs': {'enabled': True, 'days': 3}}}
    # The actual HTTP server commits before the local transport loses delivery;
    # the UI has no operationId and must query using only the original key.
    send = c.client.send
    observed = []
    def lose(request, **kwargs):
        result = send(request, **kwargs)
        assert result.status_code == 200, result.text
        observed.append(result.json())
        raise httpx.ReadError('owned response dropped after server commit', request=request)
    monkeypatch.setattr(c.client, 'send', lose)
    with pytest.raises(httpx.ReadError, match='response dropped'):
        c.client.put('/api/job-maintenance/retention-settings', json=body)
    monkeypatch.setattr(c.client, 'send', send)
    before = c.client.get('/api/job-maintenance/retention-settings').json()
    params = {'operation': 'update', 'requestKey': body['requestKey']}
    response = c.client.get('/api/operations', params=params)
    assert response.status_code == 200 and response.json() == observed[0]
    assert response.json()['result']['settings']['revision'] == before['revision'] == 1
    assert c.client.get('/api/job-maintenance/retention-settings').json() == before
    missing = c.client.get('/api/operations', params={**params, 'requestKey': 'owned-not-admitted'})
    assert missing.status_code == 404 and missing.json()['error']['code'] == 'not_found'
    assert c.client.get('/api/job-maintenance/retention-settings').json() == before


@pytest.mark.parametrize('archive', [False, True])
def test_real_log_unlink_history_reduction_cold_identity_and_no_replay(live, archive):
    c = live
    original, path = settled(c)
    assert path.is_file() and path.stat().st_size > 0
    if archive:
        response = c.client.request('DELETE', '/api/jobs/' + c.job['id'], json={
            'expectedRevision': c.job['configRevision'], 'expectedStateRevision': c.job['stateRevision'],
            'requestKey': 'retire'})
        assert response.status_code == 200, response.text
        c.service.final.delete(c.job['id'], expected_revision=c.job['configRevision'])
    configure(c, {'completed': {'enabled': True, 'days': 1}, 'logs': {'enabled': True, 'days': 2}})
    result = retention.scan(c.service, at=POINT)
    with c.store.connect() as db:
        pending = db.execute('SELECT payload FROM execution_outbox WHERE acked=0').fetchall()
    assert result['completed']['expired'] == 1, (result, pending, c.store.get(c.eid))
    assert result['logs']['expired'] == 1 and result['logs']['errors'] == [], result
    assert not path.exists()
    retention.reduce_executions(c.service)
    doc = c.store.get(c.eid)
    assert doc['historyExpired']['runId'] == c.rid and doc['logExpired']['logRef'] == original['logRef']
    assert 'frozenPlan' not in doc and 'original business output' not in json.dumps(doc)
    assert c.client.get(c.url).status_code == 410
    assert c.client.get(c.url).json()['error']['code'] == 'log_expired'
    projection = c.client.get('/api/job-runs/' + c.rid + '/logs').json()['items'][0]
    assert projection['status'] == 'expired' and projection['reason'] == 'log_expired'
    assert c.client.get('/api/job-runs/' + c.rid).json()['error']['code'] == 'history_expired'
    from packages.jobs.window import command_window_options
    code = """import asyncio,json,sys
from packages.jobs.final_service import FinalService
from packages.jobs.final_api import CanonicalUserAuth
from packages.jobs.final_contracts import ContractError
s=FinalService(sys.argv[1],CanonicalUserAuth(sys.argv[2]))
who={'type':'user','id':'local'}
operation=s.operation_for_key('create','run',actor=who)
assert operation['runId']==sys.argv[3]
try:
 asyncio.run(s.read_process_log(sys.argv[3],sys.argv[4],actor=who))
except ContractError as error:
 assert error.code=='log_expired'
else:
 raise AssertionError('Expired log must be explicit')
print(json.dumps({'runId':operation['runId'],'status':operation['status'],'log':'log_expired'}))
asyncio.run(s.close())
"""
    cold = subprocess.run([sys.executable, '-c', code, str(c.root), str(c.tmp / 'automation'), c.rid, c.aid],
        capture_output=True, text=True, timeout=20, **command_window_options())
    assert cold.returncode == 0, cold.stderr
    assert json.loads(cold.stdout) == {'runId': c.rid, 'status': 'completed', 'log': 'log_expired'}
    # The original execution store admits exactly the original receipt, even
    # after output/frozen plan reduction; another lease cannot retry it.
    replay, fresh = c.store.admit(job_id=c.job['id'], attempt=original['attempt'],
        effect_key=c.key, frozen_plan=original['frozenPlan'])
    assert not fresh and replay['effectId'] == c.eid
    with pytest.raises(fc.ContractError) as expired:
        c.store.request_cleanup(c.eid, original['attempt'])
    assert expired.value.code == 'history_expired'
    later = {**original['attempt'], 'attemptId': 'later', 'generation': original['attempt']['generation'] + 1}
    with pytest.raises(ExecutionConflict, match='expired'):
        c.store.admit(job_id=c.job['id'], attempt=later, effect_key=c.key,
                      frozen_plan=original['frozenPlan'], retry=True)
    asyncio.run(c.service.dispatch_once())
    assert (c.tmp / 'effects').read_text().splitlines() == ['once']


@pytest.mark.parametrize('hold', ['pin', 'unknown', 'pending_notice', 'history', 'shared_archive', 'recent',
                                 'hardlink', 'root_reparse', 'fence', 'unsafe_ref'])
def test_real_log_protection_and_independent_days(live, hold, monkeypatch):
    c = live
    doc, path = settled(c)
    if hold == 'pin':
        c.service.final.set_pin(c.job['id'], True, expected_revision=0)
    elif hold == 'unknown':
        c.service.final.store.mutate(c.job['id'], lambda job:
            job['finalRuntime']['runs'][c.rid].update(state='uncertain'))
    elif hold in {'pending_notice', 'history'}:
        with c.store.connect() as db:
            if hold == 'pending_notice':
                db.execute('INSERT INTO execution_outbox VALUES(?,?,0)', ('owned-pending', json.dumps({'effectId': c.eid})))
            else:
                doc['history'] = [copy.deepcopy(doc)]
                c.store._save(db, doc)
    elif hold == 'shared_archive':
        archived = c.service.final.store.get(c.job['id'])
        archived['jobId'] = 'owned-other-archive'
        with c.service.transaction() as db:
            c.service._save(db, 'runtime_archive', archived['jobId'], archived)
    elif hold == 'recent':
        os.utime(path, (POINT + retention.DAY, POINT + retention.DAY))
    elif hold == 'hardlink':
        os.link(path, c.tmp / 'retained-hardlink')
    elif hold == 'root_reparse':
        from packages.core import background_jobs
        monkeypatch.setattr(background_jobs, '_path_has_reparse_component', lambda _: True)
    else:
        if hold == 'fence':
            doc['attempt']['leaseId'] = 'other-original-lease'
        else:
            doc['logRef']['id'] = '../outside'
        with c.store.connect() as db:
            c.store._save(db, doc)
    before = path.read_bytes()
    configure(c, {'logs': {'enabled': True, 'days': 11}})
    assert retention.scan(c.service, at=POINT)['logs']['expired'] == 0
    configure(c, {'logs': {'days': 1}}, 'shorter')
    result = retention.scan(c.service, at=POINT + retention.DAY)
    assert result['logs']['expired'] == 0, result
    assert path.read_bytes() == before
    assert 'logExpired' not in c.store.get(c.eid)


def test_interrupted_original_log_purge_resumes_without_execution(live, monkeypatch):
    c = live
    doc, path = settled(c)
    configure(c, {'logs': {'enabled': True, 'days': 1}})
    from packages.jobs.independent_executor import ExecutionStore
    original_save = ExecutionStore._save
    def interrupted(db, value):
        if value.get('logExpired'):
            raise OSError('owned injected failure after unlink')
        original_save(db, value)
    monkeypatch.setattr(ExecutionStore, '_save', staticmethod(interrupted))
    result = retention.scan(c.service, at=POINT)
    assert result['logs']['errors'] and not path.exists()
    assert c.store.get(c.eid)['logPurge']['logRef'] == doc['logRef']
    monkeypatch.setattr(ExecutionStore, '_save', staticmethod(original_save))
    result = retention.scan(c.service, at=POINT + retention.DAY)
    assert result['logs']['expired'] == 1 and not result['logs']['errors']
    assert c.client.get(c.url).json()['error']['code'] == 'log_expired'
    assert (c.tmp / 'effects').read_text().splitlines() == ['once']


def test_real_cancelled_run_uses_cancelled_days_and_preserves_independent_log(live):
    c = live
    start(c, "import time\nopen('effects','a').write('once\\n')\nopen('ready','w').write('yes')\ntime.sleep(30)\n")
    wait(lambda: (c.tmp / 'ready').exists(), bool)
    run = c.client.get('/api/job-runs/' + c.rid).json()
    result = c.client.post('/api/job-runs/' + c.rid + '/operations', json={
        'operation': 'cancel', 'expectedStateRevision': run['stateRevision'], 'requestKey': 'owned-cancel'})
    assert result.status_code == 202, result.text
    doc = terminal(c, 'cancelled')
    state = c.service.final.store.get(c.job['id'])['finalRuntime']
    assert state['cancellations'][c.rid]['status'] == 'confirmed'
    assert 'pendingProcessAttempt' not in state['plans'][c.rid]
    for row in state['outbox'].values():
        c.service.final.settle_notification(c.job['id'], row['legacyId'], outbox_id=row['outboxId'],
            receipt={'outcome': 'suppressed', 'originalKey': row['legacyId'],
                     'outboxId': row['outboxId'], 'reason': 'Owned cancelled retention fixture'})
    c.service.final.store.mutate(c.job['id'], lambda job:
        job['finalRuntime']['runs'][c.rid].update(finishedAt=POINT - 10 * retention.DAY))
    configure(c, {k: {'enabled': True, 'days': 11 if k == 'cancelled' else 1}
                  for k in ('completed', 'failed', 'timed_out', 'cancelled')})
    assert all(row['expired'] == 0 for row in retention.scan(c.service, at=POINT).values())
    result = retention.scan(c.service, at=POINT + retention.DAY)
    assert result['cancelled']['expired'] == 1, result
    assert all(row['expired'] == 0 for key, row in result.items() if key != 'cancelled')
    assert (c.root / 'executions' / 'logs' / (doc['logRef']['id'] + '.log')).is_file()
    assert c.client.get(c.url).status_code == 200
    assert (c.tmp / 'effects').read_text().splitlines() == ['once']


def test_log_only_cleanup_preserves_schema_valid_history_and_original_run(live):
    c = live
    original, path = settled(c)
    before = c.client.get('/api/job-runs/' + c.rid).json()
    configure(c, {'logs': {'enabled': True, 'days': 1}})
    result = retention.scan(c.service, at=POINT)['logs']
    assert result['expired'] == 1 and not result['errors'] and not path.exists()
    assert c.client.get('/api/job-runs/' + c.rid).json() == before
    state = c.service.final.store.get(c.job['id'])['finalRuntime']
    for row in state['effects']:
        assert fc.validate_record('effect', row) == row
    assert state['expiredLogs'][c.aid]['logRef'] == original['logRef']
    assert c.client.get('/api/job-runs/' + c.rid + '/logs').json()['items'][0]['status'] == 'expired'
    assert c.client.get(c.url).json()['error']['code'] == 'log_expired'


def next_attempt(doc):
    return {**doc['attempt'], 'attemptId': 'owned-next-attempt', 'leaseId': 'owned-next-lease',
            'generation': doc['attempt']['generation'] + 1}


def test_history_reservation_fences_new_attempt_before_and_after_canonical_commit(live, monkeypatch):
    c = live
    original, _ = settled(c, failed=True)
    configure(c, {'failed': {'enabled': True, 'days': 1}})
    expire = retention.expire_history
    def before_commit(service, job, state, rid, point):
        assert c.store.get(c.eid)['historyPurge']['runId'] == rid
        with pytest.raises(ExecutionConflict, match='cleanup admitted'):
            c.store.admit(job_id=c.job['id'], attempt=next_attempt(original), effect_key=c.key,
                          frozen_plan=original['frozenPlan'], retry=True)
        return expire(service, job, state, rid, point)
    monkeypatch.setattr(retention, 'expire_history', before_commit)
    assert retention.scan(c.service, at=POINT)['failed']['expired'] == 1
    assert c.store.get(c.eid).get('frozenPlan')  # Intent covers the reduction interval.
    with pytest.raises(ExecutionConflict, match='cleanup admitted'):
        c.store.admit(job_id=c.job['id'], attempt=next_attempt(original), effect_key=c.key,
                      frozen_plan=original['frozenPlan'], retry=True)
    retention.reduce_executions(c.service)
    assert c.store.get(c.eid).get('historyExpired') and 'historyPurge' not in c.store.get(c.eid)
    assert (c.tmp / 'effects').read_text().splitlines() == ['once']


def test_concurrent_attempt_wins_preflight_and_is_protected_at_reservation(live, monkeypatch):
    c = live
    original, _ = settled(c, failed=True)
    configure(c, {'failed': {'enabled': True, 'days': 1}})
    before = c.service.final.store.get(c.job['id'])
    check = retention.external_safe
    def race(service, job, state, rid):
        assert check(service, job, state, rid)
        admitted, fresh = c.store.admit(job_id=c.job['id'], attempt=next_attempt(original), effect_key=c.key,
            frozen_plan=original['frozenPlan'], retry=True)
        assert fresh and admitted['result'] is None
        return True
    monkeypatch.setattr(retention, 'external_safe', race)
    result = retention.scan(c.service, at=POINT)['failed']
    assert result['expired'] == 0 and result['protected'] == 1
    assert c.service.final.store.get(c.job['id']) == before
    assert 'historyPurge' not in c.store.get(c.eid)
    assert (c.tmp / 'effects').read_text().splitlines() == ['once']


def test_history_reservation_survives_rollback_and_cold_owner_releases_only_original_intent(live, monkeypatch):
    from packages.jobs.final_service import FinalService
    c = live
    c.service.owner.claim(0)
    original, _ = settled(c, failed=True)
    configure(c, {'failed': {'enabled': True, 'days': 1}})
    before = c.service.final.store.get(c.job['id'])
    save = c.service._save
    def rollback(db, kind, identity, value):
        if kind == 'retention_settings' and value['lastRuns']['failed']:
            raise OSError('owned rollback after external history reservation')
        return save(db, kind, identity, value)
    monkeypatch.setattr(c.service, '_save', rollback)
    with pytest.raises(OSError, match='owned rollback'):
        retention.scan(c.service, at=POINT)
    assert c.service.final.store.get(c.job['id']) == before
    assert c.store.get(c.eid)['historyPurge']['runId'] == c.rid
    with pytest.raises(ExecutionConflict, match='cleanup admitted'):
        c.store.admit(job_id=c.job['id'], attempt=next_attempt(original), effect_key=c.key,
                      frozen_plan=original['frozenPlan'], retry=True)
    asyncio.run(c.service.close())
    cold = FinalService(c.root, c.auth)
    cold.owner.claim(cold.owner.status()['epoch'])
    try:
        retention.recover_history_intents(cold)
        assert 'historyPurge' not in c.store.get(c.eid)
        assert retention.scan(cold, at=POINT)['failed']['expired'] == 1
        retention.reduce_executions(cold)
        assert c.store.get(c.eid).get('historyExpired')
        assert cold.operation_for_key('create', 'run', actor={'type': 'user', 'id': 'local'})['runId'] == c.rid
    finally:
        asyncio.run(cold.close())
    assert (c.tmp / 'effects').read_text().splitlines() == ['once']
