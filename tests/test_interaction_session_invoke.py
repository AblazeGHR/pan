"""Direct Session Card calls share canonical operations and original providers."""
import asyncio

import pytest

from packages.interactions.contracts import InteractionError
from packages.interactions.store import InteractionStore, UserPrincipal
from packages.interactions.trusted import create
from packages.jobs.final_service import object_ref
from tests.test_interaction_canonical_binding import h

U = UserPrincipal('user')
PRODUCTS = ['product_session_message', 'product_session_broadcast',
            'product_session_message_v2', 'product_session_broadcast_v2', 'product_assign_v2']


def grant(h):
    permissions = h.svc.auth._default_permissions()
    for values in permissions.values():
        for key in values:
            values[key] = True
    h.svc.auth.change_permissions(permissions, h.svc.auth.permissions_status()['revision'])


def request(h, product, *, args=None, key='direct-session'):
    action = next(a for a in h.host.actions(actor={'type': 'user', 'id': 'local'})['items'] if a['id'] == product)
    if args is None:
        args = {'sessionIds': [h.target.id, h.target.id], 'text': 'fixed Session body'} if 'broadcast' in product else {'sessionId': h.target.id, 'text': 'fixed Session body'}
        if product == 'product_session_broadcast':
            args['sessionIds'] = [h.target.id]
    return {'ownerSessionId': h.owner.id, 'recipientId': 'user', 'requestKey': key,
            'card': {'schemaVersion': 3, 'title': 'Direct Session Action', 'units': [
                {'id': 'q', 'prompt': 'Fixed Session choice', 'type': 'single_choice',
                 'choices': [{'id': 'go', 'label': 'Send fixed body', 'actionRef': object_ref('action', action), 'arguments': args}]}]}}


def answer(h, c, *, reject=False):
    return h.store.respond(U, c['id'], {'requestId': 'submit', 'expectedRevision': 0,
        'unitAnswers': {'q': {'rejected': True, 'btw': 'reject note'} if reject else {'choiceId': 'go', 'btw': 'BTW never replaces fixed body'}}})


@pytest.mark.parametrize('product', PRODUCTS)
def test_fixed_product_direct_once_lost_receipt_cold_no_hidden_job(h, product):
    grant(h)
    c = create(h.svc, h.store, request(h, product), producer_id=h.owner.id)
    assert c['trustedChoices']['q']['go']['targetIds'] == [h.target.id]
    answer(h, c)
    original = h.host.invoke_action
    async def lost(*args, **kwargs):
        await original(*args, **kwargs)
        raise ConnectionError('Lost original canonical response')
    h.host.invoke_action = lost
    assert asyncio.run(h.store.dispatch(U, c['id'], h.adapter))['delivery']['state'] == 'unknown'
    h.host.invoke_action = original
    # Reopen the actual canonical host as well as the Card store. No in-memory
    # invocation/provider receipt cache may authorize another call.
    from packages.jobs.final_service import FinalService
    root = h.host.root
    asyncio.run(h.host.close())
    cold = FinalService(root, h.svc.auth)
    cold.owner.claim(cold.owner.status()["epoch"])
    cold.owner.set_state('active')
    h.host = cold
    h.svc.final_jobs = cold
    restored = InteractionStore(h.store.path)
    done = asyncio.run(restored.dispatch(U, c['id'], h.adapter))
    assert done['delivery']['state'] == 'accepted'
    assert len(h.target.queue_pending) == 1
    assert h.target.queue_pending[0]['text'] == 'fixed Session body'
    assert done['units'][0]['executions'][0]['receiptId'].startswith('op_')
    assert asyncio.run(restored.dispatch(U, c['id'], h.adapter)) == done
    with h.host.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM final_operations').fetchone()[0] == 1


@pytest.mark.parametrize('product', ['product_assign_runtime_v1', 'product_session_message_runtime_v1', 'product_session_broadcast_runtime_v1'])
def test_runtime_release_declined_at_definition_creation(h, product):
    grant(h)
    with pytest.raises(InteractionError, match='Job.*deliveryRuntime|deliveryRuntime.*Job'):
        create(h.svc, h.store, request(h, product), producer_id=h.owner.id)
    with h.store.db() as db:
        assert db.execute('SELECT count(*) FROM cards').fetchone()[0] == 0


def test_create_checks_scope_target_existence_and_source(h):
    grant(h)
    h.owner.pan_access['restrict_to_managed'] = True
    from packages.core import session
    session.save(h.owner)
    with pytest.raises(InteractionError):
        create(h.svc, h.store, request(h, 'product_session_message_v2'), producer_id=h.owner.id)
    assert session.claim(h.owner.id, h.target.id) is None
    with pytest.raises(InteractionError):
        create(h.svc, h.store, request(h, 'product_session_message_v2', args={'sessionId': 'missing', 'text': 'body'}), producer_id=h.owner.id)
    with pytest.raises(InteractionError):
        create(h.svc, h.store, request(h, 'product_session_message_v2', args={'sessionId': h.target.id, 'text': 'body', 'source': 'user'}), producer_id=h.owner.id)
    assert not h.target.queue_pending


def test_reject_keeps_btw_and_never_invokes_session_provider(h):
    grant(h)
    c = create(h.svc, h.store, request(h, 'product_assign_v2'), producer_id=h.owner.id)
    result = answer(h, c, reject=True)
    assert result['response']['answers']['q']['btw'] == 'reject note'
    asyncio.run(h.store.dispatch(U, c['id'], h.adapter))
    assert not h.target.queue_pending
    with h.host.transaction(readonly=True) as db:
        assert db.execute('SELECT count(*) FROM final_operations').fetchone()[0] == 0


def test_discovery_explains_each_product_before_creation(h):
    from packages.interactions.action_binding import capabilities
    grant(h)
    rows = {a['actionRef']['id']: a for a in capabilities(h.svc)['actions']}
    assert all(rows[product]['bindable'] for product in PRODUCTS)
    for product in ('product_assign_runtime_v1', 'product_session_message_runtime_v1', 'product_session_broadcast_runtime_v1'):
        assert rows[product]['bindable'] is False
        assert 'deliveryRuntime' in rows[product]['reason']
        assert rows[product]['inputSchema']['required']


def test_source_scope_revocation_between_fanout_effects(h, tmp_path):
    from packages.core import session
    grant(h)
    second = session.create('second', workdir=str(tmp_path))
    second.queue_paused = True
    session.save(second)
    h.owner.pan_access['restrict_to_managed'] = True
    session.save(h.owner)
    for target in (h.target, second):
        assert session.claim(h.owner.id, target.id) is None
    args = {'sessionIds': [h.target.id, second.id], 'text': 'fixed fanout'}
    c = create(h.svc, h.store, request(h, 'product_session_broadcast_v2', args=args), producer_id=h.owner.id)
    answer(h, c)
    provider = h.host.executor.session_provider
    original = provider.send_session
    async def changed(*args, **kwargs):
        result = await original(*args, **kwargs)
        assert session.unclaim(h.owner.id, second.id) is None
        return result
    provider.send_session = changed
    done = asyncio.run(h.store.dispatch(U, c['id'], h.adapter))
    assert len(h.target.queue_pending) == 1
    assert second.queue_pending == []
    actor = c['trustedChoices']['q']['go']['actor']
    receipt = done['units'][0]['executions'][0]['receiptId']
    op = h.host.operation(receipt, actor=actor)
    assert op['result']['outcome'] == 'partial'
    assert len(op['result']['providerReceipts']) == 2
    assert asyncio.run(h.store.dispatch(U, c['id'], h.adapter)) == done


@pytest.mark.parametrize('field,value', [('revision', 999), ('digest', 'f' * 64)])
def test_creation_rejects_wrong_fixed_action_version(h, field, value):
    grant(h)
    body = request(h, 'product_session_message_v2')
    body['card']['units'][0]['choices'][0]['actionRef'][field] = value
    with pytest.raises(InteractionError):
        create(h.svc, h.store, body, producer_id=h.owner.id)
    assert not h.target.queue_pending


def test_unknown_provider_result_is_original_operation_query_only(h):
    grant(h)
    class Unknown:
        calls = 0
        async def send_session(self, *args, **kwargs):
            self.calls += 1
            return {'status': 'unknown'}
    provider = Unknown()
    h.host.executor.session_provider = provider
    c = create(h.svc, h.store, request(h, 'product_session_message_v2'), producer_id=h.owner.id)
    answer(h, c)
    done = asyncio.run(h.store.dispatch(U, c['id'], h.adapter))
    op = h.host.operation(done['units'][0]['executions'][0]['receiptId'], actor=c['trustedChoices']['q']['go']['actor'])
    assert op['status'] == 'unknown' and op['result']['outcome'] == 'unknown'
    asyncio.run(InteractionStore(h.store.path).dispatch(U, c['id'], h.adapter))
    assert provider.calls == 1


@pytest.mark.parametrize('count', [2, 4])
def test_answer_reject_btw_notice_threshold_and_submission_identity(tmp_path, count):
    from tests.test_interaction_units import make, send
    s, c = make(tmp_path, count)
    answers = {str(i): {'text': f'answer {i}', 'btw': f'answer BTW {i}'} if i % 2 == 0 else
               {'rejected': True, 'btw': f'reject BTW {i}'} for i in range(count)}
    done = send(s, c, answers)
    class Queue:
        payload = None
        async def put(self, owner, qid, payload):
            self.payload = payload
            return {'status': 'accepted'}
    queue = Queue()
    asyncio.run(s.deliver_responses(queue))
    text = queue.payload['content']['text']
    envelope = queue.payload['content']['envelope']
    assert done['submissions'][0] in text
    assert envelope['rejectCount'] == count // 2
    if count <= 3:
        assert 'answer BTW 0' in text and 'reject BTW 1' in text and 'Question 0' in text
        assert envelope['answers'] == answers
    else:
        assert 'answers' not in envelope and 'units' not in envelope
        assert 'answer BTW' not in text and 'reject BTW' not in text
        from packages.interactions.units import read_submission
        batch = read_submission(s, 'owner', c['id'], done['submissions'][0])
        assert batch['answers'] == answers
