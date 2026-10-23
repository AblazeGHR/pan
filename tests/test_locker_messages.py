"""Dedicated Locker messages: persistence, identity, migration and user scope."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from packages.core import session
from packages.interactions.api import register, get_store
from packages.interactions.contracts import InteractionError
from packages.interactions.locker_messages import accept_locker_message
from packages.interactions.store import InteractionStore, UserPrincipal

USER = UserPrincipal('user')


def msg(mid='first', **extra):
    return {'messageId': mid, 'role': 'assistant', 'content': '## Dedicated Locker msg',
            'ts': '2026-10-23T12:00:00Z', 'parts': [{'type': 'text', 'text': '## Dedicated Locker msg'}],
            **extra}


def test_persist_unknown_parts_fields_idempotency_conflict_and_no_history(tmp_path):
    store = InteractionStore(tmp_path/'cards.sqlite')
    value = msg(future={'v': [1, 'two']}, parts=[{'type': 'text', 'text': 'hi', 'extra': 1},
                 {'type': 'future-part', 'bytes': {'x': True}}])
    row = store.accept_message('source', 'user', value)
    assert row['kind'] == 'message' and 'definition' not in row
    assert row['message'] == value and not row['read']
    assert InteractionStore(store.path).get_message(USER, row['id']) == row
    assert store.accept_message('source', 'user', deepcopy(value)) == row
    with pytest.raises(InteractionError, match='conflict'):
        store.accept_message('source', 'user', {**value, 'content': 'changed'})
    assert store.accept_message('other-source', 'user', value)['id'] != row['id']
    assert store.accept_message('source', 'another-user', value)['id'] != row['id']
    assert list(tmp_path.glob('*.history.jsonl')) == []


def test_read_delete_original_identity_tombstone_and_no_notification(tmp_path):
    store = InteractionStore(tmp_path/'cards.sqlite')
    row = store.accept_message('source', 'user', msg())
    first_read = store.mark_message_read(USER, row['id'])
    assert first_read['read'] and first_read['revision'] == row['revision'] + 1
    assert not InteractionStore(store.path).mark_message_read(USER, row['id'], False)['read']
    with pytest.raises(InteractionError):
        store.mark_message_read(USER, row['id'], 1)
    for operation in (store.get_message, store.mark_message_read, store.delete_message):
        with pytest.raises(InteractionError, match='not found'):
            operation(UserPrincipal('other'), row['id'])
    assert store.delete_message(USER, row['id']) == store.delete_message(USER, row['id'])
    with pytest.raises(InteractionError, match='deleted'):
        store.accept_message('source', 'user', msg())
    with store.db() as db:
        assert db.execute('SELECT count(*) FROM locker_messages').fetchone()[0] == 0
        tombstone = dict(db.execute('SELECT * FROM locker_message_tombstones').fetchone())
        assert 'Dedicated' not in json.dumps(tombstone)
        assert db.execute('SELECT count(*) FROM response_outbox').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM rejection_outbox').fetchone()[0] == 0


def test_cursor_is_query_only_scoped_stable_after_delete_and_no_capacity_truncation(tmp_path):
    store = InteractionStore(tmp_path/'cards.sqlite', capacity=3)
    rows = [store.accept_message('source', 'user', msg(str(i))) for i in range(3)]
    page = store.list_messages(USER, limit=1)
    assert page['messages'] == [rows[0]] and not page['messages'][0]['read']
    store.delete_message(USER, rows[0]['id'])
    assert store.list_messages(USER, limit=2, after=page['nextAfter'])['messages'] == rows[1:]
    with pytest.raises(InteractionError, match='cursor'):
        store.list_messages(UserPrincipal('other'), after=page['nextAfter'])
    for bad in ('not-base64', 'e30=', 'W10=', '!!!'):
        with pytest.raises(InteractionError):
            store.list_messages(USER, after=bad)
    store.accept_message('source', 'user', msg('3'))
    with pytest.raises(InteractionError, match='capacity'):
        store.accept_message('empty-source', 'user', msg('4'))
    assert len(store.list_messages(USER)['messages']) == 3
    with store.db() as db:
        assert db.execute('SELECT 1 FROM user_lockers WHERE owner=?', ('empty-source',)).fetchone() is None


@pytest.mark.parametrize('change', [
    {'messageId': ''}, {'role': 3}, {'content': None}, {'ts': 'invalid'},
    {'ts': '2026-10-23T12:00:00'}, {'parts': [{'type': 'text'}]},
    {'parts': [{'type': 'attachment', 'attachmentId': '../secret', 'displayName': 'bad'}]},
    {'content': 'x' * 66000},
])
def test_schema_rejects_invalid_without_creating_locker(tmp_path, change):
    store = InteractionStore(tmp_path/'cards.sqlite')
    with pytest.raises(InteractionError):
        store.accept_message('source', 'user', msg(**change))
    with store.db() as db:
        assert db.execute('SELECT count(*) FROM user_lockers').fetchone()[0] == 0


def test_legacy_cards_migration_is_additive_idempotent_and_concurrent_admission(tmp_path):
    path = tmp_path/'cards.sqlite'
    # Pre-Locker legacy schema without is_read/bindings or metadata columns.
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE cards(id TEXT PRIMARY KEY, owner TEXT NOT NULL, recipient TEXT NOT NULL,
            request_key TEXT NOT NULL, signature TEXT NOT NULL, definition TEXT NOT NULL, created_at TEXT NOT NULL,
            revision INTEGER NOT NULL DEFAULT 0, response TEXT, response_key TEXT, event TEXT, delivery TEXT NOT NULL,
            UNIQUE(owner,request_key))''')
        db.execute('INSERT INTO cards VALUES(?,?,?,?,?,?,?,0,NULL,NULL,NULL,?)',
                   ('legacy', 'source', 'user', 'old', 'old', '{"title":"Old","body":"Retained"}',
                    '2026-10-22T00:00:00Z', '{"state":"not_requested"}'))
    for _ in range(3):
        store = InteractionStore(path)
        assert store.get_for_user(USER, 'legacy')['definition']['body'] == 'Retained'
    def admit(_):
        return InteractionStore(path).accept_message('source', 'user', msg())
    with ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(admit, range(8)))
    assert len({row['id'] for row in rows}) == 1
    assert len(store.list_messages(USER)['messages']) == 1
    assert store.list_for_user(USER)[0]['id'] == 'legacy'


def test_internal_source_boundary_and_user_api_auth_scope(tmp_path):
    source = session.create('Locker-source', workdir=str(tmp_path))
    untouched = session.create('No dedicated messages', workdir=str(tmp_path))
    row = accept_locker_message(source.id, 'user', msg())
    assert session.get(source.id).history == []
    with pytest.raises(InteractionError):
        accept_locker_message('missing', 'user', msg())
    app = FastAPI(); register(app); client = TestClient(app)
    assert client.get('/api/user-locker-messages').status_code == 503
    app.state.user_inbox_principal = lambda req: USER if req.headers.get('authorization') == 'Bearer fake' else None
    assert client.get('/api/user-locker-messages').status_code == 401
    headers = {'Authorization': 'Bearer fake'}
    assert client.get('/api/user-locker-messages', headers=headers).json()['messages'] == [row]
    lockers = client.get('/api/user-lockers', headers=headers).json()['lockers']
    assert lockers[0]['total'] == 1 and lockers[0]['unread'] == 1
    assert all(locker['sessionId'] != untouched.id for locker in lockers)
    assert client.put(f"/api/user-locker-messages/{row['id']}/read", headers=headers, json={'read': True}).json()['message']['read']
    assert client.put(f"/api/user-locker-messages/{row['id']}/read", headers=headers, json={'read': False, 'recipientId': 'other'}).status_code == 422
    assert client.post('/api/user-locker-messages', headers=headers, json=msg()).status_code == 405
    app.state.user_inbox_principal = lambda req: UserPrincipal('other')
    assert client.get(f"/api/user-locker-messages/{row['id']}", headers=headers).status_code == 404
    assert client.delete(f"/api/user-locker-messages/{row['id']}", headers=headers).status_code == 404
    assert get_store().get_message(USER, row['id'])['read']
