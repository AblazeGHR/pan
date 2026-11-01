"""Thread invariants at the durable transaction boundary, no real profiles/services."""
import pytest
from packages.interactions.contracts import InteractionError, identity
from packages.interactions.store import InteractionStore, UserPrincipal
from packages.interactions.user_cards import UserCardStore
from tests.test_user_cards import payload
from tests.test_locker_messages import msg
from tests.test_user_locker_lifecycle import make, req

USER = UserPrincipal('user')


def test_concurrent_attachment_admission_is_serialized(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    store = InteractionStore(tmp_path/'cards.sqlite')
    box = UserCardStore(store)
    root = box.create(USER, payload('root'))
    barrier = Barrier(2)

    def attach(key):
        barrier.wait(timeout=5)
        try:
            return box.create(USER, {**payload(key), 'parent': {'type': 'user_card', 'id': root['id']}})
        except InteractionError as error:
            assert error.code == 'conflict'
            return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attach, ['first', 'second']))
    assert sum(isinstance(result, dict) for result in outcomes) == 1
    assert sum(isinstance(result, InteractionError) for result in outcomes) == 1
    assert len(box.list(principal=USER)) == 2
    with box.db() as db:
        assert db.execute('SELECT count(*) FROM user_card_outbox').fetchone()[0] == 2


@pytest.mark.parametrize('kind', ['locker_message', 'interaction_card', 'user_card'])
def test_parent_roundtrip_terminal_read_independence_and_cascade(tmp_path, kind):
    store = InteractionStore(tmp_path/'cards.sqlite')
    box = UserCardStore(store)
    if kind == 'locker_message':
        parent = store.accept_message('owner', 'user', msg())
    elif kind == 'interaction_card':
        parent = make(store)
    else:
        parent = box.create(USER, payload('parent'))
    link = {'type': kind, 'id': parent['id']}
    child_data = {**payload('child'), 'parent': link}
    child = box.create(USER, child_data)
    grand_data = {**payload('grand'), 'parent': {'type': 'user_card', 'id': child['id']}}
    grand = box.create(USER, grand_data)
    assert box.create(USER, child_data) == child
    # A new request cannot create a sibling; linked leaves may still grow a chain.
    with pytest.raises(InteractionError, match='already has an attached') as duplicate:
        box.create(USER, {**child_data, 'requestKey': 'duplicate'})
    assert duplicate.value.code == 'conflict'
    assert box.create(USER, grand_data) == grand
    assert UserCardStore(InteractionStore(store.path)).list(owner='owner')[-2]['parent'] == link
    if kind == 'locker_message':
        store.mark_message_read(USER, parent['id'])
    elif kind == 'interaction_card':
        store.reject(USER, parent['id'], req())
    else:
        box.answer('owner', {'cardId': parent['id'], 'body': 'done', 'expectedRevision': 0})
    # Parent terminal/read changes preserve every child, its state and its revision.
    assert box.list(owner='owner')[-2:] == [child, grand]
    answered = box.answer('owner', {'cardId': child['id'], 'body': 'answer', 'expectedRevision': 0})
    assert not answered['read'] and box.list(owner='owner')[-1] == grand
    assert store.unread_summary() >= 1
    if kind == 'locker_message':
        assert store.get_message(USER, parent['id'])['read']
        store.delete_message(USER, parent['id'])
    elif kind == 'interaction_card':
        store.delete(USER, parent['id'])
    else:
        assert not box.list(principal=USER)[0]['read']
        box.delete(USER, parent['id'])
    assert box.list(principal=USER) == []
    assert box.reminders() == []
    for data in (child_data, grand_data):
        with pytest.raises(InteractionError, match='deleted'):
            box.create(USER, data)
    with pytest.raises(InteractionError, match='Parent unavailable'):
        box.create(USER, {**payload('after-delete'), 'parent': link})


@pytest.mark.parametrize('parent', [True, [], {}, {'type': 'unknown', 'id': 'a'},
    {'type': [], 'id': 'a'}, {'type': 'user_card', 'id': 'a', 'extra': 1},
    {'type': 'user_card', 'id': ''}])
def test_parent_schema_rejected(tmp_path, parent):
    box = UserCardStore(InteractionStore(tmp_path/'cards.sqlite'))
    with pytest.raises(InteractionError):
        box.create(USER, {**payload(), 'parent': parent})
    assert box.list(principal=USER) == []


def test_no_answer_needed_does_not_cascade(tmp_path):
    store = InteractionStore(tmp_path/'cards.sqlite')
    box = UserCardStore(store)
    request = {'ownerSessionId': 'owner', 'recipientId': 'user', 'requestKey': 'one',
               'card': {'title': 'Question', 'body': 'body'}}
    parent = store.create(request, producer_id='producer')
    child = box.create(USER, {**payload(), 'parent': {'type': 'interaction_card', 'id': parent['id']}})
    store.no_answer_needed('producer', parent['id'], 0)
    assert box.list(owner='owner') == [child]
    store.delete(USER, parent['id'])
    assert box.list(owner='owner') == []


def test_scope_types_and_cycles(tmp_path):
    store = InteractionStore(tmp_path/'cards.sqlite')
    box = UserCardStore(store)
    parent = box.create(USER, payload('parent'))
    link = {'type': 'user_card', 'id': parent['id']}
    for data in ({**payload('child', owner='foreign'), 'parent': link},
                 {**payload('child'), 'parent': {'type': 'interaction_card', 'id': parent['id']}}):
        with pytest.raises(InteractionError, match='Parent unavailable'):
            box.create(USER, data)
    with pytest.raises(InteractionError):
        box.create(UserPrincipal('foreign'), {**payload('child'), 'parent': link})
    self_id = identity('ucard_', ['user', 'owner', 'self'])
    with pytest.raises(InteractionError, match='cycle'):
        box.create(USER, {**payload('self'), 'parent': {'type': 'user_card', 'id': self_id}})
    # Corrupt historical input cannot make admission or traversal loop forever.
    with box.db() as db:
        db.execute('UPDATE user_cards SET parent_type=?,parent_id=? WHERE id=?',
                   ('user_card', parent['id'], parent['id']))
    with pytest.raises(InteractionError, match='cycle'):
        box.create(USER, {**payload('cycle-child'), 'parent': link})


def test_legacy_schema_upgrade_and_deep_chain(tmp_path):
    store = InteractionStore(tmp_path/'cards.sqlite')
    box = UserCardStore(store)
    root = box.create(USER, payload('root'))
    assert root['parent'] is None
    # Build a depth beyond Python's recursion limit, bypassing the unrelated capacity limit.
    with box.db() as db:
        row = dict(db.execute('SELECT * FROM user_cards').fetchone())
        for i in range(1500):
            row.update(id=f'deep{i}', parent_type='user_card', parent_id=root['id'] if i == 0 else f'deep{i-1}')
            columns = [key for key in row if key != 'seq']
            db.execute(f"INSERT INTO user_cards({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                       [row[key] for key in columns])
    box.delete(USER, root['id'])
    assert box.list(principal=USER) == []


def test_upgrade_preserves_old_cards(tmp_path):
    store = InteractionStore(tmp_path/'cards.sqlite')
    box = UserCardStore(store)
    old = box.create(USER, payload('old'))
    with box.db() as db:
        db.execute('DROP INDEX user_cards_parent')
        db.execute('ALTER TABLE user_cards DROP COLUMN parent_type')
        db.execute('ALTER TABLE user_cards DROP COLUMN parent_id')
    reopened = UserCardStore(InteractionStore(store.path))
    assert reopened.list(principal=USER) == [old]
    with reopened.db() as db:
        assert {'parent_type', 'parent_id'} <= {row[1] for row in db.execute('PRAGMA table_info(user_cards)')}


def test_cascade_failure_rolls_back_parent_and_descendants(tmp_path):
    import sqlite3
    store = InteractionStore(tmp_path/'cards.sqlite')
    box = UserCardStore(store)
    parent = store.accept_message('owner', 'user', msg())
    child = box.create(USER, {**payload(), 'parent': {'type': 'locker_message', 'id': parent['id']}})
    with box.db() as db:
        db.execute("CREATE TRIGGER deny_delete BEFORE DELETE ON user_cards BEGIN SELECT RAISE(ABORT, 'injected'); END")
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        store.delete_message(USER, parent['id'])
    assert store.get_message(USER, parent['id']) == parent
    assert box.list(principal=USER) == [child]
    with box.db() as db:
        assert db.execute('SELECT count(*) FROM user_card_deleted').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM locker_message_tombstones').fetchone()[0] == 0


def test_http_user_create_and_owner_tool_return_parent(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from packages.core import session
    from packages.interactions.api import register, get_store
    from packages.interactions.user_cards import ReminderScheduler
    from unittest.mock import AsyncMock
    app = FastAPI()
    register(app)
    source = session.create('thread-owner', workdir=str(tmp_path))
    app.state.user_inbox_principal = lambda request: USER if request.headers.get('Authorization') == 'Bearer test' else None
    store = get_store()
    box = UserCardStore(store)
    app.state.user_card_scheduler = ReminderScheduler(box, AsyncMock(return_value=True))
    parent = store.accept_message(source.id, 'user', msg())
    data = {**payload('http-child', owner=source.id), 'parent': {'type': 'locker_message', 'id': parent['id']}}
    client = TestClient(app, client=('127.0.0.1', 12345))
    auth = {'Authorization': 'Bearer test'}
    assert client.post('/api/reminding-cards', json=data).status_code == 401
    created = client.post('/api/reminding-cards', json=data, headers=auth)
    assert created.status_code == 200, created.text
    card = created.json()['card']
    assert card['parent'] == data['parent']
    assert client.post('/api/reminding-cards', json=data, headers=auth).json()['card'] == card
    duplicate = client.post('/api/reminding-cards', json={**data, 'requestKey': 'duplicate'}, headers=auth)
    assert duplicate.status_code == 409
    assert client.get('/api/reminding-cards', headers=auth).json()['cards'] == [card]
    caller = {'X-Pan-Queue-Caller': source.id}
    assert client.post('/api/reminding-card-tools/list_pending', json={}, headers=caller).json()['cards'] == [card]
    assert client.post('/api/reminding-card-tools/create', json=data, headers=caller).status_code == 422
    store.delete_message(USER, parent['id'])
    assert client.post('/api/reminding-card-tools/list_pending', json={}, headers=caller).json()['cards'] == []
