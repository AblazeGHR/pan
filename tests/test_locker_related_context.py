import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from packages.core import session
from packages.interactions.api import register, get_store
from packages.interactions.related_context import previous_user_message_id, is_navigation_user, MAX_BYTES, MAX_ROWS
from packages.interactions.store import InteractionStore, UserPrincipal
from tests.test_locker_messages import msg


def append(owner, content='Question', **fields):
    row = {'role': 'user', 'content': content, **fields}
    session.append_history(owner, row)
    return row.get('messageId')


def test_owner_context_delegation_null_automation_and_immutable(tmp_path):
    ma = session.create('MA', workdir=str(tmp_path))
    child = session.create('Child', workdir=str(tmp_path))
    ma.managed.append(child.id); session.save(ma)
    ma_id = append(ma, 'MA question')
    app = FastAPI(); register(app)
    app.state.user_inbox_principal = lambda _: UserPrincipal('user')
    client = TestClient(app, client=('127.0.0.1', 49123))
    def card(key, owner=child.id):
        response = client.post('/api/interaction-card-tools/create',
            headers={'X-Pan-Queue-Caller': ma.id}, json={'ownerSessionId': owner,
            'recipientId': 'user', 'requestKey': key, 'card': {'title': 'Question', 'body': 'Body'}})
        assert response.status_code == 200, response.text
        return response.json()['card']
    def message(document):
        response = client.post('/api/locker-message-tools/create',
            headers={'X-Pan-Queue-Caller': child.id}, json={'message': document})
        assert response.status_code == 200, response.text
        return response.json()['message']
    empty = card('empty'); empty_msg = message(msg(messageId='empty'))
    assert empty['relatedMessageId'] is empty_msg['relatedMessageId'] is None
    child_id = append(child)
    append(child, 'Notification', source='report')
    append(child, 'MA assign', source='agent')
    append(child, ' ////by pan system\nJob complete')
    append(child, '@@@@by agent : report')
    created = card('new'); document = msg(messageId='new'); created_msg = message(document)
    assert created['relatedMessageId'] == created_msg['relatedMessageId'] == child_id != ma_id
    append(child, 'Later user')
    assert card('new') == created and message(document) == created_msg
    assert card('empty') == empty and message(msg(messageId='empty')) == empty_msg
    store = get_store()
    automatic = client.post('/api/interaction-cards', json={'ownerSessionId': child.id,
        'recipientId': 'user', 'requestKey': 'automation', 'card': {'title': 'Job', 'body': ''}}).json()['card']
    from packages.interactions.locker_messages import accept_locker_message
    auto_msg = accept_locker_message(child.id, 'user', msg(messageId='job'))
    assert automatic['relatedMessageId'] is auto_msg['relatedMessageId'] is None
    assert created in client.get('/api/user-inbox').json()['cards']
    assert created_msg in client.get('/api/user-locker-messages').json()['messages']
    with store.db() as db:
        assert db.execute('SELECT related_message_id FROM cards WHERE id=?', (created['id'],)).fetchone()[0] == child_id
        assert db.execute('SELECT related_message_id FROM locker_messages WHERE id=?', (created_msg['id'],)).fetchone()[0] == child_id


def test_bounded_hot_cold_missing_legacy_and_no_hydration(tmp_path, monkeypatch):
    owner = session.create('Owner', workdir=str(tmp_path))
    assert previous_user_message_id('missing') is None
    assert previous_user_message_id(owner.id) is None
    target = append(owner)
    session.save(owner)
    session._cache.clear()
    shallow = session.get(owner.id, load_history=False)
    assert not shallow._history_loaded
    monkeypatch.setattr(session, 'history_page', lambda *a, **k: pytest.fail('must not scan full history'))
    assert previous_user_message_id(owner.id) == target
    assert not shallow._history_loaded
    path = session._history_path(owner.id)
    original = path.read_bytes()
    path.write_bytes(original + b'{"role":"assistant","content":"partial')
    assert previous_user_message_id(owner.id) is None
    path.write_bytes(original + json.dumps({'role': 'assistant', 'content': 'x' * (MAX_BYTES + 10)}).encode() + b'\n')
    assert previous_user_message_id(owner.id) is None
    path.write_bytes(original + (b'{"role":"assistant","content":"tool"}\n' * MAX_ROWS))
    assert previous_user_message_id(owner.id) is None
    path.write_text('{"role":"user","content":"legacy"}\n', encoding='utf-8')
    assert previous_user_message_id(owner.id) is None


def test_navigation_classifier_shared_cases():
    cases = json.loads((Path(__file__).parents[1] / 'packages/web/src/components/chat/navigationUserCases.json').read_text(encoding='utf-8'))
    for case in cases:
        assert bool(is_navigation_user(case['message'])) == case['user'], case


def test_alter_existing_tables_defaults_null(tmp_path):
    path = tmp_path / 'old.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE cards(id TEXT PRIMARY KEY,owner TEXT,recipient TEXT,request_key TEXT,signature TEXT,definition TEXT,created_at TEXT,revision INTEGER,response TEXT,response_key TEXT,event TEXT,delivery TEXT)')
        db.execute('CREATE TABLE locker_messages(seq INTEGER PRIMARY KEY,id TEXT,owner TEXT,recipient TEXT,signature TEXT,message TEXT,created_at TEXT,is_read INTEGER)')
        db.execute("INSERT INTO cards(id) VALUES('old')")
        db.execute("INSERT INTO locker_messages(id) VALUES('old')")
    InteractionStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT related_message_id FROM cards').fetchone() == (None,)
        assert db.execute('SELECT related_message_id FROM locker_messages').fetchone() == (None,)


def test_navigation_wire_prefix_preserves_shared_classification(monkeypatch):
    from packages.web import server
    cases = json.loads((Path(__file__).parents[1] / 'packages/web/src/components/chat/navigationUserCases.json').read_text(encoding='utf-8'))
    rows = [case['message'] for case in cases]
    monkeypatch.setattr(server, '_history_page_lookup', lambda *a: {'history': rows, 'start': 0, 'total': len(rows), 'hasMore': False})
    page = server._navigation_page_lookup('unowned', 0, 500)
    assert [bool(is_navigation_user(row)) for row in page['history']] == [case['user'] for case in cases]
