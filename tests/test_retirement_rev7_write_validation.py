"""Fixed product validation at the real canonical write transaction boundary."""
import copy

import pytest

from packages.core import session
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService, definition, object_ref
from tests.job_retirement_native_fixture import h  # noqa: F401
from tests.test_job_final_http_service import USER, create, control, launch
from tests.test_job_retirement_literal_validation import snapshot
from tests.test_retirement_rev6_parity import offline_plugin, qq_job  # noqa: F401


def source_job(h, product, sid):
    target = 'sessionIds' if product == 'session_broadcast' else 'sessionId'
    args = {target: [sid] if target == 'sessionIds' else sid, 'text': '  owned text 中文  ',
            'source': 'automation'}
    return {'name': '  source-aware 合法  ', 'steps': [{'stepId': 'send',
        'actionRef': h.service.actions(actor=USER)['references']['product_' + product + '_v2'],
        'arguments': {k: {'from': 'literal', 'value': v} for k, v in args.items()},
        'completion': {'contract': 'durable-queue-accepted'}}], 'retention': {'policyRef': 'owned'}}


@pytest.mark.parametrize('product', ['session_message', 'session_broadcast', 'assign'])
def test_source_aware_existing_targets_create_edit_cas_frozen_run_and_shallow_read(h, product, monkeypatch):
    a, b = session.create('owned a'), session.create('owned b')
    value = source_job(h, product, a.id)
    session._cache.clear()
    get = session.get
    reads = []
    def shallow(sid, *, load_history=True):
        reads.append(load_history)
        return get(sid, load_history=load_history)
    monkeypatch.setattr(session, 'get', shallow)  # delegate to real storage
    before = snapshot(h)[1:]
    item = create(h, value=value)
    assert item['name'] == value['name']
    item = control(h, 'jobs', item, 'enable').json()['result']['object']
    receipt = launch(h, item)
    frozen = copy.deepcopy(h.service.final.store.get(item['id'])['finalRuntime'])
    edited = source_job(h, product, b.id)
    body = {'definition': edited, 'expectedRevision': 1, 'requestKey': 'source-edit'}
    url = '/api/jobs/' + item['id']
    response = h.client.put(url, json=body)
    assert response.status_code == 200, response.text
    assert response.json()['result']['object']['steps'] == edited['steps']
    assert h.client.put(url, json=body).json()['replayed'] is True
    state = h.service.final.store.get(item['id'])['finalRuntime']
    assert state == frozen and state['runs'][receipt['runId']]['configRevision'] == 1
    stale = snapshot(h)
    assert h.client.put(url, json={**body, 'requestKey': 'source-stale'}).status_code == 409
    assert snapshot(h) == stale
    assert snapshot(h)[1:] == before
    assert reads and all(flag is False for flag in reads)


@pytest.mark.parametrize('operation', ['create', 'update'])
def test_assign_original_fixed_target_write_contract_has_no_write_or_spawn(h, operation):
    existing = session.create('owned assign target')
    good = source_job(h, 'assign', existing.id)
    item = create(h, value=good) if operation == 'update' else None
    bad = source_job(h, 'assign', 'ses_owned_missing')
    before = snapshot(h)
    result = h.client.request('PUT' if item else 'POST', '/api/jobs' + ('/' + item['id'] if item else ''),
        json={'definition': bad, 'expectedRevision': 1 if item else 0, 'requestKey': 'bad-assign'})
    assert result.status_code == 400, result.text
    assert snapshot(h) == before


@pytest.mark.parametrize('bot', [None, '10000'])
@pytest.mark.parametrize('target,text', [('10000', '  中文 CQ [CQ:face,id=1]\n'),
    ('99999999999999999999', '\t legal English  ')])
def test_product_qq_legal_text_and_id_bytes_create_edit_and_frozen_plan(offline_plugin, bot, target, text):
    h, _ = offline_plugin
    value = qq_job(h, bot)
    value['steps'][0]['arguments']['targetId']['value'] = target
    value['steps'][0]['arguments']['text']['value'] = text
    catalog = copy.deepcopy(h.service.actions(actor=USER))
    no_spawn = snapshot(h)[1:]
    item = create(h, value=value)
    item = control(h, 'jobs', item, 'enable').json()['result']['object']
    run = launch(h, item)
    original = copy.deepcopy(h.service.final.store.get(item['id'])['finalRuntime'])
    assert original['plans'][run['runId']]['frozen']['steps'][0]['arguments']['text']['value'] == text
    edited = copy.deepcopy(value)
    edited['steps'][0]['arguments']['text']['value'] = text + ' edited '
    url = '/api/jobs/' + item['id']
    body = {'definition': edited, 'expectedRevision': 1, 'requestKey': 'legal-qq-edit'}
    result = h.client.put(url, json=body)
    assert result.status_code == 200, result.text
    assert result.json()['result']['object']['steps'] == edited['steps']
    assert h.service.final.store.get(item['id'])['finalRuntime'] == original
    before = snapshot(h)
    assert h.client.put(url, json={**body, 'requestKey': 'qq-stale'}).status_code == 409
    assert snapshot(h) == before
    assert h.service.actions(actor=USER) == catalog
    assert snapshot(h)[1:] == no_spawn


@pytest.mark.parametrize('field,bad', [('targetId','123'),('botUin','123'),('targetType','friend')])
def test_known_qq_target_literal_checked_when_text_is_dynamic(offline_plugin, field, bad):
    h, _ = offline_plugin
    value = qq_job(h, '10000')
    value['inputSchema'] = {'type': 'object', 'properties': {'message': {'type': 'string'}}}
    value['steps'][0]['arguments']['text'] = {'from': 'input', 'path': 'message'}
    value['steps'][0]['arguments'][field] = {'from': 'literal', 'value': bad}
    before = snapshot(h)
    result = h.client.post('/api/jobs', json={
        'definition': value, 'expectedRevision': 0, 'requestKey': 'invalid-mixed-qq'})
    assert result.status_code == 400, result.text
    assert snapshot(h) == before


@pytest.mark.parametrize('dynamic', ['targetId', 'targetType', 'botUin', 'text'])
def test_dynamic_qq_binding_is_preserved_and_existing_resolution_still_validates(offline_plugin, dynamic):
    h, _ = offline_plugin
    value = qq_job(h, '10000')
    value['inputSchema'] = {'type': 'object', 'properties': {'value': {'type': 'string'}}}
    value['steps'][0]['arguments'][dynamic] = {'from': 'input', 'path': 'value'}
    item = create(h, value=value)
    item = control(h, 'jobs', item, 'enable').json()['result']['object']
    if dynamic != 'text':
        before = snapshot(h)
        bad = h.client.post('/api/jobs/' + item['id'] + '/runs', json={'expectedRevision': 1,
            'requestKey': 'bad-resolution', 'input': {'value': 'not-valid'}, 'cause': {'type':'manual'}})
        assert bad.status_code == 400, bad.text
        assert snapshot(h) == before
    good = {'targetId':'123456','targetType':'group','botUin':'10000','text':'  动态 text  '}[dynamic]
    result = h.client.post('/api/jobs/' + item['id'] + '/runs', json={'expectedRevision': 1,
        'requestKey': 'dynamic-resolution', 'input': {'value': good}, 'cause': {'type':'manual'}})
    assert result.status_code == 202, result.text
    frozen = h.service.final.store.get(item['id'])['finalRuntime']['plans'][result.json()['runId']]['frozen']
    assert frozen['steps'][0]['arguments'][dynamic] == {'from':'input','path':'value'}
    assert frozen['input'] == {'value':good}


@pytest.mark.parametrize('product', ['session_message', 'session_broadcast', 'assign'])
def test_dynamic_session_target_is_deferred_but_fixed_target_with_dynamic_text_is_checked(h, product):
    existing = session.create('owned mixed target')
    value = source_job(h, product, existing.id)
    value['inputSchema'] = {'type':'object','properties':{'value':{}}}
    target = 'sessionIds' if product == 'session_broadcast' else 'sessionId'
    value['steps'][0]['arguments'][target] = {'from':'input','path':'value'}
    before = snapshot(h)[1:]
    item = create(h, value=value)
    assert item['steps'] == value['steps'] and snapshot(h)[1:] == before
    value = source_job(h, product, 'ses_owned_missing')
    value['steps'][0]['arguments']['text'] = {'from':'input','path':'value'}
    value['inputSchema'] = {'type':'object','properties':{'value':{'type':'string'}}}
    before = snapshot(h)
    result = h.client.put('/api/jobs/' + item['id'], json={
        'definition':value,'expectedRevision':1,'requestKey':'missing-fixed-mixed'})
    assert result.status_code == 400, result.text
    assert snapshot(h) == before


@pytest.mark.parametrize('kind', ['channel', 'session-message'])
def test_custom_action_argument_names_do_not_acquire_product_business_rules(offline_plugin, kind):
    h, _ = offline_plugin
    catalog = h.service.actions(actor=USER)
    product = next(a for a in catalog['items'] if a['id'] == ('product_channel' if kind == 'channel' else 'product_session_message_v2'))
    custom = definition('action', product)
    custom['system'] = False
    published = h.service.publish_action('owned_custom_' + kind.replace('-','_'), custom)
    value = qq_job(h) if kind == 'channel' else source_job(h, 'session_message', 'custom-not-a-session')
    value['steps'][0]['actionRef'] = object_ref('action', published)
    if kind == 'channel':
        value['steps'][0]['arguments']['targetId']['value'] = '123'
        value['steps'][0]['arguments']['text']['value'] = '  '
    no_spawn = snapshot(h)[1:]
    item = create(h, value=value)
    assert item['steps'] == value['steps'] and snapshot(h)[1:] == no_spawn
    bad = copy.deepcopy(value)
    bad['steps'][0]['arguments']['unexpected'] = {'from':'literal','value':True}
    before = snapshot(h)
    response = h.client.put('/api/jobs/' + item['id'], json={
        'definition':bad,'expectedRevision':1,'requestKey':'custom-schema-error'})
    assert response.status_code == 400, response.text
    assert snapshot(h) == before


@pytest.mark.parametrize('mutation', ['id', 'system', 'operation', 'provider'])
def test_qq_pure_helper_requires_resolved_product_identity_not_just_channel_shape(offline_plugin, mutation):
    h, _ = offline_plugin
    value = qq_job(h)
    value['steps'][0]['arguments']['targetId']['value'] = '123'
    with h.service.transaction(readonly=True) as db:
        action = copy.deepcopy(h.service._resolver(db)('action','product_channel'))
    if mutation == 'id': action['id'] = 'custom'
    if mutation == 'system': action['definition']['system'] = False
    if mutation == 'operation': action['definition']['execution']['operation'] = 'custom'
    if mutation == 'provider': action['definition']['providerRef']['generation'] = 99
    FinalService._check_literal_product_qq(value, [action])
    with h.service.transaction(readonly=True) as db:
        actual = h.service._resolver(db)('action','product_channel')
    with pytest.raises(fc.ContractError):
        FinalService._check_literal_product_qq(value, [actual])
