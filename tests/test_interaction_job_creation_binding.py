"""Explicit Job-producing Card Actions retain their canonical meaning."""
import asyncio
import copy

import pytest

from packages.interactions.contracts import InteractionError
from packages.interactions.store import InteractionStore, UserPrincipal
from packages.interactions.trusted import create
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService, definition, object_ref
from tests.test_interaction_canonical_binding import h
from tests.test_interaction_session_invoke import grant

USER = {'type': 'user', 'id': 'local'}
U = UserPrincipal('user')


def job_definition(h):
    action = h.host.get('action', 'product_session_message_v2', actor=USER)
    return {'name': 'Explicit full Job', 'description': 'Preserve the whole configuration',
        'steps': [{'stepId': 'send', 'actionRef': object_ref('action', action),
            'arguments': {'sessionId': {'from': 'literal', 'value': h.target.id}, 'text': {'from': 'input', 'path': 'body'}},
            'completion': {'contract': 'durable-queue-accepted'}}],
        'inputSchema': {'type': 'object', 'properties': {'body': {'type': 'string'}}, 'required': ['body'], 'additionalProperties': False},
        'arguments': {'body': 'fixed child Job body'},
        'overlap': {'mode': 'queue', 'maxActive': 1, 'maxPending': 9}, 'runLimit': {'maxRuns': 7, 'counterDomain': 'run-admission'},
        'recovery': {'automatic': False, 'maxAttempts': 3, 'backoffSeconds': ['2', '5'], 'stableResetSeconds': '60'},
        'notification': {'on': ['completed', 'uncertain'], 'recipients': []}, 'retention': {'policyRef': 'test'}}


def creator(h, *, start=False):
    from packages.actions.job_creation import product_definition
    action = product_definition()
    action['system'] = False
    action['execution']['config']['start'] = start
    return object_ref('action', h.host.publish_action('explicit_creator', action))


def template_creator(h):
    from packages.actions.legacy_families import template_successor
    from packages.actions.store import validate_action
    value = {'name': 'Fixed full Template', 'jobDefinition': fc.validate_definition('job', job_definition(h)), 'composition': {'creators': [], 'triggers': []}}
    template = h.host.write('template', None, {'definition': value, 'expectedRevision': 0, 'requestKey': 'template'}, actor=USER)['result']['object']
    template = h.host.control('template', template['id'], {'operation': 'publish', 'expectedRevision': template['revision'],
        'expectedStateRevision': template['stateRevision'], 'requestKey': 'publish', 'parameters': {}}, actor=USER)['result']['object']
    raw = validate_action({'id': 'instantiate', 'name': 'Explicit Template Job', 'enabled': True, 'effect': 'write_local',
        'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'execution': {'kind': 'job_template', 'templateId': 'jtpl_original', 'templateRevision': 1}})
    action = template_successor(raw, object_ref('template', template))
    return object_ref('action', h.host.publish_action('explicit_template', action)), template


def card(h, ref, args, key='explicit-job'):
    return create(h.svc, h.store, {'ownerSessionId': h.owner.id, 'recipientId': 'user', 'requestKey': key,
        'card': {'schemaVersion': 3, 'title': 'Explicit Job creation', 'units': [
            {'id': 'q', 'prompt': 'Create the displayed complete Job?', 'type': 'single_choice',
             'choices': [{'id': 'create', 'label': 'Create Job', 'actionRef': ref, 'arguments': args}]}]}}, producer_id=h.owner.id)


def submit(h, c, reject=False):
    return h.store.respond(U, c['id'], {'requestId': 'user', 'expectedRevision': 0,
        'unitAnswers': {'q': {'rejected': True, 'btw': 'No creation'} if reject else {'choiceId': 'create', 'btw': 'BTW must not change Job configuration'}}})


@pytest.mark.parametrize('kind', ['job.create', 'template.instantiate'])
def test_explicit_creation_full_config_lost_receipt_cold_same_identity(h, kind):
    grant(h)
    ref, args = (creator(h), {'jobDefinition': job_definition(h)}) if kind == 'job.create' else (template_creator(h)[0], {})
    c = card(h, ref, args)
    proof = c['trustedChoices']['q']['create']
    assert proof['creation']['operation'] == kind
    assert '创建' in proof['summary'] and proof['targetIds'] == [h.target.id]
    submit(h, c)
    original = h.host.invoke_action
    async def lost(*a, **kw):
        await original(*a, **kw)
        raise ConnectionError('Created, response lost')
    h.host.invoke_action = lost
    assert asyncio.run(h.store.dispatch(U, c['id'], h.adapter))['delivery']['state'] == 'unknown'
    root = h.host.root
    asyncio.run(h.host.close())
    h.host = FinalService(root, h.svc.auth)
    h.host.owner.claim(h.host.owner.status()['epoch'])
    h.host.owner.set_state('active')
    h.svc.final_jobs = h.host
    store = InteractionStore(h.store.path)
    done = asyncio.run(store.dispatch(U, c['id'], h.adapter))
    assert done['delivery']['state'] == 'accepted'
    jobs = h.host.list('job', actor=USER)['items']
    assert len(jobs) == 1
    assert definition('job', jobs[0]) == fc.validate_definition('job', job_definition(h))
    assert jobs[0]['state'] == 'disabled' and h.host.final.runs(jobs[0]['id']) == []
    assert not h.target.queue_pending
    assert asyncio.run(store.dispatch(U, c['id'], h.adapter)) == done
    assert len(h.host.list('job', actor=USER)['items']) == 1


def test_explicit_start_keeps_creation_and_original_run_separate(h):
    grant(h)
    c = card(h, creator(h, start=True), {'jobDefinition': job_definition(h)})
    submit(h, c)
    done = asyncio.run(h.store.dispatch(U, c['id'], h.adapter))
    op = h.host.operation(done['units'][0]['executions'][0]['receiptId'], actor=c['trustedChoices']['q']['create']['actor'])
    assert op['result']['creation']['status'] == 'completed'
    assert op['result']['start']['status'] == 'accepted'
    asyncio.run(h.host.dispatch_once())
    assert [q['text'] for q in h.target.queue_pending] == ['fixed child Job body']


def test_create_is_allowed_when_start_is_refused_by_original_policy(h):
    grant(h)
    permissions = h.svc.auth.permissions_status()['permissions']
    permissions['job']['execute'] = False
    h.svc.auth.change_permissions(permissions, h.svc.auth.permissions_status()['revision'])
    c = card(h, creator(h, start=True), {'jobDefinition': job_definition(h)})
    submit(h, c)
    done = asyncio.run(h.store.dispatch(U, c['id'], h.adapter))
    op = h.host.operation(done['units'][0]['executions'][0]['receiptId'], actor=c['trustedChoices']['q']['create']['actor'])
    assert op['result']['creation']['status'] == 'completed'
    assert op['result']['start'] == {'status': 'rejected', 'code': 'authorization_denied'}
    assert not h.target.queue_pending


@pytest.mark.parametrize('kind', ['job.create', 'template.instantiate'])
def test_reject_never_creates_a_job(h, kind):
    grant(h)
    ref, args = (creator(h), {'jobDefinition': job_definition(h)}) if kind == 'job.create' else (template_creator(h)[0], {})
    c = card(h, ref, args)
    submit(h, c, True)
    asyncio.run(h.store.dispatch(U, c['id'], h.adapter))
    assert h.host.list('job', actor=USER)['items'] == []


@pytest.mark.parametrize('kind', ['job.create', 'template.instantiate'])
def test_source_scope_is_checked_at_create_and_actual_creation_transaction(h, kind):
    from packages.core import session
    grant(h)
    ref, args = (creator(h), {'jobDefinition': job_definition(h)}) if kind == 'job.create' else (template_creator(h)[0], {})
    h.owner.pan_access['restrict_to_managed'] = True
    session.save(h.owner)
    with pytest.raises(InteractionError):
        card(h, ref, args)
    assert session.claim(h.owner.id, h.target.id) is None
    c = card(h, ref, args)
    submit(h, c)
    original = h.host.invoke_action
    async def revoked(*a, **kw):
        assert session.unclaim(h.owner.id, h.target.id) is None
        return await original(*a, **kw)
    h.host.invoke_action = revoked
    with pytest.raises(InteractionError):
        asyncio.run(h.store.dispatch(U, c['id'], h.adapter))
    assert h.host.list('job', actor=USER)['items'] == []


@pytest.mark.parametrize('kind', ['job.create', 'template.instantiate'])
def test_creation_grant_and_fixed_action_version_are_required_before_card(h, kind):
    grant(h)
    ref, args = (creator(h), {'jobDefinition': job_definition(h)}) if kind == 'job.create' else (template_creator(h)[0], {})
    wrong = {**ref, 'digest': 'f' * 64}
    with pytest.raises(InteractionError):
        card(h, wrong, args)
    permissions = h.svc.auth.permissions_status()['permissions']
    permissions['job']['create'] = False
    h.svc.auth.change_permissions(permissions, h.svc.auth.permissions_status()['revision'])
    with pytest.raises(InteractionError):
        card(h, ref, args)
    assert h.host.list('job', actor=USER)['items'] == []


def test_complete_job_may_contain_its_original_runtime_delivery_contract(h):
    grant(h)
    job = job_definition(h)
    runtime = h.host.get('action', 'product_session_message_runtime_v1', actor=USER)
    job['steps'][0]['actionRef'] = object_ref('action', runtime)
    job['inputSchema']['properties']['target'] = {'type': 'string'}
    job['arguments']['target'] = h.target.id
    job['steps'][0]['arguments']['sessionId'] = {'from': 'input', 'path': 'target'}
    job['deliveryRuntime'] = {'owner': 'delivery-manager', 'targetMode': 'runtime',
        'inputFields': ['target', 'body'], 'allowedStates': ['pending', 'reserved'],
        'concurrency': 'job-config-cas+event-receipt', 'audit': 'final_operations',
        'phase': 'before-reservation', 'unknownPolicy': 'query-original'}
    c = card(h, creator(h), {'jobDefinition': job})
    submit(h, c)
    asyncio.run(h.store.dispatch(U, c['id'], h.adapter))
    jobs = h.host.list('job', actor=USER)['items']
    assert len(jobs) == 1
    assert definition('job', jobs[0]) == fc.validate_definition('job', job)
    assert not h.target.queue_pending


def test_nested_job_creation_is_an_explicit_step_and_not_flattened(h):
    grant(h)
    ref = creator(h)
    child = job_definition(h)
    parent = job_definition(h)
    parent['steps'] = [{'stepId': 'child', 'actionRef': ref,
        'arguments': {'jobDefinition': {'from': 'literal', 'value': child}}, 'completion': {'contract': 'job-created'}}]
    c = card(h, ref, {'jobDefinition': parent})
    submit(h, c)
    asyncio.run(h.store.dispatch(U, c['id'], h.adapter))
    jobs = h.host.list('job', actor=USER)['items']
    assert len(jobs) == 1  # Child creation belongs to the original future Step.
    assert definition('job', jobs[0]) == fc.validate_definition('job', parent)


def test_unrestricted_dynamic_job_target_keeps_canonical_binding(h):
    grant(h)
    job = job_definition(h)
    job['steps'][0]['arguments']['sessionId'] = {'from': 'event', 'path': 'payload.sessionId'}
    c = card(h, creator(h), {'jobDefinition': job})
    assert c['trustedChoices']['q']['create']['creation']['dynamicTargets'] is True
    submit(h, c)
    asyncio.run(h.store.dispatch(U, c['id'], h.adapter))
    jobs = h.host.list('job', actor=USER)['items']
    assert definition('job', jobs[0]) == fc.validate_definition('job', job)
