"""Actual form-boundary validation and frozen identity for runtime opt-in."""
import asyncio
import copy

import pytest

from packages.jobs.final_service import object_ref, definition
from packages.core import session
from tests.test_retirement_rev14_templates import h, env, form_body, instantiate, create_template, counts
from tests.test_job_final_http_service import USER, launch


def runtime_form(h):
    body = form_body(h.client, fields={'sessionId': 'ses_a', 'text': '  owned\nraw  ',
        'runtime': True, 'taskId': 'owned-runtime-business-task'})
    action = next(a for a in h.service.actions(actor=USER)['items'] if a['id'] == 'product_assign_runtime_v1')
    body['definition']['jobDefinition']['steps'][0]['actionRef'] = object_ref('action', action)
    return body


@pytest.mark.parametrize('invalid', ['string-runtime', 'missing-session', 'conflict', 'extra'])
def test_runtime_form_rejects_bad_values_before_any_definition_operation_or_composition(h, invalid):
    body = runtime_form(h)
    if invalid == 'string-runtime': body['form']['fields']['runtime'] = 'yes'
    elif invalid == 'missing-session': body['form']['fields']['sessionId'] = 'ses_absent'
    elif invalid == 'extra': body['form']['fields']['unknown'] = True
    else: body['definition']['jobDefinition']['steps'][0]['arguments']['text'] = {'from': 'literal', 'value': 'conflict'}
    before = counts(h.service)
    response = h.client.post('/api/job-templates', json=body)
    assert response.status_code in {400, 409, 422}, response.text
    assert counts(h.service) == before
    assert not session.get('ses_a').queue_pending


def test_real_form_instance_clear_target_pending_restore_preserves_business_task_and_frozen_plan(h):
    template = create_template(h.client, runtime_form(h))
    response = instantiate(h.client, template, activate=True)
    assert response.status_code == 201, response.text
    jid = response.json()['result']['objectIds']['job']
    item = h.client.get('/api/jobs/' + jid).json()
    create = h.client.post('/api/jobs/' + jid + '/runs', json={'expectedRevision': item['configRevision'],
        'input': item['arguments'], 'cause': {'type': 'manual'}, 'requestKey': 'owned-form-run'})
    assert create.status_code == 202, create.text
    rid = create.json()['runId']
    frozen = copy.deepcopy(h.service.final.store.get(jid)['finalRuntime']['plans'][rid]['frozen'])
    cfg = definition('job', item)
    cfg['arguments']['delivery']['target'] = None
    edit = h.client.put('/api/jobs/' + jid, json={'definition': cfg, 'expectedRevision': item['configRevision'], 'requestKey': 'clear'})
    assert edit.status_code == 200, edit.text
    asyncio.run(h.service.dispatch_once())
    assert not h.service.final.get(jid)['attempts'] and not session.get('ses_a').queue_pending
    cfg['arguments']['delivery'] = {'target': 'ses_source', 'text': '  changed\nbytes  '}
    restored = h.client.put('/api/jobs/' + jid, json={'definition': cfg,
        'expectedRevision': edit.json()['result']['object']['configRevision'], 'requestKey': 'restore'})
    assert restored.status_code == 200, restored.text
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(jid)
    assert state['runs'][rid]['state'] == 'completed' and len(state['attempts']) == 1
    item = session.get('ses_source').queue_pending[0]
    assert item['taskId'] == 'owned-runtime-business-task' and item['text'] == '  changed\nbytes  '
    assert h.service.final.store.get(jid)['finalRuntime']['plans'][rid]['frozen'] == frozen
