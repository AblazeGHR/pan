"""Write-time literal validation using real canonical and Session stores.

No provider effects are invoked. Frozen plans, SQL rows, original request keys,
Session bytes and Worker registry establish the write boundary directly.
"""
import copy
import asyncio

import pytest

from packages.core import session, worker
from packages.jobs.final_service import definition, object_ref
from tests.job_retirement_native_fixture import h  # noqa: F401
from tests.test_job_final_http_service import USER, create, control, job_def, launch
from tests.test_job_retirement_provider_boundaries import channel_definition


def snapshot(h):
    with h.service.transaction(readonly=True) as db:
        rows = {table: [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in ('final_documents', 'final_operations', 'final_authorities')}
    return (rows, set(worker.workers),
        {str(p.relative_to(session.SESSION_DIR)): p.read_bytes()
         for p in session.SESSION_DIR.rglob('*') if p.is_file()})


def stored_runtime(h, job_id):
    with h.service.transaction(readonly=True) as db:
        return copy.deepcopy(h.service._get(db, 'job', job_id)['finalRuntime'])


def session_definition(h, kind, ids, **kwargs):
    value = job_def(h, **kwargs)
    if kind == 'broadcast':
        action = next(row for row in h.service.actions(actor=USER)['items']
                      if row['id'] == 'product_session_broadcast')
        value['steps'][0]['actionRef'] = object_ref('action', action)
        value['steps'][0]['arguments'] = {
            'sessionIds': {'from': 'literal', 'value': ids},
            'text': {'from': 'literal', 'value': '继续'}}
    else:
        value['steps'][0]['arguments']['sessionId']['value'] = ids[0]
    return value


@pytest.mark.parametrize('kind', ['message', 'broadcast', 'qq'])
@pytest.mark.parametrize('name', ['Legal English', '合法中文', '  合法 English  '])
def test_legal_literal_create_edit_cas_names_and_frozen_run(h, kind, name):
    first = session.create('owned first')
    second = session.create('owned second')
    value = channel_definition(h) if kind == 'qq' else session_definition(h, kind, [first.id])
    value['name'] = name
    no_spawn = (set(worker.workers), snapshot(h)[2])
    item = create(h, value=value)
    assert item['name'] == name
    item = control(h, 'jobs', item, 'enable').json()['result']['object']
    receipt = launch(h, item)
    state = stored_runtime(h, item['id'])
    frozen = copy.deepcopy((state['runs'], state['plans']))
    edited = copy.deepcopy(value)
    edited['name'] = name + ' 编辑'
    args = edited['steps'][0]['arguments']
    if kind == 'qq':
        args['targetType']['value'] = 'group'
        args['targetId']['value'] = '654321'
        args['botUin']['value'] = '456789'
    else:
        target = 'sessionIds' if kind == 'broadcast' else 'sessionId'
        args[target]['value'] = [first.id, second.id] if kind == 'broadcast' else second.id
    args['text']['value'] = '  edited text 中文  '
    url = '/api/jobs/' + item['id']
    body = {'definition': edited, 'expectedRevision': 1, 'requestKey': 'legal-edit'}
    response = h.client.put(url, json=body)
    assert response.status_code == 200, response.text
    updated = response.json()['result']['object']
    assert updated['name'] == edited['name'] and updated['configRevision'] == 2
    assert updated['steps'] == edited['steps']
    assert h.client.get(url).json() == updated
    replay = h.client.put(url, json=body).json()
    assert replay['operationId'] == response.json()['operationId'] and replay['replayed'] is True
    before_stale = snapshot(h)
    assert h.client.put(url, json={**body, 'requestKey': 'stale-edit'}).status_code == 409
    assert snapshot(h) == before_stale
    state = stored_runtime(h, item['id'])
    assert (state['runs'], state['plans']) == frozen
    assert state['runs'][receipt['runId']]['configRevision'] == 1
    assert (set(worker.workers), snapshot(h)[2]) == no_spawn


@pytest.mark.parametrize('kind', ['message', 'broadcast', 'qq'])
@pytest.mark.parametrize('operation', ['create', 'update'])
def test_invalid_literal_transaction_has_no_definition_operation_revision_or_spawn(h, kind, operation):
    existing = session.create('owned valid')
    value = channel_definition(h) if kind == 'qq' else session_definition(h, kind, [existing.id])
    item = create(h, value=value) if operation == 'update' else None
    if kind == 'qq':
        value['steps'][0]['arguments']['targetId']['value'] = 'not-numeric'
    else:
        key = 'sessionIds' if kind == 'broadcast' else 'sessionId'
        value['steps'][0]['arguments'][key]['value'] = (
            [existing.id, 'ses_owned_missing'] if kind == 'broadcast' else 'ses_owned_missing')
    before = snapshot(h)
    url = '/api/jobs' + ('/' + item['id'] if item else '')
    body = {'definition': value, 'expectedRevision': 1 if item else 0, 'requestKey': 'rejected-write'}
    response = h.client.request('PUT' if item else 'POST', url, json=body)
    assert response.status_code == 400, response.text
    assert snapshot(h) == before
    assert h.client.get('/api/operations', params={
        'operation': operation, 'requestKey': body['requestKey']}).status_code == 404


@pytest.mark.parametrize('invalid', [None, [], '', 123])
def test_fully_literal_schema_rejects_wrong_target_types_before_storage(h, invalid):
    value = channel_definition(h)
    value['steps'][0]['arguments']['targetId']['value'] = invalid
    before = snapshot(h)
    result = h.client.post('/api/jobs', json={
        'definition': value, 'expectedRevision': 0, 'requestKey': 'bad-type'})
    assert result.status_code == 400, result.text
    assert result.json()['error']['code'] == 'invalid_schema'
    assert snapshot(h) == before


@pytest.mark.parametrize('source', ['input', 'event'])
def test_mixed_bindings_defer_complete_schema_validation_until_resolution(h, source):
    value = channel_definition(h)
    args = value['steps'][0]['arguments']
    args['targetId'] = {'from': source, 'path': 'target'}
    # Closed Action schema requires targetId; validating only literal fields
    # as a complete object would reject this valid dynamic mapping prematurely.
    value['inputSchema'] = {'type': 'object', 'properties': {'target': {'type': 'string'}}}
    item = create(h, value=value)
    item = control(h, 'jobs', item, 'enable').json()['result']['object']
    before = snapshot(h)
    # Both mappings resolve to a bad value (or a missing Event field for manual
    # input). Existing admission validation must reject without persisting Run.
    result = h.client.post('/api/jobs/' + item['id'] + '/runs', json={
        'expectedRevision': 1, 'requestKey': 'bad-resolved-input',
        'input': {'target': 'not-numeric'}, 'cause': {'type': 'manual'}})
    assert result.status_code == 400, result.text
    assert snapshot(h) == before
    if source == 'input':
        good = h.client.post('/api/jobs/' + item['id'] + '/runs', json={
            'expectedRevision': 1, 'requestKey': 'good-resolved-input',
            'input': {'target': '123456'}, 'cause': {'type': 'manual'}})
        assert good.status_code == 202, good.text


@pytest.mark.parametrize('kind', ['message', 'broadcast'])
def test_literal_session_is_checked_even_when_text_is_dynamic_and_cold_store_is_used(h, kind):
    existing = session.create('owned cold Session')
    value = session_definition(h, kind, [existing.id])
    value['steps'][0]['arguments']['text'] = {'from': 'input', 'path': 'text'}
    value['inputSchema'] = {'type': 'object', 'properties': {'text': {'type': 'string'}}}
    session._cache.clear()
    before = snapshot(h)
    item = create(h, value=value)
    assert session.get(existing.id, load_history=False) is not None
    assert snapshot(h)[1:] == before[1:]
    target = 'sessionIds' if kind == 'broadcast' else 'sessionId'
    value['steps'][0]['arguments'][target]['value'] = (
        ['ses_owned_missing'] if kind == 'broadcast' else 'ses_owned_missing')
    before = snapshot(h)
    response = h.client.put('/api/jobs/' + item['id'], json={
        'definition': value, 'expectedRevision': 1, 'requestKey': 'bad-mixed-session'})
    assert response.status_code == 400, response.text
    assert snapshot(h) == before


def test_custom_session_parameter_name_does_not_acquire_product_business_validation(h):
    action = definition('action', next(row for row in h.service.actions(actor=USER)['items']
                                    if row['id'] == 'product_session_message'))
    action.update(name='custom literal semantics', system=False)
    custom = h.service.publish_action('owned_custom_message', action)
    value = job_def(h)
    value['steps'][0]['actionRef'] = object_ref('action', custom)
    value['steps'][0]['arguments']['sessionId']['value'] = 'custom-non-session-reference'
    before = snapshot(h)
    item = create(h, value=value)
    assert item['steps'] == value['steps']
    assert snapshot(h)[1:] == before[1:]


def test_resolved_immutable_action_schema_is_used_after_reference_checks(h):
    value = channel_definition(h)
    value['steps'][0]['arguments']['targetId']['value'] = 'bad'
    value['steps'][0]['actionRef']['digest'] = '0' * 64
    before = snapshot(h)
    result = h.client.post('/api/jobs', json={
        'definition': value, 'expectedRevision': 0, 'requestKey': 'wrong-digest'})
    assert result.status_code == 409, result.text
    assert snapshot(h) == before


@pytest.mark.parametrize('field,value', [('botUin', 'untrusted'), ('text', ''),
                                        ('endpoint', 'http://untrusted'), ('sessionId', 'untrusted')])
def test_literal_qq_schema_rejects_invalid_or_undeclared_fields(h, field, value):
    job = channel_definition(h)
    job['steps'][0]['arguments'][field] = {'from': 'literal', 'value': value}
    before = snapshot(h)
    result = h.client.post('/api/jobs', json={
        'definition': job, 'expectedRevision': 0, 'requestKey': 'invalid-qq-field'})
    assert result.status_code == 400, result.text
    assert snapshot(h) == before


def test_empty_literal_object_is_validated_against_required_action_fields(h):
    job = channel_definition(h)
    job['steps'][0]['arguments'] = {}
    before = snapshot(h)
    result = h.client.post('/api/jobs', json={
        'definition': job, 'expectedRevision': 0, 'requestKey': 'missing-fields'})
    assert result.status_code == 400, result.text
    assert snapshot(h) == before


@pytest.mark.parametrize('kind', ['message', 'broadcast'])
def test_dynamic_session_target_is_not_looked_up_as_a_literal(h, kind):
    job = session_definition(h, kind, ['unused'])
    target = 'sessionIds' if kind == 'broadcast' else 'sessionId'
    job['steps'][0]['arguments'][target] = {'from': 'input', 'path': 'target'}
    job['inputSchema'] = {'type': 'object', 'properties': {'target': (
        {'type': 'array', 'items': {'type': 'string'}} if kind == 'broadcast' else {'type': 'string'})}}
    before = snapshot(h)
    item = create(h, value=job)
    assert snapshot(h)[1:] == before[1:]
    # A real target resolves only at admission; no effect/Worker is dispatched.
    actual = session.create('owned dynamic target')
    item = control(h, 'jobs', item, 'enable').json()['result']['object']
    result = h.client.post('/api/jobs/' + item['id'] + '/runs', json={
        'expectedRevision': 1, 'requestKey': 'dynamic-target', 'input': {
            'target': [actual.id] if kind == 'broadcast' else actual.id}, 'cause': {'type': 'manual'}})
    assert result.status_code == 202, result.text
    frozen = stored_runtime(h, item['id'])['plans'][result.json()['runId']]['frozen']
    assert frozen['steps'][0]['arguments'][target] == {'from': 'input', 'path': 'target'}
    assert set(worker.workers) == before[1]


@pytest.mark.parametrize('kind', ['message', 'broadcast'])
@pytest.mark.parametrize('invalid', [None, 123, {}])
def test_fixed_session_target_type_in_mixed_mapping_uses_its_schema(h, kind, invalid):
    job = session_definition(h, kind, ['unused'])
    target = 'sessionIds' if kind == 'broadcast' else 'sessionId'
    job['steps'][0]['arguments'][target]['value'] = invalid
    job['steps'][0]['arguments']['text'] = {'from': 'input', 'path': 'text'}
    before = snapshot(h)
    result = h.client.post('/api/jobs', json={
        'definition': job, 'expectedRevision': 0, 'requestKey': 'bad-fixed-type'})
    assert result.status_code == 400, result.text
    assert snapshot(h) == before


def test_real_session_validation_keeps_system_grant_and_high_risk_fences(h):
    existing = session.create('owned authorization target')
    grants = h.automation.auth._default_permissions()
    grants['job']['execute'] = True
    h.automation.auth.change_permissions(grants, 0)
    product = next(row for row in h.service.actions(actor=USER)['items']
                   if row['id'] == 'product_session_message')
    value = session_definition(h, 'message', [existing.id])
    item = create(h, value=value)
    item = control(h, 'jobs', item, 'enable').json()['result']['object']
    before = snapshot(h)[1:]
    receipt = h.service.create_run(item['id'], {'expectedRevision': 1,
        'requestKey': 'agent-system-run', 'input': {}, 'cause': {'type': 'manual'}},
        actor={'type': 'agent', 'id': 'agent', 'revision': 1})
    asyncio.run(h.service.dispatch_once())
    state = stored_runtime(h, item['id'])
    assert state['runs'][receipt['runId']]['state'] == 'failed'
    assert state['attempts'] == {} and state['effects'] == []
    assert snapshot(h)[1:] == before


@pytest.mark.parametrize('requires_nonblank', [False, True])
def test_business_payload_name_uses_immutable_action_schema_through_http(h, requires_nonblank):
    value = channel_definition(h)
    action_id = value['steps'][0]['actionRef']['id']
    product = next(row for row in h.service.actions(actor=USER)['items'] if row['id'] == action_id)
    custom = definition('action', product)
    name_schema = {'type': 'string', 'minLength': 1}
    if requires_nonblank:
        name_schema['pattern'] = r'\S'
    custom['inputSchema']['properties']['payload'] = {
        'type': 'object', 'properties': {'name': name_schema}, 'required': ['name'],
        'additionalProperties': False, 'default': {'name': ' '}}
    action = h.service.publish_action('owned_business_payload', custom)
    value['steps'][0]['actionRef'] = object_ref('action', action)
    value['steps'][0]['arguments']['payload'] = {'from': 'literal', 'value': {'name': ' '}}
    before = snapshot(h)
    response = h.client.post('/api/jobs', json={
        'definition': value, 'expectedRevision': 0, 'requestKey': 'business-name'})
    assert response.status_code == (400 if requires_nonblank else 201), response.text
    if requires_nonblank:
        assert snapshot(h) == before
    else:
        item = response.json()['result']['object']
        assert item['steps'][0]['arguments']['payload']['value'] == {'name': ' '}
        assert h.client.get('/api/jobs/' + item['id']).json() == item
        assert snapshot(h)[1:] == before[1:]


def test_high_risk_custom_action_remains_fenced_after_literal_validation(h):
    existing = session.create('owned high risk target')
    product = next(row for row in h.service.actions(actor=USER)['items']
                   if row['id'] == 'product_session_message')
    custom = definition('action', product)
    custom.update(effectClass='high_risk', system=False)
    action = h.service.publish_action('owned_high_risk', custom)
    value = session_definition(h, 'message', [existing.id])
    value['steps'][0]['actionRef'] = object_ref('action', action)
    item = create(h, value=value, key='high-risk-create')
    item = control(h, 'jobs', item, 'enable', 'high-risk-enable').json()['result']['object']
    before = snapshot(h)[1:]
    receipt = launch(h, item, 'user-high-risk-run')
    asyncio.run(h.service.dispatch_once())
    state = stored_runtime(h, item['id'])
    assert state['runs'][receipt['runId']]['state'] == 'failed'
    assert state['attempts'] == {} and state['effects'] == []
    assert snapshot(h)[1:] == before
