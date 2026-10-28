"""Private fixed-target Card proof -> existing canonical directed admission."""
import asyncio
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from packages.automation.service import Service
from packages.core import session, worker
from packages.interactions.contracts import InteractionError, validate
from packages.interactions.runtime import bind
from packages.interactions.store import InteractionStore, UserPrincipal
from packages.interactions.trusted import create, verify
from packages.jobs.final_service import FinalService, definition, object_ref

USER = {'type': 'user', 'id': 'local'}


def control(host, kind, item, op):
    return host.control(kind, item['id'], {'operation': op, 'expectedRevision': item.get('configRevision', item.get('revision')),
        'expectedStateRevision': item['stateRevision'], 'requestKey': op + item['id'], 'parameters': {}}, actor=USER)['result']['object']


def graph(host, target, *, op='session-message', dynamic=False, queued=False):
    action_id = 'product_' + op.replace('-', '_') + '_v2'
    action = next(a for a in host.actions(actor=USER)['items'] if a['id'] == action_id)
    field = 'sessionIds' if op == 'session-broadcast' else 'sessionId'
    binding = {'from': 'input', 'path': 'target'} if dynamic else {'from': 'literal', 'value': [target] if field == 'sessionIds' else target}
    cfg = {'name': 'fixed', 'steps': [{'stepId': 'send', 'actionRef': object_ref('action', action),
        'arguments': {field: binding, 'text': {'from': 'literal', 'value': 'approved'}},
        'completion': {'contract': 'durable-queue-accepted'}}], 'retention': {'policyRef': 'test'}}
    if dynamic:
        cfg.update(inputSchema={'type': 'object', 'properties': {'target': {'type': 'string'}}, 'required': ['target'], 'additionalProperties': False}, arguments={'target': target})
    if queued:
        cfg['overlap'] = {'mode': 'queue', 'maxActive': 1, 'maxPending': 10}
    job = host.write('job', None, {'definition': cfg, 'expectedRevision': 0, 'requestKey': 'job'}, actor=USER)['result']['object']
    job = control(host, 'job', job, 'enable')
    trigger = host.write('trigger', None, {'definition': {'name': 'selected', 'eventTypes': ['pan.interaction.responded'],
        'creatorSelector': {'mode': 'registeredType', 'providerType': 'core'}, 'targetJobId': job['id']},
        'expectedRevision': 0, 'requestKey': 'trigger'}, actor=USER)['result']['object']
    trigger = control(host, 'trigger', trigger, 'enable')
    return job, trigger, action


@pytest.fixture
def h(tmp_path, monkeypatch):
    svc = Service(tmp_path / 'automation', worker, None)
    host = FinalService(tmp_path / 'canonical', svc.auth)
    host.owner.claim(0)
    host.owner.set_state('active')
    svc.final_jobs = host
    svc.started = True
    owner = session.create('owner', workdir=str(tmp_path))
    owner.queue_paused = True
    session.save(owner)
    target = session.create('target', workdir=str(tmp_path))
    target.queue_paused = True
    session.save(target)
    adapter = bind(svc)
    store = InteractionStore(tmp_path / 'cards.sqlite')
    yield SimpleNamespace(svc=svc, host=host, owner=owner, target=target, adapter=adapter, store=store)
    asyncio.run(host.close())


def card(h, trigger):
    return create(h.svc, h.store, {'ownerSessionId': h.owner.id, 'recipientId': 'user', 'requestKey': 'card',
        'card': {'title': 'choose', 'body': 'fixed', 'choices': [{'id': 'go', 'label': 'Go',
        'triggerRef': {'id': trigger['id'], 'revision': trigger['revision']}}]}}, producer_id=h.owner.id)


def respond(h, item):
    return h.store.respond(UserPrincipal('user'), item['id'], {'requestId': 'response', 'expectedRevision': 0, 'choiceId': 'go'})


@pytest.mark.parametrize('op', ['session-message', 'assign', 'session-broadcast'])
def test_canonical_fixed_card_directed_once_original_identity(h, op):
    job, trigger, action = graph(h.host, h.target.id, op=op)
    unrelated = h.host.write('trigger', None, {'definition': {**definition('trigger', trigger), 'name': 'unrelated'},
        'expectedRevision': 0, 'requestKey': 'unrelated'}, actor=USER)['result']['object']
    control(h.host, 'trigger', unrelated, 'enable')
    item = card(h, trigger)
    proof = item['trustedChoices']['go']
    assert proof['version'] == 2 and proof['jobRevision'] == job['configRevision']
    validate('ExecutionBinding', proof)
    respond(h, item)
    done = asyncio.run(h.store.dispatch(UserPrincipal('user'), item['id'], h.adapter))
    assert done['delivery']['state'] == 'accepted'
    assert asyncio.run(h.store.dispatch(UserPrincipal('user'), item['id'], h.adapter)) == done
    state = h.host.final.get(job['id'])
    assert len(state['runs']) == len(state['admissions']) == 1
    admission = next(iter(state['admissions'].values()))
    assert admission['cause']['triggerId'] == trigger['id']
    assert asyncio.run(h.adapter.lookup(admission['cause']['eventId']))['receiptId'] == done['delivery']['receiptId']
    assert state['effects'] == []  # admission is not effect completion
    asyncio.run(h.host.dispatch_once())
    assert len(h.target.queue_pending) == 1
    assert h.target.queue_pending[0]['text'] == 'approved'
    assert len(h.host.final.get(job['id'])['effects']) == 1


def test_dynamic_graph_rejected(h):
    _, trigger, _ = graph(h.host, h.target.id, dynamic=True)
    with pytest.raises(InteractionError, match='fixed literal'):
        card(h, trigger)


def test_current_profile_authority_revocation_blocks_final_admission(h):
    job, trigger, _ = graph(h.host, h.target.id)
    permissions = h.svc.auth._default_permissions()
    for values in permissions.values():
        for key in values:
            values[key] = True
    h.svc.auth.change_permissions(permissions, 0)
    with h.host.transaction() as db:
        db.execute("UPDATE final_authorities SET document=? WHERE kind='trigger' AND id=?",
            (json.dumps({'type': 'agent', 'id': 'agent', 'revision': 1}), trigger['id']))
    item = card(h, trigger)
    respond(h, item)
    permissions['job']['execute'] = False
    h.svc.auth.change_permissions(permissions, 1)
    with pytest.raises(InteractionError):
        asyncio.run(h.store.dispatch(UserPrincipal('user'), item['id'], h.adapter))
    assert h.host.final.get(job['id'])['runs'] == {}


def test_custom_action_copy_cannot_claim_product_scope(h):
    job, trigger, action = graph(h.host, h.target.id)
    custom = h.host.publish_action('custom_copy', definition('action', action))
    cfg = definition('job', job)
    cfg['steps'][0]['actionRef'] = object_ref('action', custom)
    h.host.write('job', job['id'], {'definition': cfg, 'expectedRevision': job['configRevision'], 'requestKey': 'custom'}, actor=USER)
    with pytest.raises(InteractionError, match='known immutable'):
        card(h, trigger)


@pytest.mark.parametrize('what', ['trigger', 'job', 'action', 'scope'])
def test_final_admission_reverifies_graph_and_scope_after_initial_check(h, what):
    job, trigger, action = graph(h.host, h.target.id)
    h.owner.pan_access['restrict_to_managed'] = True
    assert session.claim(h.owner.id, h.target.id) is None
    item = card(h, trigger)
    respond(h, item)
    original = h.adapter.submit
    async def changed(event, tid, **kwargs):
        # Exact boundary: adapter already checked its target; the final
        # transaction must detect this independently, with no timed sleep.
        if what == 'scope':
            assert session.unclaim(h.owner.id, h.target.id) is None
        elif what == 'action':
            with h.host.transaction() as db:
                changed_action = h.host._get(db, 'action', action['id'])
                changed_action['enabled'] = False
                h.host._save(db, 'action', action['id'], changed_action)
        else:
            item_now = trigger if what == 'trigger' else job
            control(h.host, what, item_now, 'disable' if what == 'trigger' else 'pause')
        return await original(event, tid, **kwargs)
    h.adapter.submit = changed
    with pytest.raises(InteractionError):
        asyncio.run(h.store.dispatch(UserPrincipal('user'), item['id'], h.adapter))
    assert h.host.final.get(job['id'])['runs'] == {}


def test_unknown_lost_admission_receipt_queries_original_after_graph_disabled(h):
    job, trigger, _ = graph(h.host, h.target.id)
    item = card(h, trigger)
    respond(h, item)
    submit = h.adapter.submit
    calls = []
    async def lost(*args, **kwargs):
        calls.append(1)
        await submit(*args, **kwargs)
        raise ConnectionError('lost persisted admission response')
    h.adapter.submit = lost
    first = asyncio.run(h.store.dispatch(UserPrincipal('user'), item['id'], h.adapter))
    assert first['delivery']['state'] == 'unknown'
    control(h.host, 'trigger', trigger, 'disable')
    cold = InteractionStore(h.store.path)
    done = asyncio.run(cold.dispatch(UserPrincipal('user'), item['id'], h.adapter))
    assert done['delivery']['state'] == 'accepted' and calls == [1]
    assert len(h.host.final.get(job['id'])['runs']) == 1


def test_missing_original_admission_stays_unknown_without_resubmit(h):
    _, trigger, _ = graph(h.host, h.target.id)
    item = card(h, trigger)
    respond(h, item)
    calls = []
    async def unconfirmed(*args, **kwargs):
        calls.append(1)
        return None
    h.adapter.submit = unconfirmed
    first = asyncio.run(h.store.dispatch(UserPrincipal('user'), item['id'], h.adapter))
    second = asyncio.run(InteractionStore(h.store.path).dispatch(UserPrincipal('user'), item['id'], h.adapter))
    assert first['delivery']['state'] == second['delivery']['state'] == 'unknown' and calls == [1]


@pytest.mark.parametrize('what', ['trigger', 'job', 'action', 'scope'])
def test_concurrent_update_cannot_enter_proof_to_admission_transaction(h, monkeypatch, what):
    job, trigger, action = graph(h.host, h.target.id)
    h.owner.pan_access['restrict_to_managed'] = True
    assert session.claim(h.owner.id, h.target.id) is None
    item = card(h, trigger)
    respond(h, item)
    reached, committed = threading.Event(), threading.Event()
    failures, threads = [], []
    original = h.host.admit_event
    def writer():
        reached.set()
        try:
            if what == 'scope':
                assert session.unclaim(h.owner.id, h.target.id) is None
            elif what == 'action':
                with h.host.transaction() as db:
                    value = h.host._get(db, 'action', action['id'])
                    value['enabled'] = False
                    h.host._save(db, 'action', action['id'], value)
            else:
                control(h.host, what, trigger if what == 'trigger' else job, 'disable' if what == 'trigger' else 'pause')
            committed.set()
        except BaseException as exc:
            failures.append(exc)
    def boundary(event, target):
        thread = threading.Thread(target=writer)
        threads.append(thread)
        thread.start()
        assert reached.wait(3)
        assert not committed.is_set()
        result = original(event, target)
        assert not committed.is_set()  # existing joined transaction still owns definition writer
        return result
    monkeypatch.setattr(h.host, 'admit_event', boundary)
    try:
        done = asyncio.run(h.store.dispatch(UserPrincipal('user'), item['id'], h.adapter))
    finally:
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
    assert not failures and committed.is_set()
    assert done['delivery']['state'] == 'accepted'
    assert len(h.host.final.get(job['id'])['runs']) == 1


def test_no_answer_and_spoofed_graph_never_admit(h):
    _, trigger, _ = graph(h.host, h.target.id)
    item = card(h, trigger)
    proof = copy.deepcopy(item['trustedChoices']['go'])
    proof['jobRevision'] = 0
    with pytest.raises(InteractionError):
        validate('ExecutionBinding', proof)
    proof['jobRevision'] = 1
    proof['callerAuthority'] = {'type': 'user'}
    with pytest.raises(InteractionError):
        validate('ExecutionBinding', proof)
    # Only private server-built bindings are accepted; public create cannot
    # carry a proof supplied by its caller.
    with pytest.raises(InteractionError):
        validate('Create', {'ownerSessionId': h.owner.id, 'recipientId': 'user', 'requestKey': 'spoof',
                           'card': item['definition'], 'bindings': {'go': proof}})
    h.store.no_answer_needed(h.owner.id, item['id'], 0)
    assert asyncio.run(h.adapter.lookup('missing-original-event')) is None
    with pytest.raises(InteractionError):
        respond(h, item)


def test_v1_historical_proof_remains_readable_but_not_canonical_authority(h):
    _, trigger, action = graph(h.host, h.target.id)
    from packages.interactions.trusted import digest, scope
    proof = {'version': 1, 'triggerId': trigger['id'], 'triggerRevision': trigger['revision'],
        'triggerDigest': digest(trigger), 'actionId': action['id'], 'actionDigest': digest(action),
        'action': action, 'arguments': {}, 'selector': {'mode': 'explicit', 'ids': [h.target.id]},
        'authorization': scope(h.owner.id, h.owner.id, [h.target.id]), 'summary': 'historical', 'targetIds': [h.target.id]}
    validate('ExecutionBinding', proof)
    item = h.store.create({'ownerSessionId': h.owner.id, 'recipientId': 'user', 'requestKey': 'old',
        'card': {'title': 'old', 'body': 'old', 'choices': [{'id': 'go', 'label': 'Go', 'triggerRef': {'id': trigger['id']}}]}},
        bindings={'go': proof}, producer_id=h.owner.id)
    respond(h, item)
    with pytest.raises(InteractionError, match='Legacy binding'):
        asyncio.run(h.store.dispatch(UserPrincipal('user'), item['id'], h.adapter))
    assert h.store.get_for_user(UserPrincipal('user'), item['id'])['trustedChoices']['go'] == proof


@pytest.mark.parametrize('delete', [False, True])
def test_v2_question_original_ids_reconcile_independently_without_resend(h, delete):
    job, trigger, _ = graph(h.host, h.target.id, queued=True)
    choices = [{'id': 'go', 'label': 'Go', 'triggerRef': {'id': trigger['id'], 'revision': trigger['revision']}}]
    item = create(h.svc, h.store, {'ownerSessionId': h.owner.id, 'recipientId': 'user', 'requestKey': 'questions',
        'card': {'schemaVersion': 2, 'title': 'Questions', 'questions': [
            {'id': 'one', 'prompt': 'One', 'type': 'single_choice', 'choices': choices},
            {'id': 'two', 'prompt': 'Two', 'type': 'single_choice', 'choices': choices},
            {'id': 'note', 'prompt': 'Note', 'type': 'text'},
            {'id': 'note2', 'prompt': 'Note 2', 'type': 'text'}]}}, producer_id=h.owner.id)
    answers = {'one': {'choiceId': 'go'}, 'two': {'choiceId': 'go'}, 'note': {'text': '😀' * 10000}, 'note2': {'text': '文' * 10000}}
    h.store.respond(UserPrincipal('user'), item['id'], {'requestId': 'questions-response', 'expectedRevision': 0, 'answers': answers})
    submit = h.adapter.submit
    calls = []
    async def lost(event, *args, **kwargs):
        calls.append(event['id'])
        await submit(event, *args, **kwargs)
        return None
    h.adapter.submit = lost
    first = asyncio.run(h.store.dispatch(UserPrincipal('user'), item['id'], h.adapter))
    assert first['delivery']['state'] == 'unknown' and len(set(calls)) == 2
    control(h.host, 'trigger', trigger, 'disable')
    if delete:
        h.store.delete(UserPrincipal('user'), item['id'])
        asyncio.run(InteractionStore(h.store.path).recover_detached(h.adapter))
    else:
        second = asyncio.run(InteractionStore(h.store.path).dispatch(UserPrincipal('user'), item['id'], h.adapter))
        assert second['delivery']['state'] == 'accepted'
    assert len(calls) == len(h.host.final.get(job['id'])['admissions']) == 2
    for eid in calls:
        assert asyncio.run(h.adapter.lookup(eid))['status'] == 'accepted'


def test_large_v2_http_canonical_admission_and_stale_proof_before_response(h, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from packages.interactions import api
    job, trigger, _ = graph(h.host, h.target.id, queued=True)
    choices = [{'id': 'go', 'label': 'Go', 'triggerRef': {'id': trigger['id']}}]
    request = {'ownerSessionId': h.owner.id, 'recipientId': 'user', 'requestKey': 'large',
        'card': {'schemaVersion': 2, 'title': 'Large', 'questions': [
            {'id': 'one', 'prompt': 'One', 'type': 'single_choice', 'choices': choices},
            {'id': 'text', 'prompt': 'Text', 'type': 'text'}, {'id': 'more', 'prompt': 'More', 'type': 'text'}]}}
    item = create(h.svc, h.store, request)
    app = FastAPI()
    api.register(app)
    app.state.automation = h.svc
    app.state.interaction_trigger_adapter = h.adapter
    app.state.user_inbox_principal = lambda _: UserPrincipal('user')
    monkeypatch.setattr(api, 'get_store', lambda: h.store)
    payload = {'requestId': 'large', 'expectedRevision': 0, 'answers': {
        'one': {'choiceId': 'go'}, 'text': {'text': '😀' * 10000}, 'more': {'text': '文' * 10000}}}
    with TestClient(app) as client:
        response = client.post(f"/api/user-inbox/{item['id']}/submit", json=payload)
        assert response.status_code == 200, response.text[:200]
        assert response.json()['card']['delivery']['state'] == 'accepted'
        request['requestKey'] = 'stale-large'
        stale = create(h.svc, h.store, request)
        cfg = definition('job', job)
        cfg['steps'][0]['arguments']['text']['value'] = 'changed meaning'
        h.host.write('job', job['id'], {'definition': cfg, 'expectedRevision': job['configRevision'], 'requestKey': 'change'}, actor=USER)
        denied = client.post(f"/api/user-inbox/{stale['id']}/submit", json=payload)
        assert denied.status_code == 409 and denied.json()['detail']['code'] == 'stale'
        assert h.store.get_for_user(UserPrincipal('user'), stale['id'])['status'] == 'open'
