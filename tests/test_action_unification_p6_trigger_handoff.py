"""Owned cooperative Trigger receipt handoff; no invented old effects/writer."""
import asyncio
import os

import pytest

from packages.automation.store import Ledger
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService
from packages.jobs.runtime_owner import OwnerFence, enroll_fresh_profile
from tests.test_automation_canonical import canonical, subscription, fact, admit, USER
from tests.test_job_final_clients import tcp


@pytest.fixture
def h(original):
    return original


@pytest.fixture
def original(canonical, tmp_path):
    h = canonical
    job, trigger = subscription(h)
    event = fact(h)
    receipt = admit(h, event, trigger)
    root = tmp_path / 'cooperative-original'
    # Eligibility predates all source data. Never enroll an existing profile.
    enroll_fresh_profile(root)
    ledger = Ledger(root / 'automation')
    ledger.path.parent.mkdir()
    record = {'id': 'run_original_admission', 'recordType': 'triggerAdmission',
              'triggerId': trigger['id'], 'triggerRevision': trigger['revision'],
              'event': event, 'targetJobId': job['id'], 'result': receipt,
              'status': 'admitted', 'revision': 7}
    # Actual old store commit, fenced by its actual cooperating writer.
    ledger.mutate(lambda state: state['runs'].update({record['id']: record}))
    h.original_root, h.original_ledger, h.original_record = root, ledger, record
    yield h


def handoff(h):
    from packages.automation.retained_handoff import TriggerReceiptHandoff
    return TriggerReceiptHandoff(h.service, h.original_root)


def test_P6_TRIG_01_original_receipt_handoff_cold_query_cancel_no_new_identity(original):
    h = original
    rid = h.original_record['result']['runId']
    raw = h.original_ledger.path.read_bytes()
    if os.environ.get('P6_TRIGGER_BASELINE') == '1':
        # Original retained control cannot reconcile the admitted Job identity.
        # Execute the actual original ledger behavior, not an imitation.
        h.original_ledger.cancel(h.original_record['id'], 7)
    port = handoff(h)
    plan = port.preview([h.original_record['id']], actor=USER)
    assert plan['blocked'] == []
    before = h.service.final.get(h.original_record['targetJobId'])
    result = port.apply(plan, request_key='handoff-original', actor=USER)
    again = port.apply(plan, request_key='handoff-original', actor=USER)
    assert again['operationId'] == result['operationId'] and again['replayed']
    assert h.service.final.get(h.original_record['targetJobId']) == before
    cold = FinalService(h.root, h.automation.auth)
    try:
        from packages.automation.retained_handoff import TriggerReceiptHandoff
        current = TriggerReceiptHandoff(cold, h.original_root).query(h.original_record['id'], actor=USER)
        assert current['run']['runId'] == rid
        assert current['handoff']['revision'] == 7
        assert current['attempts'] == current['effects'] == current['notifications'] == []
    finally:
        asyncio.run(cold.close())
    # Control the SAME original Run via the real canonical HTTP boundary.
    response = h.client.post('/api/job-runs/' + rid + '/operations', json={
        'operation': 'cancel', 'expectedStateRevision': 0, 'requestKey': 'original-cancel'})
    assert response.status_code == 202, response.text
    asyncio.run(h.service.dispatch_once())
    assert port.query(h.original_record['id'], actor=USER)['run']['state'] == 'cancelled'
    assert h.original_ledger.path.read_bytes() == raw
    with pytest.raises(fc.ContractError):
        h.original_ledger.mutate(lambda state: state.update(revision=999))
    assert not h.provider.calls


def test_P6_TRIG_02_live_and_abandoned_lease_unknown_writer_and_bytes(original, tmp_path):
    h = original
    port = handoff(h)
    plan = port.preview([h.original_record['id']], actor=USER)
    raw = h.original_ledger.path.read_bytes()
    source_owner = OwnerFence(h.original_root)
    with source_owner.legacy_pass():
        with pytest.raises(fc.ContractError, match='Legacy pass'):
            port.apply(plan, request_key='live-lease', actor=USER)
    with source_owner.transaction() as db:
        db.execute("INSERT INTO legacy_leases VALUES('abandoned-original-writer')")
    with pytest.raises(fc.ContractError, match='Legacy pass'):
        port.apply(plan, request_key='unknown-lease', actor=USER)
    unknown = tmp_path / 'unknown'
    unknown.mkdir()
    from packages.automation.retained_handoff import TriggerReceiptHandoff
    with pytest.raises(fc.ContractError, match='Unknown original writer'):
        TriggerReceiptHandoff(h.service, unknown).preview(['anything'], actor=USER)
    assert not (unknown / 'job-runtime-owner.sqlite3').exists()
    with pytest.raises(FileExistsError):
        enroll_fresh_profile(unknown)
    assert h.original_ledger.path.read_bytes() == raw and not h.provider.calls


def test_P6_TRIG_03_crash_after_source_freeze_resumes_same_receipt(original, monkeypatch):
    h = original
    port = handoff(h)
    plan = port.preview([h.original_record['id']], actor=USER)
    method = h.service._operation
    def crash(*args, **kw):
        raise OSError('owned crash before canonical commit')
    monkeypatch.setattr(h.service, '_operation', crash)
    with pytest.raises(OSError, match='owned crash'):
        port.apply(plan, request_key='crash-handoff', actor=USER)
    with pytest.raises(fc.ContractError):
        h.original_ledger.mutate(lambda state: state.update(revision=99))
    monkeypatch.setattr(h.service, '_operation', method)
    receipt = port.apply(plan, request_key='crash-handoff', actor=USER)
    assert receipt['status'] == 'completed'
    assert port.query(h.original_record['id'], actor=USER)['run']['runId'] == h.original_record['result']['runId']
    assert not h.provider.calls


@pytest.mark.parametrize('change', ['missing-run', 'foreign-event', 'effectful-old-plan', 'runtime-drift'])
def test_P6_TRIG_04_incomplete_identity_and_drift_preserved(original, change):
    h = original
    port = handoff(h)
    plan = port.preview([h.original_record['id']], actor=USER)
    if change == 'runtime-drift':
        rid = h.original_record['result']['runId']
        h.service.control_run(rid, {'operation': 'pause', 'expectedStateRevision': 0,
                                  'requestKey': 'drift'}, actor=USER)
        with pytest.raises(fc.ContractError, match='runtime changed'):
            port.apply(plan, request_key='drifted', actor=USER)
    else:
        def alter(state):
            record = state['runs'][h.original_record['id']]
            if change == 'missing-run':
                record['result']['runId'] = 'run_missing'
            elif change == 'foreign-event':
                record['event']['id'] = 'foreign'
            else:
                record.pop('recordType')
        h.original_ledger.mutate(alter)
        plan = port.preview([h.original_record['id']], actor=USER)
        assert plan['entries'] == [] and len(plan['blocked']) == 1
        with pytest.raises(fc.ContractError):
            port.apply(plan, request_key='blocked', actor=USER)
    assert not h.provider.calls


def test_P6_TRIG_05_unknown_effect_is_kept_and_never_replayed(original):
    h = original
    h.provider.unknown = True
    asyncio.run(h.service.dispatch_once())
    assert len(h.provider.calls) == 1
    port = handoff(h)
    plan = port.preview([h.original_record['id']], actor=USER)
    assert plan['blocked'] == []
    before = h.service.final.get(h.original_record['targetJobId'])
    port.apply(plan, request_key='unknown-original', actor=USER)
    current = port.query(h.original_record['id'], actor=USER)
    assert current['run']['state'] == 'uncertain'
    assert current['attempts'] and current['effects']
    assert h.service.final.get(h.original_record['targetJobId']) == before
    asyncio.run(h.service.dispatch_once())
    assert len(h.provider.calls) == 1


def test_P6_TRIG_06_real_tcp_retained_fields_and_original_runtime(h, tcp):
    import httpx
    h.automation.ledger = h.original_ledger
    port = handoff(h)
    port.apply(port.preview([h.original_record['id']], actor=USER), request_key='http-handoff', actor=USER)
    with httpx.Client(base_url=tcp, trust_env=False) as client:
        assert client.get('/api/automation').status_code == 401
        response = client.get('/api/automation', headers={'Authorization': 'Bearer ' + h.token})
        assert response.status_code == 200, response.text
        record = response.json()['runs'][h.original_record['id']]
        assert record['id'] == h.original_record['id'] and record['revision'] == 7
        assert record['status'] == 'admitted'
        assert record['canonicalOriginal']['run']['runId'] == h.original_record['result']['runId']
        cancel = client.post('/api/automation/runs/' + h.original_record['id'] + '/cancel',
            json={'expectedRevision': 7}, headers={'Authorization': 'Bearer ' + h.token})
        assert cancel.status_code == 200, cancel.text
        assert cancel.json()['run']['id'] == h.original_record['id']
        assert cancel.json()['run']['cancellationRequested'] is True
        repeated = client.post('/api/automation/runs/' + h.original_record['id'] + '/cancel',
            json={'expectedRevision': 7}, headers={'Authorization': 'Bearer ' + h.token})
        assert repeated.json()['operation']['operationId'] == cancel.json()['operation']['operationId']
        refreshed = client.get('/api/automation', headers={'Authorization': 'Bearer ' + h.token}).json()
        assert refreshed['runs'][h.original_record['id']]['revision'] == 8
    asyncio.run(h.service.dispatch_once())
    assert port.query(h.original_record['id'], actor=USER)['run']['state'] == 'cancelled'
    assert not h.provider.calls


def test_P6_TRIG_07_original_lifecycle_grant_does_not_borrow_execute_or_read(original):
    h = original
    port = handoff(h)
    port.apply(port.preview([h.original_record['id']], actor=USER), request_key='grant-handoff', actor=USER)
    permissions = h.automation.auth._default_permissions()
    for row in permissions.values():
        for key in row:
            row[key] = False
    permissions['job']['lifecycle'] = True
    status = h.automation.auth.change_permissions(permissions, h.automation.auth.permissions_status()['revision'])
    actor = {'type': 'agent', 'id': 'owned-legacy-caller', 'revision': status['revision']}
    response = asyncio.run(port.cancel(h.original_record['id'], 7, actor=actor))
    assert response['run']['cancellationRequested'] is True
    assert not h.automation.auth.permission_allowed('job', 'execute')[0]
    assert not h.automation.auth.permission_allowed('job', 'read')[0]
    with pytest.raises(fc.ContractError):
        h.service.control_run(response['operation']['runId'], {'operation': 'resume',
            'expectedStateRevision': 1, 'requestKey': 'cannot-resume'}, actor=actor)
    permissions['job']['lifecycle'] = False
    h.automation.auth.change_permissions(permissions, status['revision'])
    with pytest.raises(fc.ContractError):
        asyncio.run(port.cancel(h.original_record['id'], 7, actor=actor))
    assert not h.provider.calls


def test_P6_TRIG_08_same_profile_joins_actual_owner_without_clobbering_activation(tmp_path):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from packages.automation.service import Service
    from packages.jobs import final_api
    from packages.jobs.final_service import bind, definition, object_ref
    from tests.test_automation_canonical import SessionProvider
    root = tmp_path / 'same-profile'
    enroll_fresh_profile(root)
    automation = Service(root / 'automation', SimpleNamespace(workers={}, set_running_observer=lambda cb: None), None)
    automation.auth = final_api.CanonicalUserAuth(root / 'automation')
    app = FastAPI()
    service = bind(app, automation, root / 'canonical')
    app.state.automation = automation
    final_api.install(app)
    client = TestClient(app)
    client.headers['Authorization'] = 'Bearer ' + automation.auth.login(automation.auth.key())
    try:
        product = next(item for item in service.actions(actor=USER)['items'] if item['id'] == 'product_session_message')
        value = definition('action', product)
        value['system'] = False
        action = service.publish_action('owned_same_profile_message', value)
        h = SimpleNamespace(service=service, automation=automation, client=client,
                            action=object_ref('action', action))
        job, trigger = subscription(h)
        event = fact(h)
        receipt = admit(h, event, trigger)
        ledger = automation.ledger
        record = {'id': 'run_same_profile_admission', 'recordType': 'triggerAdmission',
                  'triggerId': trigger['id'], 'triggerRevision': trigger['revision'],
                  'event': event, 'targetJobId': job['id'], 'result': receipt, 'status': 'admitted', 'revision': 3}
        ledger.mutate(lambda state: state['runs'].update({record['id']: record}))
        raw = ledger.path.read_bytes()
        service.owner.claim(0)
        service.owner.activation_intent('original-profile-activation')
        service.owner.set_state('active')
        from packages.automation.retained_handoff import TriggerReceiptHandoff
        port = TriggerReceiptHandoff(service, root)
        plan = port.preview([record['id']], actor=USER)
        result = port.apply(plan, request_key='same-profile-handoff', actor=USER)
        assert result['status'] == 'completed'
        assert service.owner.status()['state'] == 'active'
        assert service.owner.activation_intent() == 'original-profile-activation'
        assert port.query(record['id'], actor=USER)['run']['runId'] == receipt['runId']
        assert ledger.path.read_bytes() == raw
    finally:
        client.close()
        asyncio.run(automation.close())
        asyncio.run(service.close())


def test_P6_TRIG_09_stale_destination_owner_cannot_freeze_source(original):
    h = original
    port = handoff(h)
    plan = port.preview([h.original_record['id']], actor=USER)
    token = h.service.owner.token
    h.service.owner.token = (token[0], 'unowned-nonce')
    try:
        with pytest.raises(fc.ContractError, match='owner epoch'):
            port.apply(plan, request_key='stale-owner', actor=USER)
        assert OwnerFence(h.original_root).status() is None
    finally:
        h.service.owner.token = token
