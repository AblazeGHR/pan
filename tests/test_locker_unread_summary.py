import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from packages.interactions import api
from packages.interactions.store import InteractionStore
from packages.interactions.user_cards import UserCardStore

SECRET = 'SECRET-CARD-TITLE'


def insert_card(db, card_id, owner, request_key, is_read, definition='{}'):
    db.execute("INSERT INTO cards(id,owner,recipient,request_key,signature,definition,created_at,delivery,is_read) VALUES(?,?,?,?,?,?,?,?,?)",
               (card_id, owner, 'user', request_key, 'sig', definition, 'now', '{}', is_read))


def insert_message(db, message_id, owner, is_read):
    db.execute("INSERT INTO locker_messages(id,owner,recipient,signature,message,created_at,is_read) VALUES(?,?,?,?,?,?,?)",
               (message_id, owner, 'user', 'sig', '{}', 'now', is_read))


def insert_user_card(db, card_id, owner, answered_at, is_read, signature='sig'):
    db.execute("INSERT INTO user_cards(id,owner,recipient,signature,kind,body,attachments,created_at,answered_at,is_read) VALUES(?,?,?,?,?,?,?,?,?,?)",
               (card_id, owner, 'user', signature, 'question', 'answer', '[]', 'now', answered_at, is_read))


def make_store(tmp_path):
    store = InteractionStore(tmp_path / 'cards.sqlite')
    UserCardStore(store)
    with store.db() as db:
        insert_card(db, 'card-unread', 'owner', 'r1', 0, json.dumps({'title': SECRET}))
        insert_card(db, 'card-read', 'owner', 'r2', 1)
        insert_message(db, 'message-unread', 'owner', 0)
        insert_message(db, 'message-read', 'owner', 1)
        insert_user_card(db, 'reminding-answered', 'owner', 'answered', 0)
        insert_user_card(db, 'reminding-pending', 'owner', None, 0, 'sig2')
        # A second Session with unread items, to prove the map is per owner.
        insert_card(db, 'card-other-a', 'owner-two', 'r3', 0)
        insert_card(db, 'card-other-b', 'owner-two', 'r4', 0)
        insert_message(db, 'message-other', 'owner-two', 0)
        # A Session whose only items are read: it must be absent, never zero.
        insert_card(db, 'card-read-only', 'owner-read', 'r5', 1)
        insert_user_card(db, 'reminding-read-only-pending', 'owner-read', None, 0, 'sig3')
    return store


def mount(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    monkeypatch.setattr(api, 'get_store', lambda: store)
    app = FastAPI()
    api.register(app)
    return TestClient(app).get('/api/locker-unread-summary')


def test_summary_returns_total_and_per_session_unread_counts(tmp_path, monkeypatch):
    response = mount(tmp_path, monkeypatch)
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {'unread', 'sessions'}
    assert payload['unread'] == 6
    assert payload['sessions'] == {'owner': 3, 'owner-two': 3}
    assert payload['unread'] == sum(payload['sessions'].values())


def test_summary_excludes_read_and_unanswered_items(tmp_path, monkeypatch):
    sessions = mount(tmp_path, monkeypatch).json()['sessions']
    # A source whose items are all read/pending is absent, not a fabricated 0.
    assert 'owner-read' not in sessions
    assert all(isinstance(count, int) and count > 0 for count in sessions.values())


def test_summary_leaks_no_content_or_identifiers(tmp_path, monkeypatch):
    response = mount(tmp_path, monkeypatch)
    assert SECRET not in response.text
    for leaked in ('card-unread', 'card-other-a', 'message-unread', 'reminding-answered'):
        assert leaked not in response.text
    assert set(response.json()['sessions']) == {'owner', 'owner-two'}


def test_summary_does_not_require_bearer(tmp_path, monkeypatch):
    assert mount(tmp_path, monkeypatch).status_code == 200
