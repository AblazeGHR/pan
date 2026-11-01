"""V2 entire-card submission, per-question rejection and v1 compatibility."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.interactions.api import register
from packages.interactions.contracts import InteractionError, validate
from packages.interactions.store import InteractionStore, UserPrincipal
from packages.interactions.trusted import create

USER = UserPrincipal('user')
QUESTIONS = [
    {'id': 'mode', 'prompt': '模式', 'type': 'single_choice', 'choices': [{'id': 'a', 'label': 'A'}]},
    {'id': 'targets', 'prompt': '目标', 'type': 'multi_choice', 'choices': [{'id': 'a', 'label': 'A'}, {'id': 'b', 'label': 'B'}]},
    {'id': 'note', 'prompt': '说明', 'type': 'text'},
    {'id': 'optional', 'prompt': '可选', 'type': 'text', 'required': False},
]
ANSWERS = {'mode': {'choiceId': 'a'}, 'targets': {'choiceIds': ['a', 'b']}, 'note': {'rejected': True, 'reason': '稍后讨论'}}


def request(questions=None):
    return {'ownerSessionId': 'owner', 'recipientId': 'user', 'requestKey': 'multi',
            'card': {'schemaVersion': 2, 'title': '多问题', 'questions': copy.deepcopy(questions or QUESTIONS)}}


def submission(answers=None):
    return {'requestId': 'once', 'expectedRevision': 0, 'answers': copy.deepcopy(ANSWERS if answers is None else answers)}


def test_mixed_answers_reject_persistence_receipt_and_delivery(tmp_path):
    store = InteractionStore(tmp_path / 'cards.db')
    card = store.create(request())
    assert card['schemaVersion'] == 2 and not card['read']
    from packages.interactions.user_cards import UserCardStore
    UserCardStore(store)
    assert store.unread_summary() == 1  # Four questions are one unread card.
    done = store.respond(USER, card['id'], submission())
    validate('Card', done)
    assert done['status'] == 'responded' and done['response']['answers'] == ANSWERS
    assert store.unread_summary() == 0
    assert done['rejectionDelivery']['state'] == 'not_requested'
    assert store.respond(USER, card['id'], submission()) == done
    for data in [submission({**ANSWERS, 'note': {'text': 'changed'}}), {**submission(), 'requestId': 'new'}]:
        with pytest.raises(InteractionError):
            store.respond(USER, card['id'], data)
    with pytest.raises(InteractionError):
        store.reject(USER, card['id'], {'requestId': 'reject', 'expectedRevision': 1})
    reopened = InteractionStore(store.path)
    assert reopened.get_for_owner('owner', card['id'])['response']['answers'] == ANSWERS
    queue = type('Queue', (), {'put': AsyncMock(return_value={'status': 'accepted'}), 'lookup': AsyncMock()})()
    asyncio.run(reopened.deliver_responses(queue))
    assert queue.put.await_count == 1
    notice = queue.put.call_args.args[2]['content']
    assert notice['envelope']['answers'] == ANSWERS and 'answers' in notice['text']
    asyncio.run(reopened.deliver_rejections(queue))
    assert queue.put.await_count == 1
    reopened.delete(USER, card['id'])
    assert reopened.receipt_for_owner('owner', card['id'])['response']['answers'] == ANSWERS


@pytest.mark.parametrize('answers', [
    {}, {**ANSWERS, 'mode': {'choiceId': 'missing'}}, {**ANSWERS, 'mode': {'text': 'a'}},
    {**ANSWERS, 'targets': {'choiceIds': ['a', 'a']}}, {**ANSWERS, 'targets': {'choiceIds': []}},
    {**ANSWERS, 'note': {'text': ' '}}, {**ANSWERS, 'note': {'text': 'a' * 10001}},
    {**ANSWERS, 'note': {'rejected': True, 'text': 'both'}}, {**ANSWERS, 'unknown': {'text': 'x'}},
    {**ANSWERS, 'note': {'rejected': False}}, {**ANSWERS, 'note': {'rejected': True, 'reason': 'x' * 10001}},
])
def test_invalid_submission_is_atomic(tmp_path, answers):
    store = InteractionStore(tmp_path / 'cards.db'); card = store.create(request())
    with pytest.raises(InteractionError):
        store.respond(USER, card['id'], submission(answers), allow_empty=True)
    current = store.get_for_user(USER, card['id'])
    assert current['status'] == 'open' and current['revision'] == 0
    with store.db() as db:
        assert db.execute('SELECT count(*) FROM response_outbox').fetchone()[0] == 0


def test_all_rejected_and_whole_card_reject_are_terminal(tmp_path):
    for whole in [False, True]:
        store = InteractionStore(tmp_path / f'{whole}.db'); card = store.create(request())
        rejected = {q['id']: {'rejected': True} for q in QUESTIONS}
        data = {'requestId': 'once', 'expectedRevision': 0}
        done = store.reject(USER, card['id'], data) if whole else store.respond(USER, card['id'], submission(rejected))
        assert done['status'] == 'rejected' and done['rejectionDelivery']['state'] == 'pending'
        assert done['delivery']['state'] == 'not_requested'
        if not whole:
            assert store.respond(USER, card['id'], submission(rejected)) == done
            # A different endpoint cannot reuse the same request ID to erase answers.
            with pytest.raises(InteractionError):
                store.reject(USER, card['id'], data)
        with pytest.raises(InteractionError):
            store.respond(USER, card['id'], submission())
        queue = type('Queue', (), {'put': AsyncMock(return_value={'status': 'accepted'}), 'lookup': AsyncMock()})()
        asyncio.run(store.deliver_rejections(queue)); asyncio.run(store.deliver_rejections(queue))
        assert queue.put.await_count == 1


@pytest.mark.parametrize('change', ['question_duplicate', 'choice_duplicate', '21_questions', '21_choices', 'prompt', 'missing_version', 'hybrid'])
def test_definition_limits(change):
    data = request(); definition = data['card']
    if change == 'question_duplicate': definition['questions'][1]['id'] = 'mode'
    if change == 'choice_duplicate': definition['questions'][1]['choices'][1]['id'] = 'a'
    if change == '21_questions': definition['questions'] = [{'id': str(i), 'prompt': 'q', 'type': 'text'} for i in range(21)]
    if change == '21_choices': definition['questions'][0]['choices'] = [{'id': str(i), 'label': 'x'} for i in range(21)]
    if change == 'prompt': definition['questions'][0]['prompt'] = 'x' * 501
    if change == 'missing_version': del definition['schemaVersion']
    if change == 'hybrid': definition['choices'] = []
    with pytest.raises(InteractionError): validate('Create', data)


def test_maximum_unicode_answers_and_legacy_separation(tmp_path):
    store = InteractionStore(tmp_path / 'cards.db')
    questions = [{'id': str(i), 'prompt': '题' * 500, 'type': 'text'} for i in range(20)]
    card = store.create(request(questions))
    done = store.respond(USER, card['id'], submission({str(i): {'text': '文' * 10000} for i in range(20)}))
    validate('Card', done)
    legacy = store.create({'ownerSessionId': 'owner', 'recipientId': 'user', 'requestKey': 'old', 'card': {'title': 'old', 'body': 'body'}})
    with pytest.raises(InteractionError): store.respond(USER, legacy['id'], submission())
    assert store.respond(USER, legacy['id'], {'requestId': 'old', 'expectedRevision': 0, 'text': 'v1 text'})['response']['answer'] == 'v1 text'


def test_api_v2_and_mcp_create_get_receipt(tmp_path, monkeypatch):
    from packages.interactions import api
    from packages.mcp import server
    from packages.core import session
    store = InteractionStore(tmp_path / 'cards.db')
    owner = session.create('producer', workdir=str(tmp_path))
    monkeypatch.setattr(api, 'get_store', lambda: store)
    app = FastAPI(); register(app); app.state.user_inbox_principal = lambda _: USER
    client = TestClient(app)
    monkeypatch.setattr(server, '_local_producer_api', lambda path, body: client.post('/api/interaction-cards', json={'ownerSessionId': owner.id, **body}).json())
    monkeypatch.setattr(server, '_require_caller', lambda: ({'id': owner.id}, None))
    monkeypatch.setattr(server, '_api', lambda method, path: client.request(method, path).json())
    card = server.interaction_card('create', recipient_id='user', request_key='mcp', card=request()['card'])['card']
    assert client.post(f"/api/user-inbox/{card['id']}/submit", json=submission({})).status_code == 422
    response = client.post(f"/api/user-inbox/{card['id']}/submit", json=submission())
    assert response.status_code == 200, response.text
    assert server.interaction_card('get', card_id=card['id'])['card']['response']['answers'] == ANSWERS
    assert server.interaction_card('receipt', card_id=card['id'])['receipt']['response']['answers'] == ANSWERS


def test_trusted_question_scopes_and_independent_unknown_reconciliation(tmp_path, monkeypatch):
    from tests.test_locker_trusted_binding import setup
    async def run():
        svc, adapter, owner, target, calls, old_request = await setup(tmp_path, monkeypatch)
        try:
            questions = [{'id': qid, 'prompt': qid, 'type': 'single_choice', 'choices': old_request['card']['choices']} for qid in ['one', 'two']]
            data = {**request(questions), 'ownerSessionId': owner.id}
            store = InteractionStore(tmp_path / 'cards.db'); card = create(svc, store, data)
            validate('Card', card)
            assert set(card['trustedChoices']) == {'one', 'two'}
            assert card['trustedChoices']['one']['go']['targetIds'] == [target.id]
            answers = {'one': {'choiceId': 'go'}, 'two': {'choiceId': 'go'}}
            store.respond(USER, card['id'], submission(answers))
            fake = type('Adapter', (), {'authorize': AsyncMock(return_value=True), 'accept': AsyncMock(return_value=None),
                                        'lookup': AsyncMock(return_value={'status': 'accepted', 'receiptId': 'receipt'})})()
            first = await store.dispatch(USER, card['id'], fake)
            assert first['delivery']['state'] == 'unknown' and fake.accept.await_count == 2
            ids = {call.args[0]['id'] for call in fake.accept.call_args_list}
            assert len(ids) == 2
            second = await InteractionStore(store.path).dispatch(USER, card['id'], fake)
            assert second['delivery']['state'] == 'accepted' and fake.accept.await_count == 2
            assert {call.args[0] for call in fake.lookup.call_args_list} == ids
            await store.dispatch(USER, card['id'], fake)
            assert fake.accept.await_count == 2
            assert calls == []  # Creation and persistence have no execution authority.
        finally: await svc.close()
    asyncio.run(run())


def test_large_v2_runtime_admission_and_scope_recheck(tmp_path, monkeypatch):
    import httpx
    from packages.interactions import api
    from tests.test_locker_trusted_binding import setup
    from tests.test_automation_core import action
    async def run():
        svc, adapter, owner, target, calls, old_request = await setup(tmp_path, monkeypatch)
        try:
            questions = [{'id': 'one', 'prompt': '执行', 'type': 'multi_choice', 'choices': old_request['card']['choices']},
                         {'id': 'two', 'prompt': '说明', 'type': 'text'}]
            store = InteractionStore(tmp_path / 'cards.db')
            monkeypatch.setattr(api, 'get_store', lambda: store)
            data = {**request(questions), 'ownerSessionId': owner.id}
            card = create(svc, store, data)
            app = FastAPI(); register(app); app.state.automation = svc
            app.state.interaction_trigger_adapter = adapter
            app.state.user_inbox_principal = lambda _: USER
            answers = {'one': {'choiceIds': ['go']}, 'two': {'text': '😀' * 10000}}
            # Include a second full Unicode answer so the directed payload exceeds 64KiB.
            questions.append({'id': 'three', 'prompt': '更多说明', 'type': 'text'})
            data['card']['questions'] = copy.deepcopy(questions)
            data['requestKey'] = 'large'
            card = create(svc, store, data)
            answers['three'] = {'text': '文' * 10000}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                result = await client.post(f"/api/user-inbox/{card['id']}/submit", json=submission(answers))
                assert result.status_code == 200, result.text
                assert result.json()['card']['delivery']['state'] == 'accepted', result.text
                events = svc.events.page()['events']
                assert len(events) == 1 and events[0]['event']['payload']['response']['answers'] == answers
                # Revoked scope/changed meaning blocks the whole card before persistence.
                data['requestKey'] = 'stale'
                stale_card = create(svc, store, data)
                svc.engine.actions.put('actions', action([owner.id]))
                denied = await client.post(f"/api/user-inbox/{stale_card['id']}/submit", json=submission(answers))
                assert denied.status_code == 409 and denied.json()['detail']['code'] == 'stale'
                assert store.get_for_user(USER, stale_card['id'])['status'] == 'open'
        finally: await svc.close()
    asyncio.run(run())


def test_question_event_survives_deletion_without_resend(tmp_path, monkeypatch):
    from tests.test_locker_trusted_binding import setup
    async def run():
        svc, adapter, owner, target, calls, old_request = await setup(tmp_path, monkeypatch)
        try:
            questions = [{'id': 'one', 'prompt': 'one', 'type': 'single_choice', 'choices': old_request['card']['choices']}]
            store = InteractionStore(tmp_path / 'cards.db')
            card = create(svc, store, {**request(questions), 'ownerSessionId': owner.id})
            store.respond(USER, card['id'], submission({'one': {'choiceId': 'go'}}))
            fake = type('Adapter', (), {'authorize': AsyncMock(return_value=True), 'accept': AsyncMock(return_value=None),
                                        'lookup': AsyncMock(return_value={'status': 'accepted', 'receiptId': 'receipt'})})()
            await store.dispatch(USER, card['id'], fake)
            original = fake.accept.call_args.args[0]['id']
            store.delete(USER, card['id'])
            await InteractionStore(store.path).recover_detached(fake)
            assert fake.accept.await_count == 1 and fake.lookup.call_args.args == (original,)
            assert store.receipt_for_owner(owner.id, card['id'])['response']['answers']['one'] == {'choiceId': 'go'}
        finally: await svc.close()
    asyncio.run(run())


def test_two_v2_submissions_have_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store = InteractionStore(tmp_path / 'cards.db'); card = store.create(request())
    def submit(request_id):
        try:
            return InteractionStore(store.path).respond(USER, card['id'], {**submission(), 'requestId': request_id})['status']
        except InteractionError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(submit, ['one', 'two'])) == ['conflict', 'responded']


def test_v1_capacity_and_other_event_limits_remain(tmp_path):
    from packages.automation.events import Bus
    from packages.automation.event_store import EventStore
    with pytest.raises(InteractionError):
        validate('Definition', {'title': 'old', 'body': '文' * 30000})
    bus = Bus(); core = bus.activate('core', 'test'); plugin = bus.activate('plugin', 'plugin')
    bus.register(core, 'test.small', {'type': 'object'})
    with pytest.raises(ValueError): bus.prepare(core, 'test.small', {'text': 'x' * 65536})
    with pytest.raises(ValueError): bus.register(plugin, 'plugin.large', {'type': 'object'}, max_payload_bytes=2_000_000)
    events = EventStore(tmp_path); events.initialize()
    with pytest.raises(ValueError):
        events.append({'id': 'test', 'type': 'test.small', 'schemaVersion': 1, 'source': {'type': 'core', 'id': 'test'},
                       'occurredAt': 'now', 'payload': {'text': 'x' * 65536}}, [])
