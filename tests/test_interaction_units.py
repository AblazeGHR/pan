import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from packages.interactions.contracts import InteractionError, validate
from packages.interactions.store import InteractionStore, UserPrincipal
from packages.interactions.units import read_submission
from tests.test_interaction_canonical_binding import h, graph

U = UserPrincipal('user')


def make(tmp_path, n=4, version=3, choices=None):
    store = InteractionStore(tmp_path / 'cards.sqlite')
    qs = [{'id': str(i), 'prompt': f'Question {i}', 'type': 'text'} for i in range(n)]
    if choices:
        qs[0].update(type='single_choice', choices=choices)
    definition = {'schemaVersion': version, 'title': 'Units', 'units' if version == 3 else 'questions': qs}
    card = store.create({'ownerSessionId': 'owner', 'recipientId': 'user', 'requestKey': 'card', 'card': definition})
    return store, card


def send(store, card, answers, key='batch', revision=None):
    return store.respond(U, card['id'], {'requestId': key, 'expectedRevision': card['revision'] if revision is None else revision,
                                        'unitAnswers': answers})


def test_atomic_validation_lock_and_batch_identity(tmp_path):
    s, c = make(tmp_path)
    with pytest.raises(InteractionError):
        send(s, c, {'0': {'text': 'valid'}, '1': {'text': '  '}})
    assert s.get_for_user(U, c['id'])['submissions'] == []
    c1 = send(s, c, {'0': {'text': 'first'}, '1': {'rejected': True}})
    assert c1['unitStatus'] == 'partial'
    assert [q['state'] for q in c1['units']] == ['answered', 'rejected', 'pending', 'pending']
    assert send(s, c, {'0': {'text': 'first'}, '1': {'rejected': True}}) == c1
    with pytest.raises(InteractionError):
        send(s, c1, {'0': {'text': 'edit'}}, 'edit')
    c2 = send(s, c1, {'2': {'text': 'later'}, '3': {'rejected': True, 'btw': 'reason'}} , 'second')
    assert c2['unitStatus'] == 'processed'
    batch = read_submission(s, 'owner', c['id'], c2['submissions'][-1])
    assert set(batch['answers']) == {'2', '3'}
    with pytest.raises(InteractionError):
        read_submission(s, 'other', c['id'], batch['submissionId'])
    assert InteractionStore(s.path).get_for_user(U, c['id']) == c2


@pytest.mark.parametrize('count', [1, 3, 4, 20])
def test_notice_full_threshold_and_no_old_answers(tmp_path, count):
    s, c = make(tmp_path, count)
    out = send(s, c, {str(i): {'rejected': True} for i in range(count)})
    with s.db() as db:
        notice = json.loads(db.execute('SELECT payload FROM response_outbox').fetchone()[0])
    assert notice['count'] == notice['rejectCount'] == count
    assert ('answers' in notice) == (count <= 3)
    assert ('units' in notice) == (count <= 3)
    assert out['submissions'][0] in notice['readCommand']


def test_concurrent_cas_and_same_request(tmp_path):
    s, c = make(tmp_path)
    def attempt(i):
        try:
            return send(s, c, {str(i): {'text': 'x'}}, str(i))
        except InteractionError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(attempt, [0, 1]))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert 'conflict' in results


def test_v2_original_terminal_projection_and_partial(tmp_path):
    s, c = make(tmp_path, version=2)
    partial = send(s, c, {'0': {'rejected': True}})
    assert partial['unitStatus'] == 'partial'
    with pytest.raises(InteractionError):
        s.no_answer_needed('owner', c['id'], partial['revision'])


def test_unknown_cold_lookup_only_and_reject_never_executes(tmp_path):
    choice = {'id': 'go', 'label': 'Go', 'triggerRef': {'id': 'trigger', 'revision': 1}}
    s, c = make(tmp_path, choices=[choice])
    with s.db() as db:
        db.execute('UPDATE cards SET bindings=? WHERE id=?', (json.dumps({'0': {'go': {}}}), c['id']))
    # Install a real v1 binding shape using the existing fixture in other tests;
    # here a lost original event is persisted directly to exercise claim recovery.
    c = send(s, c, {'0': {'rejected': True}})
    assert asyncio.run(s.dispatch(U, c['id'], None))['delivery']['state'] == 'not_requested'
    with s.db() as db:
        assert db.execute('SELECT count(*) FROM question_events').fetchone()[0] == 0
        event = {'id': 'original', 'payload': {'questionId': '0'}}
        db.execute('INSERT INTO question_events VALUES(?,?,?,?)', ('original', c['id'], json.dumps(event), '{"state":"unknown"}'))
    class QueryOnly:
        async def authorize(self, event):
            pytest.fail('unknown cannot reauthorize')
        async def accept(self, event):
            pytest.fail('unknown cannot replay')
        async def lookup(self, eid):
            assert eid == 'original'
            return None
    restored = InteractionStore(s.path)
    assert asyncio.run(restored.dispatch(U, c['id'], QueryOnly()))['delivery']['state'] == 'unknown'


def test_action_ref_real_command_btw_no_replay_or_hidden_job(h, tmp_path):
    import sys
    from packages.actions.command_execution import product_definition
    from packages.interactions.trusted import create
    from packages.jobs.final_service import object_ref
    definition = product_definition('process.command')
    definition['system'] = False
    action = h.host.publish_action('card_command', definition)
    permissions = h.svc.auth._default_permissions()
    for values in permissions.values():
        for key in values:
            values[key] = True
    h.svc.auth.change_permissions(permissions, 0)
    marker = tmp_path / 'effects.txt'
    arguments = {'argv': [sys.executable, '-c', "from pathlib import Path; p=Path('effects.txt'); p.write_text(p.read_text()+'x' if p.exists() else 'x')"], 'cwd': str(tmp_path)}
    choice = {'id': 'go', 'label': 'Fixed command', 'actionRef': object_ref('action', action), 'arguments': arguments}
    c = create(h.svc, h.store, {'ownerSessionId': h.owner.id, 'recipientId': 'user', 'requestKey': 'action',
        'card': {'schemaVersion': 3, 'title': 'Fixed Action', 'units': [
            {'id': 'q', 'prompt': 'Run fixed command?', 'type': 'single_choice', 'choices': [choice]}]}}, producer_id=h.owner.id)
    c1 = send(h.store, c, {'q': {'choiceId': 'go', 'btw': 'This note must never change executable arguments'}})
    original_invoke = h.host.invoke_action
    async def lost(*args, **kwargs):
        await original_invoke(*args, **kwargs)
        raise ConnectionError('lost canonical receipt')
    h.host.invoke_action = lost
    unknown = asyncio.run(h.store.dispatch(U, c['id'], h.adapter))
    assert unknown['delivery']['state'] == 'unknown'
    h.host.invoke_action = original_invoke
    result = asyncio.run(InteractionStore(h.store.path).dispatch(U, c['id'], h.adapter))
    assert result['delivery']['state'] == 'accepted'
    assert marker.read_text() == 'x'
    assert send(h.store, c, {'q': {'choiceId': 'go', 'btw': 'This note must never change executable arguments'}})['submissions'] == c1['submissions']
    assert asyncio.run(InteractionStore(h.store.path).dispatch(U, c['id'], h.adapter)) == result
    assert marker.read_text() == 'x'
    receipt = read_submission(h.store, h.owner.id, c['id'], c1['submissions'][0])['executions'][0]
    op = h.host.operation(receipt['receiptId'], actor=c['trustedChoices']['q']['go']['actor'])
    assert op['result']['commands'][0]['exitCode'] == 0
    with h.host.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0
    h.store.delete(U, c['id'])
    retained = read_submission(h.store, h.owner.id, c['id'], c1['submissions'][0])
    assert retained['executions'][0]['receiptId'] == receipt['receiptId']


def test_trigger_ref_partial_btw_and_cold_no_replay(h):
    from packages.interactions.trusted import create
    job, trigger, _ = graph(h.host, h.target.id)
    c = create(h.svc, h.store, {'ownerSessionId': h.owner.id, 'recipientId': 'user', 'requestKey': 'v3-trigger',
        'card': {'schemaVersion': 3, 'title': 'Fixed Trigger', 'units': [
            {'id': 'q', 'prompt': 'Run?', 'type': 'single_choice', 'choices': [{'id': 'go', 'label': 'Go', 'triggerRef': {'id': trigger['id'], 'revision': trigger['revision']}}]},
            {'id': 'text', 'prompt': 'Later question', 'type': 'text'}]}}, producer_id=h.owner.id)
    c1 = send(h.store, c, {'q': {'choiceId': 'go', 'btw': 'Only a note'}})
    assert c1['unitStatus'] == 'partial'
    asyncio.run(h.store.dispatch(U, c['id'], h.adapter))
    asyncio.run(h.host.dispatch_once())
    assert [q['text'] for q in h.target.queue_pending] == ['approved']
    s = InteractionStore(h.store.path)
    c2 = send(s, s.get_for_user(U, c['id']), {'text': {'rejected': True}}, 'later')
    asyncio.run(s.dispatch(U, c['id'], h.adapter))
    asyncio.run(h.host.dispatch_once())
    assert len(h.target.queue_pending) == 1
    assert len(h.host.final.get(job['id'])['runs']) == 1
    assert set(read_submission(s, h.owner.id, c['id'], c2['submissions'][-1])['answers']) == {'text'}


def test_new_batch_lost_notice_is_lookup_only_after_restart(tmp_path):
    s, c = make(tmp_path)
    send(s, c, {'0': {'text': 'persisted before notice'}})
    class Queue:
        def __init__(self):
            self.puts = 0
            self.queries = 0
        async def put(self, owner, qid, payload):
            self.puts += 1
            assert 'persisted before notice' in payload['content']['text']
            raise ConnectionError('lost receipt')
        async def lookup(self, owner, qid):
            self.queries += 1
            return None
    queue = Queue()
    asyncio.run(s.deliver_responses(queue))
    asyncio.run(InteractionStore(s.path).deliver_responses(queue))
    assert queue.puts == queue.queries == 1
    with s.db() as db:
        assert db.execute('SELECT state FROM response_outbox').fetchone()[0] == 'unknown'


def test_execution_failure_of_one_unit_does_not_block_another(h):
    from packages.interactions.trusted import create
    _, trigger, _ = graph(h.host, h.target.id)
    c = create(h.svc, h.store, {'ownerSessionId': h.owner.id, 'recipientId': 'user', 'requestKey': 'independent',
        'card': {'schemaVersion': 3, 'title': 'Independent execution', 'units': [
            {'id': q, 'prompt': q, 'type': 'single_choice', 'choices': [{'id': 'go', 'label': 'Go', 'triggerRef': {'id': trigger['id'], 'revision': trigger['revision']}}]}
            for q in ('a', 'b')]}}, producer_id=h.owner.id)
    send(h.store, c, {q: {'choiceId': 'go'} for q in ('a', 'b')})
    class Independent:
        calls = 0
        async def authorize(self, event):
            if event['payload']['questionId'] == 'a':
                raise InteractionError('stale', 'Unit A authorization revoked')
            return True
        async def accept(self, event):
            self.calls += 1
            return {'status': 'accepted', 'receiptId': event['id']}
    adapter = Independent()
    with pytest.raises(InteractionError, match='revoked'):
        asyncio.run(h.store.dispatch(U, c['id'], adapter))
    result = h.store.get_for_user(U, c['id'])
    assert [q['executions'][0]['state'] for q in result['units']] == ['rejected', 'accepted']
    asyncio.run(h.store.dispatch(U, c['id'], adapter))
    assert adapter.calls == 1


def test_legacy_v1_partial_protocol_preserves_original_answer_fact(tmp_path):
    s = InteractionStore(tmp_path / 'cards.sqlite')
    c = s.create({'ownerSessionId': 'owner', 'recipientId': 'user', 'requestKey': 'old',
                  'card': {'title': 'Original', 'body': 'Body', 'answerInput': {}}})
    done = s.respond(U, c['id'], {'requestId': 'old-response', 'expectedRevision': 0, 'answer': 'original answer'})
    assert done['units'][0]['answer'] == {'text': 'original answer'}
    assert done['submissions'] == []
    with pytest.raises(InteractionError):
        send(s, done, {'legacy': {'text': 'overwrite'}}, 'new')
    assert s.get_for_user(U, c['id'])['response']['requestId'] == 'old-response'
