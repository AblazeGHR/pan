import concurrent.futures
import threading

import pytest

from packages.interactions.contracts import InteractionError, validate
from packages.interactions.store import InteractionStore, UserPrincipal


def create(store, key='q', producer='producer', owner='owner'):
    return store.create({'ownerSessionId': owner, 'recipientId': 'user', 'requestKey': key,
                         'card': {'title': 'Question', 'body': 'Keep visible', 'answerInput': {}}},
                        producer_id=producer)


def test_terminal_retained_idempotent_no_response_or_notice_and_delete(tmp_path):
    store = InteractionStore(tmp_path / 'cards.sqlite')
    card = create(store)
    done = store.no_answer_needed('producer', card['id'], 0)
    validate('Card', done)
    assert done['status'] == 'no_answer_needed' and done['revision'] == 1
    assert done['response'] is None and done['delivery']['state'] == 'not_requested'
    assert store.no_answer_needed('producer', card['id'], 0) == done
    store = InteractionStore(store.path)
    assert store.list_pending_for_producer('producer')['cards'] == []
    assert store.list_for_user(UserPrincipal('user')) == [done]
    with store.db() as db:
        assert db.execute('SELECT count(*) FROM response_outbox').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM rejection_outbox').fetchone()[0] == 0
    for action in (store.respond, store.reject):
        with pytest.raises(InteractionError, match='no longer requires'):
            action(UserPrincipal('user'), card['id'], {'requestId': 'late', 'expectedRevision': 1, 'answer': 'late'})
    with pytest.raises(InteractionError):
        store.mark_read(UserPrincipal('user'), card['id'])
    assert store.delete(UserPrincipal('user'), card['id'])['deleted']
    with pytest.raises(InteractionError):
        store.no_answer_needed('producer', card['id'], 0)
    with pytest.raises(InteractionError, match='deleted'):
        create(store)


def test_creator_not_owner_or_manager_and_pagination(tmp_path):
    store = InteractionStore(tmp_path / 'cards.sqlite')
    cards = [create(store, str(i)) for i in range(3)]
    create(store, 'foreign', producer='foreign')
    legacy = store.create({'ownerSessionId': 'producer', 'recipientId': 'user', 'requestKey': 'legacy',
                           'card': {'title': 'Old', 'body': ''}})
    page = store.list_pending_for_producer('producer', limit=1)
    assert [c['id'] for c in page['cards']] == [cards[0]['id']]
    assert store.list_pending_for_producer('producer', after=page['nextAfter'])['cards'][0]['id'] == cards[1]['id']
    for actor in ('owner', 'manager', 'foreign'):
        with pytest.raises(InteractionError):
            store.no_answer_needed(actor, cards[0]['id'], 0)
    with pytest.raises(InteractionError):
        store.no_answer_needed('producer', legacy['id'], 0)
    with pytest.raises(InteractionError):
        store.no_answer_needed('producer', cards[0]['id'], 9)
    for limit in (0, 501, True):
        with pytest.raises(InteractionError):
            store.list_pending_for_producer('producer', limit=limit)


@pytest.mark.parametrize('operation', ['respond', 'reject'])
def test_two_terminal_writers_barrier(tmp_path, operation):
    store = InteractionStore(tmp_path / 'cards.sqlite')
    for i in range(12):
        card = create(store, str(i))
        barrier = threading.Barrier(2)
        def run(mark):
            barrier.wait()
            try:
                if mark:
                    return store.no_answer_needed('producer', card['id'], 0)['status']
                return getattr(store, operation)(UserPrincipal('user'), card['id'],
                    {'requestId': 'user', 'expectedRevision': 0, 'answer': 'answer'})['status']
            except InteractionError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(run, [True, False]))
        assert results.count('conflict') == 1, results
        done = store.get_for_user(UserPrincipal('user'), card['id'])
        assert done['revision'] == 1 and done['status'] in results


@pytest.mark.parametrize('state', ['pending', 'in_flight', 'unknown', 'accepted', 'rejected'])
def test_effect_state_never_overwritten(tmp_path, state):
    store = InteractionStore(tmp_path / 'cards.sqlite')
    card = create(store)
    with store.db() as db:
        db.execute('UPDATE cards SET delivery=? WHERE id=?', ('{"state":"'+state+'"}', card['id']))
    with pytest.raises(InteractionError):
        store.no_answer_needed('producer', card['id'], 0)


def test_old_schema_migration_never_invents_creator(tmp_path):
    import sqlite3
    path = tmp_path / 'cards.sqlite'
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE cards(id TEXT PRIMARY KEY, owner TEXT NOT NULL, recipient TEXT NOT NULL,
            request_key TEXT NOT NULL, signature TEXT NOT NULL, definition TEXT NOT NULL, created_at TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0, response TEXT, response_key TEXT, event TEXT, delivery TEXT NOT NULL,
            UNIQUE(owner,request_key))""")
        db.execute("INSERT INTO cards VALUES(?,?,?,?,?,?,?,0,NULL,NULL,NULL,?)", ('old','producer','user','old','{}',
            '{"title":"Old","body":"Keep"}', '2026-10-01T00:00:00Z', '{"state":"not_requested"}'))
    store = InteractionStore(path)
    assert store.get_for_user(UserPrincipal('user'), 'old')['status'] == 'open'
    assert store.list_pending_for_producer('producer')['cards'] == []
    assert InteractionStore(path).get_for_user(UserPrincipal('user'), 'old')['revision'] == 0
    with pytest.raises(InteractionError):
        store.no_answer_needed('producer', 'old', 0)


def test_trusted_http_creator_scope_and_negative_controls(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from packages.core import session
    from packages.interactions.api import register, get_store
    owner = session.create('owner', workdir=str(tmp_path))
    other = session.create('other', workdir=str(tmp_path))
    app = FastAPI(); register(app)
    app.state.user_inbox_principal = lambda request: UserPrincipal('user') if request.headers.get('authorization') == 'Bearer test' else None
    client = TestClient(app, client=('127.0.0.1', 12345))
    headers = {'X-Pan-Queue-Caller': owner.id}
    payload = {'recipientId':'user', 'requestKey':'q', 'card':{'title':'Question','body':'Keep'}}
    route = '/api/interaction-card-tools/'
    remote = TestClient(app, client=('192.0.2.1', 12345))
    assert remote.post(route+'list_pending', json={}, headers=headers).status_code == 403
    for h in ({}, {**headers,'Origin':'http://localhost'}, {**headers,'Sec-Fetch-Site':'same-origin'}):
        assert client.post(route+'create', json=payload, headers=h).status_code == 403
    card = client.post(route+'create', json=payload, headers=headers).json()['card']
    assert client.post(route+'list_pending', json={'ownerSessionId':other.id}, headers=headers).status_code == 422
    assert client.post(route+'list_pending', json={}, headers=headers).json()['cards'][0]['id'] == card['id']
    mark = {'cardId':card['id'],'expectedRevision':0}
    assert client.post(route+'no_answer_needed', json=mark, headers={'X-Pan-Queue-Caller':other.id}).status_code == 404
    assert client.post(route+'no_answer_needed', json={**mark,'producerSessionId':owner.id}, headers=headers).status_code == 422
    assert client.post(route+'no_answer_needed', json=mark, headers=headers).json()['card']['status'] == 'no_answer_needed'
    for operation in ('submit','respond','reject'):
        assert client.post(f"/api/user-inbox/{card['id']}/{operation}", json={'requestId':'stale','expectedRevision':0},
                           headers={'Authorization':'Bearer test'}).status_code == 409
    assert client.post(f"/api/user-inbox/{card['id']}/dispatch", headers={'Authorization':'Bearer test'}).status_code == 409
    assert client.put(f"/api/user-inbox/{card['id']}/read", json={'read':True}, headers={'Authorization':'Bearer test'}).status_code == 409
    assert client.post(route+'delete', json={'cardId':card['id']}, headers=headers).status_code == 422
    assert client.delete(f"/api/user-inbox/{card['id']}",headers=headers).status_code == 401
    assert client.delete(f"/api/user-inbox/{card['id']}",headers={'Authorization':'Bearer test'}).status_code == 200
    # Untrusted legacy create payloads never acquire creator rights.
    forged = get_store().create({'ownerSessionId':owner.id,'producerSessionId':owner.id,'recipientId':'user',
        'requestKey':'forged','card':{'title':'Forged','body':''}})
    assert client.post(route+'list_pending',json={},headers=headers).json()['cards'] == []
    assert client.post(route+'no_answer_needed',json={'cardId':forged['id'],'expectedRevision':0},headers=headers).status_code == 404
    owner.managed.append(other.id)
    session.save(owner)
    delegated = client.post(route+'create', json={**payload,'requestKey':'delegated','ownerSessionId':other.id}, headers=headers).json()['card']
    other_headers = {'X-Pan-Queue-Caller':other.id}
    assert client.post(route+'list_pending',json={},headers=other_headers).json()['cards'] == []
    assert client.post(route+'no_answer_needed',json={'cardId':delegated['id'],'expectedRevision':0},headers=other_headers).status_code == 404
    assert client.post(route+'no_answer_needed',json={'cardId':delegated['id'],'expectedRevision':0},headers=headers).status_code == 200
    # Retrying an old untrusted identity through trusted create cannot claim it.
    assert client.post(route+'create',json={'recipientId':'user','requestKey':'forged','card':{'title':'Forged','body':''}},headers=headers).status_code == 200
    assert client.post(route+'list_pending',json={},headers=headers).json()['cards'] == []


def test_mcp_remote_and_owner_override_fail_closed(monkeypatch):
    from packages.mcp import server
    monkeypatch.setenv('PAN_AGENT_SESSION_ID','self')
    monkeypatch.setattr(server, '_terminal_transport', 'sse')
    assert server.interaction_card('list_pending')['error'] == 'local_stdio_caller_required'
    assert server.interaction_card('no_answer_needed',owner_session_id='self')['error']


@pytest.mark.parametrize('operation', ['respond', 'reject'])
@pytest.mark.parametrize('winner', ['producer', 'user'])
def test_each_writer_wins_while_other_waits_at_transaction_barrier(tmp_path, operation, winner):
    from contextlib import contextmanager
    store = InteractionStore(tmp_path / 'cards.sqlite')
    card = create(store)
    original = store.db
    acquired = threading.Event()
    contender = threading.Event()
    release = threading.Event()
    winning_thread = []
    @contextmanager
    def gated():
        is_winner = threading.get_ident() == winning_thread[0]
        if not is_winner:
            contender.set()
        with original() as db:
            if is_winner:
                acquired.set()
                assert release.wait(5)
            yield db
    store.db = gated
    def run(actor):
        if actor == winner:
            winning_thread.append(threading.get_ident())
        try:
            if actor == 'producer':
                return store.no_answer_needed('producer', card['id'], 0)['status']
            return getattr(store, operation)(UserPrincipal('user'), card['id'],
                {'requestId':'user', 'expectedRevision':0, 'answer':'answer'})['status']
        except InteractionError as exc:
            return exc.code
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        first = pool.submit(run, winner)
        assert acquired.wait(5)
        second = pool.submit(run, 'user' if winner == 'producer' else 'producer')
        assert contender.wait(5)
        release.set()
        assert first.result() != 'conflict'
        assert second.result() == 'conflict'
    store.db = original
    done = store.get_for_user(UserPrincipal('user'), card['id'])
    assert done['revision'] == 1
    assert (done['status'] == 'no_answer_needed') == (winner == 'producer')
